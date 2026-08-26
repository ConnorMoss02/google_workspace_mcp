"""Tests for extract_office_xml_text in core/utils.py.

Focus is text FIDELITY for Word documents: the extracted string has to be the
string a human reads, because callers search it. A search that silently misses
is worse than no search — it produces a confident "not present" that is wrong.
"""

import io
import zipfile

from core.utils import extract_office_xml_text

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _docx(body: str, **members: str) -> bytes:
    """Minimal .docx: a body, plus any extra word/*.xml members by name."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {W_NS}><w:body>{body}</w:body>'
            "</w:document>",
        )
        for name, xml in members.items():
            zf.writestr(f"word/{name}.xml", xml)
    return buf.getvalue()


def _hdr(body: str) -> str:
    return f'<?xml version="1.0"?><w:hdr {W_NS}>{body}</w:hdr>'


def _p(*runs: str) -> str:
    inner = "".join(f"<w:r><w:t>{r}</w:t></w:r>" for r in runs)
    return f"<w:p>{inner}</w:p>"


class TestRunsWithinAParagraph:
    def test_word_split_across_runs_is_not_broken_by_a_space(self):
        """The regression this suite exists for.

        Word splits a single word across runs constantly — spell-check state,
        formatting, tracked changes. Joining runs with a space turns one token
        into two, and every search for that token then fails.
        """
        blob = _docx(_p("Project", "X2024"))
        assert extract_office_xml_text(blob, DOCX_MIME) == "ProjectX2024"

    def test_xml_space_preserve_run_keeps_its_spacing(self):
        body = (
            '<w:p><w:r><w:t xml:space="preserve">hello </w:t></w:r>'
            "<w:r><w:t>world</w:t></w:r></w:p>"
        )
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "hello world"

    def test_leading_and_trailing_whitespace_of_a_paragraph_is_trimmed(self):
        body = '<w:p><w:r><w:t xml:space="preserve">  padded  </w:t></w:r></w:p>'
        assert extract_office_xml_text(_docx(body), DOCX_MIME) == "padded"


class TestParagraphBoundaries:
    def test_paragraphs_are_newline_separated(self):
        blob = _docx(_p("First.") + _p("Second."))
        assert extract_office_xml_text(blob, DOCX_MIME) == "First.\nSecond."

    def test_empty_paragraphs_do_not_produce_blank_lines(self):
        blob = _docx(_p("First.") + "<w:p/>" + _p("Second."))
        assert extract_office_xml_text(blob, DOCX_MIME) == "First.\nSecond."


class TestHeadersFootersAndNotes:
    def test_header_text_is_included(self):
        blob = _docx(_p("body text"), header1=_hdr(_p("CONFIDENTIAL")))
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert "body text" in out
        assert "CONFIDENTIAL" in out

    def test_body_comes_before_header_text(self):
        blob = _docx(_p("body text"), header1=_hdr(_p("CONFIDENTIAL")))
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert out.index("body text") < out.index("CONFIDENTIAL")

    def test_footer_and_footnotes_are_included(self):
        blob = _docx(
            _p("body"),
            footer1=_hdr(_p("page footer")),
            footnotes=f'<?xml version="1.0"?><w:footnotes {W_NS}>'
            f"{_p('a footnote')}</w:footnotes>",
        )
        out = extract_office_xml_text(blob, DOCX_MIME)
        assert "page footer" in out
        assert "a footnote" in out

    def test_unrelated_word_members_are_not_scraped(self):
        """settings.xml and friends are configuration, not document text."""
        blob = _docx(
            _p("body"),
            settings=f'<?xml version="1.0"?><w:settings {W_NS}>'
            f"{_p('NOT DOCUMENT TEXT')}</w:settings>",
        )
        assert "NOT DOCUMENT TEXT" not in extract_office_xml_text(blob, DOCX_MIME)


class TestFallbackAndOtherFormats:
    def test_text_outside_any_paragraph_is_still_found(self):
        """Some shapes and tables carry w:t outside a w:p; do not lose them."""
        blob = _docx("<w:tbl><w:r><w:t>orphan cell</w:t></w:r></w:tbl>")
        assert "orphan cell" in extract_office_xml_text(blob, DOCX_MIME)

    def test_corrupt_zip_returns_none(self):
        assert extract_office_xml_text(b"definitely not a zip", DOCX_MIME) is None

    def test_spreadsheet_cells_remain_space_joined(self):
        """Sheets have no paragraphs; this fix must not change their behaviour."""
        buf = io.BytesIO()
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                f'<?xml version="1.0"?><worksheet {ns}><sheetData><row>'
                '<c r="A1" t="str"><v>alpha</v></c>'
                '<c r="B1" t="str"><v>beta</v></c>'
                "</row></sheetData></worksheet>",
            )
        out = extract_office_xml_text(buf.getvalue(), XLSX_MIME)
        assert out is not None
        assert "alpha" in out and "beta" in out
