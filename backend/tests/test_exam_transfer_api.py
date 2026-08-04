"""Whole-exam export/import (``GET /api/exams/{id}/export``, ``POST /api/exams/import``).

Not part of any §15 milestone — added afterwards as a backup/transfer feature. The properties
that get disproportionate attention here, because a "helpful" import could silently break them:

* the §7.4/§8.1 distinction between ``attended: false`` (points kept, but "n.e." wins) and
  ``attended: null`` (not yet recorded) must survive the round trip unchanged;
* a **missing** points key must stay missing on import, never become a stored ``"0"`` (§8.1);
* an **excluded** registration must round-trip too (§5.3: excluded is an audit flag, never a
  deletion — dropping it on export/import would be a silent, and permanent, data loss);
* the file never carries ``owner_id``; the importer always becomes the new exam's owner;
* the whole import is all-or-nothing, same posture as the registration-PDF import (§5.3).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.grading.schema import GRADES
from app.models import Exam, Lecture, StudentRegistration, User
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

#: A complete, strictly decreasing §7.2 schema (§7.5's own values).
VALID_PERCENTAGES = ["95", "90", "85", "80", "75", "70", "65", "60", "55", "50"]
VALID_SCHEMA = [
    {"grade": grade, "percentage": percentage}
    for grade, percentage in zip(GRADES, VALID_PERCENTAGES, strict=True)
]
DEFAULT_COURSE = "B.Sc. WiIng ET/IT"
DEFAULT_TITLE = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"


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


def create_lecture(client: TestClient, name: str = "Grundlagen der Informationstechnik") -> int:
    response = client.post("/api/lectures", json={"name": name})
    assert response.status_code == 201, response.text
    lecture_id: int = response.json()["id"]
    return lecture_id


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
    response = client.put(f"/api/registrations/{registration_id}/points", json=body)
    assert response.status_code == 200, response.text
    return response


def export_exam(client: TestClient, exam_id: int) -> Any:
    response = client.get(f"/api/exams/{exam_id}/export")
    assert response.status_code == 200, response.text
    return response


def import_bytes(client: TestClient, content: bytes) -> Any:
    return client.post(
        "/api/exams/import", files={"file": ("export.json", content, "application/json")}
    )


def import_payload(client: TestClient, payload: dict[str, Any]) -> Any:
    return import_bytes(client, json.dumps(payload).encode("utf-8"))


@pytest.fixture
def built_exam(instructor_client: TestClient) -> dict[str, Any]:
    """A fully populated exam: two exercises, a full schema, bonus, and four registrations
    covering every attendance/points edge case the round trip must preserve.

    * ``vollstaendig``: attended, both exercises entered.
    * ``nicht-erschienen``: ``attended=false`` **with points still stored** — §7.4/§8.1's
      "flipping attendance never discards already-entered points" case.
    * ``nicht-erfasst``: ``attended=null`` (not yet recorded), one exercise entered.
    * ``ausgeschlossen``: excluded, no points at all.
    """
    lecture_id = create_lecture(instructor_client)
    exam = post_exam(
        instructor_client,
        lecture_id,
        exam_date="2026-07-15",
        bonus_mode="ONLY_IF_PASSING_WITHOUT_BONUS",
        bonus_points="2.5",
        exercises=[
            {"name": "Aufgabe 1", "max_points": "10"},
            {"name": "Aufgabe 2", "max_points": "20"},
        ],
        grading_schema=VALID_SCHEMA,
    )
    exam_id = exam["id"]
    ex1, ex2 = exam["exercises"][0]["id"], exam["exercises"][1]["id"]

    complete = create_registration(instructor_client, exam_id, "1000001")
    save_points(
        instructor_client,
        complete["id"],
        attended=True,
        points={str(ex1): "9.5", str(ex2): "18"},
    )

    absent = create_registration(instructor_client, exam_id, "1000002")
    save_points(
        instructor_client,
        absent["id"],
        attended=False,
        points={str(ex1): "7", str(ex2): "10"},
    )

    unrecorded = create_registration(instructor_client, exam_id, "1000003")
    save_points(instructor_client, unrecorded["id"], attended=None, points={str(ex1): "3"})

    excluded = create_registration(
        instructor_client, exam_id, "1000004", excluded=True, kommentar="storniert"
    )

    return {
        "lecture_id": lecture_id,
        "exam_id": exam_id,
        "matrikelnummern": {
            "complete": complete["matrikelnummer"],
            "absent": absent["matrikelnummer"],
            "unrecorded": unrecorded["matrikelnummer"],
            "excluded": excluded["matrikelnummer"],
        },
    }


# --------------------------------------------------------------------------------------------
# Authentication / authorization
# --------------------------------------------------------------------------------------------


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/api/exams/1/export").status_code == 401


def test_import_requires_auth(client: TestClient) -> None:
    assert import_bytes(client, b"{}").status_code == 401


def test_export_of_unknown_exam_is_404(instructor_client: TestClient) -> None:
    assert instructor_client.get("/api/exams/999999/export").status_code == 404


def test_export_of_another_instructors_exam_is_404_not_403(
    other_client: TestClient, built_exam: dict[str, Any]
) -> None:
    response = other_client.get(f"/api/exams/{built_exam['exam_id']}/export")
    assert response.status_code == 404


# --------------------------------------------------------------------------------------------
# Export shape
# --------------------------------------------------------------------------------------------


def test_export_headers(instructor_client: TestClient, built_exam: dict[str, Any]) -> None:
    response = export_exam(instructor_client, built_exam["exam_id"])
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"


def test_export_never_carries_owner_id(
    instructor_client: TestClient, built_exam: dict[str, Any]
) -> None:
    body = export_exam(instructor_client, built_exam["exam_id"]).json()
    assert "owner_id" not in body
    assert all("owner_id" not in registration for registration in body["registrations"])


def test_export_includes_excluded_registrations(
    instructor_client: TestClient, built_exam: dict[str, Any]
) -> None:
    body = export_exam(instructor_client, built_exam["exam_id"]).json()
    matrikelnummern = {row["matrikelnummer"] for row in body["registrations"]}
    assert built_exam["matrikelnummern"]["excluded"] in matrikelnummern


# --------------------------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------------------------


def test_round_trip_preserves_everything(
    instructor_client: TestClient, built_exam: dict[str, Any], session: Session
) -> None:
    """Re-imports into the **same** lecture (the export's ``lecture_name`` matches the exam's own
    lecture, which the instructor already has) — ``test_reimporting_into_the_same_lecture_name_
    reuses_it`` covers that reuse specifically; this test is about the round-tripped content."""
    exported = export_exam(instructor_client, built_exam["exam_id"]).json()

    result = import_payload(instructor_client, exported).json()
    assert result["lecture_created"] is False
    assert result["registrations_imported"] == 4

    imported_exam = result["exam"]
    new_exam_id = imported_exam["id"]
    assert new_exam_id != built_exam["exam_id"]
    assert imported_exam["lecture_id"] == built_exam["lecture_id"]
    assert imported_exam["semester"] == "WiSe 23/24"
    assert imported_exam["termin"] == "1. Termin"
    assert imported_exam["exam_date"] == "2026-07-15"
    assert imported_exam["bonus_mode"] == "ONLY_IF_PASSING_WITHOUT_BONUS"
    assert imported_exam["bonus_points"] == "2.5"
    assert [ex["max_points"] for ex in imported_exam["exercises"]] == ["10", "20"]
    assert len(imported_exam["grading_schema"]) == 10

    # The new exam's owner is the importer, not anything read from the file (there is nothing to
    # read — the export never carries owner_id at all, see test_export_never_carries_owner_id).
    instructor = session.execute(select(User).where(User.username == "dozentin")).scalar_one()
    stored_exam = session.get(Exam, new_exam_id)
    assert stored_exam is not None
    assert stored_exam.owner_id == instructor.id

    grid = instructor_client.get(f"/api/exams/{new_exam_id}/points").json()
    by_matrikelnummer = {row["matrikelnummer"]: row for row in grid["entries"]}
    names = built_exam["matrikelnummern"]

    new_ex1, new_ex2 = (str(ex["id"]) for ex in imported_exam["exercises"])

    complete = by_matrikelnummer[names["complete"]]
    assert complete["attended"] is True
    assert complete["points"] == {new_ex1: "9.5", new_ex2: "18"}

    # §7.4/§8.1: attended=false keeps its already-entered points in the database.
    absent = by_matrikelnummer[names["absent"]]
    assert absent["attended"] is False
    assert absent["points"] == {new_ex1: "7", new_ex2: "10"}
    assert absent["grade"] == "n.e."

    # attended=null ("not yet recorded") must not become false, and the un-entered exercise must
    # stay absent from `points`, never a stored zero.
    unrecorded = by_matrikelnummer[names["unrecorded"]]
    assert unrecorded["attended"] is None
    assert unrecorded["points"] == {new_ex1: "3"}
    assert new_ex2 not in unrecorded["points"]

    # Excluded registrations don't appear in the points grid (§5.3) but must still exist in the
    # database, exactly like on the original exam.
    assert names["excluded"] not in by_matrikelnummer
    excluded_row = session.execute(
        select(func.count())
        .select_from(StudentRegistration)
        .where(
            StudentRegistration.exam_id == new_exam_id,
            StudentRegistration.matrikelnummer == names["excluded"],
            StudentRegistration.excluded.is_(True),
        )
    ).scalar_one()
    assert excluded_row == 1


def test_reimporting_into_the_same_lecture_name_reuses_it(
    instructor_client: TestClient, built_exam: dict[str, Any], session: Session
) -> None:
    exported = export_exam(instructor_client, built_exam["exam_id"]).json()

    result = import_payload(instructor_client, exported).json()
    assert result["lecture_created"] is False
    assert result["exam"]["lecture_id"] == built_exam["lecture_id"]

    owner_id = result["exam"]["owner_id"]
    lecture_count = session.execute(
        select(func.count()).select_from(Lecture).where(Lecture.owner_id == owner_id)
    ).scalar_one()
    assert lecture_count == 1


def test_import_never_reuses_another_instructors_lecture(
    instructor_client: TestClient, other_client: TestClient, built_exam: dict[str, Any]
) -> None:
    """§4-style scoping: a same-named lecture belonging to someone else must be invisible, not
    merely unwritable — reusing it would attach this import to another instructor's data."""
    exported = export_exam(instructor_client, built_exam["exam_id"]).json()

    result = import_payload(other_client, exported).json()
    assert result["lecture_created"] is True
    assert result["exam"]["lecture_id"] != built_exam["lecture_id"]


