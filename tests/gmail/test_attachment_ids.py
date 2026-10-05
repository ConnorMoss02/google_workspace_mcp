"""Tests for the X-Attachment-Id / Content-ID headers on regular attachments.

Gmail web gives every attachment both headers. A draft created through the
API without them looks fine at first, but the first time Gmail saves the draft
itself (the user types in it), every attachment ends up with an empty
X-Attachment-Id and ``Content-ID: <>``. With several attachments, Gmail can no
longer tell them apart: the file names still look right, but downloading one
serves another attachment's bytes. Unique ids set at creation survive Gmail's
save.
"""

import base64
from email import message_from_bytes
from email.policy import SMTP

from gmail.gmail_tools import _prepare_gmail_message


def _pdf(filename: str, payload: bytes) -> dict:
    return {
        "content": base64.b64encode(payload).decode(),
        "filename": filename,
        "mime_type": "application/pdf",
    }


def _attachment_parts(raw_b64url: str):
    msg = message_from_bytes(base64.urlsafe_b64decode(raw_b64url), policy=SMTP)
    return [p for p in msg.walk() if p.get_content_disposition() == "attachment"]


def test_each_regular_attachment_gets_a_unique_attachment_id():
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="attachment-id-test",
        body="three files",
        to="someone@example.com",
        attachments=[
            _pdf("presentation.pdf", b"%PDF-1.7 presentation"),
            _pdf("pricing.pdf", b"%PDF-1.7 pricing"),
            _pdf("contract.pdf", b"%PDF-1.7 contract"),
        ],
    )

    assert errors == []
    assert attached == 3

    parts = _attachment_parts(raw_b64)
    ids = [str(p.get("X-Attachment-Id", "")).strip() for p in parts]
    assert len(ids) == 3
    assert all(ids), f"empty X-Attachment-Id: {ids}"
    assert len(set(ids)) == 3, f"X-Attachment-Id not unique: {ids}"


def test_regular_attachment_content_id_matches_attachment_id():
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="attachment-id-test",
        body="one file",
        to="someone@example.com",
        attachments=[_pdf("presentation.pdf", b"%PDF-1.7 presentation")],
    )

    assert errors == []
    assert attached == 1

    (part,) = _attachment_parts(raw_b64)
    assert part["Content-ID"] == f"<{part['X-Attachment-Id']}>"
