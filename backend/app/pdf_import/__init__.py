"""Registration-PDF import (SPECIFICATION.md §5).

Pure, database-free parsing layer: :func:`parse_registration_pdf` turns the bytes of one
university registration export into header metadata plus student rows, or raises a
:class:`PdfImportError` carrying a German, user-showable message. It is importable without
FastAPI and never touches the ORM — mapping a ``ParsedFile`` onto ``StudentRegistration`` rows
(including the §5.3 cross-file checks: duplicate Matrikelnummer, semester/Termin mismatch) is
the import API's job, not this package's.
"""

from app.pdf_import.errors import (
    PdfHeaderError,
    PdfImportError,
    PdfLayoutError,
    RegistrationCompletenessError,
    ScannedPdfError,
    UnreadablePdfError,
)
from app.pdf_import.parser import (
    NORMAL_KOMMENTAR,
    Engine,
    ParsedFile,
    ParsedRow,
    parse_registration_pdf,
)

__all__ = [
    "NORMAL_KOMMENTAR",
    "Engine",
    "ParsedFile",
    "ParsedRow",
    "PdfHeaderError",
    "PdfImportError",
    "PdfLayoutError",
    "RegistrationCompletenessError",
    "ScannedPdfError",
    "UnreadablePdfError",
    "parse_registration_pdf",
]
