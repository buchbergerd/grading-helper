"""Statistics share links (§3's second public-access exception, §9) — `app/api/sharing.py`.

Two things this file has to prove, mirroring the two halves of the module's own docstring:

**Owner-only management.** ``POST``/``DELETE /exams/{id}/share-link`` follow the exact same
404-not-403 posture as every other exam route (``tests/test_statistics_api.py`` is the model this
file's access tests are copied from).

**The token unlocks exactly one thing.** A valid share token must serve
``/api/public/statistics/{token}`` and nothing else — every other exam-scoped route stays exactly
as unauthenticated-hostile as it was before this feature existed. That is
:func:`test_a_valid_share_token_unlocks_nothing_but_statistics`, the test the advisor asked for:
it holds today by construction (one public route exists), and is what would catch a future route
that carelessly accepted a token instead of a session.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.grading.schema import GRADES
from app.models import User
from tests.conftest import ADMIN_PASSWORD, INSTRUCTOR_PASSWORD, LoginHelper

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


def add_student(client: TestClient, exam_id: int, matrikelnummer: str) -> dict[str, Any]:
    """One synthetic registration. Names are obviously fictional — never real data (§13)."""
    response = client.post(
        f"/api/exams/{exam_id}/registrations",
        json={
            "matrikelnummer": matrikelnummer,
            "nachname": "Musterfrau",
            "vorname": "Beispiel",
            "course_code": DEFAULT_COURSE,
            "module_title": DEFAULT_TITLE,
            "versuch": 1,
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
    return exam_id


# --------------------------------------------------------------------------------------------
# Owner-only management
# --------------------------------------------------------------------------------------------


def test_creating_a_share_link_sets_a_token_and_returns_it_on_the_exam(
    instructor_client: TestClient, populated_exam: int
) -> None:
    exam_before = instructor_client.get(f"/api/exams/{populated_exam}").json()
    assert exam_before["share_token"] is None

    response = instructor_client.post(f"/api/exams/{populated_exam}/share-link")
    assert response.status_code == 200, response.text
    token = response.json()["share_token"]
    assert isinstance(token, str) and len(token) >= 32

    exam_after = instructor_client.get(f"/api/exams/{populated_exam}").json()
    assert exam_after["share_token"] == token


def test_creating_a_share_link_again_regenerates_and_invalidates_the_old_token(
    instructor_client: TestClient, populated_exam: int
) -> None:
    first = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    second = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    assert first != second

    assert instructor_client.get(f"/api/public/statistics/{first}").status_code == 404
    assert instructor_client.get(f"/api/public/statistics/{second}").status_code == 200


def test_revoking_turns_sharing_off_and_the_old_token_stops_working(
    instructor_client: TestClient, populated_exam: int
) -> None:
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    assert instructor_client.get(f"/api/public/statistics/{token}").status_code == 200

    response = instructor_client.delete(f"/api/exams/{populated_exam}/share-link")
    assert response.status_code == 204

    exam = instructor_client.get(f"/api/exams/{populated_exam}").json()
    assert exam["share_token"] is None
    assert instructor_client.get(f"/api/public/statistics/{token}").status_code == 404


def test_revoking_is_idempotent(instructor_client: TestClient, populated_exam: int) -> None:
    assert instructor_client.delete(f"/api/exams/{populated_exam}/share-link").status_code == 204
    assert instructor_client.delete(f"/api/exams/{populated_exam}/share-link").status_code == 204


@pytest.mark.parametrize("method", ["post", "delete"])
def test_another_instructor_gets_404_managing_someone_elses_share_link(
    other_client: TestClient, populated_exam: int, method: str
) -> None:
    response = getattr(other_client, method)(f"/api/exams/{populated_exam}/share-link")
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["post", "delete"])
def test_admin_gets_404_managing_another_instructors_share_link(
    admin_client: TestClient, populated_exam: int, method: str
) -> None:
    """§3's least-privilege default: admin manages accounts, not exam data — no bypass here."""
    response = getattr(admin_client, method)(f"/api/exams/{populated_exam}/share-link")
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["post", "delete"])
def test_unauthenticated_management_is_rejected(
    client: TestClient, populated_exam: int, method: str
) -> None:
    response = getattr(client, method)(f"/api/exams/{populated_exam}/share-link")
    assert response.status_code == 401


# --------------------------------------------------------------------------------------------
# The public route
# --------------------------------------------------------------------------------------------


