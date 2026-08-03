"""Registration import and registration CRUD (SPECIFICATION.md §5.1, §5.3, §6).

This module owns the *database-facing* half of the registration-PDF import. The parsing half is
``app/pdf_import/``, which is pure and knows nothing about the ORM; everything §5.3 calls
"merge behaviour" — the cross-file checks, the duplicate rule, the warnings — happens here.

Three decisions this module pins down, because §5.3 states the requirement but not the
mechanics:

**The whole request is atomic — all files or none.** §5.3 only mandates that a file failing the
completeness check contributes *nothing* ("rather than silently importing a partial list"). This
module goes one step further and makes the *request* the unit of success: every uploaded file is
parsed and every cross-file check runs before a single row is written, and one bad file rejects
the entire upload. The reasoning is the same as §5.3's own: an exam left half-imported looks
finished, and the instructor's natural next step — fix the one bad PDF and upload the set again
— would then hit a wall of duplicate-Matrikelnummer errors for the files that did go through.
All-or-nothing makes re-uploading the corrected set the obvious, always-safe repair.

**Duplicate Matrikelnummern are never resolved automatically.** §5.3 requires them to be
"surfaced to the instructor to pick which course/row to keep, never silently merged or
duplicated", so the import is rejected with a machine-readable list of the competing rows and no
auto-merge of any kind. The check spans, in one pass, rows within a single file, rows across the
uploaded files, and rows already stored for that exam.

**Re-importing is explicit, never implicit.** Uploading a file whose students are already stored
is, by the rule above, a duplicate error. The escape hatch is the ``replace_existing`` form
field, which first deletes this exam's existing registrations for the course codes present in
the upload — the "I uploaded the wrong file, let me redo it" path. It is never the default,
because it discards data (see :func:`import_registrations`).

Ownership is delegated to ``app/api/exams.py::get_owned_exam``: another instructor's exam
answers ``404``, not ``403``, on every route here (see that module for the reasoning).

**Nothing in this module logs student data** (CLAUDE.md): Matrikelnummern and names appear only
in the HTTP response sent back to the instructor who owns the exam, never in a log record, and
names appear in no error payload at all.
"""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.exams import get_owned_exam
from app.api.schemas import (
    CourseHeadCount,
    ImportedFileSummary,
    RegistrationCreateRequest,
    RegistrationHeadCount,
    RegistrationImportResult,
    RegistrationOut,
    RegistrationUpdateRequest,
)
from app.auth.dependencies import CurrentUser, DbSession
from app.collation import german_sort_key
from app.models import Exam, StudentRegistration, User
from app.pdf_import import (
    NORMAL_KOMMENTAR,
    ParsedFile,
    PdfHeaderError,
    PdfImportError,
    PdfLayoutError,
    RegistrationCompletenessError,
    ScannedPdfError,
    UnreadablePdfError,
    parse_registration_pdf,
)

router = APIRouter(tags=["registrations"])

REGISTRATION_NOT_FOUND_DETAIL = "Anmeldung nicht gefunden."
NO_FILES_DETAIL = "Es wurde keine PDF-Datei hochgeladen."
FALLBACK_FILENAME = "unbenannt.pdf"
REGISTRATIONS_DELETE_ALL_CONFIRM_DETAIL = (
    "Das Entfernen aller Anmeldungen löscht unwiderruflich auch alle bereits eingetragenen "
    "Punkte dieser Prüfung. Bitte mit ?confirm=true bestätigen."
)

#: ``PdfImportError`` subclass -> stable machine-readable code for the ``422`` payload. The
#: German ``message`` is what the instructor reads; the code is what the frontend branches on,
#: so it must not change when a message is reworded.
_ERROR_CODES: tuple[tuple[type[PdfImportError], str], ...] = (
    (RegistrationCompletenessError, "unvollstaendige_liste"),
    (UnreadablePdfError, "datei_unlesbar"),
    (ScannedPdfError, "scan_ohne_text"),
    (PdfHeaderError, "kopfbereich_unlesbar"),
    (PdfLayoutError, "tabelle_unlesbar"),
)


