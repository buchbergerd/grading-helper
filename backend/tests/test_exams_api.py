"""Exam routes (``/api/exams*``, §4/§7/§8.1/§13 and ``docs/api-contract.md``).

Three properties get disproportionate attention because they are the ones that would be wrong
*silently*:

* a decimal must never touch a binary float — the wire format is a JSON **string**, and a JSON
  number is refused rather than quietly parsed through a double (§7.0);
* authorization anchors on ``Exam.owner_id``, never on the parent lecture's owner (§4), and
  cross-owner access answers ``404`` rather than ``403``;
* a malformed grading schema is a clean ``422`` with German messages — the pure functions in
  ``app/grading/schema.py`` raise ``TypeError`` on a float by design, so a float reaching them
  would surface as a ``500``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.grading.schema import GRADES
from app.models import (
    Exam,
    Exercise,
    ExercisePoints,
    GradeThreshold,
    StudentRegistration,
    User,
)
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

EXAM_ROUTES = [
    ("GET", "/api/exams"),
    ("GET", "/api/exams/1"),
    ("PATCH", "/api/exams/1"),
    ("DELETE", "/api/exams/1"),
    ("POST", "/api/lectures/1/exams"),
]

#: A complete, strictly decreasing §7.2 schema. 1.0 at 95 % and 4.0 at 50 % are the §7.5 values.
VALID_PERCENTAGES = ["95", "90", "85", "80", "75", "70", "65", "60", "55", "50"]
VALID_SCHEMA = [
    {"grade": grade, "percentage": percentage}
    for grade, percentage in zip(GRADES, VALID_PERCENTAGES, strict=True)
]


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


def post_exam(client: TestClient, lecture_id: int, **body: object) -> dict[str, object]:
    payload: dict[str, object] = {"semester": "WiSe 23/24", "termin": "1. Termin"}
    payload.update(body)
    response = client.post(f"/api/lectures/{lecture_id}/exams", json=payload)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


def add_registration(session: Session, exam_id: int, matrikelnummer: str) -> StudentRegistration:
    registration = StudentRegistration(
        exam_id=exam_id,
        matrikelnummer=matrikelnummer,
        nachname="Musterfrau",
        vorname="Erika",
        course_code="B.Sc. WiIng ET/IT",
        module_title="Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)",
        versuch=1,
    )
    session.add(registration)
    session.commit()
    return registration


# --------------------------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), EXAM_ROUTES)
def test_unauthenticated_gets_401_on_every_exam_route(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, json={"semester": "WiSe 23/24", "termin": "1. Termin"})

    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


# --------------------------------------------------------------------------------------------
# §7.0 — decimals cross the wire as strings, never as JSON numbers
# --------------------------------------------------------------------------------------------


def test_max_points_as_a_json_number_is_rejected(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """A JSON float has already been through an IEEE-754 double before pydantic sees it."""
    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={
            "semester": "WiSe 23/24",
            "termin": "1. Termin",
            "exercises": [{"name": "Aufgabe 1", "max_points": 0.75}],
        },
    )

    assert response.status_code == 422
    assert "Zeichenkette" in response.text


@pytest.mark.parametrize("value", [12, True, None, "NaN", "Infinity", "1E+2", "", "abc"])
def test_max_points_rejects_every_non_decimal_string(
    instructor_client: TestClient, lecture_id: int, value: object
) -> None:
    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={
            "semester": "WiSe 23/24",
            "termin": "1. Termin",
            "exercises": [{"name": "Aufgabe 1", "max_points": value}],
        },
    )

    assert response.status_code == 422, f"{value!r} was accepted"


def test_percentage_as_a_json_number_is_rejected(
    instructor_client: TestClient, lecture_id: int
) -> None:
    schema = [dict(entry) for entry in VALID_SCHEMA]
    schema[0]["percentage"] = 95.5  # type: ignore[assignment]

    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={"semester": "WiSe 23/24", "termin": "1. Termin", "grading_schema": schema},
    )

    assert response.status_code == 422
    assert "Zeichenkette" in response.text


def test_max_points_string_lands_in_the_database_exactly(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "0.75"}],
    )

    session.expire_all()
    stored = session.execute(select(Exercise).where(Exercise.exam_id == exam["id"])).scalar_one()
    assert isinstance(stored.max_points, Decimal)
    assert stored.max_points == Decimal("0.75")
    assert str(stored.max_points) == "0.75"


def test_trailing_zeros_survive_the_round_trip(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """ "12.50" in must come "12.50" out — not "12.5", and certainly not 12.5."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "12.50"}],
        grading_schema=VALID_SCHEMA,
    )

    body = instructor_client.get(f"/api/exams/{exam['id']}").json()

    assert body["exercises"][0]["max_points"] == "12.50"
    assert body["total_max_points"] == "12.50"
    assert body["grading_schema"][0]["percentage"] == "95"


