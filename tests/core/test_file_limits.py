"""Tests for WORKSPACE_MCP_MAX_FILE_BYTES and download_media_bytes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from core.file_limits import (
    FileTooLargeError,
    download_media_bytes,
    ensure_within_file_size_limit,
    get_max_file_bytes,
)


class _FakeDownloader:
    """Writes all bytes on init; next_chunk reports done immediately."""

    def __init__(self, fh, _request, data: bytes, chunksize=None):
        fh.write(data)
        fh.seek(0)
        self._fh = fh
        self.chunksize = chunksize

    def next_chunk(self):
        return None, True


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
        side_effect=lambda fh, req, chunksize=None: _FakeDownloader(
            fh, req, data, chunksize=chunksize
        ),
    ):
        result = await download_media_bytes(Mock())
    assert result == data


def _mock_stream_response(
    *,
    body: bytes = b"",
    status_code: int = 200,
    headers: dict | None = None,
):
    """Build an async context-manager stream response for httpx.AsyncClient."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason_phrase = "OK" if status_code < 400 else "Error"
    resp.headers = httpx.Headers(headers or {})

    async def _aiter_bytes(chunk_size=65536):
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]

    async def _aread():
        return body

    resp.aiter_bytes = _aiter_bytes
    resp.aread = _aread

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = resp
    stream_cm.__aexit__.return_value = None

    client = MagicMock()
    client.stream.return_value = stream_cm

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client
    client_cm.__aexit__.return_value = None
    return client_cm, resp


@pytest.mark.asyncio
async def test_download_media_bytes_rejects_via_content_length(monkeypatch):
    """Drive export often sends Content-Length and ignores Range — reject before body."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    request = Mock(uri="https://www.googleapis.com/drive/v3/files/x/export", method="GET")
    request.headers = {}
    request.http = Mock(credentials=None)

    client_cm, _resp = _mock_stream_response(
        body=b"x" * 5000,  # would be large if read
        headers={"content-length": "276690"},
    )

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(
                request, file_name="CV_Oleg_Kulyk", file_id="f1"
            )

    assert "276,690" in str(exc.value) or "276690" in str(exc.value)
    assert "CV_Oleg_Kulyk" in str(exc.value)
    # Body must not have been consumed when Content-Length rejects.
    # aiter_bytes is never awaited if we raise first — verify stream entered.
    client_cm.__aenter__.assert_awaited()


@pytest.mark.asyncio
async def test_download_media_bytes_aborts_mid_stream(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "15")
    request = Mock(uri="https://www.googleapis.com/drive/v3/files/x?alt=media", method="GET")
    request.headers = {}
    request.http = Mock(credentials=None)

    # No Content-Length: must abort while streaming.
    client_cm, _resp = _mock_stream_response(body=b"a" * 40)

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(request, file_name="stream.bin")

    assert "stream.bin" in str(exc.value)


@pytest.mark.asyncio
async def test_download_media_bytes_capped_success(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    request = Mock(uri="https://www.googleapis.com/drive/v3/files/x?alt=media", method="GET")
    request.headers = {}
    request.http = Mock(credentials=None)

    data = b"ok-payload"
    client_cm, _resp = _mock_stream_response(
        body=data, headers={"content-length": str(len(data))}
    )

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        result = await download_media_bytes(request)

    assert result == data
