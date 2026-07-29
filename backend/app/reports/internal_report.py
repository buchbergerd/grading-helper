"""The internal report (SPECIFICATION.md §9): a Typst PDF over the shared statistics payload.

Unlike :mod:`app.reports.attendance_list`, there is deliberately **no** ``build_*_data`` function
in this module. §9 requires the PDF and the interactive in-app dashboard to share "one backend
statistics-computation module so numbers are always consistent between them" — that module is
``app/statistics.py`` (:func:`~app.statistics.build_exam_statistics`), and it is the *only* place
that reads the ORM, decodes ``Decimal``s, rounds a percentage or builds a histogram bin label.
This module only turns the resulting :class:`~app.statistics.ExamStatistics` mapping into PDF
bytes. If a `build_*_data` step existed here too, the PDF and the dashboard would each compute
their own numbers from the ORM and could disagree — exactly the failure mode §9 names.

:func:`render_internal_report` therefore mirrors only the second half of
``attendance_list.py``'s split: it is a pure function of its argument, never touching the
database, and crosses the payload into ``templates/internal_report.typ`` as one JSON string via
Typst's ``sys.inputs``, same as the attendance list.

The template's charts are drawn with the ``cetz``/``cetz-plot`` Typst packages (§12), imported as
``@preview/cetz:0.3.4`` and ``@preview/cetz-plot:0.1.1``. Typst would otherwise fetch those from
its ``@preview`` network registry the first time the template compiles — which §13 forbids at
*runtime* on a machine holding real exam data (names, Matrikelnummern). §13's actual constraint is
that exam data never leaves the machine, not that nothing is ever downloaded: fetching a public
package while *building* the image is a different, offline-safe act, done once by
``scripts/vendor_typst_packages.py`` into :data:`TYPST_PACKAGE_PATH`, well before any exam data
exists in the container. Passing that directory to ``typst.compile`` as ``package_path`` makes the
render itself consult only the local tree — the registry is never contacted while a report is
being generated. :func:`render_internal_report` checks once, cheaply, that the vendored tree is
present and raises a `RuntimeError` naming the vendoring command if it is missing, so a missing
build-time dependency fails loudly here instead of surfacing as a raw Typst "package not found".

``ignore_system_fonts=True`` additionally pins output to the fonts embedded in the typst binary
itself, so the render also does not depend on which fonts happen to be installed on the host.

The filename helpers (``sanitize_filename_part``, ``to_ascii``) and ``content_disposition`` are
imported from ``attendance_list.py`` rather than duplicated — that module promoted them from
private to public names for exactly this reuse.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import typst

from app.models import Exam
from app.reports.attendance_list import content_disposition, sanitize_filename_part
from app.statistics import ExamStatistics

TEMPLATE_PATH = Path(__file__).parent / "templates" / "internal_report.typ"

#: The vendored `cetz`/`cetz-plot` tree, built once by `scripts/vendor_typst_packages.py` (see the
#: module docstring). Passed to `typst.compile` as `package_path` so a render resolves the
#: template's `@preview` imports from disk and never from the network.
TYPST_PACKAGE_PATH = Path(__file__).parent / "typst_packages"

#: A fixed, arbitrary instant passed to `typst.compile`'s `timestamp` argument. Without it, Typst
#: stamps the PDF's own `/CreationDate` metadata with the *actual* wall-clock time of the render,
#: which `render_internal_report` never otherwise depends on — the report's own visible "Erstellt
#: am" line comes from `data["generated_at"]` (`app/statistics.py`), not this metadata field. Two
#: calls with identical `data` a few hundred milliseconds apart can straddle a wall-clock second,
#: producing byte-different PDFs from byte-identical input; the more text this template draws
#: (every histogram now draws many small chart labels), the longer a render takes and the more
#: likely that becomes. Pinning it is what makes `render_internal_report` a pure function of its
#: argument in practice, not just in the docstring below — see
#: `test_render_is_a_pure_function_of_its_data`.
_FIXED_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)

__all__ = [
    "TEMPLATE_PATH",
    "TYPST_PACKAGE_PATH",
    "content_disposition",
    "internal_report_filename",
    "render_internal_report",
]


@lru_cache(maxsize=1)
def _template_source() -> bytes:
    """The template, read once. It is a static file that ships with the package."""
    return TEMPLATE_PATH.read_bytes()


def render_internal_report(data: ExamStatistics) -> bytes:
    """Render the internal report to PDF bytes. Pure — no database, no request, no filesystem
    beyond the static template and the vendored package tree.

    An exam with nothing entered yet (zero registrations, no grading schema configured, empty
    histograms) renders a valid PDF stating plainly that nothing has been computed, rather than
    raising: §9 explicitly describes this as "a live view over current data... useful while
    grading is still in progress", so an early look at the report is a normal use case, not an
    error.

    The template imports ``@preview/cetz`` and ``@preview/cetz-plot`` for its charts (§12).
    ``package_path=TYPST_PACKAGE_PATH`` is what keeps that import offline (§13) — it points Typst
    at the tree ``scripts/vendor_typst_packages.py`` populates at build time instead of the
    network registry those imports would otherwise hit. That fetch is a one-time, build-time
    setup step, never something this function — or any request-handling code — does itself; see
    the module docstring for why that split is still §13-compliant. If the tree is missing (a
    fresh checkout that hasn't run the vendoring script yet), fail with a clear, actionable
    ``RuntimeError`` rather than letting Typst's own "package not found" surface to a caller who
    has no reason to know what ``@preview/cetz`` even is.
    """
    if not TYPST_PACKAGE_PATH.is_dir():
        raise RuntimeError(
            "Vendored Typst packages not found at "
            f"{TYPST_PACKAGE_PATH}. Run `uv run python scripts/vendor_typst_packages.py` "
            "from backend/ once after `uv sync` (and again if the pinned cetz/cetz-plot "
            "versions ever change) before rendering the internal report."
        )
    return typst.compile(
        _template_source(),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        # §13: no outbound network calls at runtime. `package_path` resolves the template's
        # @preview imports from the vendored tree instead of Typst's network registry; forbidding
        # system fonts additionally guarantees the byte output does not depend on which fonts the
        # host happens to have installed.
        package_path=str(TYPST_PACKAGE_PATH),
        ignore_system_fonts=True,
        # Purity, not a display value — see _FIXED_TIMESTAMP's own comment. The report's visible
        # generation time is `data["generated_at"]`, computed once in app/statistics.py.
        timestamp=_FIXED_TIMESTAMP,
    )


def internal_report_filename(exam: Exam) -> str:
    """The German download filename, e.g. ``Interner_Bericht_WiSe_23-24_1._Termin.pdf``."""
    parts = [
        "Interner_Bericht",
        sanitize_filename_part(exam.semester),
        sanitize_filename_part(exam.termin),
    ]
    return "_".join(parts) + ".pdf"
