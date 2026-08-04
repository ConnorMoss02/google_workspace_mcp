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

from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

_ENV_NAME = "WORKSPACE_MCP_MAX_FILE_BYTES"


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

    When ``WORKSPACE_MCP_MAX_FILE_BYTES`` is set, aborts as soon as buffered
    bytes exceed the limit so a multi‑MB/GB download cannot fill the cgroup.
    """
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_obj)
    done = False
    while not done:
        _status, done = await asyncio.to_thread(downloader.next_chunk)
        buffered = fh.getbuffer().nbytes
        if limit is not None and buffered > limit:
            fh.close()
            raise FileTooLargeError(
                format_file_too_large_message(
                    size_bytes=buffered,
                    max_bytes=limit,
                    file_name=file_name,
                    file_id=file_id,
                    web_view_link=web_view_link,
                    kind=kind,
                )
            )
    return fh.getvalue()