# --------------------------------------------------------------------------------------------
# Validation — rejected imports leave the database untouched
# --------------------------------------------------------------------------------------------


def _counts(session: Session) -> tuple[int, int]:
    lectures = session.execute(select(func.count()).select_from(Lecture)).scalar_one()
    exams = session.execute(select(func.count()).select_from(Exam)).scalar_one()
    return int(lectures), int(exams)


def test_invalid_json_is_rejected(instructor_client: TestClient, session: Session) -> None:
    before = _counts(session)
    response = import_bytes(instructor_client, b"not json at all {")
    assert response.status_code == 422
    assert _counts(session) == before


def test_structurally_invalid_payload_is_rejected(
    instructor_client: TestClient, session: Session
) -> None:
    before = _counts(session)
    response = import_payload(instructor_client, {"lecture_name": "X"})  # missing semester/termin
    assert response.status_code == 422
    assert _counts(session) == before


def _minimal_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format_version": 1,
        "lecture_name": "Testvorlesung",
        "semester": "WiSe 23/24",
        "termin": "1. Termin",
        "exercises": [{"name": "Aufgabe 1", "max_points": "10", "position": 1}],
        "grading_schema": [],
        "registrations": [],
    }
    payload.update(overrides)
    return payload


def test_unsupported_format_version_is_rejected(
    instructor_client: TestClient, session: Session
) -> None:
    before = _counts(session)
    response = import_payload(instructor_client, _minimal_payload(format_version=2))
    assert response.status_code == 422
    assert _counts(session) == before