# --------------------------------------------------------------------------------------------
# §4 — authorization anchors on Exam.owner_id, never on the lecture's owner
# --------------------------------------------------------------------------------------------


def test_another_instructors_exam_is_404_not_403(
    instructor_client: TestClient, lecture_id: int, other_client: TestClient
) -> None:
    exam = post_exam(instructor_client, lecture_id)

    for method, body in (("GET", None), ("PATCH", {"termin": "2. Termin"}), ("DELETE", None)):
        response = other_client.request(method, f"/api/exams/{exam['id']}?confirm=true", json=body)
        assert response.status_code == 404, f"{method} returned {response.status_code}"
        assert response.json()["detail"] == "Prüfung nicht gefunden."


def test_reassigning_the_owner_transfers_access_both_ways(
    instructor_client: TestClient,
    lecture_id: int,
    other_client: TestClient,
    other_instructor: User,
) -> None:
    """§4's independently editable exam owner is exactly why authorizing via the lecture is wrong.

    The lecture still belongs to instructor A throughout; only the exam moves.
    """
    exam = post_exam(instructor_client, lecture_id)
    assert other_client.get(f"/api/exams/{exam['id']}").status_code == 404

    response = instructor_client.patch(
        f"/api/exams/{exam['id']}", json={"owner_id": other_instructor.id}
    )
    assert response.status_code == 200
    assert response.json()["owner_id"] == other_instructor.id

    assert instructor_client.get(f"/api/exams/{exam['id']}").status_code == 404
    assert other_client.get(f"/api/exams/{exam['id']}").status_code == 200
    # ... and the new owner sees it in their own list even though the lecture is not theirs.
    assert [entry["id"] for entry in other_client.get("/api/exams").json()] == [exam["id"]]


def test_owner_id_must_be_an_existing_active_user(
    instructor_client: TestClient, lecture_id: int, other_instructor: User, session: Session
) -> None:
    exam = post_exam(instructor_client, lecture_id)

    unknown = instructor_client.patch(f"/api/exams/{exam['id']}", json={"owner_id": 9999})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["errors"] == [
        "Der angegebene Besitzer existiert nicht oder ist deaktiviert."
    ]

    other_instructor.is_active = False
    session.commit()
    deactivated = instructor_client.patch(
        f"/api/exams/{exam['id']}", json={"owner_id": other_instructor.id}
    )
    assert deactivated.status_code == 422
    # The rejected request must not have moved the exam.
    assert instructor_client.get(f"/api/exams/{exam['id']}").status_code == 200


def test_creating_an_exam_under_a_foreign_lecture_is_404(
    instructor_client: TestClient, lecture_id: int, other_client: TestClient
) -> None:
    response = other_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={"semester": "WiSe 23/24", "termin": "1. Termin"},
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------------------------
# CRUD happy paths
# --------------------------------------------------------------------------------------------


