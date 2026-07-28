"""Engine-specific text/table extraction for registration PDFs (SPECIFICATION.md §5.2).

Both supported engines are reduced to the same engine-neutral :class:`RawPage` shape, so that
*all* interpretation — header patterns, column matching by header text, §5.3 validation — lives
in :mod:`app.pdf_import.parser` and behaves identically no matter which engine produced the
page. Nothing in this module knows what a student is.

Engines (§5.2):

* ``pdfplumber`` — primary. Reads the PDF's own table structure.
* ``PyMuPDF``/``fitz`` — fallback, used only when pdfplumber finds no usable table. See
  :func:`extract_with_pymupdf` for why it reconstructs the table from word coordinates instead
  of trusting ``page.get_text()``'s line order.
"""

from __future__ import annotations

import io
import statistics
from dataclasses import dataclass

import fitz  # type: ignore[import-untyped]  # PyMuPDF, §5.2's fallback engine
import pdfplumber

from app.pdf_import.errors import UnreadablePdfError

__all__ = ["RawPage", "extract_with_pdfplumber", "extract_with_pymupdf"]

# Marker used to tell the registration table apart from decorative/stray tables on a page.
_HEADER_TOKEN = "nr."


@dataclass(frozen=True, slots=True)
class RawPage:
    """One physical PDF page, reduced to what the parser needs.

    ``lines`` is the page's text in reading order (used for the header block and the
    ``Seite X von Y`` footer). ``table`` is the registration table if one was found: the first
    element is the header row, the rest are data rows, every row padded to the same width.
    ``table`` is empty when the page has no recognisable table — the parser decides whether that
    is fatal, not this module.
    """

    number: int  # 1-based *physical* position in the file (not the printed page number)
    lines: tuple[str, ...]
    table: tuple[tuple[str, ...], ...]


def _looks_like_table_header(row: list[str | None] | tuple[str, ...]) -> bool:
    """Does this row look like the ``Nr. | Matr.-Nr. | ...`` header row?

    Only the ``Nr.`` marker is checked here; verifying that all *required* columns are present
    is the parser's job, because a table that has a ``Nr.`` column but not the rest must fail
    loudly (§14 #1) rather than be quietly ignored as "not our table".
    """
    return any((cell or "").strip().casefold() == _HEADER_TOKEN for cell in row)


def _normalise_table(rows: list[list[str | None]]) -> tuple[tuple[str, ...], ...]:
    """Trim a pdfplumber table to (header row, *data rows) with ``None`` cells as ``""``."""
    for index, row in enumerate(rows):
        if _looks_like_table_header(row):
            kept = rows[index:]
            width = max(len(row) for row in kept)
            return tuple(
                tuple((cell or "") for cell in row) + ("",) * (width - len(row)) for row in kept
            )
    return ()


def extract_with_pdfplumber(data: bytes) -> list[RawPage]:
    """Primary engine (§5.2)."""
    pages: list[RawPage] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                lines = tuple((page.extract_text() or "").splitlines())
                table: tuple[tuple[str, ...], ...] = ()
                for candidate in page.extract_tables():
                    # The real export's letterhead graphics are drawn like table cells, so
                    # pdfplumber reports a couple of empty [['']] pseudo-tables ahead of the
                    # real one. Take the first table that actually has a Nr. header.
                    table = _normalise_table(candidate)
                    if table:
                        break
                pages.append(RawPage(number=number, lines=lines, table=table))
    except UnreadablePdfError:
        raise
    except Exception as exc:  # pdfplumber/pdfminer raise a zoo of types on damaged input
        raise UnreadablePdfError(
            "Die Datei konnte nicht als PDF gelesen werden. Bitte prüfen Sie, ob es sich um "
            "eine unbeschädigte PDF-Datei handelt."
        ) from exc
    return pages


# --- PyMuPDF fallback -------------------------------------------------------------------------


def _cluster_words_into_lines(
    words: list[tuple[float, float, float, float, str]],
) -> list[list[tuple[float, float, float, float, str]]]:
    """Group words into visual lines by vertical position, each line sorted left to right.

    ``page.get_text()``'s own line grouping cannot be used: on the *real* sample the table is
    drawn as filled rects rather than ruled lines, and its cells come back from the content
    stream in roughly column-major order (all Nr. values, then all Matr.-Nr. values, ...), i.e.
    badly scrambled versus reading order. Word coordinates are unaffected by that ordering,
    which is why this reconstruction works on the real file where ``get_text()`` does not.
    """
    if not words:
        return []
    tolerance = max(2.0, 0.5 * statistics.median(word[3] - word[1] for word in words))
    lines: list[tuple[float, list[tuple[float, float, float, float, str]]]] = []
    for centre, word in sorted(((w[1] + w[3]) / 2, w) for w in words):
        if lines and centre - lines[-1][0] <= tolerance:
            lines[-1][1].append(word)
        else:
            lines.append((centre, [word]))
    return [sorted(line, key=lambda word: word[0]) for _, line in lines]


def _assign_to_columns(
    line: list[tuple[float, float, float, float, str]], boundaries: list[float]
) -> tuple[str, ...]:
    """Bucket a line's words into columns using x boundaries between the header words."""
    cells = [""] * (len(boundaries) + 1)
    for word in line:
        index = sum(1 for boundary in boundaries if word[0] >= boundary)
        cells[index] = f"{cells[index]} {word[4]}".strip()
    return tuple(cells)


def extract_with_pymupdf(data: bytes) -> list[RawPage]:
    """Fallback engine (§5.2), used when pdfplumber finds no usable table.

    Columns are taken from the *individual words* of the header row rather than from
    gap-clustered groups: on the real sample the gap between ``Vers.`` and ``Kommentar`` is
    3.9pt, barely wider than an intra-label space, so any gap heuristic merges those two columns
    (and their data) on that exact file. One word per column is unambiguous instead, and a
    header label that really is two words simply fails to match in the parser — loudly, per
    §14 #1, rather than by silently gluing two columns together.
    """
    pages: list[RawPage] = []
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnreadablePdfError(
            "Die Datei konnte nicht als PDF gelesen werden. Bitte prüfen Sie, ob es sich um "
            "eine unbeschädigte PDF-Datei handelt."
        ) from exc
    try:
        for number, page in enumerate(document, start=1):
            words = [
                (float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
                for w in page.get_text("words")
            ]
            lines = _cluster_words_into_lines(words)
            text_lines = tuple(" ".join(word[4] for word in line) for line in lines)

            table: tuple[tuple[str, ...], ...] = ()
            for index, line in enumerate(lines):
                if not _looks_like_table_header(tuple(word[4] for word in line)):
                    continue
                boundaries = [
                    (line[i][2] + line[i + 1][0]) / 2  # midway between two header words
                    for i in range(len(line) - 1)
                ]
                header = tuple(word[4].strip() for word in line)
                body = lines[index + 1 :]
                table = (header, *(_assign_to_columns(row, boundaries) for row in body))
                break

            pages.append(RawPage(number=number, lines=text_lines, table=table))
    finally:
        document.close()
    return pages
