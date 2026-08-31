"""Tests for background_color and text_color on manage_gmail_label.

Colors are optional, so every color test is paired with a default-behavior test
proving a call that omits them sends the exact body it sent before.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError as ToolExecutionError

from gmail.gmail_helpers import GMAIL_LABEL_COLORS, build_label_color
from gmail.gmail_tools import manage_gmail_label


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _build_mock_service(current_label: dict | None = None) -> MagicMock:
    service = MagicMock()
    labels = service.users.return_value.labels.return_value
    labels.create.return_value.execute.return_value = {
        "id": "Label_1",
        "name": "Urgent",
    }
    labels.update.return_value.execute.return_value = {
        "id": "Label_1",
        "name": "Urgent",
    }
    labels.get.return_value.execute.return_value = current_label or {
        "id": "Label_1",
        "name": "Urgent",
    }
    return service


def _sent_body(service: MagicMock, method: str) -> dict:
    labels = service.users.return_value.labels.return_value
    return getattr(labels, method).call_args.kwargs["body"]


async def _create(service: MagicMock, **kwargs) -> str:
    return await _unwrap(manage_gmail_label)(
        service=service,
        user_google_email="user@example.com",
        action="create",
        name="Urgent",
        **kwargs,
    )


async def _update(service: MagicMock, **kwargs) -> str:
    return await _unwrap(manage_gmail_label)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        label_id="Label_1",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_create_sends_color_when_supplied():
    service = _build_mock_service()
    await _create(service, background_color="#fb4c2f", text_color="#ffffff")

    assert _sent_body(service, "create")["color"] == {
        "backgroundColor": "#fb4c2f",
        "textColor": "#ffffff",
    }


@pytest.mark.asyncio
async def test_create_omits_color_by_default():
    service = _build_mock_service()
    await _create(service)

    assert "color" not in _sent_body(service, "create")


@pytest.mark.asyncio
async def test_update_keeps_the_other_half_of_an_existing_color():
    service = _build_mock_service(
        {
            "id": "Label_1",
            "name": "Urgent",
            "color": {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},
        }
    )
    await _update(service, background_color="#16a766")

    assert _sent_body(service, "update")["color"] == {
        "backgroundColor": "#16a766",
        "textColor": "#ffffff",
    }


@pytest.mark.asyncio
async def test_update_without_colors_preserves_the_existing_color():
    existing = {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"}
    service = _build_mock_service(
        {"id": "Label_1", "name": "Urgent", "color": existing}
    )
    await _update(service, name="Renamed")

    assert _sent_body(service, "update")["color"] == existing


@pytest.mark.asyncio
async def test_update_omits_color_when_the_label_never_had_one():
    service = _build_mock_service()
    await _update(service, name="Renamed")

    assert "color" not in _sent_body(service, "update")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"background_color": "#ff0000"},
        {"text_color": "#123456"},
        {"background_color": "not-a-color"},
    ],
)
async def test_color_outside_the_palette_is_rejected_before_the_request(kwargs):
    service = _build_mock_service()

    with pytest.raises(ToolExecutionError, match="not a Gmail label color"):
        await _create(service, **kwargs)

    service.users.return_value.labels.return_value.create.assert_not_called()


def test_build_label_color_normalizes_case_and_whitespace():
    assert build_label_color(" #FB4C2F ") == {"backgroundColor": "#fb4c2f"}


def test_build_label_color_returns_none_when_no_color_is_given():
    assert build_label_color(None, None) is None
    assert build_label_color(None, None, {"backgroundColor": "#fb4c2f"}) is None


def test_palette_matches_the_documented_size():
    # 102 colors, per the Label reference:
    # https://developers.google.com/gmail/api/reference/rest/v1/users.labels#Label
    assert len(GMAIL_LABEL_COLORS) == 102
    assert all(c.startswith("#") and len(c) == 7 for c in GMAIL_LABEL_COLORS)
