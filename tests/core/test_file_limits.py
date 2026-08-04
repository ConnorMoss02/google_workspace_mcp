"""Tests for WORKSPACE_MCP_MAX_FILE_BYTES and download_media_bytes."""

from __future__ import annotations

import io
from unittest.mock import Mock, patch

import pytest

from core.file_limits import (
    FileTooLargeError,
    download_media_bytes,
    ensure_within_file_size_limit,
    get_max_file_bytes,
)


class _FakeDownloader:
    """Writes all bytes on init; next_chunk reports done immediately."""

    def __init__(self, fh, _request, data: bytes):
        fh.write(data)
        fh.seek(0)
        self._fh = fh

    def next_chunk(self):
        return None, True


class _ChunkedDownloader:
    """Writes one chunk per next_chunk call so mid-download limits can fire."""

    def __init__(self, fh, _request, chunks: list[bytes]):
        self._fh = fh
        self._chunks = list(chunks)
        self._idx = 0

    def next_chunk(self):
        if self._idx >= len(self._chunks):
            return None, True
        self._fh.write(self._chunks[self._idx])
        self._idx += 1
        done = self._idx >= len(self._chunks)
        return None, done


def test_get_max_file_bytes_default_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    assert get_max_file_bytes() is None


def test_get_max_file_bytes_zero_uncapped(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "0")
    assert get_max_file_bytes() is None


def test_get_max_file_bytes_positive(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "5242880")
    assert get_max_file_bytes() == 5242880


def test_get_max_file_bytes_invalid_uncapped(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "nope")
    assert get_max_file_bytes() is None


def test_ensure_within_file_size_limit_noop_when_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    ensure_within_file_size_limit(10**12, file_name="huge.bin")


def test_ensure_within_file_size_limit_raises(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    with pytest.raises(FileTooLargeError) as exc:
        ensure_within_file_size_limit(
            101,
            file_name="huge.bin",
            file_id="abc",
            web_view_link="https://drive.google.com/file/d/abc",
        )
    msg = str(exc.value)
    assert "101" in msg
    assert "100" in msg
    assert "huge.bin" in msg
    assert "https://drive.google.com/file/d/abc" in msg
    assert "get_doc_content" in msg


@pytest.mark.asyncio
async def test_download_media_bytes_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    data = b"hello-world"

    with patch(
        "core.file_limits.MediaIoBaseDownload",
        side_effect=lambda fh, req: _FakeDownloader(fh, req, data),
    ):
        result = await download_media_bytes(Mock())
    assert result == data


@pytest.mark.asyncio
async def test_download_media_bytes_rejects_over_limit(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10")
    data = b"x" * 50

    with patch(
        "core.file_limits.MediaIoBaseDownload",
        side_effect=lambda fh, req: _FakeDownloader(fh, req, data),
    ):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(Mock(), file_name="big.bin", file_id="f1")
    assert "big.bin" in str(exc.value)
    assert "50" in str(exc.value)


@pytest.mark.asyncio
async def test_download_media_bytes_aborts_mid_stream(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "15")
    chunks = [b"a" * 10, b"b" * 10, b"c" * 10]

    with patch(
        "core.file_limits.MediaIoBaseDownload",
        side_effect=lambda fh, req: _ChunkedDownloader(fh, req, chunks),
    ):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(Mock(), file_name="stream.bin")
    # After second chunk we are at 20 bytes > 15
    assert "20" in str(exc.value) or "stream.bin" in str(exc.value)