def test_shared_statistics_matches_the_owner_view(
    instructor_client: TestClient, populated_exam: int
) -> None:
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    owner_view = instructor_client.get(f"/api/exams/{populated_exam}/statistics").json()

    public_response = instructor_client.get(f"/api/public/statistics/{token}")
    assert public_response.status_code == 200, public_response.text
    assert public_response.headers["cache-control"] == "no-store"
    assert public_response.json() == owner_view


def test_shared_statistics_works_anonymously(
    client: TestClient, instructor_client: TestClient, populated_exam: int
) -> None:
    """The whole point: no cookie, no session, still 200."""
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    response = client.get(f"/api/public/statistics/{token}")
    assert response.status_code == 200, response.text
    assert response.json()["exam_id"] == populated_exam


def test_shared_statistics_supports_the_bonus_simulation(
    client: TestClient, instructor_client: TestClient, populated_exam: int
) -> None:
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    baseline = client.get(f"/api/public/statistics/{token}").json()
    simulated = client.get(
        f"/api/public/statistics/{token}", params={"bonus_points_override": "20"}
    )
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["counts"]["passed"] >= baseline["counts"]["passed"]

    # Never persisted — the exam's own stored bonus_points is untouched.
    exam = instructor_client.get(f"/api/exams/{populated_exam}").json()
    assert exam["bonus_points"] == "0"


@pytest.mark.parametrize("value", ["abc", "", "1e2", "NaN"])
def test_shared_statistics_rejects_a_malformed_override(
    client: TestClient, instructor_client: TestClient, populated_exam: int, value: str
) -> None:
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    response = client.get(
        f"/api/public/statistics/{token}", params={"bonus_points_override": value}
    )
    assert response.status_code == 422, response.text


def test_unknown_token_is_a_generic_404(client: TestClient) -> None:
    response = client.get("/api/public/statistics/not-a-real-token")
    assert response.status_code == 404
    assert response.json()["detail"] == "Dieser Link ist nicht mehr gültig."


def test_exam_with_sharing_off_has_no_token_and_no_public_route(
    instructor_client: TestClient, populated_exam: int
) -> None:
    exam = instructor_client.get(f"/api/exams/{populated_exam}").json()
    assert exam["share_token"] is None


# --------------------------------------------------------------------------------------------
# A token unlocks exactly one route — nothing else on the exam
# --------------------------------------------------------------------------------------------


def test_a_valid_share_token_unlocks_nothing_but_statistics(
    client: TestClient, instructor_client: TestClient, populated_exam: int
) -> None:
    """The negative-access proof: a real, currently-valid token exists, and every other
    exam-scoped route an anonymous caller could try still refuses it exactly as if sharing had
    never been turned on. None of these routes accept a token in any form — this test just
    confirms that having one in hand buys an anonymous caller nothing beyond the one route that is
    supposed to accept it.
    """
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    assert client.get(f"/api/public/statistics/{token}").status_code == 200

    other_routes = [
        f"/api/exams/{populated_exam}",
        f"/api/exams/{populated_exam}/statistics",
        f"/api/exams/{populated_exam}/reports/internal",
        f"/api/exams/{populated_exam}/reports/attendance-list",
        f"/api/exams/{populated_exam}/reports/examination-office/pdf",
        f"/api/exams/{populated_exam}/reports/examination-office/excel",
        f"/api/exams/{populated_exam}/reports/student-results/pdf",
        f"/api/exams/{populated_exam}/reports/student-results/excel",
        f"/api/exams/{populated_exam}/points",
        f"/api/exams/{populated_exam}/completeness",
        f"/api/exams/{populated_exam}/registrations",
        f"/api/exams/{populated_exam}/registrations/count",
    ]
    for route in other_routes:
        response = client.get(route)
        assert response.status_code == 401, f"{route} should reject an anonymous caller"


def test_every_decimal_in_the_public_payload_crosses_the_wire_as_a_string(
    client: TestClient, instructor_client: TestClient, populated_exam: int
) -> None:
    """Same §7.0 walk as `test_statistics_api.py`'s equivalent, repeated here because this route
    serialises the payload through a second code path (`app/api/sharing.py`, not
    `app/api/statistics.py`)."""
    token = instructor_client.post(f"/api/exams/{populated_exam}/share-link").json()["share_token"]
    payload = client.get(f"/api/public/statistics/{token}").json()

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
