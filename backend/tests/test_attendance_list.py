"""The printed attendance list (SPECIFICATION.md §6) — data shape, sort order and rendered PDF.

Two properties dominate this file because they are the ones that fail *visibly on paper* and
*silently in code*:

* **the sort** (§6). It is asserted against
  :func:`~app.reports.attendance_list.build_attendance_list_data`, not against text scraped back
  out of the PDF, so a regression fails on an exact list comparison instead of a fuzzy substring
  match. ``test_a_naive_sort_would_mis_order_the_printed_sheet`` pins the bug being prevented:
  it shows what a plain ``sorted()``/SQL ``ORDER BY`` produces, so anyone tempted to "simplify"
  the sort sees exactly what breaks.
* **the umlauts actually reaching the paper**. A font or encoding regression in the Typst
  template is invisible until someone prints the sheet, so the PDF is parsed back with
  ``pdfplumber`` and "Öztürk"/"Straßer"/"Müller" are asserted in the extracted text. Parsing the
  output back is also the only way to know the template rendered the *data* rather than an empty
  table.

Zero registrations is a documented, tested non-error: a valid PDF with a head count of 0.
"""

from __future__ import annotations

import io
from datetime import date

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.models import Exam, Lecture, StudentRegistration, User
from app.reports.attendance_list import (
    TEMPLATE_PATH,
    AttendanceListData,
    attendance_list_filename,
    build_attendance_list_data,
    content_disposition,
    format_german_date,
    render_attendance_list,
)
from tests.conftest import INSTRUCTOR_PASSWORD, LoginHelper

COURSE_A = "B.Sc. ET/IT"
COURSE_B = "M.Sc. WiIng"

#: ``(course_code, nachname, vorname, matrikelnummer, excluded)`` in a deliberately jumbled order,
#: so nothing about the expected output can come from the insertion order.
#:
#: The Strasser/Straßer pair carries Vornamen chosen to *document* the tie-break: "Straßer, Anna"
#: would come first if the Vorname were consulted before the surname's exact spelling. It is not
#: — ``german_sort_key`` returns ``(folded, original)`` and the original spelling separates the
#: two surnames first ("Strasser" < "Straßer"), so "Strasser, Zoe" wins. See ``_sort_key``.
ROSTER: list[tuple[str, str, str, str, bool]] = [
    (COURSE_A, "Zimmermann", "Cem", "9990900", False),
    (COURSE_B, "Zimmermann", "Nele", "9991900", False),
    (COURSE_A, "Öztürk", "Aylin", "9990600", False),
    (COURSE_A, "Straßer", "Anna", "9990800", False),
    (COURSE_A, "Müller", "Anna", "9990300", False),
    (COURSE_A, "von Arendelle", "Leyla Olivia", "9990500", False),
    (COURSE_B, "Ackermann", "Zoe", "9991100", False),
    (COURSE_A, "Ostermann", "Finn", "9990700", False),
    (COURSE_A, "Müller", "Jonas", "9990200", False),
    (COURSE_A, "Strasser", "Zoe", "9990400", False),
    (COURSE_A, "Müller", "Anna", "9990100", False),
    (COURSE_A, "Obermeier", "Bernd", "9990010", False),
    # §5.3: excluded ≠ deleted — stays in the database, appears on no list and in no head count.
    (COURSE_A, "Ausgeschlossen", "Petra", "9990999", True),
    (COURSE_B, "Beurlaubt", "Ben", "9991999", True),
]

#: The exact printed order §6 requires. Course first (German-collated too), then Nachname, then
#: Vorname, then Matrikelnummer.
#:
#: Within ``B.Sc. ET/IT`` this is the whole point of the feature: "Öztürk" sits between the
#: O-names and "Strasser" instead of being stranded after "Zimmermann"; "ß" collates as "ss" so
#: "Straßer" lands next to "Strasser" rather than after "Zimmermann"; "von Arendelle" sorts under
#: V exactly as printed in the source PDF, with no nobiliary-particle reordering. And
#: ``M.Sc. WiIng``'s "Ackermann" comes after ``B.Sc. ET/IT``'s "Zimmermann" because the course is
#: the primary key of the sort, not the name.
EXPECTED_ORDER: list[tuple[str, str, str, str]] = [
    (COURSE_A, "Müller", "Anna", "9990100"),
    (COURSE_A, "Müller", "Anna", "9990300"),
    (COURSE_A, "Müller", "Jonas", "9990200"),
    (COURSE_A, "Obermeier", "Bernd", "9990010"),
    (COURSE_A, "Ostermann", "Finn", "9990700"),
    (COURSE_A, "Öztürk", "Aylin", "9990600"),
    (COURSE_A, "Strasser", "Zoe", "9990400"),
    (COURSE_A, "Straßer", "Anna", "9990800"),
    (COURSE_A, "von Arendelle", "Leyla Olivia", "9990500"),
    (COURSE_A, "Zimmermann", "Cem", "9990900"),
    (COURSE_B, "Ackermann", "Zoe", "9991100"),
    (COURSE_B, "Zimmermann", "Nele", "9991900"),
]


