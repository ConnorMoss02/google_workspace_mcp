"""
Unit tests for Google Sheets Smart Chips (Jetons intelligents) support.

Tests insertion and extraction of Google Drive and People smart chips.
"""

from unittest.mock import Mock
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_helpers import (
    _create_chip_cell_data,
    _normalize_chips_input,
)
from gsheets.sheets_tools import (
    _insert_smart_chips_impl,
    read_sheet_values,
)


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def create_mock_sheets_service(sheets_metadata=None):
    """Create a mock Sheets service for testing."""
    mock_service = Mock()
    if sheets_metadata is None:
        sheets_metadata = {
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Sheet1"}},
                {"properties": {"sheetId": 12345, "title": "Elections"}},
            ]
        }
    spreadsheets_mock = Mock()
    mock_service.spreadsheets.return_value = spreadsheets_mock
    spreadsheets_mock.get.return_value.execute.return_value = sheets_metadata
    spreadsheets_mock.batchUpdate.return_value.execute.return_value = {}
    return mock_service


# ---------------------------------------------------------------------------
# Tests for _create_chip_cell_data
# ---------------------------------------------------------------------------


def test_create_chip_cell_data_drive_url():
    """Drive URL creates a richLinkProperties chip with stringValue '@'."""
    url = "https://drive.google.com/drive/folders/0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"
    cell = _create_chip_cell_data(url)
    assert cell is not None
    assert cell["userEnteredValue"] == {"stringValue": "@"}
    assert len(cell["chipRuns"]) == 1
    assert cell["chipRuns"][0]["startIndex"] == 0
    assert cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"] == url


def test_create_chip_cell_data_person_email():
    """Email string creates a personProperties chip."""
    email = "antoine.beaubien@gmail.com"
    cell = _create_chip_cell_data(email)
    assert cell is not None
    assert cell["userEnteredValue"] == {"stringValue": "@"}
    assert len(cell["chipRuns"]) == 1
    assert cell["chipRuns"][0]["chip"]["personProperties"]["email"] == email


def test_create_chip_cell_data_drive_id():
    """Raw Drive ID is converted to open?id= URL."""
    drive_id = "0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"
    cell = _create_chip_cell_data(drive_id)
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == f"https://drive.google.com/open?id={drive_id}"
    )


def test_create_chip_cell_data_dict_folder_id():
    """Dictionary with folder_id creates a Drive folder URL."""
    folder_id = "folder12345"
    cell = _create_chip_cell_data({"folder_id": folder_id})
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == f"https://drive.google.com/drive/folders/{folder_id}"
    )


def test_create_chip_cell_data_dict_person():
    """Dictionary with type=person and email."""
    cell = _create_chip_cell_data({"type": "person", "email": "test@example.com"})
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["personProperties"]["email"] == "test@example.com"
    )


def test_create_chip_cell_data_empty():
    """Empty or None items return None (skipped)."""
    assert _create_chip_cell_data(None) is None
    assert _create_chip_cell_data("") is None


def test_create_chip_cell_data_invalid_type():
    """Invalid chip_type raises UserInputError."""
    with pytest.raises(UserInputError, match="Unknown chip_type 'calendar'"):
        _create_chip_cell_data("something", default_type="calendar")


# ---------------------------------------------------------------------------
# Tests for _normalize_chips_input
# ---------------------------------------------------------------------------


