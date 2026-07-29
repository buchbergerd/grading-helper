"""The internal report (SPECIFICATION.md §9) — Typst rendering over a hand-built statistics payload.

Deliberately no dependency on :func:`app.statistics.build_exam_statistics` here: another agent is
implementing that function concurrently with this file, and this module's job is only
``render_internal_report`` — a pure function of an :class:`~app.statistics.ExamStatistics`
mapping. Every payload below is therefore built by hand, exactly to the shapes documented in
``app/statistics.py``, the same way ``test_attendance_list.py`` builds an ``AttendanceListData``
by hand in ``test_render_is_a_pure_function_of_its_data``. None of it is real or realistic student
data (§13) — there are no names in this payload at all.

Two properties dominate this file for the same reason they dominate the attendance-list tests:

* **the PDF must never read as a final result while grading is in progress or unconfigured**
  (§9). ``test_the_in_progress_status_block_appears_...`` and its negative counterpart pin the
  exact condition (``counts.incomplete > 0 or counts.attendance_not_recorded > 0``, or
  ``grading_configured is False``) that flips the status block on and off.
* **no renderer divides** — every ``Rate`` is asserted to render as both its ``percent`` *and*
  its own ``numerator``/``denominator``, never just one or the other.

An exam with nothing entered at all is a documented, tested non-error: a valid PDF stating that no
grading schema is configured, not an exception.
"""

from __future__ import annotations

import copy
import io
import re
from datetime import date
from typing import Any

import pdfplumber
from sqlalchemy.orm import Session

from app.models import Exam, Lecture, User
from app.reports.internal_report import (
    TEMPLATE_PATH,
    TYPST_PACKAGE_PATH,
    content_disposition,
    internal_report_filename,
    render_internal_report,
)

# --------------------------------------------------------------------------------------------
# Payload construction — plain dicts shaped exactly like app/statistics.py's TypedDicts.
# --------------------------------------------------------------------------------------------

#: The ten grades of the §7.1 scale, best to worst — mirrors app/grading/schema.py::GRADES
#: without importing it, since this file must not depend on the grading engine either.
GRADES: tuple[str, ...] = (
    "1.0",
    "1.3",
    "1.7",
    "2.0",
    "2.3",
    "2.7",
    "3.0",
    "3.3",
    "3.7",
    "4.0",
)


def _rate(numerator: int, denominator: int, percent: str | None) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator, "percent": percent}


def _bin(lower: str, upper: str, label: str, count: int) -> dict[str, Any]:
    return {"lower": lower, "upper": upper, "label": label, "count": count}


def _histogram(
    title: str,
    reference_max: str,
    bins: list[dict[str, Any]],
    max_observed: str | None,
) -> dict[str, Any]:
    return {
        "title": title,
        "bin_width": "1.0",
        "reference_max": reference_max,
        "max_observed": max_observed,
        "included_count": sum(b["count"] for b in bins),
        "bins": bins,
    }


def _versuch(
    versuch: int,
    registered: int,
    attended: int,
    not_attended: int,
    attendance_not_recorded: int,
    graded: int,
    incomplete: int,
    passed: int,
    failed: int,
    failure_rate: dict[str, Any],
    awaiting_schema: int = 0,
) -> dict[str, Any]:
    return {
        "versuch": versuch,
        "label": f"{versuch}. Versuch",
        "registered": registered,
        "attended": attended,
        "not_attended": not_attended,
        "attendance_not_recorded": attendance_not_recorded,
        "graded": graded,
        "incomplete": incomplete,
        "awaiting_schema": awaiting_schema,
        "passed": passed,
        "failed": failed,
        "failure_rate": failure_rate,
    }


