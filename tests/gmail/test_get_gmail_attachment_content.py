"""
Tests for ``get_gmail_attachment_content``, in particular the ``return_base64``
option added for sandboxed clients that cannot reach the MCP server's
localhost download URLs or local file paths.
"""

import base64
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from core.server import server
from core.tool_registry import get_tool_components
from gmail.gmail_tools import (
    _format_base64_content_block,
    get_gmail_attachment_content,
)


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _build_mock_service(
    payload: bytes,
    *,
    filename: str = "attachment.bin",
    mime_type: str = "application/octet-stream",
) -> Mock:
    """Build a Mock google-api service returning ``payload`` as an attachment."""
    urlsafe_b64 = base64.urlsafe_b64encode(payload).decode("ascii")

    mock_service = Mock()

    # attachments().get(...).execute() returns the raw attachment dict
    mock_service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": urlsafe_b64,
    }

    # messages().get(...).execute() is called to resolve filename/mime;
    # return a payload with a single matching part.
    mock_service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": filename,
                    "mimeType": mime_type,
                    "body": {"attachmentId": "att-123", "size": len(payload)},
                }
            ],
        },
    }

    return mock_service


def _build_mock_service_nested(
    payload: bytes,
    *,
    filename: str,
    mime_type: str,
    attachment_id: str,
) -> Mock:
    """Build a Mock service whose payload mirrors an S/MIME-signed message:
    multipart/signed -> [multipart/mixed -> [the real attachment], smime.p7s].
    The real attachment sits two levels deep, and the sibling signature part
    is itself named ``smime.p7s`` with its own attachmentId.

    A real Gmail API `fields` mask is not recursive: `parts(...)` nested `n`
    levels deep in the mask string only returns `n` levels of `parts` in the
    response, silently dropping anything nested deeper. The mock's `.get()`
    reproduces that truncation for whatever depth the `fields` kwarg actually
    asks for (parsed by counting `parts(` occurrences), rather than
    hardcoding one depth — so this test stays meaningful against the real
    mask's depth, not just a mock frozen to the old, buggy depth. A plain
    ``Mock(return_value=...)`` would ignore the kwarg entirely and couldn't
    catch this class of regression at all.
    """
    urlsafe_b64 = base64.urlsafe_b64encode(payload).decode("ascii")

    full_payload = {
        "mimeType": "multipart/signed",
        "parts": [
            {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "filename": filename,
                        "mimeType": mime_type,
                        "body": {
                            "attachmentId": attachment_id,
                            "size": len(payload),
                        },
                    }
                ],
            },
            {
                "filename": "smime.p7s",
                "mimeType": "application/pkcs7-signature",
                "body": {"attachmentId": "sig-456", "size": 4771},
            },
        ],
    }

    def truncate_to_depth(part: dict, remaining_depth: int) -> dict:
        if remaining_depth <= 0:
            return {k: v for k, v in part.items() if k != "parts"}
        truncated = {k: v for k, v in part.items() if k != "parts"}
        if "parts" in part:
            truncated["parts"] = [
                truncate_to_depth(child, remaining_depth - 1) for child in part["parts"]
            ]
        return truncated

    def messages_get(**kwargs):
        fields = kwargs.get("fields")
        if fields:
            depth = fields.count("parts(")
            result = {"payload": truncate_to_depth(full_payload, depth)}
        else:
            result = {"payload": full_payload}
        return Mock(execute=Mock(return_value=result))

    mock_service = Mock()
    mock_service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": urlsafe_b64,
    }
    mock_service.users().messages().get = Mock(side_effect=messages_get)
    return mock_service