def _raise_validation_errors(errors: list[str], **extra: Any) -> NoReturn:
    """Raise the contract's ``422`` shape, optionally with machine-readable extras.

    ``{"detail": {"errors": [<German>, ...], ...}}`` — the ``errors`` key is what the frontend's
    single German-message renderer consumes (``docs/api-contract.md``); the extra keys carry
    the structured detail (per-file parser errors, duplicate Matrikelnummern) a UI needs to offer
    a resolution rather than just a sentence.

    Literal ``422`` for the same reason as ``app/api/exams.py``: starlette has deprecated the
    ``HTTP_422_UNPROCESSABLE_ENTITY`` constant in favour of a renamed one.
    """
    raise HTTPException(status_code=422, detail={"errors": errors, **extra})


# --------------------------------------------------------------------------------------------
# Access helpers
# --------------------------------------------------------------------------------------------


def get_owned_registration(db: Session, user: User, registration_id: int) -> StudentRegistration:
    """One registration belonging to one of the caller's exams, or ``404``.

    "No such registration" and "someone else's registration" answer identically — a ``403`` on
    the second would confirm the row exists, which leaks another instructor's exam data.
    """
    registration = db.get(StudentRegistration, registration_id)
    if registration is not None:
        exam = db.get(Exam, registration.exam_id)
        if exam is not None and exam.owner_id == user.id:
            return registration
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=REGISTRATION_NOT_FOUND_DETAIL)


def _registrations_of(db: Session, exam: Exam) -> list[StudentRegistration]:
    """Every registration of the exam, excluded ones included (§5.3: excluded ≠ deleted)."""
    return list(
        db.execute(select(StudentRegistration).where(StudentRegistration.exam_id == exam.id))
        .scalars()
        .all()
    )


def _sorted_for_display(
    registrations: list[StudentRegistration],
) -> list[StudentRegistration]:
    """Course, then last name, then first name — German DIN 5007-1 collation (§6).

    Sorted in Python: the collation key is not expressible in SQLite's ``ORDER BY``, and a
    codepoint sort would put "Öztürk" after "Z" on the list the instructor reads.
    """
    return sorted(
        registrations,
        key=lambda item: (
            german_sort_key(item.course_code),
            german_sort_key(item.nachname),
            german_sort_key(item.vorname),
        ),
    )


# --------------------------------------------------------------------------------------------
# Import helpers (§5.3)
# --------------------------------------------------------------------------------------------


def _safe_filename(raw: str | None) -> str:
    """The upload's own basename, path components stripped and length capped.

    The value is client-supplied and is echoed back in responses and stored in
    ``source_filename``, so it never keeps a directory prefix.
    """
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    return (name or FALLBACK_FILENAME)[:255]


def _error_code(exc: PdfImportError) -> str:
    for error_type, code in _ERROR_CODES:
        if isinstance(exc, error_type):
            return code
    return "import_fehlgeschlagen"


def _file_error_payload(filename: str, exc: PdfImportError) -> dict[str, Any]:
    """Machine-readable detail for one file that failed to parse.

    Carries the structured attributes of :class:`RegistrationCompletenessError` (the missing
    ``Nr.`` values and pages §5.3 requires to be named) so the UI can render them itself. They
    are integers and page numbers only — no student data, which is what keeps this payload safe
    to surface anywhere.
    """
    payload: dict[str, Any] = {
        "filename": filename,
        "code": _error_code(exc),
        "message": exc.message,
    }
    if isinstance(exc, RegistrationCompletenessError):
        payload.update(
            missing_nrs=list(exc.missing_nrs),
            duplicate_nrs=list(exc.duplicate_nrs),
            missing_pages=list(exc.missing_pages),
            duplicate_pages=list(exc.duplicate_pages),
            declared_page_count=exc.declared_page_count,
        )
    return payload


