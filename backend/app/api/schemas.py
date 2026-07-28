"""Pydantic v2 request/response models for the HTTP API.

Shared by every route module; later milestones add the Lecture/Exam/registration shapes here.
See ``docs/api-contract-m1.md`` for the authoritative field lists.

Decimal-valued fields cross the wire as JSON **strings** (contract preamble, §7.0). Use the
:data:`DecimalString` annotation for every one of them — see :func:`_parse_decimal_string` for
why a bare ``Decimal`` field would silently defeat §7.0.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_serializer,
)

from app.models import BonusMode


class UserIdentity(BaseModel):
    """The caller's own identity — response of ``/api/auth/login`` and ``/api/auth/me``.

    Deliberately narrower than :class:`UserAccount`: a non-admin has no business receiving the
    account-management fields, and ``is_active`` is trivially true for anyone who can call these.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool


class UserAccount(UserIdentity):
    """Full account record — admin-only responses under ``/api/admin/users``."""

    is_active: bool
    created_at: datetime

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        """Always emit an explicit UTC offset.

        SQLite has no datetime type and hands back a naive value even though a
        ``DateTime(timezone=True)`` column was written with an aware one; without this the
        frontend would receive a bare ``2026-07-27T09:00:00`` and parse it as local time.
        """
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)


class PasswordChangeRequest(BaseModel):
    """Self-service password change (``POST /api/auth/password``)."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class UserCreateRequest(BaseModel):
    """``POST /api/admin/users``."""

    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)
    is_admin: bool = False


class UserUpdateRequest(BaseModel):
    """``PATCH /api/admin/users/{id}`` — omitted fields are left unchanged."""

    is_active: bool | None = None
    is_admin: bool | None = None


class PasswordResetRequest(BaseModel):
    """``POST /api/admin/users/{id}/password`` — admin reset, no current password needed."""

    new_password: str = Field(min_length=1)


class ValidationErrors(BaseModel):
    """Body of a ``422`` carrying German messages meant to be shown verbatim.

    Matches the shape the contract specifies for grading-schema validation
    (``{"detail": {"errors": [...]}}``), reused for the password policy.
    """

    errors: list[str]


# --------------------------------------------------------------------------------------------
# Lectures and exams (§4, §7 — ``docs/api-contract-m1.md`` sections "Lectures" and "Exams")
# --------------------------------------------------------------------------------------------


def _parse_decimal_string(value: Any) -> Decimal:
    """Parse a wire value into an exact :class:`~decimal.Decimal`, or reject it (§7.0).

    The contract says decimal-valued fields cross the wire as JSON **strings**. This validator
    enforces that literally and refuses every JSON *number*:

    * a JSON float has already been through a binary double by the time pydantic sees it
      (``0.1 + 0.2``-class error), which is exactly what §7.0 forbids — accepting it would
      silently undo the whole ``DecimalText`` storage design;
    * a JSON integer is lossless, but accepting it would make "sometimes a number is fine"
      the observed behaviour of the API and invite a client to send ``0.75`` next. Deliberate
      contract narrowing: numbers are refused across the board, with a message that says so.
    * ``bool`` is checked first because ``isinstance(True, int)`` is ``True`` in Python — a
      naive ``int`` guard would happily turn ``true`` into ``Decimal(1)``.

    Also refused: non-finite values (``Decimal("NaN")``/``"Infinity"`` parse happily and would
    poison every later comparison) and exponent notation (``"1E+2"``), which ``str()`` would
    round-trip back to the client verbatim as ``"1E+2"``.

    A :class:`Decimal` passes through unchanged so that response models — which are built from
    already-decoded database values — validate without a pointless string round-trip.
    """
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(DECIMAL_NOT_FINITE_MESSAGE)
        return value
    if isinstance(value, (bool, int, float)):
        raise ValueError(DECIMAL_MUST_BE_STRING_MESSAGE)
    if not isinstance(value, str):
        raise ValueError(DECIMAL_MUST_BE_STRING_MESSAGE)

    text = value.strip()
    if not text:
        raise ValueError(DECIMAL_MUST_BE_STRING_MESSAGE)
    if "e" in text.lower():
        raise ValueError(DECIMAL_NO_EXPONENT_MESSAGE)
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(DECIMAL_MUST_BE_STRING_MESSAGE) from exc
    if not parsed.is_finite():
        raise ValueError(DECIMAL_NOT_FINITE_MESSAGE)
    return parsed


def _serialize_decimal(value: Decimal) -> str:
    """Emit a decimal as its exact string, trailing zeros intact ("12.50" in → "12.50" out)."""
    return str(value)


DECIMAL_MUST_BE_STRING_MESSAGE = (
    "Dezimalwerte müssen als Zeichenkette übertragen werden (z. B. '12.50'), nicht als JSON-Zahl."
)
DECIMAL_NOT_FINITE_MESSAGE = "Dezimalwert muss eine endliche Zahl sein."
DECIMAL_NO_EXPONENT_MESSAGE = (
    "Dezimalwerte dürfen nicht in Exponentialschreibweise angegeben werden."
)

#: A decimal that is a JSON string in both directions. Never declare a points/percentage field
#: as a bare ``Decimal``: pydantic's lax mode would route a JSON float through a binary double.
DecimalString = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal_string),
    PlainSerializer(_serialize_decimal, return_type=str),
]


class ExerciseInput(BaseModel):
    """One exercise in a create/replace payload.

    ``id`` and ``position`` are accepted (the contract's exercise shape carries them, so a
    client can round-trip a detail response straight back) but **ignored**: positions are
    renumbered ``1..N`` server-side in submitted order, and identity is not preserved across a
    full replace.
    """

    name: str
    max_points: DecimalString
    position: int | None = None
    id: int | None = None


class GradeThresholdInput(BaseModel):
    """One grade's required percentage in a create/replace payload (§7.2)."""

    grade: str
    percentage: DecimalString


class ExerciseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    max_points: DecimalString
    position: int


class GradeThresholdOut(BaseModel):
    """A grade's percentage plus the point threshold the backend computed from it (§7.2).

    ``threshold_points`` is authoritative and always recomputed from the exam's current total
    max points — the frontend may preview it, but never sends it.
    """

    grade: str
    percentage: DecimalString
    threshold_points: DecimalString


class RecomputationWarning(BaseModel):
    """§8.1: an exercise/schema edit shifted the grade boundaries under existing student data."""

    changed: bool
    affected_registrations: int


class ExamSummary(BaseModel):
    id: int
    lecture_id: int
    lecture_name: str
    semester: str
    termin: str
    exam_date: date | None
    bonus_mode: BonusMode
    owner_id: int


class ExamDetail(ExamSummary):
    exercises: list[ExerciseOut]
    grading_schema: list[GradeThresholdOut]
    registration_count: int
    #: Sum of all exercises' ``max_points`` — the basis of every ``threshold_points`` above.
    #: Not in the original contract; added so the frontend can preview a schema edit without
    #: re-summing decimal strings itself.
    total_max_points: DecimalString
    #: Non-null only on the PATCH response, and only when the edit actually moved thresholds
    #: while student data exists (§8.1). Not in the original contract — see the report.
    recomputation_warning: RecomputationWarning | None = None


class LectureSummary(BaseModel):
    id: int
    name: str
    created_at: datetime
    exam_count: int

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat()


class LectureDetail(LectureSummary):
    exams: list[ExamSummary]


class LectureCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class LectureUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ExamCreateRequest(BaseModel):
    """``POST /api/lectures/{id}/exams``.

    ``bonus_mode``, ``exercises`` and ``grading_schema`` are copied forward from the lecture's
    most recent prior exam when **absent** (§4). "Absent" is decided via ``model_fields_set``,
    so an explicitly sent ``[]`` (or ``null``) means "start empty" and suppresses the copy.
    """

    semester: str
    termin: str
    exam_date: date | None = None
    bonus_mode: BonusMode | None = None
    exercises: list[ExerciseInput] | None = None
    grading_schema: list[GradeThresholdInput] | None = None


class ExamUpdateRequest(BaseModel):
    """``PATCH /api/exams/{id}`` — omitted fields are left unchanged.

    Present ``exercises``/``grading_schema`` **replace** the whole collection, never merge.
    ``exam_date`` is the one field for which an explicit ``null`` is meaningful (clear the
    date), so presence is again decided via ``model_fields_set``.
    """

    semester: str | None = None
    termin: str | None = None
    exam_date: date | None = None
    bonus_mode: BonusMode | None = None
    owner_id: int | None = None
    exercises: list[ExerciseInput] | None = None
    grading_schema: list[GradeThresholdInput] | None = None