def test_normalize_chips_input_single_cell():
    """Single cell target with single URL."""
    url = "https://drive.google.com/file/d/123/view"
    updates = _normalize_chips_input(
        chips=url,
        start_row=2,
        end_row=2,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 1
    r, c, data = updates[0]
    assert r == 2
    assert c == 5
    assert data["chipRuns"][0]["chip"]["richLinkProperties"]["uri"] == url


def test_normalize_chips_input_column_range():
    """1D list for a column range F3:F5 maps vertically."""
    urls = [
        "https://drive.google.com/folder1",
        "https://drive.google.com/folder2",
        "https://drive.google.com/folder3",
    ]
    updates = _normalize_chips_input(
        chips=urls,
        start_row=2,
        end_row=4,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 3
    assert [u[0] for u in updates] == [2, 3, 4]
    assert [u[1] for u in updates] == [5, 5, 5]


def test_normalize_chips_input_row_range():
    """1D list for a row range A1:C1 maps horizontally."""
    emails = ["a@example.com", "b@example.com", "c@example.com"]
    updates = _normalize_chips_input(
        chips=emails,
        start_row=0,
        end_row=0,
        start_col=0,
        end_col=2,
    )
    assert len(updates) == 3
    assert [u[0] for u in updates] == [0, 0, 0]
    assert [u[1] for u in updates] == [0, 1, 2]


def test_normalize_chips_input_json_string():
    """JSON string is automatically parsed."""
    json_str = '["https://drive.google.com/1", "https://drive.google.com/2"]'
    updates = _normalize_chips_input(
        chips=json_str,
        start_row=0,
        end_row=1,
        start_col=0,
        end_col=0,
    )
    assert len(updates) == 2


# ---------------------------------------------------------------------------
# Tests for _insert_smart_chips_impl (Batching & Execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_smart_chips_single():
    """Test inserting a single chip into a cell."""
    service = create_mock_sheets_service()
    url = "https://drive.google.com/drive/folders/0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"

    result = await _insert_smart_chips_impl(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Elections!F3",
        chips=url,
    )

    assert "Successfully inserted 1 smart chip(s)" in result
    assert "Elections!F3" in result

    # Check batchUpdate was called
    service.spreadsheets().batchUpdate.assert_called_once()
    call_args = service.spreadsheets().batchUpdate.call_args
    assert call_args[1]["spreadsheetId"] == "test_sheet_id"
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 1
    assert requests[0]["updateCells"]["range"]["sheetId"] == 12345
    assert requests[0]["updateCells"]["range"]["startRowIndex"] == 2
    assert requests[0]["updateCells"]["range"]["startColumnIndex"] == 5


@pytest.mark.asyncio
async def test_insert_smart_chips_batches_over_limit():
    """Test inserting 19 chips chunks them into batches of <= 8."""
    service = create_mock_sheets_service()
    urls = [f"https://drive.google.com/folder_{i}" for i in range(19)]

    result = await _insert_smart_chips_impl(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Sheet1!F3:F21",
        chips=urls,
    )

    assert "Successfully inserted 19 smart chip(s)" in result

    # 19 chips with BATCH_SIZE=8 -> 3 batchUpdate calls: 8 + 8 + 3
    batch_calls = service.spreadsheets().batchUpdate.call_args_list
    assert len(batch_calls) == 3
    assert len(batch_calls[0][1]["body"]["requests"]) == 8
    assert len(batch_calls[1][1]["body"]["requests"]) == 8
    assert len(batch_calls[2][1]["body"]["requests"]) == 3


@pytest.mark.asyncio
async def test_insert_smart_chips_unknown_sheet():
    """Test error when sheet name is not found."""
    service = create_mock_sheets_service()
    with pytest.raises(UserInputError, match="Sheet 'NonExistent' not found"):
        await _insert_smart_chips_impl(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="NonExistent!A1",
            chips="https://drive.google.com/1",
        )


# ---------------------------------------------------------------------------
# Tests for read_sheet_values with include_smart_chips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_sheet_values_with_smart_chips():
    """Test read_sheet_values extracts and formats smart chips."""
    mock_service = Mock()
    # Mock values().get
    mock_service.spreadsheets().values().get().execute = Mock(
        return_value={
            "range": "Sheet1!F3:F4",
            "values": [["@"], ["@"]],
        }
    )
    # Mock spreadsheets().get with includeGridData=True
    grid_response = {
        "sheets": [
            {
                "properties": {"title": "Sheet1"},
                "data": [
                    {
                        "startRow": 2,
                        "startColumn": 5,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "formattedValue": "Folder Chicoutimi",
                                        "chipRuns": [
                                            {
                                                "startIndex": 0,
                                                "chip": {
                                                    "richLinkProperties": {
                                                        "uri": "https://drive.google.com/folders/111",
                                                        "title": "Folder Chicoutimi",
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                            {
                                "values": [
                                    {
                                        "formattedValue": "Antoine Beaubien",
                                        "chipRuns": [
                                            {
                                                "startIndex": 0,
                                                "chip": {
                                                    "personProperties": {
                                                        "email": "antoine@example.com",
                                                        "name": "Antoine Beaubien",
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                        ],
                    }
                ],
            }
        ]
    }
    mock_service.spreadsheets().get().execute = Mock(return_value=grid_response)

    result = await _unwrap(read_sheet_values)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Sheet1!F3:F4",
        include_smart_chips=True,
    )

    assert "Smart Chips in range 'Sheet1!F3:F4':" in result
    assert (
        '- Sheet1!F3: [Drive Chip] "Folder Chicoutimi" (https://drive.google.com/folders/111)'
        in result
    )
    assert "- Sheet1!F4: [Person Chip] Antoine Beaubien <antoine@example.com>" in result