# --------------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------------


def _make_exam(session: Session, owner: User, registrations: list[StudentRegistration]) -> Exam:
    lecture = Lecture(name="Grundlagen der Informationstechnik", owner_id=owner.id)
    session.add(lecture)
    session.flush()
    exam = Exam(
        lecture_id=lecture.id,
        owner_id=owner.id,
        semester="WiSe 23/24",
        termin="1. Termin",
        exam_date=date(2024, 2, 12),
    )
    exam.registrations.extend(registrations)
    session.add(exam)
    session.commit()
    return exam


@pytest.fixture
def roster_exam(session: Session, instructor_user: User) -> Exam:
    """The full :data:`ROSTER` committed as one exam owned by ``instructor_user``."""
    registrations = [
        StudentRegistration(
            matrikelnummer=matrikelnummer,
            nachname=nachname,
            vorname=vorname,
            course_code=course_code,
            module_title=f"Grundlagen der Informationstechnik ({course_code})",
            versuch=1,
            excluded=excluded,
        )
        for course_code, nachname, vorname, matrikelnummer, excluded in ROSTER
    ]
    return _make_exam(session, instructor_user, registrations)


@pytest.fixture
def empty_exam(session: Session, instructor_user: User) -> Exam:
    """An exam with no registrations at all — the "printed before the import" case."""
    return _make_exam(session, instructor_user, [])


@pytest.fixture
def instructor_client(login: LoginHelper, instructor_user: User) -> TestClient:
    client, _token = login("dozentin", INSTRUCTOR_PASSWORD)
    return client


@pytest.fixture
def other_client(login: LoginHelper, session: Session) -> TestClient:
    session.add(
        User(
            username="dozent-b",
            password_hash=hash_password(INSTRUCTOR_PASSWORD),
            is_admin=False,
            is_active=True,
        )
    )
    session.commit()
    client, _token = login("dozent-b", INSTRUCTOR_PASSWORD)
    return client


def _rows(data: AttendanceListData) -> list[tuple[str, str, str, str]]:
    return [
        (s["course_code"], s["nachname"], s["vorname"], s["matrikelnummer"])
        for s in data["students"]
    ]


def _pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _url(exam_id: int) -> str:
    return f"/api/exams/{exam_id}/reports/attendance-list"


# --------------------------------------------------------------------------------------------
# §6 — the sort
# --------------------------------------------------------------------------------------------


def test_rows_are_grouped_by_course_and_german_collated_within_it(roster_exam: Exam) -> None:
    data = build_attendance_list_data(roster_exam)

    assert _rows(data) == EXPECTED_ORDER


def test_umlaut_name_sorts_among_the_o_names_not_after_z(roster_exam: Exam) -> None:
    """The single most visible §6 symptom, asserted on its own so a failure names itself."""
    names = [s["nachname"] for s in build_attendance_list_data(roster_exam)["students"]]
    course_a = names[: names.index("Ackermann")]

    assert course_a.index("Ostermann") < course_a.index("Öztürk") < course_a.index("Zimmermann")
    assert course_a.index("Öztürk") < course_a.index("Strasser")


def test_nobiliary_particle_is_not_reordered(roster_exam: Exam) -> None:
    """ "von Arendelle" sorts under V, exactly as printed in the source PDF (§6)."""
    names = [s["nachname"] for s in build_attendance_list_data(roster_exam)["students"]]

    assert names.index("Straßer") < names.index("von Arendelle") < names.index("Zimmermann")


