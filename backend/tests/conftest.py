"""Shared fixtures: a real, file-backed SQLite database in a tmp_path.

Deliberately *not* in-memory: the point of these tests is exercising the actual storage layer —
TEXT round-tripping of decimals, the ``PRAGMA foreign_keys`` connection hook, and DB-level
``ON DELETE CASCADE``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import create_engine_for, get_db, init_db
from app.main import app
from app.models import Exam, Exercise, Lecture, StudentRegistration, User

#: Passwords used by the API fixtures. Long enough to satisfy the §3 policy
#: (``app.auth.passwords.MIN_PASSWORD_LENGTH``) so tests exercise the happy path by default.
ADMIN_PASSWORD = "admin-passwort-1"
INSTRUCTOR_PASSWORD = "dozent-passwort-1"

type ClientFactory = Callable[[], TestClient]
type LoginHelper = Callable[[str, str], tuple[TestClient, str]]


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """A fresh, file-backed SQLite engine with the full schema created."""
    db_path = tmp_path / "gradinghelper_test.db"
    eng = create_engine_for(f"sqlite:///{db_path}")
    init_db(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as db:
        yield db


@pytest.fixture
def exam(session: Session) -> Exam:
    """A minimal committed Exam with one exercise and one registration."""
    user = User(username="pruefer", password_hash="not-a-real-hash")
    session.add(user)
    session.flush()

    lecture = Lecture(name="Grundlagen der Informationstechnik", owner_id=user.id)
    session.add(lecture)
    session.flush()

    ex = Exam(
        lecture_id=lecture.id,
        owner_id=user.id,
        semester="WiSe 23/24",
        termin="1. Termin",
    )
    ex.exercises.append(Exercise(name="Aufgabe 1", max_points=Decimal(60), position=1))
    ex.registrations.append(
        StudentRegistration(
            matrikelnummer="1000001",
            nachname="Musterfrau",
            vorname="Erika",
            course_code="B.Sc. WiIng ET/IT",
            module_title="Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)",
            versuch=1,
        )
    )
    session.add(ex)
    session.commit()
    return ex


# --------------------------------------------------------------------------------------------
# HTTP API fixtures (§3 auth / account management)
# --------------------------------------------------------------------------------------------


@pytest.fixture
def cookie_name() -> str:
    return get_settings().session_cookie_name


@pytest.fixture
def client_factory(session_factory: sessionmaker[Session]) -> Iterator[ClientFactory]:
    """Build ``TestClient``s bound to the tmp_path database, each with its own cookie jar.

    ``TestClient`` is intentionally **not** used as a context manager: entering it runs the
    app's lifespan, whose ``run_migrations()`` resolves the *configured* database URL and would
    create ``backend/data/gradinghelper.db`` in the working tree. The ``get_db`` override is what
    points the routes at the test database.
    """

    def override_get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    created: list[TestClient] = []

    def make() -> TestClient:
        client = TestClient(app)
        created.append(client)
        return client

    try:
        yield make
    finally:
        for client in created:
            client.close()
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(client_factory: ClientFactory) -> TestClient:
    """An unauthenticated API client."""
    return client_factory()


@pytest.fixture
def admin_user(session: Session) -> User:
    """A committed, active admin account with :data:`ADMIN_PASSWORD`."""
    user = User(
        username="admin",
        password_hash=hash_password(ADMIN_PASSWORD),
        is_admin=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def instructor_user(session: Session) -> User:
    """A committed, active non-admin account with :data:`INSTRUCTOR_PASSWORD`."""
    user = User(
        username="dozentin",
        password_hash=hash_password(INSTRUCTOR_PASSWORD),
        is_admin=False,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def login(client_factory: ClientFactory, cookie_name: str) -> LoginHelper:
    """Log in on a fresh client; return ``(client, token)``.

    A fresh client per login means two "browsers" for the same user do not share a cookie jar —
    which is what the revocation tests need.
    """

    def _login(username: str, password: str) -> tuple[TestClient, str]:
        client = client_factory()
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return client, client.cookies[cookie_name]

    return _login
