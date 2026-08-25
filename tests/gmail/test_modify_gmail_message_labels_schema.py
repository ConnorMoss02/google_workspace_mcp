import gmail.gmail_tools  # noqa: F401
from core.server import server
from core.tool_registry import get_tool_components


def _assert_optional_string_list_schema(field_schema):
    assert "type" not in field_schema
    assert "items" not in field_schema
    assert field_schema["anyOf"] == [
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]
    assert field_schema["default"] is None


def test_modify_gmail_message_labels_optional_arrays_publish_array_type():
    components = get_tool_components(server)
    schema = components["modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        _assert_optional_string_list_schema(schema[field_name])


def test_batch_modify_gmail_message_labels_optional_arrays_publish_array_type():
    components = get_tool_components(server)
    schema = components["batch_modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        _assert_optional_string_list_schema(schema[field_name])
