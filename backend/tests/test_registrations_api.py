"""Registration import and registration CRUD over HTTP (SPECIFICATION.md §5.1, §5.3, §6).

The parser has its own, database-free suite (``test_pdf_import.py``); what is pinned here is the
API layer's half of §5.3 — atomicity, the duplicate-Matrikelnummer stop, the flag-don't-drop
rule, warnings that don't block, excluded-is-not-deleted, and the 404-not-403 ownership rule.

The "nothing was written" assertions deliberately count rows through a **separate** session
(:func:`_count_registrations`): the app's sessionmaker runs with ``expire_on_commit=False``, so
an ORM collection held by a fixture can hand back stale state and make a broken atomicity
guarantee look intact.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF — used here only to *build* a PDF no committed fixture provides
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.collation import german_sort_key
from app.models import Exam, Lecture, StudentRegistration, User
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

TEST_DATA_DIR = Path(__file__).resolve().parents[2] / "test_data"

MULTIPAGE = TEST_DATA_DIR / "registration_synthetic_multipage.pdf"
SECOND_COURSE = TEST_DATA_DIR / "registration_synthetic_second_course.pdf"
DUPLICATE = TEST_DATA_DIR / "registration_synthetic_duplicate_matrikelnummer.pdf"
BROKEN_GAP = TEST_DATA_DIR / "registration_synthetic_broken_gap.pdf"
BROKEN_MISSING_PAGE = TEST_DATA_DIR / "registration_synthetic_broken_missing_page.pdf"

COURSE_1 = "B.Sc. WiIng ET/IT"
COURSE_2 = "B.Sc. WiIng ET/IT M.Sc."
TITLE_1 = "Grundlagen der Informationstechnik (B.Sc. WiIng ET/IT)"
TITLE_2 = (
    "Grundlagen der Informationstechnik für Wirtschaftsingenieurwesen "
    "(B.Sc. WiIng ET/IT M.Sc.), 6 CP, BPO 2020/2024 Kombinationsprüfung"
)
#: The Matrikelnummer ``registration_synthetic_duplicate_matrikelnummer.pdf`` shares with the
#: multi-page fixture — the whole point of that fixture (§5.3).
SHARED_MATRIKELNUMMER = "9990005"


# --------------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------------


@pytest.fixture
def instructor_exam(session: Session, instructor_user: User) -> Exam:
    """An empty exam owned by ``instructor_user``, matching the fixtures' semester/Termin."""
    lecture = Lecture(name="Grundlagen der Informationstechnik", owner_id=instructor_user.id)
    session.add(lecture)
    session.flush()
    exam = Exam(
        lecture_id=lecture.id,
        owner_id=instructor_user.id,
        semester="WiSe 23/24",
        termin="1. Termin",
    )
    session.add(exam)
    session.commit()
    return exam