def _duplicate_occurrences(
    parsed: list[ParsedFile], existing: list[StudentRegistration]
) -> dict[str, list[dict[str, Any]]]:
    """Every place a Matrikelnummer occurs, keyed by Matrikelnummer.

    One flat map over *all* rows of *all* uploaded files plus the rows already stored for the
    exam, so a single ``len(...) > 1`` test catches the three cases at once: the same student
    twice inside one file, the same student in two files of one upload, and a student already
    imported earlier. The parser guarantees a contiguous ``Nr.`` sequence but says nothing about
    Matrikelnummern, so the within-file case is real and would otherwise reach the database as an
    ``IntegrityError`` on ``uq_registration_exam_matrikelnummer``.

    Already-stored rows count even when ``excluded`` is true: an excluded student is kept in the
    database (§5.3) and still occupies the unique key, so a new upload containing them is a
    conflict the instructor must resolve, not a silent re-activation.
    """
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for parsed_file in parsed:
        for row in parsed_file.rows:
            occurrences.setdefault(row.matrikelnummer, []).append(
                {
                    "source": "upload",
                    "filename": parsed_file.filename,
                    "course_code": parsed_file.course_code,
                    "module_title": parsed_file.module_title,
                    "registration_id": None,
                }
            )
    for registration in existing:
        occurrences.setdefault(registration.matrikelnummer, []).append(
            {
                "source": "database",
                "filename": registration.source_filename,
                "course_code": registration.course_code,
                "module_title": registration.module_title,
                "registration_id": registration.id,
            }
        )
    return occurrences


def _describe_occurrence(occurrence: dict[str, Any]) -> str:
    """One competing row as a German phrase (Studiengang plus where it came from)."""
    course = occurrence["course_code"]
    filename = occurrence["filename"]
    if occurrence["source"] == "upload":
        origin = f"Datei „{filename}“" if filename else "hochgeladene Datei"
        return f"{course} ({origin})"
    origin = f"bereits importiert aus „{filename}“" if filename else "bereits importiert"
    return f"{course} ({origin})"


def _reject_duplicates(duplicates: dict[str, list[dict[str, Any]]]) -> NoReturn:
    """§5.3's duplicate-Matrikelnummer stop: reject the whole import, resolve nothing.

    The Matrikelnummer is named because the instructor cannot pick a row without it; names are
    deliberately not included, and this path writes nothing to any log.
    """
    errors = [
        f"Die Matrikelnummer {matrikelnummer} kommt mehrfach vor: "
        + "; ".join(_describe_occurrence(occurrence) for occurrence in occurrences)
        + ". Bitte entscheiden Sie manuell, welche Zeile übernommen werden soll."
        for matrikelnummer, occurrences in sorted(duplicates.items())
    ]
    errors.append(
        "Es wurde nichts importiert. Entfernen Sie die doppelten Zeilen oder laden Sie die "
        "Dateien mit „vorhandene Anmeldungen ersetzen“ erneut hoch."
    )
    _raise_validation_errors(
        errors,
        duplicates=[
            {"matrikelnummer": matrikelnummer, "occurrences": occurrences}
            for matrikelnummer, occurrences in sorted(duplicates.items())
        ],
    )


def _consistency_warnings(exam: Exam, parsed: list[ParsedFile]) -> list[str]:
    """§5.3's semester/Termin cross-check — a warning, never a block.

    Two comparisons: the uploaded files against each other (§5.3's own wording), and each file
    against the exam it is being imported into. Both usually mean the wrong file was picked, and
    both are recoverable by the instructor looking at the result, so neither stops the import.

    ``module_title`` is deliberately absent from this check: §4/§5.1 make a per-course difference
    legitimate for a Kombinationsprüfung, so comparing it would produce a warning on correct
    input.
    """
    warnings: list[str] = []
    for label, values in (
        ("Semester", sorted({parsed_file.semester for parsed_file in parsed})),
        ("Termine", sorted({parsed_file.termin for parsed_file in parsed})),
    ):
        if len(values) > 1:
            warnings.append(
                f"Die hochgeladenen Dateien nennen unterschiedliche {label}: "
                + ", ".join(f"„{value}“" for value in values)
                + ". Bitte prüfen Sie, ob alle Dateien zu dieser Prüfung gehören."
            )
    for parsed_file in parsed:
        name = parsed_file.filename or FALLBACK_FILENAME
        if parsed_file.semester != exam.semester:
            warnings.append(
                f"Die Datei „{name}“ nennt das Semester „{parsed_file.semester}“, die Prüfung "
                f"ist aber für „{exam.semester}“ angelegt."
            )
        if parsed_file.termin != exam.termin:
            warnings.append(
                f"Die Datei „{name}“ nennt den Termin „{parsed_file.termin}“, die Prüfung ist "
                f"aber für „{exam.termin}“ angelegt."
            )
    return warnings


