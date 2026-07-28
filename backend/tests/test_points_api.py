"""Points/attendance entry and the §8.1 completeness gate over HTTP (§7, §8, §8.1).

The single most important test here is :func:`test_worked_example_7_5_through_the_http_api`: it
drives §7.5's worked example through the real HTTP routes end to end (create exam, create
students, ``PUT`` their points, read back the computed grade) rather than calling
``app.grading.engine`` directly — that engine already has 108 unit tests of its own
(``tests/test_grading_engine.py``); what is new and worth pinning here is that the API layer
wires ``ExercisePoints``/``bonus_points``/``attended``/``bonus_mode`` into it correctly.

Everything else follows the same three properties ``test_exams_api.py`` and
``test_registrations_api.py`` already lean on:

* decimals cross the wire as JSON **strings**, never numbers (§7.0);
* another instructor's exam/registration answers ``404``, never ``403``;
* §8.1's "not entered" vs. "entered zero" distinction is a database fact (row absent vs. a row
  holding ``Decimal("0")``), never an API-layer approximation.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password
from app.grading.schema import GRADES
from app.models import ExercisePoints, User
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

#: §7.5's schema: 1.0 at 95 %, 4.0 (pass) at 50 %, ten strictly decreasing grades. On a 60-point
#: exam every percentage already floors cleanly to itself (§7.2), so the point thresholds are
#: 57.0, 54.0, 51.0, 48.0, 45.0, 42.0, 39.0, 36.0, 33.0, 30.0 in that grade order.
VALID_PERCENTAGES = ["95", "90", "85", "80", "75", "70", "65", "60", "55", "50"]
VALID_SCHEMA = [
    {"grade": grade, "percentage": percentage}
    for grade, percentage in zip(GRADES, VALID_PERCENTAGES, strict=True)
]

DEFAULT_COURSE = "B.Sc. WiIng ET/IT"
DEFAULT_TITLE = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"

POINTS_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/exams/1/points", None),
    ("GET", "/api/exams/1/completeness", None),
    ("PUT", "/api/registrations/1/points", {}),
    ("PUT", "/api/exams/1/points", {"entries": []}),
]


# --------------------------------------------------------------------------------------------
# Fixtures and small helpers (mirrors tests/test_exams_api.py's conventions)
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
def lecture_id(instructor_client: TestClient) -> int:
    response = instructor_client.post(
        "/api/lectures", json={"name": "Grundlagen der Informationstechnik"}
    )
    assert response.status_code == 201, response.text
    created: int = response.json()["id"]
    return created


@pytest.fixture
def fresh_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session independent of the request's, for "what is *really* in the database" checks."""
    with session_factory() as db:
        yield db


def post_exam(client: TestClient, lecture_id: int, **body: object) -> dict[str, Any]:
    payload: dict[str, object] = {"semester": "WiSe 23/24", "termin": "1. Termin"}
    payload.update(body)
    response = client.post(f"/api/lectures/{lecture_id}/exams", json=payload)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def create_registration(
    client: TestClient, exam_id: int, matrikelnummer: str, **overrides: object
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "matrikelnummer": matrikelnummer,
        "nachname": "Nachname",
        "vorname": "Vorname",
        "course_code": DEFAULT_COURSE,
        "module_title": DEFAULT_TITLE,
        "versuch": 1,
    }
    payload.update(overrides)
    response = client.post(f"/api/exams/{exam_id}/registrations", json=payload)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def save_points(client: TestClient, registration_id: int, **body: object) -> Any:
    return client.put(f"/api/registrations/{registration_id}/points", json=body)


def points_row_count(db: Session, registration_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(ExercisePoints)
            .where(ExercisePoints.registration_id == registration_id)
        ).scalar_one()
    )


def exercise_points(db: Session, registration_id: int, exercise_id: int) -> Decimal | None:
    row = db.execute(
        select(ExercisePoints).where(
            ExercisePoints.registration_id == registration_id,
            ExercisePoints.exercise_id == exercise_id,
        )
    ).scalar_one_or_none()
    return None if row is None else row.points