def _full_payload() -> dict[str, Any]:
    """A fully populated, internally plausible :class:`~app.statistics.ExamStatistics`.

    "Plausible" only in the sense that the numbers are mutually consistent enough to be a
    realistic report; the template itself never checks that (§9: it renders exactly what it is
    given), so tests are free to build contradictory payloads too where that is the point.
    """
    numeric_counts = [2, 3, 4, 5, 4, 3, 3, 2, 1, 1]
    return {
        "exam_id": 1,
        "lecture_name": "Grundlagen der Informationstechnik",
        "semester": "WiSe 23/24",
        "termin": "1. Termin",
        "exam_date": "12.02.2024",
        "generated_at": "28.07.2026 14:00",
        "max_points": "90",
        "bonus_mode": "NONE",
        "grading_configured": True,
        "passing_threshold": "45.0",
        "counts": {
            "registered": 39,
            "excluded": 1,
            "attended": 35,
            "not_attended": 4,
            "attendance_not_recorded": 0,
            "graded": 33,
            "incomplete": 2,
            "awaiting_schema": 0,
            "passed": 28,
            "failed": 5,
        },
        "rates": {
            "attendance": _rate(35, 39, "89.7"),
            "passing": _rate(28, 33, "84.8"),
            "failure": _rate(5, 33, "15.2"),
        },
        "grade_distribution": {
            "numeric": [
                {"grade": grade, "count": count}
                for grade, count in zip(GRADES, numeric_counts, strict=True)
            ],
            "numeric_count": sum(numeric_counts),
            "failed_count": 5,
            "not_attended_count": 4,
            "mean": "2.35",
            "median": "2.20",
        },
        "total_points_histogram": _histogram(
            "Gesamtpunkte",
            "90",
            [
                _bin("0", "1", "0,0 – 1,0", 1),  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
                _bin("1", "2", "1,0 – 2,0", 3),  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
                _bin("2", "3", "2,0 – 3,0", 5),  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
            ],
            max_observed="2.5",
        ),
        "exercise_histograms": [
            _histogram(
                "Aufgabe 1",
                "10",
                [_bin("0.0", "0.5", "0,0 – 0,5", 1), _bin("0.5", "1.0", "0,5 – 1,0", 2)],  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
                max_observed="0.9",
            ),
            _histogram(
                "Aufgabe 2",
                "20",
                [_bin("0.0", "0.5", "0,0 – 0,5", 4)],  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
                max_observed="0.3",
            ),
        ],
        "versuch_breakdown": [
            _versuch(1, 30, 28, 2, 0, 27, 1, 24, 3, _rate(3, 27, "11.1")),
            _versuch(2, 9, 7, 2, 0, 6, 1, 4, 2, _rate(2, 6, "33.3")),
        ],
    }


def _empty_payload() -> dict[str, Any]:
    """Zero registrations, no grading schema, nothing entered — the "before the import" case."""
    empty_rate = _rate(0, 0, None)
    empty_counts = {
        "registered": 0,
        "excluded": 0,
        "attended": 0,
        "not_attended": 0,
        "attendance_not_recorded": 0,
        "graded": 0,
        "incomplete": 0,
        "awaiting_schema": 0,
        "passed": 0,
        "failed": 0,
    }
    return {
        "exam_id": 2,
        "lecture_name": "Leere Prüfung",
        "semester": "SoSe 25",
        "termin": "2. Termin",
        "exam_date": None,
        "generated_at": "28.07.2026 14:00",
        "max_points": "0",
        "bonus_mode": "NONE",
        "grading_configured": False,
        "passing_threshold": None,
        "counts": empty_counts,
        "rates": {"attendance": empty_rate, "passing": empty_rate, "failure": empty_rate},
        "grade_distribution": {
            "numeric": [{"grade": grade, "count": 0} for grade in GRADES],
            "numeric_count": 0,
            "failed_count": 0,
            "not_attended_count": 0,
            "mean": None,
            "median": None,
        },
        "total_points_histogram": _histogram("Gesamtpunkte", "0", [], max_observed=None),
        "exercise_histograms": [],
        "versuch_breakdown": [],
    }


def _pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# --------------------------------------------------------------------------------------------
# Rendering — the happy paths
# --------------------------------------------------------------------------------------------


def test_fully_populated_payload_renders_a_pdf() -> None:
    pdf_bytes = render_internal_report(_full_payload())

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 0


def test_empty_exam_renders_without_raising() -> None:
    """Zero registrations, no schema, empty histograms — a valid PDF, not an exception (§9)."""
    pdf_bytes = render_internal_report(_empty_payload())

    assert pdf_bytes.startswith(b"%PDF")
    text = _pdf_text(pdf_bytes)
    assert "Kein Notenschema konfiguriert" in text
    assert "keine Daten" in text


