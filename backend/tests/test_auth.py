"""Auth: password hashing, session lifecycle, and the ``/api/auth/*`` routes (§3).

The tests run against a real file-backed SQLite database (see ``conftest.py``) — several of the
properties here (naive datetime round-trip, immediate revocation) only exist once a value has
actually made the trip through the storage layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.auth.sessions import (
    create_session,
    delete_all_sessions_for_user,
    delete_session,
    get_valid_session,
    purge_expired_sessions,
)
from app.models import User, UserSession
from app.models.common import utcnow
from tests.conftest import ADMIN_PASSWORD, INSTRUCTOR_PASSWORD, ClientFactory, LoginHelper

# --------------------------------------------------------------------------------------------
# Password hashing (app/auth/passwords.py)
# --------------------------------------------------------------------------------------------


def test_hash_is_salted_and_verifies() -> None:
    first = hash_password("ein-gutes-passwort")
    second = hash_password("ein-gutes-passwort")

    assert first != second, "identical passwords must not produce identical hashes (salting)"
    assert "ein-gutes-passwort" not in first
    assert verify_password(first, "ein-gutes-passwort")
    assert verify_password(second, "ein-gutes-passwort")


def test_verify_returns_false_instead_of_raising() -> None:
    stored = hash_password("ein-gutes-passwort")

    assert verify_password(stored, "ein-falsches-passwort") is False
    # A stored value that is not a parseable argon2 hash must fail closed, not raise: the
    # existing `exam` fixture writes exactly such a placeholder.
    assert verify_password("not-a-real-hash", "irgendwas") is False


def test_needs_rehash_is_false_for_a_current_hash_and_true_for_garbage() -> None:
    assert needs_rehash(hash_password("ein-gutes-passwort")) is False
    assert needs_rehash("not-a-real-hash") is True


def test_password_policy_rejects_short_passwords_in_german() -> None:
    errors = validate_password_strength("kurz")

    assert errors, "a 4-character password must be rejected"
    assert str(MIN_PASSWORD_LENGTH) in errors[0]
    assert "Passwort" in errors[0], "policy messages are shown verbatim in the German UI"
    assert validate_password_strength("a" * MIN_PASSWORD_LENGTH) == []


def test_password_policy_rejects_whitespace_only() -> None:
    assert validate_password_strength(" " * (MIN_PASSWORD_LENGTH + 2)) != []


# --------------------------------------------------------------------------------------------
# Session store (app/auth/sessions.py)
# --------------------------------------------------------------------------------------------


@pytest.fixture
def stored_user(session: Session) -> User:
    user = User(username="sitzung", password_hash=hash_password("ein-gutes-passwort"))
    session.add(user)
    session.commit()
    return user


def test_create_session_sets_expiry_from_settings(session: Session, stored_user: User) -> None:
    from app.config import get_settings

    issued = create_session(session, stored_user)

    assert len(issued.token) >= 32
    lifetime = timedelta(hours=get_settings().session_lifetime_hours)
    assert abs((issued.expires_at - issued.created_at) - lifetime) < timedelta(seconds=1)


def test_expires_at_survives_the_sqlite_round_trip_in_a_fresh_session(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    """A session created in one connection must still validate when loaded in another.

    This is the regression test for the naive/aware datetime trap. ``DateTime(timezone=True)``
    is written with a tz-aware value but SQLite has no datetime type, so a *freshly loaded* row
    comes back **naive** — and ``naive <= aware`` raises ``TypeError``. Reading back through the
    same Session would not catch it: ``expire_on_commit=False`` means the identity map hands
    back the original aware Python object and the storage layer is never exercised.
    """
    with session_factory() as first:
        user = User(username="roundtrip", password_hash=hash_password("ein-gutes-passwort"))
        first.add(user)
        first.commit()
        token = create_session(first, user).token

    with session_factory() as second:
        # Guard on the test itself: if this ever starts coming back tz-aware, the assertion
        # below stops proving anything and this test must be revisited.
        loaded = second.get(UserSession, token)
        assert loaded is not None
        assert loaded.expires_at.tzinfo is None, (
            "SQLite is expected to return a naive datetime here; if it no longer does, "
            "get_valid_session's normalization is no longer covered by this test"
        )

        assert get_valid_session(second, token) is not None


def test_get_valid_session_rejects_unknown_expired_and_inactive(
    session: Session, stored_user: User
) -> None:
    assert get_valid_session(session, "gibt-es-nicht") is None
    assert get_valid_session(session, "") is None

    expired = UserSession(
        token="abgelaufen",
        user_id=stored_user.id,
        expires_at=utcnow() - timedelta(minutes=1),
    )
    session.add(expired)
    session.commit()
    assert get_valid_session(session, "abgelaufen") is None

    live = create_session(session, stored_user)
    assert get_valid_session(session, live.token) is not None

    stored_user.is_active = False
    session.commit()
    assert get_valid_session(session, live.token) is None


def test_delete_session_and_delete_all_sessions(session: Session, stored_user: User) -> None:
    first = create_session(session, stored_user).token
    second = create_session(session, stored_user).token
    third = create_session(session, stored_user).token

    delete_session(session, first)
    assert get_valid_session(session, first) is None
    delete_session(session, first)  # deleting a gone token is not an error

    assert delete_all_sessions_for_user(session, stored_user.id, except_token=second) == 1
    assert get_valid_session(session, second) is not None
    assert get_valid_session(session, third) is None

    assert delete_all_sessions_for_user(session, stored_user.id) == 1
    assert get_valid_session(session, second) is None


def test_purge_expired_sessions_leaves_live_ones(session: Session, stored_user: User) -> None:
    live = create_session(session, stored_user).token
    session.add(
        UserSession(token="alt", user_id=stored_user.id, expires_at=utcnow() - timedelta(hours=1))
    )
    session.commit()

    assert purge_expired_sessions(session) == 1
    assert session.get(UserSession, "alt") is None
    assert get_valid_session(session, live) is not None
    assert purge_expired_sessions(session) == 0


# --------------------------------------------------------------------------------------------
# /api/auth/* routes
# --------------------------------------------------------------------------------------------


def test_login_sets_httponly_cookie_and_me_works(
    client: TestClient, instructor_user: User, cookie_name: str
) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "dozentin", "password": INSTRUCTOR_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": instructor_user.id,
        "username": "dozentin",
        "is_admin": False,
    }

    set_cookie = response.headers["set-cookie"]
    assert cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.replace("samesite", "SameSite")
    assert "Path=/" in set_cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "dozentin"
    assert "password" not in me.text and "token" not in me.text


def test_login_response_never_contains_the_token(
    client: TestClient, instructor_user: User, cookie_name: str
) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "dozentin", "password": INSTRUCTOR_PASSWORD},
    )
    token = client.cookies[cookie_name]

    assert token not in response.text
    assert INSTRUCTOR_PASSWORD not in response.text


def test_logout_deletes_the_session_row_and_the_old_cookie_stops_working(
    session: Session,
    client_factory: ClientFactory,
    login: LoginHelper,
    instructor_user: User,
    cookie_name: str,
) -> None:
    client, token = login("dozentin", INSTRUCTOR_PASSWORD)

    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert client.cookies.get(cookie_name) in (None, "")

    session.expire_all()
    assert session.get(UserSession, token) is None

    # Replaying the raw token on a fresh jar — the client-side cookie deletion proves nothing
    # about server-side revocation.
    replay = client_factory()
    replay.cookies.set(cookie_name, token)
    assert replay.get("/api/auth/me").status_code == 401


def test_logout_is_idempotent_without_a_valid_session(client: TestClient, cookie_name: str) -> None:
    """Logging out twice, or with no cookie at all, still succeeds and still clears the cookie.

    A 401 here would strand an expired cookie in the browser with no way to clear it.
    """
    assert client.post("/api/auth/logout").status_code == 204

    client.cookies.set(cookie_name, "kein-gueltiges-token")
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    # Assert on the response header rather than the client's jar: a cookie set manually via
    # httpx has no domain, so the server's deletion never matches it jar-side even though the
    # Set-Cookie that a real browser would honour is present.
    set_cookie = response.headers.get("set-cookie", "")
    assert cookie_name in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=Thu, 01 Jan 1970" in set_cookie.lower()


def test_me_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user_give_the_identical_401(
    client: TestClient, instructor_user: User
) -> None:
    wrong = client.post(
        "/api/auth/login", json={"username": "dozentin", "password": "falsches-passwort-x"}
    )
    unknown = client.post(
        "/api/auth/login", json={"username": "gibt-es-nicht", "password": "falsches-passwort-x"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert "dozentin" not in wrong.text, "the response must not echo the submitted username"


def test_deactivated_user_cannot_log_in_with_the_same_message(
    session: Session, client: TestClient, instructor_user: User
) -> None:
    baseline = client.post(
        "/api/auth/login", json={"username": "gibt-es-nicht", "password": "falsches-passwort-x"}
    ).json()["detail"]

    instructor_user.is_active = False
    session.commit()

    response = client.post(
        "/api/auth/login", json={"username": "dozentin", "password": INSTRUCTOR_PASSWORD}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == baseline


def test_deactivation_revokes_an_already_issued_session_immediately(
    session: Session, login: LoginHelper, instructor_user: User
) -> None:
    """The property that justifies a DB-backed token over a JWT (§3, contract "Auth")."""
    client, _token = login("dozentin", INSTRUCTOR_PASSWORD)
    assert client.get("/api/auth/me").status_code == 200

    instructor_user.is_active = False
    session.commit()

    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(
    session: Session,
    client_factory: ClientFactory,
    instructor_user: User,
    cookie_name: str,
) -> None:
    session.add(
        UserSession(
            token="abgelaufenes-token",
            user_id=instructor_user.id,
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    session.commit()

    client = client_factory()
    client.cookies.set(cookie_name, "abgelaufenes-token")
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_cookie_is_rejected(client: TestClient, cookie_name: str) -> None:
    client.cookies.set(cookie_name, "voellig-erfundenes-token")
    assert client.get("/api/auth/me").status_code == 401


# --------------------------------------------------------------------------------------------
# Self-service password change
# --------------------------------------------------------------------------------------------


def test_password_change_keeps_this_session_and_kills_the_others(
    session: Session, login: LoginHelper, instructor_user: User
) -> None:
    active_client, _ = login("dozentin", INSTRUCTOR_PASSWORD)
    other_client, other_token = login("dozentin", INSTRUCTOR_PASSWORD)
    assert other_client.get("/api/auth/me").status_code == 200

    response = active_client.post(
        "/api/auth/password",
        json={"current_password": INSTRUCTOR_PASSWORD, "new_password": "neues-passwort-2026"},
    )
    assert response.status_code == 204

    assert active_client.get("/api/auth/me").status_code == 200, "own session must survive"
    assert other_client.get("/api/auth/me").status_code == 401, "other sessions must be revoked"

    session.expire_all()
    assert session.get(UserSession, other_token) is None

    # The new password is what actually works now.
    fresh, _ = login("dozentin", "neues-passwort-2026")
    assert fresh.get("/api/auth/me").status_code == 200
    assert (
        fresh.post(
            "/api/auth/login",
            json={"username": "dozentin", "password": INSTRUCTOR_PASSWORD},
        ).status_code
        == 401
    )


def test_password_change_with_a_wrong_current_password_is_rejected(
    login: LoginHelper, instructor_user: User
) -> None:
    client, _ = login("dozentin", INSTRUCTOR_PASSWORD)

    response = client.post(
        "/api/auth/password",
        json={"current_password": "falsches-passwort-x", "new_password": "neues-passwort-2026"},
    )

    assert response.status_code == 401
    assert client.get("/api/auth/me").status_code == 200, "a failed change must not log you out"


def test_password_change_rejects_a_too_short_new_password(
    login: LoginHelper, instructor_user: User
) -> None:
    client, _ = login("dozentin", INSTRUCTOR_PASSWORD)

    response = client.post(
        "/api/auth/password",
        json={"current_password": INSTRUCTOR_PASSWORD, "new_password": "kurz"},
    )

    assert response.status_code == 422
    assert str(MIN_PASSWORD_LENGTH) in response.json()["detail"]["errors"][0]

    # The old password must still be the valid one.
    assert client.get("/api/auth/me").status_code == 200


def test_password_change_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "neues-passwort-2026"},
    )
    assert response.status_code == 401
