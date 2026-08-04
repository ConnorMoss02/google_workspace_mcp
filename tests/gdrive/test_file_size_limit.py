"""Tool-level coverage for WORKSPACE_MCP_MAX_FILE_BYTES on Drive downloads."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from gdrive.drive_tools import get_drive_file_content, get_drive_file_download_url


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeDownloader:
    def __init__(self, fh, data, chunksize=None):
        fh.write(data)
        fh.seek(0)
        self.chunksize = chunksize

    def next_chunk(self):
        return None, True


@pytest.mark.asyncio
async def test_get_drive_file_content_rejects_declared_oversized(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    mock_service = Mock()

    with patch("gdrive.drive_tools.resolve_drive_item") as resolve:
        resolve.return_value = (
            "file123",
            {
                "name": "huge.bin",
                "mimeType": "application/octet-stream",
                "webViewLink": "https://drive.google.com/file/d/file123",
                "size": "500",
            },
        )
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert result.startswith("Error:")
    assert "huge.bin" in result
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in result
    mock_service.files().get_media.assert_not_called()


@pytest.mark.asyncio
async def test_get_drive_file_download_url_rejects_mid_download(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "20")
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"
    data = b"x" * 50

    with (
        patch("gdrive.drive_tools.resolve_drive_item") as resolve,
        patch(
            "core.file_limits.MediaIoBaseDownload",
            side_effect=lambda fh, req, chunksize=None: _FakeDownloader(fh, data, chunksize=chunksize),
        ),
    ):
        resolve.return_value = (
            "file123",
            {
                "name": "blob.bin",
                "mimeType": "application/octet-stream",
                "webViewLink": "https://drive.google.com/file/d/file123",
                # Declared size under limit so we exercise streaming check.
                "size": "10",
            },
        )
        result = await _unwrap(get_drive_file_download_url)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert result.startswith("Error:")
    assert "blob.bin" in result


@pytest.mark.asyncio
async def test_get_drive_file_content_allows_under_limit(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"
    data = b"hello under limit"

    with (
        patch("gdrive.drive_tools.resolve_drive_item") as resolve,
        patch(
            "core.file_limits.MediaIoBaseDownload",
            side_effect=lambda fh, req, chunksize=None: _FakeDownloader(fh, data, chunksize=chunksize),
        ),
    ):
        resolve.return_value = (
            "file123",
            {
                "name": "note.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/file/d/file123",
                "size": str(len(data)),
            },
        )
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "hello under limit" in result
    assert not result.startswith("Error:")
