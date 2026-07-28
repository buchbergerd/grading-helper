"""Lecture routes (``/api/lectures*``, §4/§13 and ``docs/api-contract.md``).

The properties under the most scrutiny here are the ones that fail silently: another
instructor's lecture answering ``404`` rather than ``403`` (a ``403`` confirms the row exists),
and the delete confirmation gate, behind which sits a cascade that destroys grades.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.models import Exam, Exercise, Lecture, User
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

#: Every lecture route, as (method, path) — a newly added route cannot quietly skip the 401 test.
LECTURE_ROUTES = [
    ("GET", "/api/lectures"),
    ("POST", "/api/lectures"),
    ("GET", "/api/lectures/1"),
    ("PATCH", "/api/lectures/1"),
    ("DELETE", "/api/lectures/1"),
    ("POST", "/api/lectures/1/exams"),
]


@pytest.fixture
def instructor_client(login: LoginHelper, instructor_user: User) -> TestClient:
    client, _token = login("dozentin", INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def other_instructor(session: Session) -> User:
    """A second instructor account — the "someone else" of every cross-owner test."""
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


# --------------------------------------------------------------------------------------------
# Authentication / authorization
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), LECTURE_ROUTES)
def test_unauthenticated_gets_401_on_every_lecture_route(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, json={"name": "x", "semester": "s", "termin": "t"})

    assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


def test_another_instructors_lecture_is_404_not_403(
    instructor_client: TestClient, other_client: TestClient
) -> None:
    """A ``403`` would confirm the lecture exists — an existence leak over foreign exam data."""
    lecture_id = create_lecture(instructor_client)

    for method, body in (
        ("GET", None),
        ("PATCH", {"name": "gekapert"}),
        ("DELETE", None),
    ):
        response = other_client.request(
            method, f"/api/lectures/{lecture_id}?confirm=true", json=body
        )
        assert response.status_code == 404, f"{method} returned {response.status_code}"
        assert response.json()["detail"] == "Vorlesung nicht gefunden."


def test_unknown_lecture_is_404(instructor_client: TestClient) -> None:
    assert instructor_client.get("/api/lectures/9999").status_code == 404


def test_list_returns_only_the_callers_lectures(
    instructor_client: TestClient, other_client: TestClient
) -> None:
    create_lecture(instructor_client, "Meine Vorlesung")
    create_lecture(other_client, "Fremde Vorlesung")

    body = instructor_client.get("/api/lectures").json()

    assert [entry["name"] for entry in body] == ["Meine Vorlesung"]


# --------------------------------------------------------------------------------------------
# CRUD happy paths
# --------------------------------------------------------------------------------------------


def test_create_lecture_returns_the_contract_shape(
    instructor_client: TestClient, instructor_user: User, session: Session
) -> None:
    response = instructor_client.post("/api/lectures", json={"name": "  Digitaltechnik  "})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "created_at", "exam_count"}
    assert body["name"] == "Digitaltechnik"  # whitespace-trimmed
    assert body["exam_count"] == 0

    stored = session.get(Lecture, body["id"])
    assert stored is not None
    assert stored.owner_id == instructor_user.id


def test_create_lecture_rejects_an_empty_name(instructor_client: TestClient) -> None:
    assert instructor_client.post("/api/lectures", json={"name": ""}).status_code == 422


def test_lectures_are_listed_by_name(instructor_client: TestClient) -> None:
    create_lecture(instructor_client, "Zeitreihenanalyse")
    create_lecture(instructor_client, "Algorithmen")

    body = instructor_client.get("/api/lectures").json()

    assert [entry["name"] for entry in body] == ["Algorithmen", "Zeitreihenanalyse"]


def test_lecture_detail_lists_its_exams_newest_first(instructor_client: TestClient) -> None:
    lecture_id = create_lecture(instructor_client)
    for semester, exam_date in (
        ("WiSe 22/23", "2023-02-14"),
        ("WiSe 23/24", "2024-02-13"),
        ("ohne Datum", None),
    ):
        response = instructor_client.post(
            f"/api/lectures/{lecture_id}/exams",
            json={"semester": semester, "termin": "1. Termin", "exam_date": exam_date},
        )
        assert response.status_code == 201, response.text

    body = instructor_client.get(f"/api/lectures/{lecture_id}").json()

    assert body["exam_count"] == 3
    # exam_date descending, undated last (see exams.most_recent_prior_exam).
    assert [exam["semester"] for exam in body["exams"]] == [
        "WiSe 23/24",
        "WiSe 22/23",
        "ohne Datum",
    ]
    assert set(body["exams"][0]) == {
        "id",
        "lecture_id",
        "lecture_name",
        "semester",
        "termin",
        "exam_date",
        "bonus_mode",
        "owner_id",
    }
    assert body["exams"][0]["lecture_name"] == "Grundlagen der Informationstechnik"


def test_patch_renames_a_lecture(instructor_client: TestClient) -> None:
    lecture_id = create_lecture(instructor_client, "Alter Name")

    response = instructor_client.patch(f"/api/lectures/{lecture_id}", json={"name": "Neuer Name"})

    assert response.status_code == 200
    assert response.json()["name"] == "Neuer Name"
    assert instructor_client.get(f"/api/lectures/{lecture_id}").json()["name"] == "Neuer Name"


def test_exam_count_reflects_created_exams(instructor_client: TestClient) -> None:
    lecture_id = create_lecture(instructor_client)
    instructor_client.post(
        f"/api/lectures/{lecture_id}/exams", json={"semester": "WiSe 23/24", "termin": "1. Termin"}
    )

    listed = instructor_client.get("/api/lectures").json()

    assert listed[0]["exam_count"] == 1


# --------------------------------------------------------------------------------------------
# Deletion (§13)
# --------------------------------------------------------------------------------------------


def test_delete_without_confirm_is_409_with_a_german_message(
    instructor_client: TestClient, session: Session
) -> None:
    lecture_id = create_lecture(instructor_client)

    response = instructor_client.delete(f"/api/lectures/{lecture_id}")

    assert response.status_code == 409
    assert "confirm=true" in response.json()["detail"]
    assert "unwiderruflich" in response.json()["detail"]
    assert session.get(Lecture, lecture_id) is not None


def test_delete_with_confirm_cascades_to_exams_and_their_exercises(
    instructor_client: TestClient, session: Session
) -> None:
    lecture_id = create_lecture(instructor_client)
    exam_id = instructor_client.post(
        f"/api/lectures/{lecture_id}/exams",
        json={
            "semester": "WiSe 23/24",
            "termin": "1. Termin",
            "exercises": [{"name": "Aufgabe 1", "max_points": "60"}],
        },
    ).json()["id"]

    response = instructor_client.delete(f"/api/lectures/{lecture_id}?confirm=true")

    assert response.status_code == 204
    session.expire_all()
    assert session.get(Lecture, lecture_id) is None
    assert session.get(Exam, exam_id) is None
    remaining = session.execute(
        select(func.count()).select_from(Exercise).where(Exercise.exam_id == exam_id)
    ).scalar_one()
    assert remaining == 0