def test_negative_bonus_points_is_rejected(instructor_client: TestClient) -> None:
    response = import_payload(instructor_client, _minimal_payload(bonus_points="-1"))
    assert response.status_code == 422


def test_incomplete_grading_schema_is_rejected(instructor_client: TestClient) -> None:
    response = import_payload(
        instructor_client, _minimal_payload(grading_schema=VALID_SCHEMA[:5])
    )
    assert response.status_code == 422


def test_duplicate_matrikelnummer_within_file_is_rejected(
    instructor_client: TestClient, session: Session
) -> None:
    before = _counts(session)
    registration = {
        "matrikelnummer": "1234567",
        "nachname": "A",
        "vorname": "B",
        "course_code": DEFAULT_COURSE,
        "module_title": DEFAULT_TITLE,
        "versuch": 1,
        "points": {},
    }
    response = import_payload(
        instructor_client,
        _minimal_payload(registrations=[registration, dict(registration)]),
    )
    assert response.status_code == 422
    assert "1234567" in response.text
    assert _counts(session) == before


def test_points_referencing_an_unknown_exercise_index_is_rejected(
    instructor_client: TestClient,
) -> None:
    registration = {
        "matrikelnummer": "1234567",
        "nachname": "A",
        "vorname": "B",
        "course_code": DEFAULT_COURSE,
        "module_title": DEFAULT_TITLE,
        "versuch": 1,
        "points": {"2": "5"},  # only one exercise (index 1) exists in this file
    }
    response = import_payload(instructor_client, _minimal_payload(registrations=[registration]))
    assert response.status_code == 422


def test_negative_points_are_rejected(instructor_client: TestClient) -> None:
    registration = {
        "matrikelnummer": "1234567",
        "nachname": "A",
        "vorname": "B",
        "course_code": DEFAULT_COURSE,
        "module_title": DEFAULT_TITLE,
        "versuch": 1,
        "points": {"1": "-1"},
    }
    response = import_payload(instructor_client, _minimal_payload(registrations=[registration]))
    assert response.status_code == 422


def test_a_json_number_for_a_decimal_field_is_rejected(instructor_client: TestClient) -> None:
    """§7.0 end-to-end: even inside an uploaded file, a decimal must be a JSON string."""
    payload = _minimal_payload()
    payload["exercises"][0]["max_points"] = 10  # JSON number, not "10"
    response = import_payload(instructor_client, payload)
    assert response.status_code == 422