# --------------------------------------------------------------------------------------------
# Student registrations (§5.1, §5.3, §6 — see ``app/api/registrations.py``)
# --------------------------------------------------------------------------------------------


class RegistrationOut(BaseModel):
    """One student registration as the API returns it.

    ``flagged`` and ``excluded`` are both carried on every response: §5.3 requires the UI to
    highlight a row whose ``Kommentar`` is not the normal "(angemeldet)", and to show — rather
    than hide — a student the instructor has excluded, because ``excluded`` is a flag and never
    a deletion.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    exam_id: int
    matrikelnummer: str
    nachname: str
    vorname: str
    course_code: str
    module_title: str
    versuch: int
    kommentar: str | None
    flagged: bool
    excluded: bool
    attended: bool | None
    bonus_points: DecimalString
    source_filename: str | None


class RegistrationCreateRequest(BaseModel):
    """``POST /api/exams/{exam_id}/registrations`` — the manual add of §5.3.

    ``course_code`` and ``module_title`` are **required**: a manually added late registration has
    no source PDF to take them from, and both are per-course data that must not be guessed at
    from another file of the same exam (§4, §5.1 — a Kombinationsprüfung legitimately carries a
    different module name per course).

    ``flagged`` is derived from ``kommentar`` when omitted (anything other than the normal
    "(angemeldet)" — including a free-text remark — flags the row for review, per §5.3), and can
    be set explicitly to override that.
    """

    matrikelnummer: str = Field(min_length=1, max_length=64)
    nachname: str = Field(min_length=1, max_length=255)
    vorname: str = Field(min_length=1, max_length=255)
    course_code: str = Field(min_length=1, max_length=255)
    module_title: str = Field(min_length=1)
    versuch: int = Field(default=1, ge=1)
    kommentar: str | None = None
    flagged: bool | None = None
    excluded: bool = False


class RegistrationUpdateRequest(BaseModel):
    """``PATCH /api/registrations/{id}`` — omitted fields are left unchanged.

    Presence is decided via ``model_fields_set`` because an explicit ``null`` is meaningful for
    ``kommentar`` (clear the remark) and for ``attended`` (§4: ``NULL`` means "not yet
    recorded", which the §8.1 completeness gate must be able to tell apart from an explicit
    ``false``).
    """

    matrikelnummer: str | None = Field(default=None, min_length=1, max_length=64)
    nachname: str | None = Field(default=None, min_length=1, max_length=255)
    vorname: str | None = Field(default=None, min_length=1, max_length=255)
    course_code: str | None = Field(default=None, min_length=1, max_length=255)
    module_title: str | None = Field(default=None, min_length=1)
    versuch: int | None = Field(default=None, ge=1)
    kommentar: str | None = None
    flagged: bool | None = None
    excluded: bool | None = None
    attended: bool | None = None
    bonus_points: DecimalString | None = None


class ImportedFileSummary(BaseModel):
    """What one uploaded PDF contributed to the exam (§5.1: one PDF = one Studiengang).

    ``module_title`` is the source file's title line verbatim and is deliberately *not*
    reconciled with the other files' (§4, §5.1); ``course_code`` is the short parenthetical
    grouping key. ``engine`` names the extraction engine that read the file (§5.2) — pdfplumber
    normally, ``pymupdf`` when the fallback kicked in — so an odd import can be diagnosed
    without re-running the parser.
    """

    filename: str
    course_code: str
    module_title: str
    semester: str
    termin: str
    row_count: int
    flagged_count: int
    engine: str


class RegistrationImportResult(BaseModel):
    """``POST /api/exams/{exam_id}/registrations/import``.

    ``warnings`` are German sentences meant to be shown verbatim; they never block an import
    (§5.3 only makes semester/Termin mismatches a warning). ``replaced_count`` is the number of
    pre-existing registrations ``replace_existing=true`` deleted.
    """

    imported_total: int
    replaced_count: int
    files: list[ImportedFileSummary]
    warnings: list[str]


class CourseHeadCount(BaseModel):
    course_code: str
    count: int


class RegistrationHeadCount(BaseModel):
    """``GET /api/exams/{exam_id}/registrations/count`` — §6's "how many exam copies to print".

    Excluded students are left out of both numbers: §5.3 keeps them in the database for audit
    but omits them from the attendance list, so counting them would over-print.
    """

    total: int
    per_course: list[CourseHeadCount]