# --------------------------------------------------------------------------------------------
# §7.5's worked example, driven through the HTTP API end to end
# --------------------------------------------------------------------------------------------


def test_worked_example_7_5_through_the_http_api(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """§7.5's six rows, saved via ``PUT`` and read back via the same route's response.

    Rows 1-4 run under ``bonus_mode = ALWAYS`` (the exam's default at creation); rows 5-6 need
    ``ONLY_IF_PASSING_WITHOUT_BONUS`` for the same raw/bonus values to mean something different,
    so the exam's ``bonus_mode`` is flipped via ``PATCH`` in between. That ``PATCH`` touches
    neither ``exercises`` nor ``grading_schema``, so it must not fire a §8.1 recomputation
    warning and must not disturb any already-saved row.
    """
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exercise_id = exam["exercises"][0]["id"]
    exam_id = exam["id"]

    rows = [create_registration(instructor_client, exam_id, f"100000{n}") for n in range(1, 7)]

    # Row 1: 30.0/0/attended → exactly meets the 30.0 (4.0) threshold.
    response = save_points(
        instructor_client,
        rows[0]["id"],
        attended=True,
        points={str(exercise_id): "30.0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["registration"]
    assert body["raw_total"] == "30.0"
    assert body["final_total"] == "30.0"
    assert body["grade"] == "4.0"

    # Row 2: 29.5/0/attended → below 30.0.
    body = save_points(
        instructor_client, rows[1]["id"], attended=True, points={str(exercise_id): "29.5"}
    ).json()["registration"]
    assert body["final_total"] == "29.5"
    assert body["grade"] == "nicht bestanden"

    # Row 3: 29.5/0/not attended → "n.e." regardless of the points on record.
    body = save_points(
        instructor_client, rows[2]["id"], attended=False, points={str(exercise_id): "29.5"}
    ).json()["registration"]
    assert body["grade"] == "n.e."
    assert body["final_total"] is None
    assert body["raw_total"] == "29.5"

    # Row 4: 28.0/+3/ALWAYS → bonus applied unconditionally, now clears 30.0.
    body = save_points(
        instructor_client,
        rows[3]["id"],
        attended=True,
        bonus_points="3",
        points={str(exercise_id): "28.0"},
    ).json()["registration"]
    assert body["raw_total"] == "28.0"
    assert body["final_total"] == "31.0"
    assert body["grade"] == "4.0"

    # Switch bonus_mode for rows 5-6. No exercises/grading_schema touched → no recompute warning.
    patched = instructor_client.patch(
        f"/api/exams/{exam_id}", json={"bonus_mode": "ONLY_IF_PASSING_WITHOUT_BONUS"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["recomputation_warning"] is None

    # Row 5: 28.0/+3/ONLY_IF_PASSING_WITHOUT_BONUS → raw_total 28.0 < 30.0, bonus withheld.
    body = save_points(
        instructor_client,
        rows[4]["id"],
        attended=True,
        bonus_points="3",
        points={str(exercise_id): "28.0"},
    ).json()["registration"]
    assert body["raw_total"] == "28.0"
    assert body["final_total"] == "28.0"
    assert body["grade"] == "nicht bestanden"

    # Row 6: 32.0/+3/ONLY_IF_PASSING_WITHOUT_BONUS → raw_total 32.0 ≥ 30.0, bonus applied.
    # final_total 35.0 meets the 3.7 threshold (33.0) but not 3.3's (36.0) → exactly "3.7".
    body = save_points(
        instructor_client,
        rows[5]["id"],
        attended=True,
        bonus_points="3",
        points={str(exercise_id): "32.0"},
    ).json()["registration"]
    assert body["raw_total"] == "32.0"
    assert body["final_total"] == "35.0"
    assert body["grade"] == "3.7"

    # Row 4's *stored* data (28.0 raw, +3 bonus) is untouched by the later PATCH/saves — but
    # nothing here is a stored grade (app/models/registration.py has no such column), so the grid
    # now reports row 4 recomputed under the *current* bonus_mode too: 28.0 < 30.0, so
    # ONLY_IF_PASSING_WITHOUT_BONUS now withholds its bonus, same as row 5.
    grid = instructor_client.get(f"/api/exams/{exam_id}/points").json()
    assert grid["bonus_mode"] == "ONLY_IF_PASSING_WITHOUT_BONUS"
    assert grid["grading_configured"] is True
    matrikelnummern = [entry["matrikelnummer"] for entry in grid["entries"]]
    assert matrikelnummern == sorted(matrikelnummern)  # sorted by Matrikelnummer
    row4 = next(e for e in grid["entries"] if e["matrikelnummer"] == "1000004")
    assert row4["points"][str(exercise_id)] == "28.0"
    assert row4["bonus_points"] == "3"
    assert row4["final_total"] == "28.0"
    assert row4["grade"] == "nicht bestanden"


# --------------------------------------------------------------------------------------------
# §7.0 — decimals cross the wire as strings
# --------------------------------------------------------------------------------------------


def test_a_json_number_in_points_is_rejected(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = save_points(instructor_client, registration["id"], points={str(exercise_id): 12.5})

    assert response.status_code == 422
    assert "Zeichenkette" in response.text


def test_a_decimal_string_lands_in_the_database_exactly(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = save_points(
        instructor_client, registration["id"], points={str(exercise_id): "12.50"}
    )
    assert response.status_code == 200, response.text

    stored = exercise_points(fresh_session, registration["id"], exercise_id)
    assert stored == Decimal("12.50")
    assert str(stored) == "12.50"
    assert response.json()["registration"]["points"][str(exercise_id)] == "12.50"


# --------------------------------------------------------------------------------------------
# §8.1 — "not entered" vs. "entered zero"; a null point deletes the row
# --------------------------------------------------------------------------------------------


def test_a_null_point_deletes_the_row_not_a_zero(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    saved = save_points(
        instructor_client,
        registration["id"],
        attended=True,
        points={str(exercise_id): "10"},
    )
    assert saved.status_code == 200, saved.text
    assert points_row_count(fresh_session, registration["id"]) == 1

    cleared = save_points(
        instructor_client,
        registration["id"],
        attended=True,
        points={str(exercise_id): None},
    )
    assert cleared.status_code == 200, cleared.text
    assert points_row_count(fresh_session, registration["id"]) == 0
    assert cleared.json()["registration"]["points"] == {}
    assert cleared.json()["registration"]["is_complete"] is False

    completeness = instructor_client.get(f"/api/exams/{exam['id']}/completeness").json()
    assert completeness["is_complete"] is False
    incomplete = completeness["incomplete_students"][0]
    assert incomplete["matrikelnummer"] == "1000001"
    assert incomplete["missing_exercises"] == ["Aufgabe 1"]


def test_a_missing_points_key_also_deletes_the_row(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    """PUT is a full replace: an exercise simply absent from ``points`` is deleted too."""
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    save_points(instructor_client, registration["id"], points={str(exercise_id): "10"})
    assert points_row_count(fresh_session, registration["id"]) == 1

    replaced = save_points(instructor_client, registration["id"], attended=True)
    assert replaced.status_code == 200, replaced.text
    assert points_row_count(fresh_session, registration["id"]) == 0


# --------------------------------------------------------------------------------------------
# §8 — warn, don't clamp; negative values are rejected
# --------------------------------------------------------------------------------------------


def test_points_above_max_points_are_saved_with_a_warning(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "10"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = save_points(instructor_client, registration["id"], points={str(exercise_id): "12"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"], "exceeding max_points must produce a warning"
    assert body["registration"]["points"][str(exercise_id)] == "12"
    assert exercise_points(fresh_session, registration["id"], exercise_id) == Decimal(12)


@pytest.mark.parametrize("field", ["points", "bonus_points"])
def test_negative_values_are_rejected(
    instructor_client: TestClient, lecture_id: int, field: str
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    body: dict[str, object] = {"attended": True}
    if field == "points":
        body["points"] = {str(exercise_id): "-1"}
    else:
        body["bonus_points"] = "-1"

    response = save_points(instructor_client, registration["id"], **body)

    assert response.status_code == 422


def test_writing_points_to_an_excluded_registration_is_422(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001", excluded=True)

    response = save_points(instructor_client, registration["id"], points={str(exercise_id): "10"})

    assert response.status_code == 422


def test_an_unknown_exercise_id_is_rejected(instructor_client: TestClient, lecture_id: int) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = save_points(instructor_client, registration["id"], points={"999999": "10"})

    assert response.status_code == 422


# --------------------------------------------------------------------------------------------
# §7.4 — attendance interaction
# --------------------------------------------------------------------------------------------


def test_attended_false_reports_n_e_and_keeps_points_in_the_database(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    first = save_points(
        instructor_client,
        registration["id"],
        attended=True,
        points={str(exercise_id): "45"},
    )
    assert first.json()["registration"]["grade"] == "2.3"  # 45 meets the 2.3 threshold (45.0)

    second = save_points(
        instructor_client,
        registration["id"],
        attended=False,
        points={str(exercise_id): "45"},
    )
    assert second.status_code == 200, second.text
    body = second.json()["registration"]
    assert body["grade"] == "n.e."
    assert body["final_total"] is None

    assert exercise_points(fresh_session, registration["id"], exercise_id) == Decimal(45)


def test_attended_null_is_not_computable_and_never_n_e(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = save_points(instructor_client, registration["id"], points={str(exercise_id): "45"})

    assert response.status_code == 200, response.text
    body = response.json()["registration"]
    assert body["attended"] is None
    assert body["grade"] is None
    assert body["status"] == "ATTENDANCE_NOT_RECORDED"
    assert body["final_total"] is None


# --------------------------------------------------------------------------------------------
# Grading schema absent/incomplete — the grid degrades gracefully (never 500s)
# --------------------------------------------------------------------------------------------


def test_grid_without_a_grading_schema_reports_grading_configured_false(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam["id"], "1000001")
    save_points(
        instructor_client, registration["id"], attended=True, points={str(exercise_id): "45"}
    )

    grid = instructor_client.get(f"/api/exams/{exam['id']}/points").json()

    assert grid["grading_configured"] is False
    entry = grid["entries"][0]
    assert entry["grade"] is None
    assert entry["status"] is None
    assert entry["raw_total"] == "45"
    assert entry["final_total"] is None


# --------------------------------------------------------------------------------------------
# §8.1 completeness gate
# --------------------------------------------------------------------------------------------


def test_completeness_gate_names_exactly_the_incomplete_students(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[
            {"name": "Aufgabe 1", "max_points": "30"},
            {"name": "Aufgabe 2", "max_points": "30"},
        ],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = exam["id"]
    ex1, ex2 = (item["id"] for item in exam["exercises"])

    missing_attendance = create_registration(instructor_client, exam_id, "1000001")

    missing_exercise = create_registration(instructor_client, exam_id, "1000002")
    save_points(instructor_client, missing_exercise["id"], attended=True, points={str(ex1): "20"})

    complete = create_registration(instructor_client, exam_id, "1000003")
    save_points(
        instructor_client,
        complete["id"],
        attended=True,
        points={str(ex1): "20", str(ex2): "20"},
    )

    excluded = create_registration(instructor_client, exam_id, "1000004", excluded=True)
    assert excluded["excluded"] is True  # never entered, never counted either way

    body = instructor_client.get(f"/api/exams/{exam_id}/completeness").json()

    assert body["is_complete"] is False
    assert body["incomplete_count"] == 2
    by_matrikelnummer = {item["matrikelnummer"]: item for item in body["incomplete_students"]}
    assert set(by_matrikelnummer) == {"1000001", "1000002"}
    assert by_matrikelnummer["1000001"]["attendance_missing"] is True
    assert by_matrikelnummer["1000001"]["missing_exercises"] == []
    assert by_matrikelnummer["1000002"]["attendance_missing"] is False
    assert by_matrikelnummer["1000002"]["missing_exercises"] == ["Aufgabe 2"]

    # Complete the two remaining rows — the gate now reports the exam ready. PUT is a full
    # replace (§8), so completing "missing_exercise" must resend the already-saved Aufgabe 1
    # value too, or that omission would itself delete it.
    save_points(
        instructor_client,
        missing_attendance["id"],
        attended=True,
        points={str(ex1): "10", str(ex2): "10"},
    )
    save_points(
        instructor_client,
        missing_exercise["id"],
        attended=True,
        points={str(ex1): "20", str(ex2): "20"},
    )

    finished = instructor_client.get(f"/api/exams/{exam_id}/completeness").json()
    assert finished["is_complete"] is True
    assert finished["incomplete_count"] == 0
    assert finished["incomplete_students"] == []


# --------------------------------------------------------------------------------------------
# §8.1 recomputation warning: grades_changed vs. affected_registrations
# --------------------------------------------------------------------------------------------


def test_recomputation_reports_the_number_of_grades_that_actually_changed(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = exam["id"]
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam_id, "1000001")
    save_points(
        instructor_client, registration["id"], attended=True, points={str(exercise_id): "29"}
    )
    before = instructor_client.get(f"/api/exams/{exam_id}/points").json()["entries"][0]
    assert before["grade"] == "nicht bestanden"  # 29 < 30.0

    # Move the 4.0 threshold from 50 % (30.0) down to 45 % (27.0). 3.7 stays at 55 % (33.0), so
    # the schema is still strictly decreasing. 29 now clears the new 4.0 threshold.
    new_schema = [dict(item) for item in VALID_SCHEMA]
    new_schema[-1] = {"grade": "4.0", "percentage": "45"}

    response = instructor_client.patch(f"/api/exams/{exam_id}", json={"grading_schema": new_schema})

    assert response.status_code == 200, response.text
    warning = response.json()["recomputation_warning"]
    assert warning["changed"] is True
    assert warning["grades_changed"] == 1

    after = instructor_client.get(f"/api/exams/{exam_id}/points").json()["entries"][0]
    assert after["grade"] == "4.0"


def test_recomputation_reports_zero_when_no_grade_actually_moves(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """A percentage edit that still floors to the same 0.5-point threshold changes nothing."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = exam["id"]
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam_id, "1000001")
    save_points(
        instructor_client, registration["id"], attended=True, points={str(exercise_id): "35"}
    )

    # 50 % and 50.5 % of 60 both floor to 30.0 (§7.2) — the point threshold is unchanged.
    new_schema = [dict(item) for item in VALID_SCHEMA]
    new_schema[-1] = {"grade": "4.0", "percentage": "50.5"}

    response = instructor_client.patch(f"/api/exams/{exam_id}", json={"grading_schema": new_schema})

    assert response.status_code == 200, response.text
    warning = response.json()["recomputation_warning"]
    assert warning["changed"] is True
    assert warning["grades_changed"] == 0


def test_recomputation_survives_an_exercise_replace_that_cascades_away_old_points(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Replacing ``exercises`` deletes the old ``Exercise`` rows (§4 contract: full replace).

    SQLite's ``ON DELETE CASCADE`` removes their ``ExercisePoints`` at the database level,
    invisibly to the ORM session — the recomputation snapshot must not choke on, or silently
    ignore, that (see ``app/api/exams.py::update_exam``'s ``db.expire_all()``).
    """
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = exam["id"]
    exercise_id = exam["exercises"][0]["id"]
    registration = create_registration(instructor_client, exam_id, "1000001")
    save_points(
        instructor_client, registration["id"], attended=True, points={str(exercise_id): "30"}
    )
    before = instructor_client.get(f"/api/exams/{exam_id}/points").json()["entries"][0]
    assert before["grade"] == "4.0"

    response = instructor_client.patch(
        f"/api/exams/{exam_id}",
        json={"exercises": [{"name": "Aufgabe 1", "max_points": "30"}]},
    )

    assert response.status_code == 200, response.text
    warning = response.json()["recomputation_warning"]
    assert warning["changed"] is True
    assert warning["grades_changed"] == 1  # old points are gone; raw_total resets to 0

    after = instructor_client.get(f"/api/exams/{exam_id}/points").json()["entries"][0]
    assert after["points"] == {}
    assert after["grade"] == "nicht bestanden"


# --------------------------------------------------------------------------------------------
# Bulk save: same per-row semantics, one transaction
# --------------------------------------------------------------------------------------------


def test_bulk_save_writes_every_row_in_one_call(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exam_id = exam["id"]
    exercise_id = exam["exercises"][0]["id"]
    reg_a = create_registration(instructor_client, exam_id, "1000001")
    reg_b = create_registration(instructor_client, exam_id, "1000002")

    response = instructor_client.put(
        f"/api/exams/{exam_id}/points",
        json={
            "entries": [
                {
                    "registration_id": reg_a["id"],
                    "attended": True,
                    "points": {str(exercise_id): "10"},
                },
                {
                    "registration_id": reg_b["id"],
                    "attended": True,
                    "bonus_points": "1.5",
                    "points": {str(exercise_id): "20"},
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {entry["matrikelnummer"] for entry in body["entries"]} == {"1000001", "1000002"}
    assert exercise_points(fresh_session, reg_a["id"], exercise_id) == Decimal(10)
    assert exercise_points(fresh_session, reg_b["id"], exercise_id) == Decimal(20)

    # A second bulk save that deletes reg_a's point (full replace, §8) must come back in the
    # *response* with an empty points map too, not the pre-delete value — the grid re-renders
    # straight from this response without a follow-up GET.
    deleted = instructor_client.put(
        f"/api/exams/{exam_id}/points",
        json={"entries": [{"registration_id": reg_a["id"], "attended": True, "points": {}}]},
    )
    assert deleted.status_code == 200, deleted.text
    entry = deleted.json()["entries"][0]
    assert entry["points"] == {}
    assert entry["is_complete"] is False
    assert points_row_count(fresh_session, reg_a["id"]) == 0


def test_bulk_save_is_atomic_one_bad_row_rejects_the_whole_batch(
    instructor_client: TestClient, lecture_id: int, fresh_session: Session
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exam_id = exam["id"]
    exercise_id = exam["exercises"][0]["id"]
    reg_ok = create_registration(instructor_client, exam_id, "1000001")
    reg_bad = create_registration(instructor_client, exam_id, "1000002")

    response = instructor_client.put(
        f"/api/exams/{exam_id}/points",
        json={
            "entries": [
                {
                    "registration_id": reg_ok["id"],
                    "attended": True,
                    "points": {str(exercise_id): "10"},
                },
                {
                    "registration_id": reg_bad["id"],
                    "attended": True,
                    "points": {str(exercise_id): "-5"},
                },
            ]
        },
    )

    assert response.status_code == 422
    assert points_row_count(fresh_session, reg_ok["id"]) == 0
    assert points_row_count(fresh_session, reg_bad["id"]) == 0


def test_bulk_save_rejects_a_registration_from_another_exam(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam_a = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    exam_b = post_exam(
        instructor_client,
        lecture_id,
        semester="WiSe 23/24",
        termin="2. Termin",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
    )
    foreign = create_registration(instructor_client, exam_b["id"], "1000001")

    response = instructor_client.put(
        f"/api/exams/{exam_a['id']}/points",
        json={"entries": [{"registration_id": foreign["id"], "attended": True}]},
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------------------------
# Ownership 404, unauthenticated 401
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), POINTS_ROUTES)
def test_unauthenticated_gets_401(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


def test_another_instructors_exam_is_404_on_the_grid_and_completeness_routes(
    instructor_client: TestClient, lecture_id: int, other_client: TestClient
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )

    for path in (f"/api/exams/{exam['id']}/points", f"/api/exams/{exam['id']}/completeness"):
        response = other_client.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "Prüfung nicht gefunden."

    bulk = other_client.put(f"/api/exams/{exam['id']}/points", json={"entries": []})
    assert bulk.status_code == 404


def test_another_instructors_registration_is_404_on_the_single_row_route(
    instructor_client: TestClient, lecture_id: int, other_client: TestClient
) -> None:
    exam = post_exam(
        instructor_client, lecture_id, exercises=[{"name": "Aufgabe 1", "max_points": "60"}]
    )
    registration = create_registration(instructor_client, exam["id"], "1000001")

    response = other_client.put(
        f"/api/registrations/{registration['id']}/points", json={"attended": True}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Anmeldung nicht gefunden."