@pytest.fixture
def isolated_attachment_env(tmp_path, monkeypatch):
    """Route attachment storage to a temp dir and force HTTP (not stateless) mode."""
    import core.attachment_storage as storage_module
    import auth.oauth_config as oauth_config_module
    import core.config as core_config_module

    monkeypatch.setattr(storage_module, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(oauth_config_module, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(core_config_module, "get_transport_mode", lambda: "http")

    # Reset the cached module-level storage singleton so our patched
    # STORAGE_DIR actually takes effect.
    monkeypatch.setattr(storage_module, "_attachment_storage", None, raising=False)

    return tmp_path


def test_get_gmail_attachment_content_schema_includes_return_base64():
    """Published MCP schema should expose the public return_base64 parameter."""
    components = get_tool_components(server)
    schema = components[get_gmail_attachment_content.__name__].parameters["properties"]

    assert "return_base64" in schema
    assert schema["return_base64"]["type"] == "boolean"
    assert schema["return_base64"]["default"] is False


def test_format_base64_content_block_converts_urlsafe_to_standard():
    """Helper should convert URL-safe base64 (Gmail API) to standard base64."""
    # Payload whose base64 produces characters that differ between alphabets
    # (the '+' vs '-' and '/' vs '_' substitutions kick in for certain bytes).
    payload = bytes(range(256))
    urlsafe_b64 = base64.urlsafe_b64encode(payload).decode("ascii")

    lines = _format_base64_content_block(urlsafe_b64)

    assert len(lines) == 2
    assert "Base64 content" in lines[0]
    assert "standard base64" in lines[0]

    standard_b64 = lines[1]
    # Standard alphabet must round-trip back to the original bytes.
    assert base64.b64decode(standard_b64) == payload


def test_format_base64_content_block_handles_invalid_input_gracefully():
    """Invalid base64 shouldn't raise — it should return a warning line."""
    lines = _format_base64_content_block("not valid base64 !!!")

    assert len(lines) == 1
    assert "Could not include base64 content" in lines[0]


@pytest.mark.asyncio
async def test_default_call_omits_base64_content(isolated_attachment_env):
    """Without return_base64, the response should not contain the base64 block (backwards compat)."""
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    mock_service = _build_mock_service(
        payload, filename="test.png", mime_type="image/png"
    )

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-123",
        user_google_email="user@example.com",
    )

    assert "Attachment downloaded successfully!" in result
    assert "📦 Base64 content" not in result
    assert "standard base64" not in result


@pytest.mark.asyncio
async def test_download_response_reports_sanitized_saved_filename(
    isolated_attachment_env,
):
    """Windows-reserved filename characters should be sanitized before saving."""
    payload = b"attached email bytes"
    mock_service = _build_mock_service(
        payload, filename="RE: Foo?.eml", mime_type="message/rfc822"
    )

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-123",
        user_google_email="user@example.com",
    )

    assert "Filename: RE: Foo?.eml" in result
    assert "Saved filename: RE_ Foo_" in result

    saved_files = list(isolated_attachment_env.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].name.startswith("RE_ Foo_")
    assert ":" not in saved_files[0].name
    assert "?" not in saved_files[0].name
    assert saved_files[0].read_bytes() == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "filename", "mime_type"),
    [
        (
            b"\x89PNG\r\n\x1a\n" + b"\xff" * 50 + bytes(range(200)),
            "test.png",
            "image/png",
        ),
        (b"PDF-ish\x00\x01\x02" + b"\xfe\xfd" * 128, "doc.pdf", "application/pdf"),
        (bytes(range(256)), "full-range.bin", "application/octet-stream"),
    ],
)
async def test_return_base64_true_includes_standard_base64_block(
    isolated_attachment_env,
    payload: bytes,
    filename: str,
    mime_type: str,
):
    """With return_base64=True, the response must contain decoded standard base64."""
    mock_service = _build_mock_service(payload, filename=filename, mime_type=mime_type)

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-123",
        user_google_email="user@example.com",
        return_base64=True,
    )

    assert "📦 Base64 content" in result
    assert "standard base64" in result

    # Extract the base64 line (the one right after the header) and verify round-trip.
    lines = result.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if "📦 Base64 content" in line), None
    )
    assert header_idx is not None, (
        "Expected _format_base64_content_block to include the '📦 Base64 content' header"
    )
    standard_b64 = lines[header_idx + 1].strip()

    assert base64.b64decode(standard_b64) == payload


@pytest.mark.asyncio
async def test_return_base64_preserves_file_save_behavior(isolated_attachment_env):
    """return_base64 should be additive: file is still saved and path/URL still returned."""
    payload = b"additive behavior check " + bytes(range(100))
    mock_service = _build_mock_service(payload, filename="doc.bin")

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-123",
        user_google_email="user@example.com",
        return_base64=True,
    )

    # Still returns the normal HTTP-mode output...
    assert "Attachment downloaded successfully!" in result
    assert "Download URL" in result
    # ...and includes the base64 block.
    assert "📦 Base64 content" in result


@pytest.mark.asyncio
async def test_resolves_correct_filename_for_nested_smime_attachment(
    isolated_attachment_env,
):
    """Regression test: on S/MIME-signed messages the real attachment is
    nested two levels deep (multipart/signed -> multipart/mixed -> file),
    while the sibling smime.p7s signature part sits at the top level. The
    metadata re-fetch used to request a `fields` mask that only covered one
    level of `parts`, so the nested attachment was invisible and the
    "only one attachment visible" fallback mislabeled every download as
    smime.p7s regardless of which attachment was actually requested.
    """
    payload = b"%PDF-1.4 fake pdf bytes" + bytes(range(200))
    mock_service = _build_mock_service_nested(
        payload,
        filename="statement.pdf",
        mime_type="application/pdf",
        attachment_id="att-pdf-123",
    )

    result = await _unwrap(get_gmail_attachment_content)(
        service=mock_service,
        message_id="msg-1",
        attachment_id="att-pdf-123",
        user_google_email="user@example.com",
    )

    assert "Filename: statement.pdf" in result
    assert "smime.p7s" not in result

    saved_files = list(isolated_attachment_env.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].suffix == ".pdf"
    assert saved_files[0].read_bytes() == payload
