"""Account management routes (``/api/admin/*``, §3 and ``docs/api-contract.md``).

Two properties get the most attention here because they are the ones that fail silently:
revocation actually happening on deactivation/reset, and an admin being unable to lock the
department out of its own account management.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.passwords import MIN_PASSWORD_LENGTH
from app.models import User, UserSession
from tests.conftest import ADMIN_PASSWORD, INSTRUCTOR_PASSWORD, ClientFactory, LoginHelper

#: Every admin route, as (method, path) — used by the authorization tests so a newly added route
#: cannot quietly skip the 401/403 checks.
ADMIN_ROUTES = [
    ("GET", "/api/admin/users"),
    ("POST", "/api/admin/users"),
    ("PATCH", "/api/admin/users/1"),
    ("POST", "/api/admin/users/1/password"),
]

NEW_USER_BODY = {"username": "neue-dozentin", "password": "ein-gutes-passwort-1"}


@pytest.fixture
def admin_client(login: LoginHelper, admin_user: User) -> TestClient:
    client, _token = login("admin", ADMIN_PASSWORD)
    return client


# --------------------------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_unauthenticated_gets_401_on_every_admin_route(
    client: TestClient, method: str, path: str
) -> None:
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_non_admin_gets_403_on_every_admin_route(
    login: LoginHelper, instructor_user: User, method: str, path: str
) -> None:
    client, _ = login("dozentin", INSTRUCTOR_PASSWORD)

    response = client.request(method, path, json=NEW_USER_BODY | {"new_password": "x" * 14})

    assert response.status_code == 403, f"{method} {path} returned {response.status_code}"


def test_admin_role_does_not_leak_password_hashes(
    admin_client: TestClient, instructor_user: User
) -> None:
    response = admin_client.get("/api/admin/users")

    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert INSTRUCTOR_PASSWORD not in response.text


# --------------------------------------------------------------------------------------------
# Listing and creation
# --------------------------------------------------------------------------------------------


def test_list_users_returns_the_contract_shape(
    admin_client: TestClient, admin_user: User, instructor_user: User
) -> None:
    body = admin_client.get("/api/admin/users").json()

    assert [entry["username"] for entry in body] == ["admin", "dozentin"]
    assert set(body[0]) == {"id", "username", "is_admin", "is_active", "created_at"}
    assert body[0]["is_admin"] is True
    assert body[1]["is_admin"] is False
    assert body[1]["is_active"] is True


def test_create_user(admin_client: TestClient, session: Session) -> None:
    response = admin_client.post("/api/admin/users", json=NEW_USER_BODY)

    assert response.status_code == 201
    created = response.json()
    assert created["username"] == "neue-dozentin"
    assert created["is_admin"] is False
    assert created["is_active"] is True
    assert NEW_USER_BODY["password"] not in response.text

    stored = session.get(User, created["id"])
    assert stored is not None
    assert stored.password_hash != NEW_USER_BODY["password"]


def test_create_admin_user(admin_client: TestClient) -> None:
    response = admin_client.post("/api/admin/users", json=NEW_USER_BODY | {"is_admin": True})

    assert response.status_code == 201
    assert response.json()["is_admin"] is True


def test_created_user_can_log_in(admin_client: TestClient, login: LoginHelper) -> None:
    admin_client.post("/api/admin/users", json=NEW_USER_BODY)

    client, _ = login("neue-dozentin", NEW_USER_BODY["password"])
    assert client.get("/api/auth/me").json()["username"] == "neue-dozentin"


def test_duplicate_username_returns_409(admin_client: TestClient, instructor_user: User) -> None:
    response = admin_client.post(
        "/api/admin/users", json={"username": "dozentin", "password": "ein-gutes-passwort-1"}
    )

    assert response.status_code == 409
    assert "vergeben" in response.json()["detail"]


def test_create_rejects_a_too_short_password(admin_client: TestClient, session: Session) -> None:
    response = admin_client.post(
        "/api/admin/users", json={"username": "zu-schwach", "password": "kurz"}
    )

    assert response.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in response.json()["detail"]["errors"][0]

    usernames = [entry["username"] for entry in admin_client.get("/api/admin/users").json()]
    assert "zu-schwach" not in usernames, "the account must not have been created"


# --------------------------------------------------------------------------------------------
# Update (activation / role)
# --------------------------------------------------------------------------------------------


def test_deactivation_revokes_the_users_sessions_immediately(
    admin_client: TestClient,
    login: LoginHelper,
    instructor_user: User,
    session: Session,
) -> None:
    victim, token = login("dozentin", INSTRUCTOR_PASSWORD)
    assert victim.get("/api/auth/me").status_code == 200

    response = admin_client.patch(
        f"/api/admin/users/{instructor_user.id}", json={"is_active": False}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert victim.get("/api/auth/me").status_code == 401
    session.expire_all()
    assert session.get(UserSession, token) is None


def test_reactivation_does_not_touch_sessions(
    admin_client: TestClient, instructor_user: User, login: LoginHelper
) -> None:
    admin_client.patch(f"/api/admin/users/{instructor_user.id}", json={"is_active": False})
    response = admin_client.patch(
        f"/api/admin/users/{instructor_user.id}", json={"is_active": True}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is True
    client, _ = login("dozentin", INSTRUCTOR_PASSWORD)
    assert client.get("/api/auth/me").status_code == 200


def test_promoting_a_user_to_admin(admin_client: TestClient, instructor_user: User) -> None:
    response = admin_client.patch(f"/api/admin/users/{instructor_user.id}", json={"is_admin": True})

    assert response.status_code == 200
    assert response.json()["is_admin"] is True


def test_admin_cannot_deactivate_themselves(admin_client: TestClient, admin_user: User) -> None:
    response = admin_client.patch(f"/api/admin/users/{admin_user.id}", json={"is_active": False})

    assert response.status_code == 400
    assert "eigene" in response.json()["detail"]
    assert admin_client.get("/api/auth/me").status_code == 200


def test_admin_cannot_demote_themselves(admin_client: TestClient, admin_user: User) -> None:
    response = admin_client.patch(f"/api/admin/users/{admin_user.id}", json={"is_admin": False})

    assert response.status_code == 400
    assert admin_client.get("/api/admin/users").status_code == 200, "still an admin"


def test_patch_unknown_user_returns_404(admin_client: TestClient) -> None:
    assert admin_client.patch("/api/admin/users/9999", json={"is_active": False}).status_code == 404


# --------------------------------------------------------------------------------------------
# Admin password reset
# --------------------------------------------------------------------------------------------


def test_password_reset_revokes_sessions_and_sets_the_new_password(
    admin_client: TestClient,
    login: LoginHelper,
    instructor_user: User,
    session: Session,
    client_factory: ClientFactory,
) -> None:
    victim, token = login("dozentin", INSTRUCTOR_PASSWORD)
    assert victim.get("/api/auth/me").status_code == 200

    response = admin_client.post(
        f"/api/admin/users/{instructor_user.id}/password",
        json={"new_password": "zuruecksetzen-2026"},
    )

    assert response.status_code == 204
    assert victim.get("/api/auth/me").status_code == 401
    session.expire_all()
    assert session.get(UserSession, token) is None

    fresh = client_factory()
    assert (
        fresh.post(
            "/api/auth/login",
            json={"username": "dozentin", "password": INSTRUCTOR_PASSWORD},
        ).status_code
        == 401
    )
    assert (
        fresh.post(
            "/api/auth/login",
            json={"username": "dozentin", "password": "zuruecksetzen-2026"},
        ).status_code
        == 200
    )


def test_password_reset_rejects_a_too_short_password(
    admin_client: TestClient, login: LoginHelper, instructor_user: User
) -> None:
    victim, _ = login("dozentin", INSTRUCTOR_PASSWORD)

    response = admin_client.post(
        f"/api/admin/users/{instructor_user.id}/password", json={"new_password": "kurz"}
    )

    assert response.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in response.json()["detail"]["errors"][0]
    assert victim.get("/api/auth/me").status_code == 200, "a rejected reset must change nothing"


def test_password_reset_for_unknown_user_returns_404(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/admin/users/9999/password", json={"new_password": "zuruecksetzen-2026"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------------------------
# §14 #5 — the admin API must not reach exam data
# --------------------------------------------------------------------------------------------


def test_admin_router_exposes_only_user_routes() -> None:
    """Guard for the least-privilege boundary: admins manage accounts, not exam data (§14 #5)."""
    from app.api import admin as admin_module

    paths = {route.path for route in admin_module.router.routes}  # type: ignore[attr-defined]

    assert paths == {"/admin/users", "/admin/users/{user_id}", "/admin/users/{user_id}/password"}