def test_create_exam_returns_the_contract_shape(
    instructor_client: TestClient, lecture_id: int, instructor_user: User
) -> None:
    body = post_exam(
        instructor_client,
        lecture_id,
        exam_date="2024-02-13",
        bonus_mode="ONLY_IF_PASSING_WITHOUT_BONUS",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    assert set(body) == {
        "id",
        "lecture_id",
        "lecture_name",
        "semester",
        "termin",
        "exam_date",
        "bonus_mode",
        "owner_id",
        "exercises",
        "grading_schema",
        "registration_count",
        "total_max_points",
        "recomputation_warning",
    }
    assert body["exam_date"] == "2024-02-13"
    assert body["bonus_mode"] == "ONLY_IF_PASSING_WITHOUT_BONUS"
    assert body["owner_id"] == instructor_user.id
    assert body["lecture_name"] == "Grundlagen der Informationstechnik"
    assert body["registration_count"] == 0
    assert body["recomputation_warning"] is None
    assert body["exercises"] == [
        {"id": body["exercises"][0]["id"], "name": "Aufgabe 1", "max_points": "60", "position": 1}
    ]
    assert len(body["grading_schema"]) == 10


def test_create_exam_defaults_are_empty_and_always(
    instructor_client: TestClient, lecture_id: int
) -> None:
    body = post_exam(instructor_client, lecture_id)

    assert body["bonus_mode"] == "ALWAYS"
    assert body["exercises"] == []
    assert body["grading_schema"] == []
    assert body["exam_date"] is None
    assert body["total_max_points"] == "0"


def test_list_exams_can_be_filtered_by_lecture(
    instructor_client: TestClient, lecture_id: int
) -> None:
    other_lecture = instructor_client.post("/api/lectures", json={"name": "Digitaltechnik"}).json()
    mine = post_exam(instructor_client, lecture_id)
    post_exam(instructor_client, other_lecture["id"])

    all_exams = instructor_client.get("/api/exams").json()
    filtered = instructor_client.get(f"/api/exams?lecture_id={lecture_id}").json()

    assert len(all_exams) == 2
    assert [entry["id"] for entry in filtered] == [mine["id"]]


def test_patch_updates_scalar_fields(instructor_client: TestClient, lecture_id: int) -> None:
    exam = post_exam(instructor_client, lecture_id, exam_date="2024-02-13")

    response = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={
            "semester": "SoSe 24",
            "termin": "2. Termin",
            "exam_date": None,
            "bonus_mode": "ONLY_IF_PASSING_WITHOUT_BONUS",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["semester"], body["termin"]) == ("SoSe 24", "2. Termin")
    assert body["exam_date"] is None  # explicit null clears the date
    assert body["bonus_mode"] == "ONLY_IF_PASSING_WITHOUT_BONUS"
    assert body["recomputation_warning"] is None  # no collection was replaced


def test_patch_leaves_omitted_fields_alone(instructor_client: TestClient, lecture_id: int) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exam_date="2024-02-13",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    body = instructor_client.patch(f"/api/exams/{exam['id']}", json={"termin": "2. Termin"}).json()

    assert body["exam_date"] == "2024-02-13"
    assert len(body["exercises"]) == 1
    assert len(body["grading_schema"]) == 10


# --------------------------------------------------------------------------------------------
# Exercises: server-side renumbering and full replace
# --------------------------------------------------------------------------------------------


def test_positions_are_renumbered_server_side(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Client-sent positions are ignored: they must be unique and contiguous ``1..N``."""
    body = post_exam(
        instructor_client,
        lecture_id,
        exercises=[
            {"name": "Aufgabe A", "max_points": "10", "position": 7},
            {"name": "Aufgabe B", "max_points": "20", "position": 7},
        ],
    )

    assert [exercise["position"] for exercise in body["exercises"]] == [1, 2]
    assert [exercise["name"] for exercise in body["exercises"]] == ["Aufgabe A", "Aufgabe B"]
    assert body["total_max_points"] == "30"


def test_patch_exercises_is_a_full_replace_not_a_merge(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[
            {"name": "Aufgabe 1", "max_points": "10"},
            {"name": "Aufgabe 2", "max_points": "20"},
            {"name": "Aufgabe 3", "max_points": "30"},
        ],
    )

    body = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={"exercises": [{"name": "Nur eine Aufgabe", "max_points": "5"}]},
    ).json()

    assert [exercise["name"] for exercise in body["exercises"]] == ["Nur eine Aufgabe"]
    assert body["total_max_points"] == "5"
    session.expire_all()
    remaining = session.execute(
        select(func.count()).select_from(Exercise).where(Exercise.exam_id == exam["id"])
    ).scalar_one()
    assert remaining == 1


def test_patch_can_reorder_the_same_exercises(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Renumbering in place would trip ``uq_exercise_exam_position``; delete-flush-insert
    must not."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[
            {"name": "Aufgabe 1", "max_points": "10"},
            {"name": "Aufgabe 2", "max_points": "20"},
        ],
    )

    response = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={
            "exercises": [
                {"name": "Aufgabe 2", "max_points": "20"},
                {"name": "Aufgabe 1", "max_points": "10"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert [exercise["name"] for exercise in response.json()["exercises"]] == [
        "Aufgabe 2",
        "Aufgabe 1",
    ]
    assert [exercise["position"] for exercise in response.json()["exercises"]] == [1, 2]


def test_patch_response_matches_a_subsequent_get(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Guards against serialising the pre-commit collection (``expire_on_commit`` is off)."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "10"}],
    )

    patched = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={
            "exercises": [
                {"name": "Neu A", "max_points": "30"},
                {"name": "Neu B", "max_points": "30"},
            ],
            "grading_schema": VALID_SCHEMA,
        },
    ).json()
    fetched = instructor_client.get(f"/api/exams/{exam['id']}").json()

    assert patched | {"recomputation_warning": None} == fetched


@pytest.mark.parametrize("max_points", ["0", "-1", "0.0", "-0.5"])
def test_non_positive_max_points_is_422(
    instructor_client: TestClient, lecture_id: int, max_points: str
) -> None:
    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={
            "semester": "WiSe 23/24",
            "termin": "1. Termin",
            "exercises": [{"name": "Aufgabe 1", "max_points": max_points}],
        },
    )

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any("größer als 0" in message for message in errors), errors


# --------------------------------------------------------------------------------------------
# §7.2 grading schema validation and §7.5 thresholds
# --------------------------------------------------------------------------------------------


def test_threshold_points_match_the_worked_example(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """§7.5: a 60-point exam, 95 % → 57.0 and 50 % → 30.0, floored to the nearest 0.5."""
    body = post_exam(
        instructor_client,
        lecture_id,
        exercises=[
            {"name": "Aufgabe 1", "max_points": "35"},
            {"name": "Aufgabe 2", "max_points": "25"},
        ],
        grading_schema=VALID_SCHEMA,
    )

    assert body["total_max_points"] == "60"
    by_grade = {entry["grade"]: entry["threshold_points"] for entry in body["grading_schema"]}
    assert by_grade["1.0"] == "57.0"
    assert by_grade["4.0"] == "30.0"
    assert [entry["grade"] for entry in body["grading_schema"]] == list(GRADES)


def test_thresholds_follow_a_max_points_change(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """§8.1: the backend is authoritative — thresholds are recomputed, never stored stale."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    body = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={"exercises": [{"name": "Aufgabe 1", "max_points": "30"}]},
    ).json()

    by_grade = {entry["grade"]: entry["threshold_points"] for entry in body["grading_schema"]}
    assert by_grade["1.0"] == "28.5"  # floor(0.95 * 30 / 0.5) * 0.5
    assert by_grade["4.0"] == "15.0"


def test_non_strictly_decreasing_schema_is_422_with_german_messages(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Must be a clean 422 — ``validate_grading_schema`` raising on a float would be a 500."""
    percentages = list(VALID_PERCENTAGES)
    percentages[1] = "95"  # 1.3 equal to 1.0 — not strictly decreasing
    schema = [
        {"grade": grade, "percentage": percentage}
        for grade, percentage in zip(GRADES, percentages, strict=True)
    ]

    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={"semester": "WiSe 23/24", "termin": "1. Termin", "grading_schema": schema},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any("streng fallend" in message for message in errors), errors


def test_incomplete_schema_is_422(instructor_client: TestClient, lecture_id: int) -> None:
    """A schema is either absent/empty or complete — half a schema is a misconfiguration."""
    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={
            "semester": "WiSe 23/24",
            "termin": "1. Termin",
            "grading_schema": VALID_SCHEMA[:3],
        },
    )

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any("Fehlende Noten" in message for message in errors), errors


def test_unknown_grade_in_schema_is_422(instructor_client: TestClient, lecture_id: int) -> None:
    schema = [*VALID_SCHEMA, {"grade": "5.0", "percentage": "10"}]

    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={"semester": "WiSe 23/24", "termin": "1. Termin", "grading_schema": schema},
    )

    assert response.status_code == 422
    assert any("Unbekannte Noten" in message for message in response.json()["detail"]["errors"])


def test_duplicate_grade_in_schema_is_422(instructor_client: TestClient, lecture_id: int) -> None:
    schema = [*VALID_SCHEMA, {"grade": "1.0", "percentage": "99"}]

    response = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={"semester": "WiSe 23/24", "termin": "1. Termin", "grading_schema": schema},
    )

    assert response.status_code == 422
    assert any("Doppelte Noten" in message for message in response.json()["detail"]["errors"])


def test_a_rejected_patch_leaves_the_exam_untouched(
    instructor_client: TestClient, lecture_id: int
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    rejected = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={
            "exercises": [{"name": "Kaputt", "max_points": "0"}],
            "grading_schema": VALID_SCHEMA[:2],
        },
    )

    assert rejected.status_code == 422
    assert instructor_client.get(f"/api/exams/{exam['id']}").json() == exam


def test_a_valid_replace_next_to_an_invalid_owner_id_is_not_applied(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Pins the ordering in ``update_exam``: nothing may mutate before the owner is resolved.

    The collection replace here is perfectly valid on its own — only ``owner_id`` is bad. If
    ``_replace_exercises`` ever moved above ``_resolve_owner``, the exercises would be committed
    while the request reports failure.
    """
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
    )

    rejected = instructor_client.patch(
        f"/api/exams/{exam['id']}",
        json={
            "exercises": [{"name": "Ganz andere Aufgabe", "max_points": "10"}],
            "owner_id": 9999,
        },
    )

    assert rejected.status_code == 422
    assert instructor_client.get(f"/api/exams/{exam['id']}").json() == exam


# --------------------------------------------------------------------------------------------
# §4 copy-forward
# --------------------------------------------------------------------------------------------


def test_copy_forward_takes_the_most_recent_prior_exam(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """Rule: ``exam_date`` DESC, NULL counts as oldest, ``id`` DESC as the tiebreaker."""
    post_exam(
        instructor_client,
        lecture_id,
        semester="ohne Datum",
        exam_date=None,
        exercises=[{"name": "Undatiert", "max_points": "1"}],
    )
    post_exam(
        instructor_client,
        lecture_id,
        semester="WiSe 23/24",
        exam_date="2024-02-13",
        bonus_mode="ONLY_IF_PASSING_WITHOUT_BONUS",
        exercises=[
            {"name": "Aufgabe 1", "max_points": "35"},
            {"name": "Aufgabe 2", "max_points": "25"},
        ],
        grading_schema=VALID_SCHEMA,
    )
    post_exam(
        instructor_client,
        lecture_id,
        semester="WiSe 22/23",
        exam_date="2023-02-14",
        exercises=[{"name": "Alt", "max_points": "99"}],
    )

    fresh = post_exam(instructor_client, lecture_id, semester="SoSe 24", termin="1. Termin")

    assert [exercise["name"] for exercise in fresh["exercises"]] == ["Aufgabe 1", "Aufgabe 2"]
    assert [exercise["max_points"] for exercise in fresh["exercises"]] == ["35", "25"]
    assert [exercise["position"] for exercise in fresh["exercises"]] == [1, 2]
    assert len(fresh["grading_schema"]) == 10
    # §4 lists bonus_mode among the copied-forward settings.
    assert fresh["bonus_mode"] == "ONLY_IF_PASSING_WITHOUT_BONUS"


def test_copy_forward_breaks_a_date_tie_by_newest_id(
    instructor_client: TestClient, lecture_id: int
) -> None:
    post_exam(
        instructor_client,
        lecture_id,
        semester="erste",
        exam_date="2024-02-13",
        exercises=[{"name": "Aelter", "max_points": "10"}],
    )
    post_exam(
        instructor_client,
        lecture_id,
        semester="zweite",
        exam_date="2024-02-13",
        exercises=[{"name": "Neuer", "max_points": "20"}],
    )

    fresh = post_exam(instructor_client, lecture_id, semester="SoSe 24")

    assert [exercise["name"] for exercise in fresh["exercises"]] == ["Neuer"]


def test_copy_forward_with_no_prior_exam_starts_empty(
    instructor_client: TestClient, lecture_id: int
) -> None:
    body = post_exam(instructor_client, lecture_id)

    assert body["exercises"] == []
    assert body["grading_schema"] == []
    assert body["bonus_mode"] == "ALWAYS"


def test_an_explicit_empty_list_suppresses_the_copy(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """ "Field absent" and "field present but empty" must not be conflated."""
    post_exam(
        instructor_client,
        lecture_id,
        exam_date="2024-02-13",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    body = post_exam(
        instructor_client,
        lecture_id,
        semester="SoSe 24",
        exercises=[],
        grading_schema=[],
    )

    assert body["exercises"] == []
    assert body["grading_schema"] == []


def test_partial_copy_forward_copies_only_the_absent_field(
    instructor_client: TestClient, lecture_id: int
) -> None:
    post_exam(
        instructor_client,
        lecture_id,
        exam_date="2024-02-13",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )

    body = post_exam(
        instructor_client,
        lecture_id,
        semester="SoSe 24",
        exercises=[{"name": "Ganz neu", "max_points": "40"}],
    )

    assert [exercise["name"] for exercise in body["exercises"]] == ["Ganz neu"]
    assert len(body["grading_schema"]) == 10  # still copied forward


def test_editing_the_copy_does_not_touch_the_source_exam(
    instructor_client: TestClient, lecture_id: int
) -> None:
    """§4: the copy-forward is one-time — nothing stays linked."""
    source = post_exam(
        instructor_client,
        lecture_id,
        exam_date="2024-02-13",
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    copy = post_exam(instructor_client, lecture_id, semester="SoSe 24")

    instructor_client.patch(
        f"/api/exams/{copy['id']}",
        json={
            "exercises": [{"name": "Umbenannt", "max_points": "10"}],
            "grading_schema": [],
        },
    )

    unchanged = instructor_client.get(f"/api/exams/{source['id']}").json()
    assert unchanged["exercises"] == source["exercises"]
    assert unchanged["total_max_points"] == "60"
    assert len(unchanged["grading_schema"]) == 10


def test_copy_forward_ignores_another_lectures_exams(
    instructor_client: TestClient, lecture_id: int
) -> None:
    other = instructor_client.post("/api/lectures", json={"name": "Digitaltechnik"}).json()
    post_exam(
        instructor_client,
        other["id"],
        exam_date="2024-02-13",
        exercises=[{"name": "Fremd", "max_points": "60"}],
    )

    body = post_exam(instructor_client, lecture_id)

    assert body["exercises"] == []


# --------------------------------------------------------------------------------------------
# §8.1 recomputation warning
# --------------------------------------------------------------------------------------------


def test_no_warning_without_registrations(instructor_client: TestClient, lecture_id: int) -> None:
    exam = post_exam(instructor_client, lecture_id)

    body = instructor_client.patch(
        f"/api/exams/{exam['id']}", json={"grading_schema": VALID_SCHEMA}
    ).json()

    assert body["recomputation_warning"] is None


def test_schema_change_with_registrations_warns(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    """§8.1: thresholds must never shift silently under data an instructor already has on paper.

    ``affected_registrations`` is 0 here because the added registration carries neither
    attendance nor points (see ``add_registration``); ``grades_changed`` is 0 because, with
    attendance unrecorded, the computed grade is ``None`` before and after regardless of the
    schema — the full points-entry recomputation test lives in ``tests/test_points_api.py``.
    """
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
    )
    add_registration(session, int(exam["id"]), "1000001")

    body = instructor_client.patch(
        f"/api/exams/{exam['id']}", json={"grading_schema": VALID_SCHEMA}
    ).json()

    assert body["registration_count"] == 1
    assert body["recomputation_warning"] == {
        "changed": True,
        "affected_registrations": 0,
        "grades_changed": 0,
    }


def test_scalar_only_patch_does_not_warn(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    exam = post_exam(instructor_client, lecture_id)
    add_registration(session, int(exam["id"]), "1000001")

    body = instructor_client.patch(f"/api/exams/{exam['id']}", json={"termin": "2. Termin"}).json()

    assert body["recomputation_warning"] is None


def test_affected_registrations_counts_students_with_recorded_data(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    """Forward-looking check of the §8.1 hook M3 has to build on."""
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
    )
    with_data = add_registration(session, int(exam["id"]), "1000001")
    add_registration(session, int(exam["id"]), "1000002")
    with_data.attended = True
    session.commit()

    body = instructor_client.patch(
        f"/api/exams/{exam['id']}", json={"grading_schema": VALID_SCHEMA}
    ).json()

    assert body["recomputation_warning"] == {
        "changed": True,
        "affected_registrations": 1,
        "grades_changed": 1,
    }


# --------------------------------------------------------------------------------------------
# Deletion (§13)
# --------------------------------------------------------------------------------------------


def test_delete_without_confirm_is_409_with_a_german_message(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    exam = post_exam(instructor_client, lecture_id)

    response = instructor_client.delete(f"/api/exams/{exam['id']}")

    assert response.status_code == 409
    assert "confirm=true" in response.json()["detail"]
    assert "unwiderruflich" in response.json()["detail"]
    assert session.get(Exam, exam["id"]) is not None


def test_delete_cascades_to_exercises_thresholds_and_registrations(
    instructor_client: TestClient, lecture_id: int, session: Session
) -> None:
    exam = post_exam(
        instructor_client,
        lecture_id,
        exercises=[{"name": "Aufgabe 1", "max_points": "60"}],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = int(exam["id"])
    registration = add_registration(session, exam_id, "1000001")
    exercise_id = int(exam["exercises"][0]["id"])
    session.add(
        ExercisePoints(
            registration_id=registration.id, exercise_id=exercise_id, points=Decimal("12.5")
        )
    )
    session.commit()

    response = instructor_client.delete(f"/api/exams/{exam_id}?confirm=true")

    assert response.status_code == 204
    session.expire_all()
    assert session.get(Exam, exam_id) is None
    for model, column in (
        (Exercise, Exercise.exam_id),
        (GradeThreshold, GradeThreshold.exam_id),
        (StudentRegistration, StudentRegistration.exam_id),
    ):
        remaining = session.execute(
            select(func.count()).select_from(model).where(column == exam_id)
        ).scalar_one()
        assert remaining == 0, model.__name__
    orphan_points = session.execute(
        select(func.count())
        .select_from(ExercisePoints)
        .where(ExercisePoints.exercise_id == exercise_id)
    ).scalar_one()
    assert orphan_points == 0