def _flagged_warning(parsed: list[ParsedFile]) -> list[str]:
    """§5.3: rows with an unusual ``Kommentar`` are imported *and* flagged, never dropped."""
    flagged = sum(1 for file in parsed for row in file.rows if row.flagged)
    if not flagged:
        return []
    return [
        f"{flagged} Anmeldung(en) haben einen abweichenden Kommentar (nicht "
        f"„{NORMAL_KOMMENTAR}“). Sie wurden importiert und zur Prüfung markiert — bitte "
        "entscheiden Sie je Zeile, ob sie ausgeschlossen werden soll."
    ]


# --------------------------------------------------------------------------------------------
# Routes — import
# --------------------------------------------------------------------------------------------


@router.post(
    "/exams/{exam_id}/registrations/import",
    response_model=RegistrationImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_registrations(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    files: Annotated[list[UploadFile] | None, File()] = None,
    replace_existing: Annotated[bool, Form()] = False,
) -> RegistrationImportResult:
    """Import one or more registration PDFs into an exam (§5.1, §5.3).

    §5.1: one PDF per Studiengang, several PDFs per exam. Every row is tagged with **its own
    file's** ``course_code`` and ``module_title``; the titles are stored verbatim and never
    reconciled between files (§4 — a Kombinationsprüfung is a different official module per
    course, and that difference is data, not noise).

    **The request is atomic.** Every file is parsed, and every §5.3 check runs, before anything
    is written; the first failure rejects the *whole* upload with ``422`` and leaves the database
    exactly as it was. This is stricter than §5.3, which only forbids importing a partial list
    out of a single failed file — see this module's docstring for why the request, not the file,
    is the unit of success. The practical contract for the instructor: fix the file that was
    named and upload the same set again.

    Rejected with ``422``:

    * any file the parser refuses (unreadable, scanned, unknown layout, unreadable header, or —
      §5.3's mandatory checksum — an incomplete ``Nr.`` sequence or a missing page). The response
      names the file and, for the incompleteness case, the missing ``Nr.`` values and pages;
    * any Matrikelnummer occurring more than once across the uploaded rows and the rows already
      stored for this exam. The response lists every competing row (Studiengang, module title,
      source file, and the registration id where one exists) so the UI can ask the instructor
      which to keep. Nothing is merged automatically (§5.3).

    Warnings — returned, never blocking (§5.3): a semester or Termin that differs between files
    or from the exam's own, and the number of rows flagged for an unusual ``Kommentar``.

    ``replace_existing=true`` first deletes this exam's existing registrations **whose
    ``course_code`` appears in the upload**, then imports. It is the "wrong file, let me redo it"
    path, and it is deliberately opt-in: the deleted rows take their ``excluded`` decisions,
    attendance and (from §15.3 on) entered exercise points with them, via the database-level
    cascade. Registrations of other courses are untouched, so replacing one Studiengang's PDF
    never disturbs the others.
    """
    exam = get_owned_exam(db, user, exam_id)
    uploads = [upload for upload in (files or []) if upload is not None]
    if not uploads:
        _raise_validation_errors([NO_FILES_DETAIL])

    parsed: list[ParsedFile] = []
    file_errors: list[dict[str, Any]] = []
    messages: list[str] = []
    for upload in uploads:
        filename = _safe_filename(upload.filename)
        try:
            # Sync ``def`` route on purpose (like every other router here): ``upload.file`` is
            # the plain synchronous file object, and pdfplumber's CPU-bound parse then runs in
            # the threadpool rather than on the event loop.
            parsed.append(parse_registration_pdf(upload.file.read(), filename=filename))
        except PdfImportError as exc:
            file_errors.append(_file_error_payload(filename, exc))
            messages.append(f"„{filename}“: {exc.message}")
    if file_errors:
        if len(uploads) > 1:
            messages.append(
                "Es wurde nichts importiert — bitte laden Sie alle Dateien nach der Korrektur "
                "erneut hoch."
            )
        _raise_validation_errors(messages, files=file_errors)

    existing = _registrations_of(db, exam)
    replaced_course_codes = (
        {parsed_file.course_code for parsed_file in parsed} if replace_existing else set()
    )
    doomed = [
        registration
        for registration in existing
        if registration.course_code in replaced_course_codes
    ]
    survivors = [registration for registration in existing if registration not in doomed]

    occurrences = _duplicate_occurrences(parsed, survivors)
    duplicates = {
        matrikelnummer: places for matrikelnummer, places in occurrences.items() if len(places) > 1
    }
    if duplicates:
        _reject_duplicates(duplicates)

    # Nothing above this line touched the session; nothing below it can fail a §5.3 check. The
    # flush between the deletes and the inserts is what lets a re-upload of the *same* file
    # under ``replace_existing`` reuse its own Matrikelnummern without tripping
    # ``uq_registration_exam_matrikelnummer`` mid-statement.
    for registration in doomed:
        db.delete(registration)
    db.flush()
    for parsed_file in parsed:
        for row in parsed_file.rows:
            db.add(
                StudentRegistration(
                    exam_id=exam.id,
                    matrikelnummer=row.matrikelnummer,
                    nachname=row.nachname,
                    vorname=row.vorname,
                    course_code=parsed_file.course_code,
                    module_title=parsed_file.module_title,
                    versuch=row.versuch,
                    kommentar=row.kommentar,
                    # §5.3: never silently dropped, never silently kept unmarked.
                    flagged=row.flagged,
                    source_filename=parsed_file.filename,
                )
            )
    db.commit()

    return RegistrationImportResult(
        imported_total=sum(len(parsed_file.rows) for parsed_file in parsed),
        replaced_count=len(doomed),
        files=[
            ImportedFileSummary(
                filename=parsed_file.filename or FALLBACK_FILENAME,
                course_code=parsed_file.course_code,
                module_title=parsed_file.module_title,
                semester=parsed_file.semester,
                termin=parsed_file.termin,
                row_count=len(parsed_file.rows),
                flagged_count=sum(1 for row in parsed_file.rows if row.flagged),
                engine=parsed_file.engine,
            )
            for parsed_file in parsed
        ],
        warnings=_consistency_warnings(exam, parsed) + _flagged_warning(parsed),
    )


# --------------------------------------------------------------------------------------------
# Routes — CRUD (§5.3: "instructors can also manually add/edit/remove")
# --------------------------------------------------------------------------------------------


@router.get("/exams/{exam_id}/registrations", response_model=list[RegistrationOut])
def list_registrations(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    course_code: str | None = Query(default=None),
    include_excluded: bool = Query(default=True),
) -> list[RegistrationOut]:
    """The exam's registrations, sorted by Studiengang then last name (§6 collation).

    Excluded students are included **by default**: §5.3 keeps them in the database for audit and
    the instructor has to be able to see and revise the decision. ``include_excluded=false`` is
    the view every consumer that must not see them uses — the attendance list, points entry and
    every report (§5.3), none of which may show an excluded student or ever give them a grade.
    """
    exam = get_owned_exam(db, user, exam_id)
    registrations = _registrations_of(db, exam)
    if course_code is not None:
        registrations = [item for item in registrations if item.course_code == course_code]
    if not include_excluded:
        registrations = [item for item in registrations if not item.excluded]
    return [RegistrationOut.model_validate(item) for item in _sorted_for_display(registrations)]


@router.get("/exams/{exam_id}/registrations/count", response_model=RegistrationHeadCount)
def count_registrations_for_exam(
    exam_id: int, user: CurrentUser, db: DbSession
) -> RegistrationHeadCount:
    """§6's head count: how many exam copies to print, without generating the PDF.

    Excluded students are left out (§5.3), and the per-course breakdown mirrors the attendance
    list's grouping so the instructor can print per Studiengang. Course codes are ordered with
    the same German collation as the list itself (§6).
    """
    exam = get_owned_exam(db, user, exam_id)
    counted = [item for item in _registrations_of(db, exam) if not item.excluded]
    per_course: dict[str, int] = {}
    for item in counted:
        per_course[item.course_code] = per_course.get(item.course_code, 0) + 1
    return RegistrationHeadCount(
        total=len(counted),
        per_course=[
            CourseHeadCount(course_code=course_code, count=per_course[course_code])
            for course_code in sorted(per_course, key=german_sort_key)
        ],
    )


@router.post(
    "/exams/{exam_id}/registrations",
    response_model=RegistrationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_registration(
    exam_id: int, payload: RegistrationCreateRequest, user: CurrentUser, db: DbSession
) -> RegistrationOut:
    """Manually add a registration — §5.3's late registration that never appeared in a PDF.

    ``course_code`` and ``module_title`` are required and taken verbatim from the request: there
    is no source PDF to read them from, and copying them from another course's file would invent
    a module title (§4, §5.1).

    A Matrikelnummer already registered for this exam answers ``409`` — the same "never silently
    merged or duplicated" rule the import enforces (§5.3), applied to a single row. Whether the
    existing row is excluded makes no difference; it still exists.
    """
    exam = get_owned_exam(db, user, exam_id)
    matrikelnummer = payload.matrikelnummer.strip()
    _reject_matrikelnummer_conflict(db, exam.id, matrikelnummer, exclude_id=None)

    kommentar = payload.kommentar.strip() if payload.kommentar is not None else None
    registration = StudentRegistration(
        exam_id=exam.id,
        matrikelnummer=matrikelnummer,
        nachname=payload.nachname.strip(),
        vorname=payload.vorname.strip(),
        course_code=payload.course_code.strip(),
        module_title=payload.module_title.strip(),
        versuch=payload.versuch,
        kommentar=kommentar,
        flagged=_derive_flagged(payload.flagged, kommentar),
        excluded=payload.excluded,
        source_filename=None,
    )
    db.add(registration)
    db.commit()
    db.refresh(registration)
    return RegistrationOut.model_validate(registration)


@router.patch("/registrations/{registration_id}", response_model=RegistrationOut)
def update_registration(
    registration_id: int,
    payload: RegistrationUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> RegistrationOut:
    """Edit one registration, including its ``excluded`` flag.

    Setting ``excluded=true`` is **not** a deletion (§5.3): the row and its source data stay in
    the database for audit, and only its participation in the attendance list, points entry,
    reports and grading ends. Use ``DELETE`` for a row that should never have existed at all.

    ``flagged`` is not touched implicitly when ``kommentar`` changes — an instructor who has
    reviewed a flagged row and wants it unflagged says so explicitly, and a flag §5.3 asked for
    should not disappear as a side effect of fixing a typo.
    """
    registration = get_owned_registration(db, user, registration_id)
    sent = payload.model_fields_set

    if payload.matrikelnummer is not None:
        matrikelnummer = payload.matrikelnummer.strip()
        _reject_matrikelnummer_conflict(
            db, registration.exam_id, matrikelnummer, exclude_id=registration.id
        )
        registration.matrikelnummer = matrikelnummer
    if payload.nachname is not None:
        registration.nachname = payload.nachname.strip()
    if payload.vorname is not None:
        registration.vorname = payload.vorname.strip()
    if payload.course_code is not None:
        registration.course_code = payload.course_code.strip()
    if payload.module_title is not None:
        registration.module_title = payload.module_title.strip()
    if payload.versuch is not None:
        registration.versuch = payload.versuch
    if "kommentar" in sent:
        registration.kommentar = (
            payload.kommentar.strip() if payload.kommentar is not None else None
        )
    if payload.flagged is not None:
        registration.flagged = payload.flagged
    if payload.excluded is not None:
        registration.excluded = payload.excluded
    if "attended" in sent:
        # NULL stays meaningful here (§4): "not yet recorded", which the §8.1 completeness gate
        # must be able to tell apart from an explicit "nicht erschienen".
        registration.attended = payload.attended

    db.commit()
    db.refresh(registration)
    return RegistrationOut.model_validate(registration)


@router.delete("/registrations/{registration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_registration(registration_id: int, user: CurrentUser, db: DbSession) -> None:
    """Really delete a registration — the row added by mistake.

    Distinct from ``excluded`` on purpose (§5.3): excluding keeps the student and the decision
    auditable, deleting removes them and their points outright. Nothing here is a soft delete, so
    a student who should merely not sit the exam must be **excluded**, not deleted.
    """
    db.delete(get_owned_registration(db, user, registration_id))
    db.commit()


@router.delete("/exams/{exam_id}/registrations", status_code=status.HTTP_204_NO_CONTENT)
def delete_all_registrations(
    exam_id: int,
    user: CurrentUser,
    db: DbSession,
    confirm: bool = Query(default=False),
) -> None:
    """Remove every registration of an exam — the "start the import over" reset ("Alle entfernen").

    **This is a real deletion, not an ``excluded`` flip.** §5.3 keeps ``excluded`` as an audit
    flag precisely so a student can be hidden without losing their data; this route is the
    opposite of that — it destroys the rows outright, cascading at the database level to every
    ``ExercisePoints`` row entered against them. Any grade already transcribed for these students
    is gone with no undo, which is why it needs ``?confirm=true`` exactly like ``DELETE
    /api/lectures/{id}`` and ``DELETE /api/exams/{id}``.

    Deletes **all** registrations of the exam, including excluded ones — this is a reset of the
    whole import, not a filtered "delete the visible ones" operation. An exam with zero
    registrations is not an error: the route is idempotent and still returns ``204``.

    One bulk ``DELETE`` statement rather than a Python loop of ``session.delete()`` per row, for
    efficiency on a large registration list; the ``ExercisePoints`` children are removed by the
    ``ON DELETE CASCADE`` on ``exercise_points.registration_id`` (``app/models/registration.py``),
    which fires because ``app/db.py`` sets ``PRAGMA foreign_keys=ON`` on every SQLite connection.
    """
    exam = get_owned_exam(db, user, exam_id)
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=REGISTRATIONS_DELETE_ALL_CONFIRM_DETAIL,
        )
    db.execute(delete(StudentRegistration).where(StudentRegistration.exam_id == exam.id))
    db.commit()


# --------------------------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------------------------


def _derive_flagged(explicit: bool | None, kommentar: str | None) -> bool:
    """§5.3's flag for a manually added row when the request did not set it.

    Mirrors the parser's rule (``app/pdf_import/parser.py``): anything other than the normal
    "(angemeldet)" — including no comment at all being replaced by free text — is flagged for
    review. An empty/absent ``Kommentar`` on a manual add is normal and is not flagged.
    """
    if explicit is not None:
        return explicit
    if not kommentar:
        return False
    return kommentar != NORMAL_KOMMENTAR


def _reject_matrikelnummer_conflict(
    db: Session, exam_id: int, matrikelnummer: str, *, exclude_id: int | None
) -> None:
    """``409`` if this exam already has that Matrikelnummer (§5.3: never silently duplicated)."""
    statement = select(StudentRegistration.id).where(
        StudentRegistration.exam_id == exam_id,
        StudentRegistration.matrikelnummer == matrikelnummer,
    )
    if exclude_id is not None:
        statement = statement.where(StudentRegistration.id != exclude_id)
    if db.execute(statement).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Für diese Prüfung ist die Matrikelnummer {matrikelnummer} bereits "
                "angemeldet. Bitte bearbeiten Sie die vorhandene Anmeldung."
            ),
        )