def test_a_naive_sort_would_mis_order_the_printed_sheet(roster_exam: Exam) -> None:
    """The bug §6 calls out, pinned: a codepoint sort is *not* the same list.

    A plain ``sorted()`` — which is also what an SQL ``ORDER BY nachname`` on these TEXT columns
    does — strands "Öztürk" at the very end (U+00D6 sorts past "Z") and puts the lowercase-"v"
    "von Arendelle" after "Zimmermann". Both are immediately obvious to an instructor scanning a
    printed sheet, and neither shows up in a test that only uses ASCII names.
    """
    printable = [
        (course_code, nachname, vorname, matrikelnummer)
        for course_code, nachname, vorname, matrikelnummer, excluded in ROSTER
        if not excluded
    ]
    naive = sorted(printable)

    assert naive != EXPECTED_ORDER

    naive_names_course_a = [nachname for course, nachname, _v, _m in naive if course == COURSE_A]
    assert naive_names_course_a[-1] == "Öztürk"
    assert naive_names_course_a.index("Zimmermann") < naive_names_course_a.index("von Arendelle")


# --------------------------------------------------------------------------------------------
# §5.3 — excluded students
# --------------------------------------------------------------------------------------------


def test_excluded_students_are_omitted_from_the_list_and_the_head_count(roster_exam: Exam) -> None:
    data = build_attendance_list_data(roster_exam)
    surnames = {s["nachname"] for s in data["students"]}

    assert "Ausgeschlossen" not in surnames
    assert "Beurlaubt" not in surnames
    assert data["head_count"] == len(EXPECTED_ORDER) == 12
    assert len(data["students"]) == 12


def test_excluded_students_are_omitted_from_the_per_course_counts(roster_exam: Exam) -> None:
    data = build_attendance_list_data(roster_exam)

    assert data["courses"] == [
        {"course_code": COURSE_A, "count": 10},
        {"course_code": COURSE_B, "count": 2},
    ]


def test_excluded_students_are_omitted_from_the_rendered_pdf(roster_exam: Exam) -> None:
    text = _pdf_text(render_attendance_list(build_attendance_list_data(roster_exam)))

    assert "Ausgeschlossen" not in text
    assert "Beurlaubt" not in text


# --------------------------------------------------------------------------------------------
# §14 #6 — German formatting
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(date(2024, 2, 12), "12.02.2024"), (date(2024, 12, 1), "01.12.2024"), (None, None)],
)
def test_dates_are_formatted_german(value: date | None, expected: str | None) -> None:
    assert format_german_date(value) == expected


def test_an_exam_without_a_date_still_renders(session: Session, instructor_user: User) -> None:
    """``exam_date`` is nullable (§4); the sheet then prints a rule to fill in by hand."""
    exam = _make_exam(session, instructor_user, [])
    exam.exam_date = None
    session.commit()

    data = build_attendance_list_data(exam)
    assert data["exam_date"] is None

    text = _pdf_text(render_attendance_list(data))
    assert "Datum:" in text


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def test_template_imports_no_package_at_all() -> None:
    """§13: a ``@preview`` import fetches from Typst's registry over the network at render time.

    Comment lines are stripped first — the template's own header comment explains *why* there is
    no such import, and that explanation must not be what makes this test pass.
    """
    code = "\n".join(
        line
        for line in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )

    assert "@preview" not in code
    assert "#import" not in code


def test_rendered_pdf_contains_the_data_and_the_german_headings(roster_exam: Exam) -> None:
    pdf_bytes = render_attendance_list(build_attendance_list_data(roster_exam))
    text = _pdf_text(pdf_bytes)

    assert pdf_bytes.startswith(b"%PDF")
    for heading in ("Anwesenheitsliste", "Studiengang", "Matr.-Nr.", "Nachname", "Vorname"):
        assert heading in text, heading
    for course_code, nachname, vorname, matrikelnummer in EXPECTED_ORDER:
        assert nachname in text
        assert vorname in text
        assert matrikelnummer in text
        assert course_code in text


def test_rendered_pdf_has_a_tick_column_and_the_head_count(roster_exam: Exam) -> None:
    """§6: the sheet exists to be ticked by hand, and to give the copy-count for printing."""
    text = _pdf_text(render_attendance_list(build_attendance_list_data(roster_exam)))

    assert "Anwesend" in text
    assert "Anzahl Studierende:" in text
    assert "12" in text
    assert "Seite 1 von" in text


def test_umlauts_survive_into_the_rendered_pdf(roster_exam: Exam) -> None:
    """A font/encoding regression here is invisible until someone prints the sheet."""
    text = _pdf_text(render_attendance_list(build_attendance_list_data(roster_exam)))

    for name in ("Öztürk", "Straßer", "Müller", "Grundlagen der Informationstechnik"):
        assert name in text, name