def test_half_graded_payload_renders() -> None:
    """``incomplete > 0`` and ``attendance_not_recorded > 0`` together — grading in progress."""
    data = _full_payload()
    data["counts"]["incomplete"] = 3
    data["counts"]["attendance_not_recorded"] = 2

    pdf_bytes = render_internal_report(data)

    assert pdf_bytes.startswith(b"%PDF")
    text = _pdf_text(pdf_bytes)
    assert "Bewertung noch nicht abgeschlossen" in text
    # 3 + 2 = 5, and this addition is the one arithmetic the template is allowed to do (CLAUDE.md
    # forbids it for points/percentages, not for adding two already-computed head counts).
    assert "5 Studierende" in text


def test_histogram_with_a_single_bin_renders() -> None:
    data = _full_payload()
    data["total_points_histogram"] = _histogram(
        "Gesamtpunkte", "90", [_bin("40", "41", "40,0 – 41,0", 12)], max_observed="40.5"  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    )

    pdf_bytes = render_internal_report(data)

    assert pdf_bytes.startswith(b"%PDF")
    assert "40,0 – 41,0" in _pdf_text(pdf_bytes)  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo


def test_histogram_with_about_forty_bins_renders() -> None:
    bins = [
        _bin(str(i), str(i + 1), f"{i},0 – {i + 1},0", (i * 7) % 11) for i in range(40)  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    ]
    data = _full_payload()
    data["total_points_histogram"] = _histogram("Gesamtpunkte", "90", bins, max_observed="39.5")

    pdf_bytes = render_internal_report(data)

    text = _pdf_text(pdf_bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert "0,0 – 1,0" in text  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    assert "39,0 – 40,0" in text  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo


def test_histogram_with_sixty_bins_still_renders() -> None:
    """A 1-point-wide bin histogram over a 60-point exam — §9's own example bin width.

    The chart thins which tick *labels* it draws well before sixty of them would fit, but every
    bin still gets its own bar; this only pins that the render doesn't choke, and that the first
    and last (always-forced, see internal_report.typ) bin captions survive into the PDF text.
    """
    bins = [
        _bin(str(i), str(i + 1), f"{i},0 – {i + 1},0", (i * 13) % 17) for i in range(60)  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    ]
    data = _full_payload()
    data["total_points_histogram"] = _histogram("Gesamtpunkte", "60", bins, max_observed="59.5")

    pdf_bytes = render_internal_report(data)

    text = _pdf_text(pdf_bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert "0,0 – 1,0" in text  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    assert "59,0 – 60,0" in text  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo


def test_a_histogram_with_no_bins_shows_keine_daten_not_a_crash() -> None:
    """§9: an exercise nobody has points for yet must not divide by a zero max count."""
    data = _full_payload()
    data["exercise_histograms"] = [_histogram("Aufgabe 3", "5", [], max_observed=None)]

    pdf_bytes = render_internal_report(data)

    assert pdf_bytes.startswith(b"%PDF")
    text = _pdf_text(pdf_bytes)
    assert "Aufgabe 3" in text
    assert "keine Daten" in text


def test_max_observed_exceeding_reference_max_renders() -> None:
    """An uncapped ALWAYS bonus (§7.3) can push a total past the exam's max points.

    ``Histogram``'s own docstring says the bin range is derived from ``max_observed``, not
    ``reference_max`` — this payload has a bin above ``reference_max`` to prove the template
    doesn't choke on, or silently drop, that student.
    """
    data = _full_payload()
    data["total_points_histogram"] = _histogram(
        "Gesamtpunkte",
        "90",
        [
            _bin("0", "1", "0,0 – 1,0", 2),  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
            _bin("94", "95", "94,0 – 95,0", 1),  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
        ],
        max_observed="94.5",
    )

    pdf_bytes = render_internal_report(data)
    text = _pdf_text(pdf_bytes)

    assert pdf_bytes.startswith(b"%PDF")
    assert "94,0 – 95,0" in text  # noqa: RUF001 -- EN DASH is the histogram label's data, not a typo
    # The reference max (90) and the observed max (94.5) both appear, uncapped and unclamped.
    assert "90" in text
    # German separator (§14 #6): the payload carries the canonical "94.5", the PDF prints it
    # with a comma, exactly as the dashboard does via `formatDecimal`.
    assert "94,5" in text


# --------------------------------------------------------------------------------------------
# §9 — never mistakable for a final result
# --------------------------------------------------------------------------------------------


def test_status_block_is_absent_for_a_complete_configured_exam() -> None:
    """The negative case: no leftover warning once grading is configured and complete."""
    data = _full_payload()
    data["counts"]["incomplete"] = 0
    data["counts"]["attendance_not_recorded"] = 0

    text = _pdf_text(render_internal_report(data))

    assert "Bewertung noch nicht abgeschlossen" not in text
    assert "Kein Notenschema konfiguriert" not in text


def test_status_block_appears_when_attendance_not_recorded_alone() -> None:
    """``attendance_not_recorded > 0`` on its own must already trigger the warning."""
    data = _full_payload()
    data["counts"]["incomplete"] = 0
    data["counts"]["attendance_not_recorded"] = 4

    text = _pdf_text(render_internal_report(data))

    assert "Bewertung noch nicht abgeschlossen" in text
    assert "4 Studierende" in text


def test_unconfigured_schema_message_appears_even_with_other_data_present() -> None:
    data = _full_payload()
    data["grading_configured"] = False
    data["passing_threshold"] = None

    text = _pdf_text(render_internal_report(data))

    assert "Kein Notenschema konfiguriert" in text


# --------------------------------------------------------------------------------------------
# Headings, internal-use notice, and the payload's own numbers surviving into the PDF
# --------------------------------------------------------------------------------------------


def test_rendered_pdf_contains_the_german_headings() -> None:
    text = _pdf_text(render_internal_report(_full_payload()))

    for heading in (
        "Interner Bericht",
        "Nur für den internen Gebrauch",
        "Kennzahlen",
        "Notenverteilung",
        "Histogramm der Gesamtpunkte",
        "Histogramme je Aufgabe",
        "Bestehensquote nach Versuch",
    ):
        assert heading in text, heading


def test_a_rate_renders_as_both_its_percentage_and_its_counts() -> None:
    """No renderer divides (§9): both the ready-made percent and the raw counts must show up."""
    text = _pdf_text(render_internal_report(_full_payload()))

    assert "89,7 %" in text
    assert "35 von 39" in text


def test_a_null_percent_renders_as_an_em_dash() -> None:
    text = _pdf_text(render_internal_report(_empty_payload()))

    assert "—" in text
    assert "0 von 0" in text


def test_exercise_and_grade_and_versuch_numbers_all_appear() -> None:
    text = _pdf_text(render_internal_report(_full_payload()))

    # Grade distribution counts.
    assert "2,35" in text  # mean
    assert "2,20" in text  # median
    # Exercise histogram titles, verbatim from the payload.
    assert "Aufgabe 1" in text
    assert "Aufgabe 2" in text
    # Versuch breakdown.
    assert "1. Versuch" in text
    assert "2. Versuch" in text
    assert "11,1 %" in text
    assert "3 von 27" in text


def test_lecture_name_semester_termin_and_generated_at_appear() -> None:
    text = _pdf_text(render_internal_report(_full_payload()))

    assert "Grundlagen der Informationstechnik" in text
    assert "WiSe 23/24" in text
    assert "1. Termin" in text
    assert "12.02.2024" in text
    assert "28.07.2026 14:00" in text


def test_template_imports_match_the_vendored_package_versions() -> None:
    """§13: an ``@preview`` import must resolve offline, not fetch from Typst's network registry.

    The template now draws its charts with ``cetz``/``cetz-plot`` (§12), so it necessarily
    contains ``@preview`` imports — the old "no ``@preview`` at all" assertion this replaces would
    now fail on the very thing this milestone was asked to build. What actually keeps the render
    offline is ``app/reports/internal_report.py`` passing ``package_path=TYPST_PACKAGE_PATH`` to
    ``typst.compile``, so Typst resolves ``@preview/...`` from that local tree instead of the
    network — see that module's docstring.

    This test pins the property that makes that resolution actually work: every ``@preview``
    import the template names must have an exact matching version vendored on disk, **and** no
    vendored version may go unused. Comment lines are stripped first, mirroring
    ``test_attendance_list.py``'s equivalent check, so this file's own header comment (which
    quotes the import lines as text) cannot be what makes it pass. This fails loudly if someone
    bumps ``#import "@preview/cetz:x.y.z"`` without re-running
    ``scripts/vendor_typst_packages.py``, or vendors a version the template does not actually
    import.
    """
    code = "\n".join(
        line
        for line in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )

    imported = dict(re.findall(r'@preview/([a-zA-Z0-9_-]+):(\d+\.\d+\.\d+)', code))
    assert imported, "template should import at least one @preview package (cetz/cetz-plot, §12)"
    assert "cetz" in imported
    assert "cetz-plot" in imported

    preview_root = TYPST_PACKAGE_PATH / "preview"
    assert preview_root.is_dir(), (
        f"{preview_root} missing — run `uv run python scripts/vendor_typst_packages.py` "
        "from backend/ first"
    )

    for name, version in imported.items():
        assert (preview_root / name / version).is_dir(), (
            f"template imports {name}:{version}, but that version is not vendored at "
            f"{preview_root / name} — re-run scripts/vendor_typst_packages.py"
        )

    # The converse: every vendored package/version must actually be the one the template imports,
    # so a stale or superfluous vendored version does not sit there unnoticed.
    vendored = {
        (package_dir.name, version_dir.name)
        for package_dir in preview_root.iterdir()
        if package_dir.is_dir()
        for version_dir in package_dir.iterdir()
        if version_dir.is_dir()
    }
    assert vendored == set(imported.items())


def test_render_is_a_pure_function_of_its_data() -> None:
    """No database, no exam, no request — just the dict, mirroring the attendance-list test."""
    data = _full_payload()

    first = render_internal_report(data)
    second = render_internal_report(copy.deepcopy(data))

    assert first == second
    assert first.startswith(b"%PDF")


# --------------------------------------------------------------------------------------------
# Filename helpers
# --------------------------------------------------------------------------------------------


def _make_exam(session: Session, owner: User, *, semester: str, termin: str) -> Exam:
    lecture = Lecture(name="Höhere Mathematik", owner_id=owner.id)
    session.add(lecture)
    session.flush()
    exam = Exam(
        lecture_id=lecture.id,
        owner_id=owner.id,
        semester=semester,
        termin=termin,
        exam_date=date(2024, 2, 12),
    )
    session.add(exam)
    session.commit()
    return exam


def test_filename_sanitises_the_semester_slash(session: Session, instructor_user: User) -> None:
    exam = _make_exam(session, instructor_user, semester="WiSe 23/24", termin="1. Termin")

    assert internal_report_filename(exam) == "Interner_Bericht_WiSe_23-24_1._Termin.pdf"


def test_filename_carries_umlauts_through_to_content_disposition(
    session: Session, instructor_user: User
) -> None:
    """``internal_report_filename`` keeps the umlaut verbatim; only the HTTP header transliterates.

    Mirrors ``attendance_list_filename``'s behaviour: the download name itself is not ASCII-
    folded, but the latin-1-safe fallback inside ``Content-Disposition`` is (§14 #6 territory —
    see ``content_disposition``'s own docstring for why the ASCII form exists at all).
    """
    exam = _make_exam(session, instructor_user, semester="SoSe 25", termin="Nachprüfung")
    filename = internal_report_filename(exam)

    assert filename == "Interner_Bericht_SoSe_25_Nachprüfung.pdf"

    disposition = content_disposition(filename)
    disposition.encode("latin-1")  # header values must be latin-1 encodable
    assert 'filename="Interner_Bericht_SoSe_25_Nachpruefung.pdf"' in disposition
    assert "filename*=UTF-8''Interner_Bericht_SoSe_25_Nachpr%C3%BCfung.pdf" in disposition


def test_content_disposition_is_latin_1_safe_and_carries_the_umlaut_name() -> None:
    disposition = content_disposition("Interner_Bericht_Nachprüfung.pdf")

    disposition.encode("latin-1")
    assert 'filename="Interner_Bericht_Nachpruefung.pdf"' in disposition
    assert "filename*=UTF-8''Interner_Bericht_Nachpr%C3%BCfung.pdf" in disposition
