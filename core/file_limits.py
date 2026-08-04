"""Env-gated limits for in-memory Google file / attachment downloads.

``WORKSPACE_MCP_MAX_FILE_BYTES`` caps how many bytes a tool may buffer into
process memory (Drive MediaIo downloads, Gmail attachments, etc.).

Default is disabled (``0`` / unset) so existing deployments keep uncapped
behavior. Set a positive integer (e.g. ``5242880`` for 5 MiB) to enable.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any, Optional

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

_ENV_NAME = "WORKSPACE_MCP_MAX_FILE_BYTES"

# Uncapped path only: keep MediaIoBaseDownload chunks modest so a single
# next_chunk() cannot pin ~100 MiB even when no env cap is set.
_DOWNLOAD_CHUNK_SIZE_BYTES = 256 * 1024  # 256 KiB

# Stream read size when a byte cap is active. Keep small so we can abort
# near the ceiling even when Content-Length is absent.
_STREAM_READ_SIZE_BYTES = 64 * 1024  # 64 KiB


class FileTooLargeError(ValueError):
    """Raised when a download would exceed ``WORKSPACE_MCP_MAX_FILE_BYTES``."""


def get_max_file_bytes() -> Optional[int]:
    """Return the configured max download size in bytes, or ``None`` if uncapped.

    Parsing rules (backwards compatible):
    - unset, empty, or ``0`` → uncapped (``None``)
    - positive int → that many bytes
    - invalid value → log a warning and treat as uncapped
    """
    raw = os.getenv(_ENV_NAME)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "Invalid %s=%r; expected a non-negative integer. Ignoring limit.",
            _ENV_NAME,
            raw,
        )
        return None
    if value < 0:
        logger.warning(
            "Invalid %s=%r; expected a non-negative integer. Ignoring limit.",
            _ENV_NAME,
            raw,
        )
        return None
    if value == 0:
        return None
    return value


def format_file_too_large_message(
    *,
    size_bytes: int,
    max_bytes: int,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
) -> str:
    """Build an agent-friendly error that points at alternatives, not file surgery."""
    label = f'"{file_name}"' if file_name else f"this {kind}"
    id_part = f" (ID: {file_id})" if file_id else ""
    link_part = ""
    if web_view_link and web_view_link != "#":
        link_part = f"\nOpen in Google Drive: {web_view_link}"

    return (
        f"Error: {label}{id_part} is too large to load into this MCP server "
        f"({size_bytes:,} bytes; limit is {max_bytes:,} bytes via {_ENV_NAME}).\n"
        f"Full binary download through this tool is not available for oversized "
        f"{kind}s.{link_part}\n"
        "Alternatives:\n"
        "- Use get_doc_content / get_doc_as_markdown for Google Docs\n"
        "- Use read_sheet_values for Google Sheets\n"
        "- Use get_drive_file_content for text-oriented exports when under the limit\n"
        "- Open the Drive link above for large binaries (video, zip, large PDF, etc.)"
    )


def ensure_within_file_size_limit(
    size_bytes: Optional[int],
    *,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> None:
    """Raise ``FileTooLargeError`` if a declared size exceeds the configured cap."""
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    if limit is None or size_bytes is None:
        return
    try:
        declared = int(size_bytes)
    except (TypeError, ValueError):
        return
    if declared > limit:
        raise FileTooLargeError(
            format_file_too_large_message(
                size_bytes=declared,
                max_bytes=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )
        )


def _raise_too_large(
    *,
    size_bytes: int,
    max_bytes: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> None:
    raise FileTooLargeError(
        format_file_too_large_message(
            size_bytes=size_bytes,
            max_bytes=max_bytes,
            file_name=file_name,
            file_id=file_id,
            web_view_link=web_view_link,
            kind=kind,
        )
    )


def _authorize_headers(request_obj: Any) -> dict[str, str]:
    """Copy request headers and apply OAuth credentials from the API client."""
    headers = dict(getattr(request_obj, "headers", None) or {})
    http = getattr(request_obj, "http", None)
    credentials = getattr(http, "credentials", None) if http is not None else None
    if credentials is None:
        return headers
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    credentials.apply(headers)
    return headers


async def _download_media_bytes_uncapped(request_obj: Any) -> bytes:
    """Download via MediaIoBaseDownload (full response may be buffered per chunk)."""
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(
        fh, request_obj, chunksize=_DOWNLOAD_CHUNK_SIZE_BYTES
    )
    done = False
    while not done:
        _status, done = await asyncio.to_thread(downloader.next_chunk)
    return fh.getvalue()


async def _download_media_bytes_capped(
    request_obj: Any,
    *,
    limit: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> bytes:
    """Stream a media URL and abort before buffering past ``limit``.

    ``MediaIoBaseDownload`` cannot enforce this for Drive ``export_media``:
    exports often ignore ``Range`` and return HTTP 200 with the entire body,
    which httplib2 buffers into ``content`` before any size check runs.
    """
    uri = getattr(request_obj, "uri", None)
    if not uri:
        raise ValueError("media request is missing uri")

    method = (getattr(request_obj, "method", None) or "GET").upper()
    headers = await asyncio.to_thread(_authorize_headers, request_obj)
    read_size = max(1, min(_STREAM_READ_SIZE_BYTES, limit))
    timeout = httpx.Timeout(120.0, connect=30.0)
    buf = bytearray()

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream(method, uri, headers=headers) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                # googleapiclient.HttpError expects an httplib2-style resp.status.
                httplib2_resp = type(
                    "Response",
                    (),
                    {
                        "status": resp.status_code,
                        "reason": resp.reason_phrase,
                    },
                )()
                raise HttpError(httplib2_resp, body, uri=uri)

            content_length = resp.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = None
                if declared is not None and declared > limit:
                    # Close without reading the body into process memory.
                    _raise_too_large(
                        size_bytes=declared,
                        max_bytes=limit,
                        file_name=file_name,
                        file_id=file_id,
                        web_view_link=web_view_link,
                        kind=kind,
                    )

            async for chunk in resp.aiter_bytes(chunk_size=read_size):
                if not chunk:
                    continue
                next_size = len(buf) + len(chunk)
                if next_size > limit:
                    _raise_too_large(
                        size_bytes=next_size,
                        max_bytes=limit,
                        file_name=file_name,
                        file_id=file_id,
                        web_view_link=web_view_link,
                        kind=kind,
                    )
                buf.extend(chunk)

    return bytes(buf)


async def download_media_bytes(
    request_obj: Any,
    *,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> bytes:
    """Download a Drive ``get_media`` / ``export_media`` request into memory.

    When ``WORKSPACE_MCP_MAX_FILE_BYTES`` is set, streams the response and
    aborts as soon as Content-Length or buffered bytes exceed the cap — so
    Google Docs PDF exports (which ignore Range) cannot fill the cgroup.
    """
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    if limit is None:
        return await _download_media_bytes_uncapped(request_obj)
    return await _download_media_bytes_capped(
        request_obj,
        limit=limit,
        file_name=file_name,
        file_id=file_id,
        web_view_link=web_view_link,
        kind=kind,
    )
