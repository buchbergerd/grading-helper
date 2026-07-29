"""The §9 internal report over HTTP: ``/statistics`` (JSON) and ``/reports/internal`` (PDF).

`tests/test_statistics.py` covers what the statistics *are*, and `tests/test_internal_report.py`
covers how a hand-built payload *renders*. This file covers the seam between them, which is where
§9's actual requirement lives:

**The two views must report the same numbers.** §9 asks for the PDF and the dashboard to share
"one backend statistics-computation module so numbers are always consistent between them".
Testing the module and testing the renderer separately cannot catch a renderer that helpfully
reformats, re-rounds or recomputes something on its way to the page, because each suite only ever
sees its own half. :func:`test_pdf_and_json_report_the_same_numbers` builds one exam, calls both
routes, and asserts the JSON's own values appear literally in the PDF's text.

**The classification buckets must add up.** Three separate pieces of code decide which bucket a
student falls into (overall counts, the grade distribution, the Versuch breakdown), so
:func:`test_counts_partition_the_registered_students` and
:func:`test_versuch_groups_sum_to_the_exam_totals` assert the identities that hold if — and only
if — all three agree.

Plus the two access rules §9 states outright: owner-only, and **not** gated by §8.1.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.grading.schema import GRADES
from app.models import User
from tests.conftest import ADMIN_PASSWORD, INSTRUCTOR_PASSWORD, LoginHelper

#: §7.5's schema on a 60-point exam: 1.0 at 95 %, 4.0 (pass) at 50 %.
VALID_SCHEMA = [
    {"grade": grade, "percentage": percentage}
    for grade, percentage in zip(
        GRADES, ["95", "90", "85", "80", "75", "70", "65", "60", "55", "50"], strict=True
    )
]

EXERCISES = [
    {"name": "Aufgabe 1", "max_points": "30", "position": 1},
    {"name": "Aufgabe 2", "max_points": "30", "position": 2},
]

DEFAULT_COURSE = "B.Sc. WiIng ET/IT"
DEFAULT_TITLE = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"

STATISTICS_ROUTES = [
    "/api/exams/{exam_id}/statistics",
    "/api/exams/{exam_id}/reports/internal",
]


# --------------------------------------------------------------------------------------------
# Fixtures — same conventions as tests/test_points_api.py
# --------------------------------------------------------------------------------------------


@pytest.fixture
def instructor_client(login: LoginHelper, instructor_user: User) -> TestClient:
    client, _token = login("dozentin", INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def other_instructor(session: Session) -> User:
    user = User(
        username="dozent-b",
        password_hash=hash_password(INSTRUCTOR_PASSWORD),
        is_admin=False,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def other_client(login: LoginHelper, other_instructor: User) -> TestClient:
    client, _token = login("dozent-b", INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def admin_client(login: LoginHelper, admin_user: User) -> TestClient:
    client, _token = login("admin", ADMIN_PASSWORD)
    return client


@pytest.fixture
def lecture_id(instructor_client: TestClient) -> int:
    response = instructor_client.post(
        "/api/lectures", json={"name": "Grundlagen der Informationstechnik"}
    )
    assert response.status_code == 201, response.text
    created: int = response.json()["id"]
    return created


def create_exam(client: TestClient, lecture_id: int, **body: object) -> dict[str, Any]:
    payload: dict[str, object] = {
        "semester": "WiSe 23/24",
        "termin": "1. Termin",
        "exam_date": "2024-02-12",
        "exercises": EXERCISES,
        "grading_schema": VALID_SCHEMA,
    }
    payload.update(body)
    response = client.post(f"/api/lectures/{lecture_id}/exams", json=payload)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def add_student(
    client: TestClient, exam_id: int, matrikelnummer: str, versuch: int = 1
) -> dict[str, Any]:
    """One synthetic registration. Names are obviously fictional — never real data (§13)."""
    response = client.post(
        f"/api/exams/{exam_id}/registrations",
        json={
            "matrikelnummer": matrikelnummer,
            "nachname": "Musterfrau",
            "vorname": "Beispiel",
            "course_code": DEFAULT_COURSE,
            "module_title": DEFAULT_TITLE,
            "versuch": versuch,
        },
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def put_points(client: TestClient, registration_id: int, **body: object) -> None:
    response = client.put(f"/api/registrations/{registration_id}/points", json=body)
    assert response.status_code == 200, response.text


@pytest.fixture
def populated_exam(instructor_client: TestClient, lecture_id: int) -> int:
    """One exam exercising every classification bucket §9 distinguishes.

    Deliberately mixed rather than tidy: a passing student, a failing one, a not-attended one, one
    whose attendance was never recorded, one who attended but is missing a point entry, an
    excluded one, and a second-attempt student — plus a bonus that pushes ``final_total`` past
    ``max_points`` (§7.3 ALWAYS is uncapped), which is the case that falls off a histogram whose
    range is derived from ``max_points`` instead of the observed maximum.
    """
    exam = create_exam(instructor_client, lecture_id)
    exam_id = int(exam["id"])
    first, second = (int(e["id"]) for e in exam["exercises"])

    passing = add_student(instructor_client, exam_id, "10000001")
    put_points(
        instructor_client,
        int(passing["id"]),
        attended=True,
        points={str(first): "25", str(second): "20"},
    )

    failing = add_student(instructor_client, exam_id, "10000002")
    put_points(
        instructor_client,
        int(failing["id"]),
        attended=True,
        points={str(first): "10", str(second): "12"},
    )

    over_max = add_student(instructor_client, exam_id, "10000003")
    put_points(
        instructor_client,
        int(over_max["id"]),
        attended=True,
        bonus_points="5",
        points={str(first): "30", str(second): "30"},
    )

    absent = add_student(instructor_client, exam_id, "10000004")
    put_points(instructor_client, int(absent["id"]), attended=False)

    # Attendance never recorded: no PUT at all.
    add_student(instructor_client, exam_id, "10000005")

    incomplete = add_student(instructor_client, exam_id, "10000006", versuch=2)
    put_points(instructor_client, int(incomplete["id"]), attended=True, points={str(first): "18"})

    second_attempt = add_student(instructor_client, exam_id, "10000007", versuch=2)
    put_points(
        instructor_client,
        int(second_attempt["id"]),
        attended=True,
        points={str(first): "8", str(second): "9"},
    )

    excluded = add_student(instructor_client, exam_id, "10000008")
    response = instructor_client.patch(
        f"/api/registrations/{excluded['id']}", json={"excluded": True}
    )
    assert response.status_code == 200, response.text

    return exam_id


def pdf_text(payload: bytes) -> str:
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def german(value: str) -> str:
    """The canonical payload decimal as the reports print it (§14 #6)."""
    return value.replace(".", ",")


# --------------------------------------------------------------------------------------------
# The point of the milestone: the two views cannot disagree
# --------------------------------------------------------------------------------------------


def test_pdf_and_json_report_the_same_numbers(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """§9's whole reason for one shared statistics module, asserted end to end.

    Both routes call ``build_exam_statistics`` on the same exam, so the numbers are identical by
    construction — what this pins is that *rendering* does not diverge: the PDF prints the
    payload's own values (comma-separated per §14 #6) rather than re-deriving or re-rounding
    them. A renderer that recomputed a rate, or rounded a mean to a different number of places,
    fails here and nowhere else in the suite.
    """
    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    report = instructor_client.get(f"/api/exams/{populated_exam}/reports/internal")
    assert report.status_code == 200, report.text
    text = pdf_text(report.content)

    for name in ("attendance", "passing", "failure"):
        rate = stats["rates"][name]
        assert rate["percent"] is not None, f"fixture should exercise a real {name} rate"
        assert f"{german(rate['percent'])} %" in text
        assert f"{rate['numerator']} von {rate['denominator']}" in text

    distribution = stats["grade_distribution"]
    assert distribution["mean"] is not None
    assert german(distribution["mean"]) in text
    assert german(distribution["median"]) in text

    for label, value in (
        ("Angemeldet", stats["counts"]["registered"]),
        ("Bewertet", stats["counts"]["graded"]),
        ("Unvollständig", stats["counts"]["incomplete"]),
    ):
        assert f"{label}: {value}" in text.replace("\n", " ")

    # The bin captions are built once, in Python, precisely so both views cannot label a bar
    # differently. Since the §9 rewrite to cetz-plot charts (see internal_report.typ), a bin's
    # `label` is drawn as an x tick of the total-points histogram, not as a table row — and with
    # dozens of 1-point-wide bins, the template thins which tick *labels* it draws (every bar
    # still gets drawn) so they do not collide on the page. That thinning always keeps the first
    # and last bin's label, which is what's still meaningful to assert here without coupling this
    # test to the template's exact thinning stride; every *other* number this test checks (rates,
    # counts, mean/median) lives in the report's plain-text Kennzahlen/table blocks, unaffected by
    # that chart-only trimming.
    bins = stats["total_points_histogram"]["bins"]
    assert len(bins) > 10, "fixture should exercise enough bins to make thinning relevant"
    assert bins[0]["label"] in text
    assert bins[-1]["label"] in text


def test_counts_partition_the_registered_students(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """Every non-excluded student lands in exactly one bucket.

    Three separate code paths assign these buckets; this identity is what proves they agree. Note
    the grade distribution's numeric counts plus its "nicht bestanden" count must together equal
    ``counts.graded`` — a student cannot hold a numeric grade *and* be counted as failed.
    """
    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    counts = stats["counts"]
    distribution = stats["grade_distribution"]

    assert (
        counts["graded"]
        + counts["incomplete"]
        + counts["awaiting_schema"]
        + counts["not_attended"]
        + counts["attendance_not_recorded"]
        == counts["registered"]
    )
    assert counts["passed"] + counts["failed"] == counts["graded"]

    numeric_total = sum(row["count"] for row in distribution["numeric"])
    assert numeric_total == distribution["numeric_count"] == counts["passed"]
    assert distribution["failed_count"] == counts["failed"]
    assert distribution["not_attended_count"] == counts["not_attended"]

    # The rates' denominators are the counts they claim to be — §9's failure rate divides by
    # graded, not attended, and an in-progress exam is exactly where those differ.
    assert stats["rates"]["attendance"]["denominator"] == counts["registered"]
    assert stats["rates"]["attendance"]["numerator"] == counts["attended"]
    assert stats["rates"]["passing"]["denominator"] == counts["graded"]
    assert stats["rates"]["failure"]["denominator"] == counts["graded"]
    assert counts["attended"] != counts["graded"], "fixture must exercise the in-progress case"


def test_versuch_groups_sum_to_the_exam_totals(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """§9's per-attempt breakdown must partition the same students the exam-wide counts do."""
    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    groups = stats["versuch_breakdown"]
    counts = stats["counts"]

    assert [group["versuch"] for group in groups] == sorted(g["versuch"] for g in groups)
    assert len(groups) > 1, "fixture must exercise more than one attempt number"

    for field in (
        "registered",
        "attended",
        "not_attended",
        "attendance_not_recorded",
        "graded",
        "incomplete",
        "awaiting_schema",
        "passed",
        "failed",
    ):
        assert sum(group[field] for group in groups) == counts[field], field


def test_histogram_range_covers_a_bonus_above_max_points(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """An uncapped §7.3 bonus must not fall off the right edge of the total-points chart."""
    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    histogram = stats["total_points_histogram"]

    assert Decimal(histogram["max_observed"]) > Decimal(histogram["reference_max"])
    assert Decimal(histogram["bins"][-1]["upper"]) >= Decimal(histogram["max_observed"])
    assert sum(b["count"] for b in histogram["bins"]) == histogram["included_count"]
    assert histogram["included_count"] == stats["counts"]["graded"]


# --------------------------------------------------------------------------------------------
# §9's two access rules
# --------------------------------------------------------------------------------------------


def test_statistics_are_not_gated_by_the_completeness_check(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """§9 is a live view over grading in progress — §8.1's gate belongs to §10/§11 only.

    The fixture exam deliberately fails the completeness gate. Both §9 routes must still answer
    ``200``, and the payload must *say* how much is missing rather than refusing to report.
    """
    gate = instructor_client.get(f"/api/exams/{populated_exam}/completeness").json()
    assert gate["is_complete"] is False, "fixture must be incomplete for this test to mean anything"

    for route in STATISTICS_ROUTES:
        response = instructor_client.get(route.format(exam_id=populated_exam))
        assert response.status_code == 200, response.text

    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    assert stats["counts"]["incomplete"] > 0
    assert stats["counts"]["attendance_not_recorded"] > 0


@pytest.mark.parametrize("route", STATISTICS_ROUTES)
def test_another_instructor_gets_404(
    other_client: TestClient, populated_exam: int, route: str
) -> None:
    """404, never 403 — a 403 would confirm the exam exists."""
    response = other_client.get(route.format(exam_id=populated_exam))
    assert response.status_code == 404


@pytest.mark.parametrize("route", STATISTICS_ROUTES)
def test_admins_cannot_read_another_instructors_statistics(
    admin_client: TestClient, populated_exam: int, route: str
) -> None:
    """§3's least-privilege default, restated by §9: "not to admins by default".

    Being an admin grants account management, nothing more — the §9 routes authorise on
    ``Exam.owner_id`` like every other exam route, with no admin bypass anywhere.
    """
    response = admin_client.get(route.format(exam_id=populated_exam))
    assert response.status_code == 404


@pytest.mark.parametrize("route", STATISTICS_ROUTES)
def test_unauthenticated_access_is_rejected(
    client: TestClient, populated_exam: int, route: str
) -> None:
    response = client.get(route.format(exam_id=populated_exam))
    assert response.status_code == 401


@pytest.mark.parametrize("route", STATISTICS_ROUTES)
def test_unknown_exam_is_404(instructor_client: TestClient, route: str) -> None:
    response = instructor_client.get(route.format(exam_id=999))
    assert response.status_code == 404


# --------------------------------------------------------------------------------------------
# Wire format
# --------------------------------------------------------------------------------------------


def test_every_decimal_crosses_the_wire_as_a_string(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """§7.0 at the API layer: a JSON number would be an IEEE-754 double in the browser.

    Walks the decoded payload and asserts no ``float`` survives anywhere — the same posture
    ``tests/test_statistics.py`` takes on the payload before serialisation, repeated here because
    JSON encoding is the step that could reintroduce one.
    """
    payload = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()

    def walk(node: object, path: str) -> None:
        assert not isinstance(node, float), f"float at {path}"
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "$")

    assert isinstance(payload["max_points"], str)
    assert isinstance(payload["rates"]["attendance"]["percent"], str)
    assert isinstance(payload["grade_distribution"]["mean"], str)
    # Counts stay JSON integers — they are cardinalities, not measurements.
    assert isinstance(payload["counts"]["registered"], int)
    assert isinstance(payload["rates"]["attendance"]["numerator"], int)


def test_internal_report_is_a_pdf_with_a_german_filename(
    instructor_client: TestClient, populated_exam: int
) -> None:
    response = instructor_client.get(f"/api/exams/{populated_exam}/reports/internal")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "Interner_Bericht" in response.headers["content-disposition"]
    # Aggregate grade data for one exam, internal-only by §9 — never in a shared cache.
    assert response.headers["cache-control"] == "no-store"


def test_an_exam_with_nothing_entered_still_reports(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """An instructor may open the dashboard before importing anyone — that is not an error."""
    exam = create_exam(instructor_client, lecture_id, exercises=[], grading_schema=[])
    exam_id = int(exam["id"])

    stats = instructor_client.get(f"/api/exams/{exam_id}/statistics")
    assert stats.status_code == 200, stats.text
    payload = stats.json()
    assert payload["counts"]["registered"] == 0
    assert payload["grading_configured"] is False
    assert payload["rates"]["attendance"]["percent"] is None
    assert payload["grade_distribution"]["mean"] is None

    report = instructor_client.get(f"/api/exams/{exam_id}/reports/internal")
    assert report.status_code == 200, report.text
    assert report.content.startswith(b"%PDF")


# --------------------------------------------------------------------------------------------
# Two states the buckets have to survive, both reachable through supported workflows
# --------------------------------------------------------------------------------------------


def test_points_entered_before_a_grading_schema_exists(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Entering points first and configuring the schema last must not lose students.

    Nothing in §7 or §8 requires the schema to exist before points are entered, and the exam
    editor lets an exam be created without one. Such a student is attended and complete, so they
    are neither ``incomplete`` nor gradeable — ``awaiting_schema`` is their bucket, and it exists
    so the five-way partition still holds. Before it did, they were counted nowhere at all and
    simply vanished from the dashboard.
    """
    exam = create_exam(instructor_client, lecture_id, grading_schema=[])
    exam_id = int(exam["id"])
    first, second = (int(e["id"]) for e in exam["exercises"])
    student = add_student(instructor_client, exam_id, "30000001")
    put_points(
        instructor_client,
        int(student["id"]),
        attended=True,
        points={str(first): "20", str(second): "20"},
    )

    stats = instructor_client.get(f"/api/exams/{exam_id}/statistics").json()
    counts = stats["counts"]

    assert stats["grading_configured"] is False
    assert stats["passing_threshold"] is None
    assert counts["awaiting_schema"] == 1
    assert counts["graded"] == 0
    assert counts["incomplete"] == 0
    assert (
        counts["graded"]
        + counts["incomplete"]
        + counts["awaiting_schema"]
        + counts["not_attended"]
        + counts["attendance_not_recorded"]
        == counts["registered"]
    )
    # Their points are still a fact about an exercise, so the per-exercise histograms show them.
    assert stats["exercise_histograms"][0]["included_count"] == 1
    # ...but there is no grade, so nothing lands in the distribution.
    assert stats["grade_distribution"]["numeric_count"] == 0

    report = instructor_client.get(f"/api/exams/{exam_id}/reports/internal")
    assert report.status_code == 200
    assert "Ohne Notenschema: 1" in pdf_text(report.content).replace("\n", " ")


def test_stale_points_of_an_absent_student_are_excluded_from_exercise_histograms(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """§7.4: a student recorded as absent gets "n.e." and their points play no part in any grade.

    This is not a hypothetical state. `docs/api-contract.md` guarantees that flipping ``attended``
    to ``false`` **keeps** previously entered points, precisely so an instructor who mis-ticked
    attendance need not re-transcribe the exam. Counting those points in the exercise
    distribution would describe a student who did not sit the exam.

    Contrast the attendance-not-yet-recorded student below, whose points must still count:
    entering points before ticking attendance is an ordinary order to work in, and excluding them
    would empty the histograms during normal grading.
    """
    exam = create_exam(instructor_client, lecture_id)
    exam_id = int(exam["id"])
    first, second = (int(e["id"]) for e in exam["exercises"])

    absent = add_student(instructor_client, exam_id, "30000002")
    put_points(
        instructor_client,
        int(absent["id"]),
        attended=True,
        points={str(first): "20", str(second): "20"},
    )
    # The supported "I mis-ticked attendance" path: points are resent and therefore retained.
    put_points(
        instructor_client,
        int(absent["id"]),
        attended=False,
        points={str(first): "20", str(second): "20"},
    )

    not_yet_recorded = add_student(instructor_client, exam_id, "30000003")
    put_points(instructor_client, int(not_yet_recorded["id"]), points={str(first): "15"})

    stats = instructor_client.get(f"/api/exams/{exam_id}/statistics").json()

    assert stats["counts"]["not_attended"] == 1
    assert stats["counts"]["attendance_not_recorded"] == 1
    # Only the not-yet-recorded student contributes to Aufgabe 1; the absent one is excluded even
    # though their 20 points are still stored.
    first_histogram = stats["exercise_histograms"][0]
    assert first_histogram["included_count"] == 1
    assert first_histogram["max_observed"] == "15"
    # Aufgabe 2 has only the absent student's stale entry, so it has no contributors at all.
    assert stats["exercise_histograms"][1]["included_count"] == 0


def test_exercise_histograms_count_exactly_the_non_absent_contributors(
    instructor_client: TestClient, populated_exam: int
) -> None:
    """Pins the exercise histograms' population, which is not the total histogram's.

    The total-points histogram takes only ``graded`` students (a partial sum would be a fake
    total). An exercise histogram takes every entered value from a student not recorded as absent,
    which is strictly more — that difference is deliberate and would otherwise be untested.
    """
    stats = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()
    counts = stats["counts"]

    eligible = counts["graded"] + counts["incomplete"] + counts["attendance_not_recorded"]
    for histogram in stats["exercise_histograms"]:
        assert 0 <= histogram["included_count"] <= eligible
        assert sum(b["count"] for b in histogram["bins"]) == histogram["included_count"]

    # The fixture's incomplete student has Aufgabe 1 entered but not Aufgabe 2, so the two
    # histograms must genuinely differ — otherwise this test would pass on a broken population.
    assert (
        stats["exercise_histograms"][0]["included_count"]
        > stats["exercise_histograms"][1]["included_count"]
    )
