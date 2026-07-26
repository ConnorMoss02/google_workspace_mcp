"""
Typed Pydantic schemas for Google Docs batch operations.

These models are used to generate a richer MCP schema for batch_update_doc so
LLMs receive a machine-readable contract instead of a free-form object array.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _coerce_json_str_to_list(v: Any) -> Any:
    """Accept JSON-encoded lists for MCP clients that serialize arrays as strings."""
    if not isinstance(v, str):
        return v

    try:
        parsed = json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v

    return parsed if isinstance(parsed, list) else v


class StrictDocOperation(BaseModel):
    """Base model for strictly typed high-impact operations."""

    model_config = ConfigDict(extra="forbid")

    tab_id: str | None = Field(
        default=None,
        description="Optional document tab ID to target.",
    )


class SegmentTargetDocOperation(StrictDocOperation):
    """Base model for operations that can target document segments."""

    segment_id: str | None = Field(
        default=None,
        description=(
            "Optional header/footer/footnote segment ID. Use a real ID returned by "
            "inspect_doc_structure; do not guess values like 'kix.header'."
        ),
    )


class InsertTextOperation(SegmentTargetDocOperation):
    type: Literal["insert_text"]
    text: str = Field(description="Text to insert.")
    index: int | None = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> InsertTextOperation:
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class ReplaceTextOperation(SegmentTargetDocOperation):
    type: Literal["replace_text"]
    start_index: int
    end_index: int
    text: str = Field(description="Replacement text.")


class DeleteTextOperation(SegmentTargetDocOperation):
    type: Literal["delete_text"]
    start_index: int
    end_index: int


class FormatTextOperation(SegmentTargetDocOperation):
    type: Literal["format_text"]
    start_index: int
    end_index: int
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    font_size: int | None = None
    font_family: str | None = None
    font_weight: int | None = None
    text_color: str | None = None
    background_color: str | None = None
    link_url: str | None = None
    clear_link: bool | None = None
    baseline_offset: str | None = None
    small_caps: bool | None = None


class UpdateParagraphStyleOperation(SegmentTargetDocOperation):
    type: Literal["update_paragraph_style"]
    start_index: int
    end_index: int
    heading_level: int | None = None
    alignment: str | None = None
    line_spacing: float | None = None
    indent_first_line: float | None = None
    indent_start: float | None = None
    indent_end: float | None = None
    space_above: float | None = None
    space_below: float | None = None
    named_style_type: str | None = None
    direction: str | None = None
    keep_lines_together: bool | None = None
    keep_with_next: bool | None = None
    avoid_widow_and_orphan: bool | None = None
    page_break_before: bool | None = None
    spacing_mode: str | None = None
    shading_color: str | None = None


class UpdateTableCellStyleOperation(StrictDocOperation):
    type: Literal["update_table_cell_style"]
    table_start_index: int
    background_color: str | None = None
    border_color: str | None = None
    border_width: float | None = None
    padding_top: float | None = None
    padding_bottom: float | None = None
    padding_left: float | None = None
    padding_right: float | None = None
    content_alignment: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    row_span: int | None = None
    column_span: int | None = None


class InsertTableOperation(SegmentTargetDocOperation):
    type: Literal["insert_table"]
    rows: int
    columns: int
    index: int | None = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> InsertTableOperation:
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertTableRowOperation(StrictDocOperation):
    type: Literal["insert_table_row"]
    table_start_index: int
    row_index: int
    insert_below: bool = True


class DeleteTableRowOperation(StrictDocOperation):
    type: Literal["delete_table_row"]
    table_start_index: int
    row_index: int


class InsertTableColumnOperation(StrictDocOperation):
    type: Literal["insert_table_column"]
    table_start_index: int
    column_index: int
    insert_right: bool = True


class DeleteTableColumnOperation(StrictDocOperation):
    type: Literal["delete_table_column"]
    table_start_index: int
    column_index: int


class MergeTableCellsOperation(StrictDocOperation):
    type: Literal["merge_table_cells"]
    table_start_index: int
    row_index: int
    column_index: int
    row_span: int
    column_span: int


class UnmergeTableCellsOperation(StrictDocOperation):
    type: Literal["unmerge_table_cells"]
    table_start_index: int
    row_index: int
    column_index: int
    row_span: int
    column_span: int


class UpdateTableColumnPropertiesOperation(StrictDocOperation):
    type: Literal["update_table_column_properties"]
    table_start_index: int
    column_indices: list[int]
    width: float | None = None
    width_type: str | None = None


class UpdateTableRowStyleOperation(StrictDocOperation):
    type: Literal["update_table_row_style"]
    table_start_index: int
    row_indices: list[int] = Field(
        description="Zero-based row indices to style, e.g. [0] for the header row."
    )
    min_row_height: float | None = Field(
        default=None,
        description="Minimum row height in points.",
    )


class PinTableHeaderRowsOperation(StrictDocOperation):
    type: Literal["pin_table_header_rows"]
    table_start_index: int
    pinned_header_rows_count: int = Field(
        ge=0,
        description="Number of leading rows to pin as a repeating header on each "
        "page. 0 unpins all rows. Use this dedicated request because the "
        "'tableHeader' value reported in TableRowStyle cannot be set through "
        "UpdateTableRowStyleRequest.",
    )


class InsertPageBreakOperation(StrictDocOperation):
    type: Literal["insert_page_break"]
    index: int | None = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the body instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> InsertPageBreakOperation:
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertSectionBreakOperation(StrictDocOperation):
    type: Literal["insert_section_break"]
    index: int | None = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the body instead of using index.",
    )
    section_type: Literal["CONTINUOUS", "NEXT_PAGE"] = "NEXT_PAGE"

    @model_validator(mode="after")
    def validate_location(self) -> InsertSectionBreakOperation:
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class FindReplaceOperation(StrictDocOperation):
    type: Literal["find_replace"]
    find_text: str
    replace_text: str
    match_case: bool = False


class CreateBulletListOperation(SegmentTargetDocOperation):
    type: Literal["create_bullet_list"]
    start_index: int
    end_index: int
    list_type: Literal["UNORDERED", "ORDERED", "CHECKBOX", "NONE"] = "UNORDERED"
    nesting_level: int | None = None
    paragraph_start_indices: list[int] | None = None
    bullet_preset: str | None = None


class CreateNamedRangeOperation(SegmentTargetDocOperation):
    type: Literal["create_named_range"]
    name: str
    start_index: int
    end_index: int


class ReplaceNamedRangeContentOperation(StrictDocOperation):
    type: Literal["replace_named_range_content"]
    text: str
    named_range_id: str | None = None
    named_range_name: str | None = None

    @model_validator(mode="after")
    def validate_named_range_target(self) -> ReplaceNamedRangeContentOperation:
        if bool(self.named_range_id) == bool(self.named_range_name):
            raise ValueError(
                "Provide exactly one of 'named_range_id' or 'named_range_name'."
            )
        return self


class DeleteNamedRangeOperation(StrictDocOperation):
    type: Literal["delete_named_range"]
    named_range_id: str | None = None
    named_range_name: str | None = None

    @model_validator(mode="after")
    def validate_named_range_target(self) -> DeleteNamedRangeOperation:
        if bool(self.named_range_id) == bool(self.named_range_name):
            raise ValueError(
                "Provide exactly one of 'named_range_id' or 'named_range_name'."
            )
        return self


class UpdateDocumentStyleOperation(StrictDocOperation):
    type: Literal["update_document_style"]
    background_color: str | None = None
    margin_top: float | None = None
    margin_bottom: float | None = None
    margin_left: float | None = None
    margin_right: float | None = None
    margin_header: float | None = None
    margin_footer: float | None = None
    page_width: float | None = None
    page_height: float | None = None
    page_number_start: int | None = None
    use_even_page_header_footer: bool | None = None
    use_first_page_header_footer: bool | None = None
    flip_page_orientation: bool | None = None
    document_mode: Literal["PAGES", "PAGELESS"] | None = None


class UpdateSectionStyleOperation(StrictDocOperation):
    type: Literal["update_section_style"]
    start_index: int
    end_index: int
    margin_top: float | None = None
    margin_bottom: float | None = None
    margin_left: float | None = None
    margin_right: float | None = None
    margin_header: float | None = None
    margin_footer: float | None = None
    page_number_start: int | None = None
    use_first_page_header_footer: bool | None = None
    flip_page_orientation: bool | None = None
    content_direction: Literal["LEFT_TO_RIGHT", "RIGHT_TO_LEFT"] | None = None
    column_count: int | None = None
    column_spacing: float | None = None
    column_separator_style: Literal["NONE", "BETWEEN_EACH_COLUMN"] | None = None


class CreateHeaderFooterOperation(StrictDocOperation):
    type: Literal["create_header_footer"]
    section_type: Literal["header", "footer"] = Field(
        description="Which section to create."
    )
    header_footer_type: Literal["DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE"] = Field(
        default="DEFAULT",
        description="Header/footer type to create.",
    )
    section_break_index: int | None = Field(
        default=None,
        description="Optional section break index for section-scoped layouts.",
    )


class InsertImageOperation(SegmentTargetDocOperation):
    type: Literal["insert_image"]
    image_uri: str = Field(description="Image URL or resolvable image URI.")
    index: int | None = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    width: int | None = None
    height: int | None = None
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> InsertImageOperation:
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["insert_doc_tab"]
    title: str
    index: int
    parent_tab_id: str | None = None


class DeleteDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delete_doc_tab"]
    tab_id: str


class UpdateDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["update_doc_tab"]
    tab_id: str
    title: str


BatchDocOperation = Annotated[
    InsertTextOperation
    | DeleteTextOperation
    | ReplaceTextOperation
    | FormatTextOperation
    | UpdateParagraphStyleOperation
    | UpdateTableCellStyleOperation
    | InsertTableOperation
    | InsertTableRowOperation
    | DeleteTableRowOperation
    | InsertTableColumnOperation
    | DeleteTableColumnOperation
    | MergeTableCellsOperation
    | UnmergeTableCellsOperation
    | UpdateTableColumnPropertiesOperation
    | UpdateTableRowStyleOperation
    | PinTableHeaderRowsOperation
    | InsertPageBreakOperation
    | InsertSectionBreakOperation
    | FindReplaceOperation
    | CreateBulletListOperation
    | CreateNamedRangeOperation
    | ReplaceNamedRangeContentOperation
    | DeleteNamedRangeOperation
    | UpdateDocumentStyleOperation
    | UpdateSectionStyleOperation
    | CreateHeaderFooterOperation
    | InsertImageOperation
    | InsertDocTabOperation
    | DeleteDocTabOperation
    | UpdateDocTabOperation,
    Field(discriminator="type"),
]

BatchDocOperations = Annotated[
    list[BatchDocOperation],
    BeforeValidator(_coerce_json_str_to_list),
]
