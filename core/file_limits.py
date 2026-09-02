"""Env-gated limits for in-memory Google file / attachment downloads.

``WORKSPACE_MCP_MAX_FILE_BYTES`` caps how many bytes a tool may buffer into
process memory (Drive MediaIo downloads, Gmail attachments, etc.).

Default is disabled (``0`` / unset) so existing deployments keep uncapped
behavior. Set a positive integer (e.g. ``5242880`` for 5 MiB) to enable.
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Any, Optional

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

_ENV_NAME = "WORKSPACE_MCP_MAX_FILE_BYTES"

# Stream read size when a byte cap is active. Keep small so we can abort
# near the ceiling even when Content-Length is absent.
_STREAM_READ_SIZE_BYTES = 64 * 1024  # 64 KiB


class FileTooLargeError(ValueError):
    """Raised when a download would exceed ``WORKSPACE_MCP_MAX_FILE_BYTES``."""


def get_max_file_bytes() -> Optional[int]:
    """Return the configured max download size in bytes, or ``None`` if uncapped.

    Parsing rules:
    - unset, empty, or ``0`` → uncapped (``None``)
    - positive int → that many bytes
    - invalid or negative value → raise ``ValueError``
    """
    raw = os.getenv(_ENV_NAME)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"Invalid {_ENV_NAME}={raw!r}; expected a non-negative integer byte count."
        ) from exc
    if value < 0:
        raise ValueError(
            f"Invalid {_ENV_NAME}={raw!r}; expected a non-negative integer byte count."
        )
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
    # Preserve googleapiclient's historical/default chunking when the optional
    # cap is disabled. Shrinking this to 256 KiB turns a 1 GiB file into 4,096
    # range requests and is not behaviorally neutral for latency or quota use.
    downloader = MediaIoBaseDownload(fh, request_obj)
    done = False
    while not done:
        _status, done = await asyncio.to_thread(downloader.next_chunk)
    return fh.getvalue()


async def _accumulate_capped_stream(
    resp: httpx.Response,
    *,
    limit: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> bytes:
    """Read an open streamed response, aborting once past ``limit``."""
    content_encoding = resp.headers.get("content-encoding", "").strip().lower()
    content_length = resp.headers.get("content-length")
    # Content-Length describes the encoded wire representation, whereas
    # aiter_bytes() yields decoded bytes. It is only comparable to the file-byte
    # limit for identity responses. Capped requests ask for identity below, but
    # keep this guard for non-compliant intermediaries.
    if content_length is not None and content_encoding in {"", "identity"}:
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

    read_size = max(1, min(_STREAM_READ_SIZE_BYTES, limit))
    # BytesIO.getvalue() exposes its immutable backing bytes without the
    # bytearray -> bytes full-size copy that previously doubled peak memory at
    # successful completion.
    buf = io.BytesIO()
    buffered_size = 0
    async for chunk in resp.aiter_bytes(chunk_size=read_size):
        if not chunk:
            continue
        next_size = buffered_size + len(chunk)
        if next_size > limit:
            _raise_too_large(
                size_bytes=next_size,
                max_bytes=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )
        buf.write(chunk)
        buffered_size = next_size
    return buf.getvalue()


def _http_status_error(resp: httpx.Response) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError for a completed (possibly streamed) response."""
    return httpx.HTTPStatusError(
        f"Client error '{resp.status_code} {resp.reason_phrase}' for url '{resp.request.url}'",
        request=resp.request,
        response=resp,
    )


async def download_http_url_bytes(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    method: str = "GET",
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> bytes:
    """Download ``url`` into memory, honoring ``WORKSPACE_MCP_MAX_FILE_BYTES``.

    When a cap is set, streams and aborts on Content-Length or mid-body.
    Raises ``FileTooLargeError`` when over the limit, or
    ``httpx.HTTPStatusError`` for non-2xx responses.
    """
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    req_headers = dict(headers or {})
    timeout = httpx.Timeout(120.0, connect=30.0)
    method = method.upper()

    if limit is not None:
        # googleapiclient media requests advertise gzip by default. httpx then
        # reports the compressed Content-Length but yields decompressed chunks,
        # making the two size checks use different units and allowing the
        # decoder to allocate far beyond the cap before a chunk is yielded.
        req_headers = {
            key: value
            for key, value in req_headers.items()
            if key.lower() != "accept-encoding"
        }
        req_headers["Accept-Encoding"] = "identity"

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if limit is None:
            resp = await client.request(method, url, headers=req_headers)
            if resp.status_code >= 400:
                raise _http_status_error(resp)
            return resp.content

        async with client.stream(method, url, headers=req_headers) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise _http_status_error(resp)
            return await _accumulate_capped_stream(
                resp,
                limit=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )


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
    try:
        return await download_http_url_bytes(
            uri,
            headers=headers,
            method=method,
            file_name=file_name,
            file_id=file_id,
            web_view_link=web_view_link,
            kind=kind,
            max_bytes=limit,
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.content or b""
        httplib2_resp = type(
            "Response",
            (),
            {
                "status": exc.response.status_code,
                "reason": exc.response.reason_phrase,
            },
        )()
        raise HttpError(httplib2_resp, body, uri=uri) from exc


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
