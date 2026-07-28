"""Errors raised by the registration-PDF parser (SPECIFICATION.md §5.2, §5.3).

All messages are German: they are shown verbatim to the instructor who uploaded the file
(CLAUDE.md — everything user-facing is German). They deliberately contain no student data
(names, Matrikelnummern) so they stay safe to log; only structural facts such as missing
``Nr.`` values, page numbers and column labels appear.
"""

from __future__ import annotations

__all__ = [
    "PdfHeaderError",
    "PdfImportError",
    "PdfLayoutError",
    "RegistrationCompletenessError",
    "ScannedPdfError",
    "UnreadablePdfError",
]


class PdfImportError(Exception):
    """Base class for every failure of :func:`app.pdf_import.parse_registration_pdf`.

    A caller may catch this single type and show ``.message`` to the user. The parser never
    returns partial data next to an error — either it returns a complete, validated
    ``ParsedFile`` or it raises (§5.3).
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnreadablePdfError(PdfImportError):
    """The upload is not a PDF at all, or is damaged beyond the point of being openable."""


class ScannedPdfError(PdfImportError):
    """The PDF carries no text layer, i.e. it is (most likely) a scan.

    OCR is explicitly out of scope for v1 (§5.2, §14 #2), so this is a hard failure with an
    explanatory message rather than a best-effort guess.
    """


class PdfLayoutError(PdfImportError):
    """The PDF has text, but not the expected registration-list table.

    Raised when no table is found at all, when the table header lacks columns the parser needs,
    or when a row that *looks* like a data row cannot be interpreted. §14 #1 requires an
    unrecognised layout to fail loudly rather than be silently misimported.
    """


class PdfHeaderError(PdfImportError):
    """The header block (semester, Termin, title line, Prüfer) could not be parsed (§5.1)."""


class RegistrationCompletenessError(PdfImportError):
    """§5.3's mandatory post-parse validation failed — the parsed list is incomplete.

    Silently dropping a page (a student never gets a grade, with nothing visibly wrong) is
    named in the spec as the single worst realistic failure mode of this feature, so this is a
    hard failure and never a warning. The structured attributes exist so the caller can render
    the problem its own way; ``message`` is a ready-made German sentence.
    """

    def __init__(
        self,
        message: str,
        *,
        missing_nrs: tuple[int, ...] = (),
        duplicate_nrs: tuple[int, ...] = (),
        missing_pages: tuple[int, ...] = (),
        duplicate_pages: tuple[int, ...] = (),
        declared_page_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.missing_nrs = missing_nrs
        self.duplicate_nrs = duplicate_nrs
        self.missing_pages = missing_pages
        self.duplicate_pages = duplicate_pages
        self.declared_page_count = declared_page_count