@pytest.fixture
def other_instructor(session: Session) -> User:
    """A second instructor, used for the 404-not-403 checks."""
    from app.auth.passwords import hash_password

    user = User(
        username="kollege",
        password_hash=hash_password(INSTRUCTOR_PASSWORD),
        is_admin=False,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def api(login: LoginHelper, instructor_user: User) -> TestClient:
    client, _ = login(instructor_user.username, INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def other_api(login: LoginHelper, other_instructor: User) -> TestClient:
    client, _ = login(other_instructor.username, INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def fresh_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session independent of the request's, for "what is *really* in the database" checks."""
    with session_factory() as db:
        yield db


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _import(
    client: TestClient,
    exam_id: int,
    *paths: Path,
    replace_existing: bool | None = None,
) -> Any:
    files = [("files", (path.name, path.read_bytes(), "application/pdf")) for path in paths]
    data: dict[str, str] = {}
    if replace_existing is not None:
        data["replace_existing"] = "true" if replace_existing else "false"
    return client.post(f"/api/exams/{exam_id}/registrations/import", files=files, data=data or None)


def _count_registrations(db: Session, exam_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(StudentRegistration)
            .where(StudentRegistration.exam_id == exam_id)
        ).scalar_one()
    )


def _rows(db: Session, exam_id: int) -> list[StudentRegistration]:
    return list(
        db.execute(select(StudentRegistration).where(StudentRegistration.exam_id == exam_id))
        .scalars()
        .all()
    )


#: x positions used by :func:`_registration_pdf`, mirroring ``test_pdf_import.py``.
_COLUMN_X = (72.0, 110.0, 190.0, 300.0, 380.0, 420.0, 520.0)


def _registration_pdf(*, semester: str, termin: str, title: str, matrikelnummer: str) -> bytes:
    """A minimal one-row registration PDF with a freely chosen header.

    Needed because every committed fixture is ``WiSe 23/24`` / ``1. Termin`` by design, so the
    §5.3 semester/Termin-mismatch warning has no fixture to trigger it. Same construction as
    ``test_pdf_import.py``: text only, no ruled table, so the PyMuPDF fallback engine reads it.
    """
    lines: tuple[str | tuple[str, ...], ...] = (
        "Datum: 22.01.2024, Stand: 09:50:53 Uhr",
        semester,
        f"Termin: {termin}",
        title,
        "Prüfer: Prof.Dr.-Ing. Armin Dekorsy",
        ("Nr.", "Matr.-Nr.", "Nachname", "Vorname", "Vers.", "Kommentar", "Note"),
        ("1", matrikelnummer, "Mustermann", "Max", "1", "(angemeldet)", ""),
        "Seite 1 von 1",
    )
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate(lines):
        y = 72 + 16 * index
        cells = (line,) if isinstance(line, str) else line
        xs = (72.0,) if isinstance(line, str) else _COLUMN_X
        for x, cell in zip(xs, cells, strict=False):
            page.insert_text((x, y), cell, fontsize=9, fontname="helv")
    try:
        return bytes(document.tobytes())
    finally:
        document.close()


def _import_bytes(
    client: TestClient, exam_id: int, *named: tuple[str, bytes], replace_existing: bool = False
) -> Any:
    files = [("files", (name, data, "application/pdf")) for name, data in named]
    return client.post(
        f"/api/exams/{exam_id}/registrations/import",
        files=files,
        data={"replace_existing": "true" if replace_existing else "false"},
    )


# --------------------------------------------------------------------------------------------
# Import — happy paths (§5.1, §5.3)
# --------------------------------------------------------------------------------------------


def test_importing_the_multipage_fixture_stores_every_row(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """50 rows over 3 pages, each tagged with its file's course_code/module_title (§5.1)."""
    response = _import(api, instructor_exam.id, MULTIPAGE)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["imported_total"] == 50
    assert body["replaced_count"] == 0
    assert len(body["files"]) == 1
    summary = body["files"][0]
    assert summary["filename"] == MULTIPAGE.name
    assert summary["course_code"] == COURSE_1
    assert summary["module_title"] == TITLE_1
    assert summary["semester"] == "WiSe 23/24"
    assert summary["termin"] == "1. Termin"
    assert summary["row_count"] == 50
    assert summary["flagged_count"] == 3
    assert summary["engine"] == "pdfplumber"

    stored = _rows(fresh_session, instructor_exam.id)
    assert len(stored) == 50
    assert {row.course_code for row in stored} == {COURSE_1}
    assert {row.module_title for row in stored} == {TITLE_1}
    assert {row.source_filename for row in stored} == {MULTIPAGE.name}
    assert {row.versuch for row in stored} == {1, 2, 3}
    assert all(row.excluded is False for row in stored)


def test_unusual_kommentar_is_imported_and_flagged_never_dropped(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3: a row whose Kommentar isn't "(angemeldet)" is kept **and** marked, not dropped."""
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201

    stored = _rows(fresh_session, instructor_exam.id)
    flagged = [row for row in stored if row.flagged]
    assert len(flagged) == 3
    assert sorted(row.kommentar or "" for row in flagged) == [
        "(exmatrikuliert)",
        "(krank gemeldet)",
        "(zurückgetreten)",
    ]
    assert all(row.kommentar == "(angemeldet)" for row in stored if not row.flagged)
    # Flagged is a marker for the instructor to decide on — never an implicit exclusion.
    assert all(row.excluded is False for row in flagged)


def test_two_courses_in_one_request_keep_both_module_titles_verbatim(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§4/§5.1: a Kombinationsprüfung's differing module titles are data, never normalised."""
    response = _import(api, instructor_exam.id, MULTIPAGE, SECOND_COURSE)

    assert response.status_code == 201, response.text
    assert response.json()["imported_total"] == 65

    stored = _rows(fresh_session, instructor_exam.id)
    assert len(stored) == 65
    assert {row.module_title for row in stored} == {TITLE_1, TITLE_2}
    by_course = {
        COURSE_1: [row for row in stored if row.course_code == COURSE_1],
        COURSE_2: [row for row in stored if row.course_code == COURSE_2],
    }
    assert len(by_course[COURSE_1]) == 50
    assert len(by_course[COURSE_2]) == 15
    assert {row.module_title for row in by_course[COURSE_1]} == {TITLE_1}
    assert {row.module_title for row in by_course[COURSE_2]} == {TITLE_2}


# --------------------------------------------------------------------------------------------
# Import — §5.3 rejections
# --------------------------------------------------------------------------------------------


def test_duplicate_matrikelnummer_across_files_is_rejected_and_writes_nothing(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3: never silently merged or duplicated — surfaced for manual resolution instead."""
    response = _import(api, instructor_exam.id, MULTIPAGE, DUPLICATE)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(SHARED_MATRIKELNUMMER in message for message in detail["errors"])

    duplicates = detail["duplicates"]
    assert [entry["matrikelnummer"] for entry in duplicates] == [SHARED_MATRIKELNUMMER]
    occurrences = duplicates[0]["occurrences"]
    assert len(occurrences) == 2, "both competing rows must be described"
    assert {occurrence["filename"] for occurrence in occurrences} == {
        MULTIPAGE.name,
        DUPLICATE.name,
    }
    assert all(occurrence["source"] == "upload" for occurrence in occurrences)
    assert all(occurrence["course_code"] for occurrence in occurrences)
    assert all(occurrence["module_title"] for occurrence in occurrences)

    assert _count_registrations(fresh_session, instructor_exam.id) == 0


def test_duplicate_against_already_stored_rows_is_rejected_too(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """A second upload colliding with what is already stored is the same §5.3 error."""
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201

    response = _import(api, instructor_exam.id, DUPLICATE)

    assert response.status_code == 422, response.text
    duplicates = response.json()["detail"]["duplicates"]
    sources = {occurrence["source"] for occurrence in duplicates[0]["occurrences"]}
    assert sources == {"upload", "database"}
    stored_occurrence = next(
        occurrence
        for occurrence in duplicates[0]["occurrences"]
        if occurrence["source"] == "database"
    )
    assert stored_occurrence["registration_id"] is not None
    # The first import survives untouched; the second one contributed nothing.
    assert _count_registrations(fresh_session, instructor_exam.id) == 50


def test_an_excluded_student_still_blocks_a_re_import(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3's "excluded ≠ deleted": the row is still there and still owns its Matrikelnummer."""
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201
    target = next(
        row
        for row in _rows(fresh_session, instructor_exam.id)
        if row.matrikelnummer == SHARED_MATRIKELNUMMER
    )
    assert api.patch(f"/api/registrations/{target.id}", json={"excluded": True}).status_code == 200

    response = _import(api, instructor_exam.id, DUPLICATE)

    assert response.status_code == 422
    assert response.json()["detail"]["duplicates"][0]["matrikelnummer"] == SHARED_MATRIKELNUMMER


@pytest.mark.parametrize(
    ("path", "expected_fragment"),
    [
        (BROKEN_GAP, "18"),
        (BROKEN_MISSING_PAGE, "Seite 2"),
    ],
)
def test_incomplete_files_hard_fail_and_write_nothing(
    api: TestClient,
    instructor_exam: Exam,
    fresh_session: Session,
    path: Path,
    expected_fragment: str,
) -> None:
    """§5.3's mandatory checksum: a partial list is never imported, and the gap is named."""
    response = _import(api, instructor_exam.id, path)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any(expected_fragment in message for message in detail["errors"])
    assert any(path.name in message for message in detail["errors"])

    file_error = detail["files"][0]
    assert file_error["filename"] == path.name
    assert file_error["code"] == "unvollstaendige_liste"
    assert file_error["missing_nrs"] or file_error["missing_pages"]

    assert _count_registrations(fresh_session, instructor_exam.id) == 0


def test_broken_gap_names_the_missing_running_number(
    api: TestClient, instructor_exam: Exam
) -> None:
    response = _import(api, instructor_exam.id, BROKEN_GAP)

    assert response.json()["detail"]["files"][0]["missing_nrs"] == [18]


def test_missing_page_names_the_missing_page(api: TestClient, instructor_exam: Exam) -> None:
    file_error = _import(api, instructor_exam.id, BROKEN_MISSING_PAGE).json()["detail"]["files"][0]

    assert file_error["missing_pages"] == [2]
    assert file_error["declared_page_count"] == 3


def test_one_broken_file_rejects_the_whole_request(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """The atomicity decision: a good file next to a bad one imports **nothing**."""
    response = _import(api, instructor_exam.id, MULTIPAGE, BROKEN_GAP)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert [entry["filename"] for entry in detail["files"]] == [BROKEN_GAP.name]
    assert any("nichts importiert" in message for message in detail["errors"])

    assert _count_registrations(fresh_session, instructor_exam.id) == 0


def test_re_uploading_the_corrected_set_after_an_atomic_failure_succeeds(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """The point of all-or-nothing: the repair is "upload the whole set again", nothing else."""
    assert _import(api, instructor_exam.id, MULTIPAGE, BROKEN_GAP).status_code == 422

    response = _import(api, instructor_exam.id, MULTIPAGE, SECOND_COURSE)

    assert response.status_code == 201, response.text
    assert _count_registrations(fresh_session, instructor_exam.id) == 65


def test_an_upload_without_files_gets_the_german_error_shape(
    api: TestClient, instructor_exam: Exam
) -> None:
    response = api.post(
        f"/api/exams/{instructor_exam.id}/registrations/import",
        data={"replace_existing": "false"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == ["Es wurde keine PDF-Datei hochgeladen."]


def test_a_non_pdf_upload_is_a_422_not_a_500(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """A parser exception must never reach the client as a server error."""
    response = _import_bytes(api, instructor_exam.id, ("notizen.txt", b"kein PDF"))

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["files"][0]["code"] == "datei_unlesbar"
    assert _count_registrations(fresh_session, instructor_exam.id) == 0


# --------------------------------------------------------------------------------------------
# Import — warnings (§5.3: warn, don't block)
# --------------------------------------------------------------------------------------------


def test_semester_and_termin_mismatch_across_files_warns_but_imports(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3 makes this a warning on purpose — it usually means the wrong file was picked."""
    odd_one_out = _registration_pdf(
        semester="SoSe 24",
        termin="2. Termin",
        title="Grundlagen der Informationstechnik (M.Sc. Sonstiges)",
        matrikelnummer="9993001",
    )

    response = _import_bytes(
        api,
        instructor_exam.id,
        (MULTIPAGE.name, MULTIPAGE.read_bytes()),
        ("anderes_semester.pdf", odd_one_out),
    )

    assert response.status_code == 201, response.text
    warnings = response.json()["warnings"]
    assert any("unterschiedliche Semester" in warning for warning in warnings)
    assert any("unterschiedliche Termine" in warning for warning in warnings)
    # …and against the exam's own semester/Termin, which is what the odd file disagrees with.
    assert any("SoSe 24" in warning and "WiSe 23/24" in warning for warning in warnings)
    assert _count_registrations(fresh_session, instructor_exam.id) == 51


def test_a_file_disagreeing_with_the_exam_alone_still_warns(
    api: TestClient, instructor_exam: Exam, session: Session
) -> None:
    """One file only: nothing to compare it against but the exam it is imported into."""
    instructor_exam.semester = "SoSe 24"
    session.commit()

    response = _import(api, instructor_exam.id, MULTIPAGE)

    assert response.status_code == 201, response.text
    warnings = response.json()["warnings"]
    assert any("WiSe 23/24" in warning and "SoSe 24" in warning for warning in warnings)


def test_flagged_rows_produce_a_warning_of_their_own(
    api: TestClient, instructor_exam: Exam
) -> None:
    warnings = _import(api, instructor_exam.id, MULTIPAGE).json()["warnings"]

    assert any("abweichenden Kommentar" in warning for warning in warnings)


# --------------------------------------------------------------------------------------------
# Import — replace_existing
# --------------------------------------------------------------------------------------------


def test_replace_existing_redoes_one_course_without_touching_the_others(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """The "wrong file, let me redo it" path — scoped to the course codes in the upload."""
    assert _import(api, instructor_exam.id, MULTIPAGE, SECOND_COURSE).status_code == 201

    response = _import(api, instructor_exam.id, MULTIPAGE, replace_existing=True)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["replaced_count"] == 50
    assert body["imported_total"] == 50

    stored = _rows(fresh_session, instructor_exam.id)
    assert len(stored) == 65
    assert len([row for row in stored if row.course_code == COURSE_2]) == 15


def test_replace_existing_defaults_to_false(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """Omitting the field must never silently discard data — the collision is reported."""
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201

    response = _import(api, instructor_exam.id, MULTIPAGE)

    assert response.status_code == 422
    assert response.json()["detail"]["duplicates"]
    assert _count_registrations(fresh_session, instructor_exam.id) == 50


def test_replace_existing_still_rejects_duplicates_inside_the_upload(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """Replacing clears the *old* rows; it does not resolve a conflict between two new files."""
    response = _import(api, instructor_exam.id, MULTIPAGE, DUPLICATE, replace_existing=True)

    assert response.status_code == 422
    assert _count_registrations(fresh_session, instructor_exam.id) == 0


# --------------------------------------------------------------------------------------------
# CRUD (§5.3: manual add/edit/remove)
# --------------------------------------------------------------------------------------------


def test_list_is_sorted_by_course_then_name_and_can_filter(
    api: TestClient, instructor_exam: Exam
) -> None:
    """Course, then last name — with §6's German collation, not a codepoint sort."""
    assert _import(api, instructor_exam.id, MULTIPAGE, SECOND_COURSE).status_code == 201

    body = api.get(f"/api/exams/{instructor_exam.id}/registrations").json()

    assert len(body) == 65
    courses = [row["course_code"] for row in body]
    assert courses == sorted(courses), "grouped by Studiengang"
    first_course = [row["nachname"] for row in body if row["course_code"] == COURSE_1]
    assert first_course == sorted(first_course, key=german_sort_key)
    # DIN 5007-1, not codepoint order: "Öztürk" belongs under O, ahead of R — a plain
    # ``sorted()`` would put it after "Zwerg" (§6).
    assert "Öztürk" in first_course
    assert first_course.index("Öztürk") < first_course.index("Rotkäppchen")
    assert {"flagged", "excluded", "attended", "bonus_points"} <= set(body[0])

    filtered = api.get(
        f"/api/exams/{instructor_exam.id}/registrations", params={"course_code": COURSE_2}
    ).json()
    assert len(filtered) == 15
    assert {row["course_code"] for row in filtered} == {COURSE_2}


def test_manual_add_edit_and_delete(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3: a late registration that never appeared in a PDF, then corrected, then removed."""
    created = api.post(
        f"/api/exams/{instructor_exam.id}/registrations",
        json={
            "matrikelnummer": "9994001",
            "nachname": "Nachzügler",
            "vorname": "Nora",
            "course_code": COURSE_1,
            "module_title": TITLE_1,
            "versuch": 2,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_filename"] is None
    assert body["flagged"] is False
    assert body["excluded"] is False
    assert body["bonus_points"] == "0"
    registration_id = body["id"]

    patched = api.patch(
        f"/api/registrations/{registration_id}",
        json={"nachname": "Nachzüglerin", "kommentar": "nachgemeldet", "flagged": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["nachname"] == "Nachzüglerin"
    assert patched.json()["flagged"] is True

    assert api.delete(f"/api/registrations/{registration_id}").status_code == 204
    assert _count_registrations(fresh_session, instructor_exam.id) == 0


def test_manual_add_requires_course_code_and_module_title(
    api: TestClient, instructor_exam: Exam
) -> None:
    """There is no PDF to take them from (§5.1), so they cannot be defaulted."""
    response = api.post(
        f"/api/exams/{instructor_exam.id}/registrations",
        json={"matrikelnummer": "9994002", "nachname": "Ohne", "vorname": "Kurs"},
    )

    assert response.status_code == 422


def test_manual_add_derives_the_flag_from_an_unusual_kommentar(
    api: TestClient, instructor_exam: Exam
) -> None:
    response = api.post(
        f"/api/exams/{instructor_exam.id}/registrations",
        json={
            "matrikelnummer": "9994003",
            "nachname": "Krank",
            "vorname": "Karl",
            "course_code": COURSE_1,
            "module_title": TITLE_1,
            "kommentar": "(krank gemeldet)",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["flagged"] is True


def test_manual_add_of_an_existing_matrikelnummer_conflicts(
    api: TestClient, instructor_exam: Exam
) -> None:
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201

    response = api.post(
        f"/api/exams/{instructor_exam.id}/registrations",
        json={
            "matrikelnummer": SHARED_MATRIKELNUMMER,
            "nachname": "Doppelt",
            "vorname": "Dora",
            "course_code": COURSE_1,
            "module_title": TITLE_1,
        },
    )

    assert response.status_code == 409
    assert SHARED_MATRIKELNUMMER in response.json()["detail"]


def test_excluding_a_student_keeps_the_row(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§5.3's central invariant: excluded is a flag, never a deletion."""
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201
    target = _rows(fresh_session, instructor_exam.id)[0]

    response = api.patch(f"/api/registrations/{target.id}", json={"excluded": True})

    assert response.status_code == 200
    assert response.json()["excluded"] is True
    assert _count_registrations(fresh_session, instructor_exam.id) == 50

    listed = api.get(f"/api/exams/{instructor_exam.id}/registrations").json()
    assert len(listed) == 50, "an excluded student stays visible and revisable in the UI list"
    without = api.get(
        f"/api/exams/{instructor_exam.id}/registrations", params={"include_excluded": "false"}
    ).json()
    assert len(without) == 49
    assert target.matrikelnummer not in {row["matrikelnummer"] for row in without}

    # …and the decision is reversible.
    assert api.patch(f"/api/registrations/{target.id}", json={"excluded": False}).status_code == 200
    assert (
        len(
            api.get(
                f"/api/exams/{instructor_exam.id}/registrations",
                params={"include_excluded": "false"},
            ).json()
        )
        == 50
    )


def test_attendance_may_be_cleared_back_to_not_recorded(
    api: TestClient, instructor_exam: Exam
) -> None:
    """§4: ``NULL`` attendance means "not yet recorded" and must stay reachable."""
    created = api.post(
        f"/api/exams/{instructor_exam.id}/registrations",
        json={
            "matrikelnummer": "9994004",
            "nachname": "Anwesend",
            "vorname": "Anna",
            "course_code": COURSE_1,
            "module_title": TITLE_1,
        },
    ).json()
    assert created["attended"] is None

    assert (
        api.patch(f"/api/registrations/{created['id']}", json={"attended": True}).json()["attended"]
        is True
    )
    assert (
        api.patch(f"/api/registrations/{created['id']}", json={"attended": None}).json()["attended"]
        is None
    )


# --------------------------------------------------------------------------------------------
# Head count (§6)
# --------------------------------------------------------------------------------------------


def test_head_count_is_per_course_and_skips_excluded_students(
    api: TestClient, instructor_exam: Exam, fresh_session: Session
) -> None:
    """§6: the number of exam copies to print, without generating the attendance PDF."""
    assert _import(api, instructor_exam.id, MULTIPAGE, SECOND_COURSE).status_code == 201
    target = next(
        row for row in _rows(fresh_session, instructor_exam.id) if row.course_code == COURSE_2
    )
    assert api.patch(f"/api/registrations/{target.id}", json={"excluded": True}).status_code == 200

    body = api.get(f"/api/exams/{instructor_exam.id}/registrations/count").json()

    assert body["total"] == 64
    assert body["per_course"] == [
        {"course_code": COURSE_1, "count": 50},
        {"course_code": COURSE_2, "count": 14},
    ]


def test_head_count_of_an_empty_exam(api: TestClient, instructor_exam: Exam) -> None:
    assert api.get(f"/api/exams/{instructor_exam.id}/registrations/count").json() == {
        "total": 0,
        "per_course": [],
    }


# --------------------------------------------------------------------------------------------
# Ownership — 404, never 403
# --------------------------------------------------------------------------------------------


def test_another_instructor_gets_404_everywhere(
    api: TestClient,
    other_api: TestClient,
    instructor_exam: Exam,
    fresh_session: Session,
) -> None:
    assert _import(api, instructor_exam.id, MULTIPAGE).status_code == 201
    registration_id = _rows(fresh_session, instructor_exam.id)[0].id

    assert _import(other_api, instructor_exam.id, MULTIPAGE).status_code == 404
    assert other_api.get(f"/api/exams/{instructor_exam.id}/registrations").status_code == 404
    assert other_api.get(f"/api/exams/{instructor_exam.id}/registrations/count").status_code == 404
    assert (
        other_api.post(
            f"/api/exams/{instructor_exam.id}/registrations",
            json={
                "matrikelnummer": "9995001",
                "nachname": "Fremd",
                "vorname": "Frieda",
                "course_code": COURSE_1,
                "module_title": TITLE_1,
            },
        ).status_code
        == 404
    )
    assert (
        other_api.patch(
            f"/api/registrations/{registration_id}", json={"excluded": True}
        ).status_code
        == 404
    )
    assert other_api.delete(f"/api/registrations/{registration_id}").status_code == 404

    # Nothing the foreign instructor tried had any effect.
    assert _count_registrations(fresh_session, instructor_exam.id) == 50


def test_an_unknown_registration_is_indistinguishable_from_a_foreign_one(
    api: TestClient, instructor_exam: Exam
) -> None:
    missing = api.patch("/api/registrations/999999", json={"excluded": True})

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Anmeldung nicht gefunden."


def test_the_import_route_requires_a_session(client: TestClient, instructor_exam: Exam) -> None:
    assert _import(client, instructor_exam.id, MULTIPAGE).status_code == 401