def test_rows_keep_their_order_in_the_rendered_pdf(roster_exam: Exam) -> None:
    """The template must not re-order what it is handed."""
    text = _pdf_text(render_attendance_list(build_attendance_list_data(roster_exam)))
    positions = [
        text.index(f"{nachname} {vorname}") for _c, nachname, vorname, _m in EXPECTED_ORDER
    ]

    assert positions == sorted(positions)


def test_render_is_a_pure_function_of_its_data() -> None:
    """No database, no exam, no request — just the dict. This is what §12 chose Typst for."""
    data = AttendanceListData(
        lecture_name="Höhere Mathematik",
        semester="SoSe 25",
        termin="2. Termin",
        exam_date="01.09.2025",
        head_count=1,
        courses=[{"course_code": "B.Sc. WiIng", "count": 1}],
        students=[
            {
                "course_code": "B.Sc. WiIng",
                "matrikelnummer": "9990001",
                "nachname": "Groß",
                "vorname": "Fritz",
            }
        ],
    )

    text = _pdf_text(render_attendance_list(data))

    assert "Höhere Mathematik" in text
    assert "Groß" in text


# --------------------------------------------------------------------------------------------
# Zero registrations — a valid PDF, not an error
# --------------------------------------------------------------------------------------------


def test_zero_registrations_yields_a_valid_pdf_with_head_count_zero(empty_exam: Exam) -> None:
    data = build_attendance_list_data(empty_exam)

    assert data["head_count"] == 0
    assert data["students"] == []
    assert data["courses"] == []

    pdf_bytes = render_attendance_list(data)
    assert pdf_bytes.startswith(b"%PDF")

    text = _pdf_text(pdf_bytes)
    assert "Anwesenheitsliste" in text
    assert "Nachname" in text  # the table header is printed even with no rows
    assert "keine Studierenden angemeldet" in text


def test_zero_registrations_route_returns_200_not_an_error(
    instructor_client: TestClient, empty_exam: Exam
) -> None:
    response = instructor_client.get(_url(empty_exam.id))

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


# --------------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------------


def test_route_returns_a_real_pdf(instructor_client: TestClient, roster_exam: Exam) -> None:
    response = instructor_client.get(_url(roster_exam.id))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")

    text = _pdf_text(response.content)
    for heading in ("Studiengang", "Matr.-Nr.", "Nachname", "Vorname", "Anwesend"):
        assert heading in text, heading
    assert "Öztürk" in text
    assert "Straßer" in text
    assert COURSE_A in text
    assert COURSE_B in text


def test_route_sets_a_german_download_filename(
    instructor_client: TestClient, roster_exam: Exam
) -> None:
    response = instructor_client.get(_url(roster_exam.id))
    disposition = response.headers["content-disposition"]

    # The slash in "WiSe 23/24" must not survive into a filename.
    assert 'filename="Anwesenheitsliste_WiSe_23-24_1._Termin.pdf"' in disposition
    assert "filename*=UTF-8''" in disposition
    assert response.headers["cache-control"] == "no-store"


def test_route_404_for_another_instructor(other_client: TestClient, roster_exam: Exam) -> None:
    """404, never 403 — a 403 would confirm that another instructor's exam exists."""
    response = other_client.get(_url(roster_exam.id))

    assert response.status_code == 404


def test_route_404_for_an_unknown_exam(instructor_client: TestClient) -> None:
    assert instructor_client.get(_url(999_999)).status_code == 404


def test_route_401_when_unauthenticated(client: TestClient, roster_exam: Exam) -> None:
    assert client.get(_url(roster_exam.id)).status_code == 401


# --------------------------------------------------------------------------------------------
# Filename helpers
# --------------------------------------------------------------------------------------------


def test_filename_sanitises_the_semester_slash(roster_exam: Exam) -> None:
    assert attendance_list_filename(roster_exam) == "Anwesenheitsliste_WiSe_23-24_1._Termin.pdf"


def test_content_disposition_is_latin_1_safe_and_carries_the_umlaut_name() -> None:
    disposition = content_disposition("Anwesenheitsliste_Nachprüfung.pdf")

    # Header values must be latin-1 encodable; the ASCII fallback transliterates ü → ue.
    disposition.encode("latin-1")
    assert 'filename="Anwesenheitsliste_Nachpruefung.pdf"' in disposition
    assert "filename*=UTF-8''Anwesenheitsliste_Nachpr%C3%BCfung.pdf" in disposition
