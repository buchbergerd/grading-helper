"""The shared exam-statistics module (SPECIFICATION.md §9).

§9 requires the internal report in two forms — a Typst PDF and an interactive in-app dashboard —
"sharing one backend statistics-computation module so numbers are always consistent between
them". This is that module, and the sharing is literal: :func:`build_exam_statistics` returns
one :class:`ExamStatistics` mapping, ``app/reports/internal_report.py`` hands it to Typst as a
JSON string, and ``GET /api/exams/{id}/statistics`` returns the very same mapping to the
frontend. Neither consumer recomputes anything.

That only holds if the payload leaves nothing for a renderer to compute, so three rules apply to
everything below:

**Every decimal is a canonical string, never a JSON number** (§7.0 and the API contract's
preamble). A JSON number is an IEEE-754 double in every JS client, which would undo the exact
``Decimal`` arithmetic this module does internally at the last possible moment.

**Every rate carries its numerator and denominator.** A renderer must never divide — two
renderers dividing independently is exactly how the PDF and the dashboard come to disagree about
a failure rate. :class:`Rate` therefore ships ``numerator``, ``denominator`` *and* the finished
``percent`` string, already rounded here.

**Every number is rounded here, once.** Mean/median grades to two decimal places, percentages to
one, both ``ROUND_HALF_UP``. If the PDF rounded to two places and the dashboard to one, the two
views would report different numbers from an identical payload — which is the failure §9's
"one module" requirement exists to prevent. Display *labels* (histogram bin captions, Versuch
captions) are likewise built here, in German, for the same reason.

What this module is **not**: it is not gated by the §8.1 completeness check. That gate belongs to
§10/§11 only. §9 is explicitly "a live view over current data, not a static export — it reflects
entered points immediately, useful while grading is still in progress", so a half-graded exam
must produce a valid payload, not an error. What a half-graded exam must *not* do is quietly
report nonsense: a student who attended but is missing two of five exercises has a partial
``raw_total`` that would render as a fake "nicht bestanden". Such students are therefore counted
in :attr:`StatisticsCounts.incomplete` and left out of the grade distribution and the
total-points histogram — while their individually entered exercise points still count towards
that exercise's own histogram, where they are a complete, well-defined fact. A student recorded
as **absent** is the one exception: §7.4 makes their points irrelevant to any grade, so stale
entries left behind by an attendance flip are excluded from the exercise histograms too.

All aggregation happens in Python over decoded ``Decimal`` values. Points are stored as ``TEXT``
(``app/types.py``), so ``ORDER BY``/``SUM()``/``MIN()`` over a points column is a *string*
operation in which ``"10.0" < "9.0"`` — every count, sum, bin edge and extreme below must stay
out of SQL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import TypedDict

from app.formatting import format_german_decimal
from app.grading.engine import check_completeness, compute_grade, passing_threshold_points
from app.grading.schema import GRADES
from app.models import BonusMode, Exam, StudentRegistration

# --------------------------------------------------------------------------------------------
# Payload contract
#
# Frozen deliberately ahead of the implementation and of both renderers: the PDF template, the
# API route and the React dashboard are all written against these shapes. The mirrored
# TypeScript interfaces live in `frontend/src/api/client.ts` and must be kept in step, as must
# the endpoint's entry in `docs/api-contract.md`.
# --------------------------------------------------------------------------------------------


class Rate(TypedDict):
    """A proportion, with the two counts it was derived from (§9's attendance/pass/failure rates).

    ``percent`` is the finished display value — a canonical decimal string rounded to one place,
    e.g. ``"84.6"`` — or ``None`` when ``denominator`` is 0, which is a real state (an exam with
    nothing entered yet) and not an error. A renderer showing ``percent`` must show ``"—"`` for
    ``None``; it must never divide the two counts itself.
    """

    numerator: int
    denominator: int
    percent: str | None


class GradeCount(TypedDict):
    """How many students earned one numeric grade of the §7.1 scale."""

    #: A grade of :data:`~app.grading.schema.GRADES`, e.g. ``"1.3"``.
    grade: str
    count: int


class GradeDistribution(TypedDict):
    """§9's "count per grade, plus mean and median grade among students with a numeric grade".

    ``numeric`` lists **all ten** grades in :data:`~app.grading.schema.GRADES` order (best to
    worst) including those nobody earned: a distribution with holes in it invites a chart with
    missing bars, and the zeros are information.

    ``mean``/``median`` are computed over numeric grades only — students graded "nicht bestanden"
    and "n.e." are excluded by §9's own wording — and are ``None`` when ``numeric_count`` is 0.
    Both are canonical decimal strings already rounded to two places (``ROUND_HALF_UP``); the
    median of an even count is the exact mean of the two middle grades before that rounding.
    """

    numeric: list[GradeCount]
    #: Denominator of ``mean``/``median``: students holding one of the ten numeric grades.
    numeric_count: int
    #: Attended, complete, below the 4.0 threshold — :data:`~app.grading.engine.GRADE_FAILED`.
    failed_count: int
    #: ``attended is False`` — :data:`~app.grading.engine.GRADE_NOT_ATTENDED` (§7.4).
    not_attended_count: int
    mean: str | None
    median: str | None


class HistogramBin(TypedDict):
    """One bar. Half-open ``[lower, upper)``, except the last bin of a histogram, which is closed.

    ``label`` is the finished German caption — both edges with a comma decimal separator, joined
    by an en dash, e.g. ``"12,0"`` to ``"13,0"`` — built here so the PDF and the dashboard cannot
    label the same bar differently.
    """

    lower: str
    upper: str
    label: str
    count: int


class Histogram(TypedDict):
    """A point distribution binned at a fixed width (§9: 1.0 for both totals and exercises).

    Bin edges start at 0 and step by ``bin_width`` up to and including the bin containing
    ``max_observed``. **The upper edge is derived from the observed maximum, not from
    ``reference_max``**: an uncapped ALWAYS bonus (§7.3) pushes ``final_total`` past the exam's
    max points, and §8 warns about — but deliberately does not clamp — an exercise entry above
    its ``max_points``. Deriving the range from ``reference_max`` would drop exactly those
    students off the right-hand edge of the chart without a trace. The range always extends at
    least to ``reference_max`` so an exam nobody did well in still shows its full scale.

    ``included_count`` is the number of students that contributed a value, and is the sum of the
    bins' counts; it is *not* the number of registered students. See this module's docstring for
    which students contribute to which histogram.
    """

    #: German heading: "Gesamtpunkte" or the exercise's name.
    title: str
    bin_width: str
    #: The exam's total max points, or the exercise's ``max_points`` — for the axis scale and for
    #: a "über Maximum" note; never used to compute the bin range.
    reference_max: str
    #: Largest value contributing to this histogram, or ``None`` when nothing contributed.
    max_observed: str | None
    included_count: int
    bins: list[HistogramBin]


class VersuchGroup(TypedDict):
    """§9's pass/fail breakdown for one attempt number.

    §9 calls this "the attempt-tracking requirement's main visible use in the app: instructors
    care most about whether failure rate climbs with attempt number". Groups are emitted in
    ascending numeric ``versuch`` order — and only for attempt numbers that actually occur, with
    no assumption that they are dense or that they stop at 3 (a Freiversuch or an imported
    fourth attempt must not fall out of the report).
    """

    versuch: int
    #: German caption, e.g. ``"1. Versuch"``.
    label: str
    registered: int
    attended: int
    not_attended: int
    attendance_not_recorded: int
    #: Attended **and** complete — the denominator of ``failure_rate``.
    graded: int
    incomplete: int
    #: See :attr:`StatisticsCounts.awaiting_schema`. Repeated per group so the same five-bucket
    #: partition holds within each attempt number as it does exam-wide.
    awaiting_schema: int
    passed: int
    failed: int
    failure_rate: Rate


class StatisticsCounts(TypedDict):
    """The head counts every other section is derived from.

    ``registered`` counts non-excluded registrations only (§5.3: excluded students appear in no
    list, report or head count). ``excluded`` is reported alongside it purely so the dashboard can
    show *why* a total differs from the imported row count.

    ``graded`` — attended and with every exercise entered — is the denominator §9's failure and
    pass rates use, deliberately not ``attended``: while grading is in progress those two differ,
    and dividing by ``attended`` would understate the failure rate by counting not-yet-graded
    students as if they had passed.
    """

    registered: int
    excluded: int
    attended: int
    not_attended: int
    #: ``attended is None`` — nobody has recorded it yet. Distinct from ``not_attended`` (§4).
    attendance_not_recorded: int
    graded: int
    #: Attended but missing at least one exercise entry. Excluded from the grade distribution and
    #: the total-points histogram; both views must show this count so a half-graded distribution
    #: is never mistaken for a final one.
    incomplete: int
    #: Attended, every exercise entered, but the exam has **no** complete ten-grade schema, so no
    #: grade exists to compute (§7.2). Nothing is missing from the *student's* data — the exam's
    #: configuration is what is missing — which is why these are not ``incomplete``. Always 0 when
    #: ``grading_configured`` is ``True``.
    #:
    #: This field exists so that the five student buckets always partition ``registered``::
    #:
    #:     graded + incomplete + awaiting_schema + not_attended + attendance_not_recorded
    #:         == registered
    #:
    #: Without it, an instructor who enters points before configuring the schema — an ordinary
    #: order to work in — sees students vanish from every count on the dashboard.
    awaiting_schema: int
    passed: int
    failed: int


class StatisticsRates(TypedDict):
    """§9's three rates. Each carries its own counts — see :class:`Rate`."""

    #: attended / registered.
    attendance: Rate
    #: passed / graded.
    passing: Rate
    #: failed / graded.
    failure: Rate


class ExamStatistics(TypedDict):
    """Everything §9 needs, for both the PDF and the dashboard. JSON-serialisable throughout.

    ``grading_configured`` is ``False`` when the exam has no complete ten-grade schema (§7.2), in
    which case no grade can be computed for anybody: ``grade_distribution`` is all zeros,
    ``passing_threshold`` is ``None`` and ``counts.graded``/``passed``/``failed`` are 0. The
    attendance figures and the per-exercise histograms remain meaningful and are still computed —
    an instructor may well look at the dashboard before configuring the schema.
    """

    exam_id: int
    lecture_name: str
    semester: str
    termin: str
    #: ``DD.MM.YYYY`` (§14 #6) or ``None``.
    exam_date: str | None
    #: ``DD.MM.YYYY HH:MM``, local time — the PDF states when it was generated. The dashboard is
    #: live and can ignore it.
    generated_at: str
    #: Sum of all exercises' ``max_points``; ``"0"`` for an exam with no exercises yet.
    max_points: str
    #: The exam's :class:`~app.models.exam.BonusMode` value.
    bonus_mode: str
    grading_configured: bool
    #: The §7.2 4.0 point threshold every pass/fail decision here was made against, or ``None``
    #: when ``grading_configured`` is ``False``.
    passing_threshold: str | None
    counts: StatisticsCounts
    rates: StatisticsRates
    grade_distribution: GradeDistribution
    total_points_histogram: Histogram
    #: One per exercise, in the exercises' ``position`` order.
    exercise_histograms: list[Histogram]
    versuch_breakdown: list[VersuchGroup]


# --------------------------------------------------------------------------------------------
# Implementation
#
# Everything below is new. The module docstring and the TypedDicts above are the frozen contract
# this implementation is written against — nothing above this line was changed beyond the imports.
# --------------------------------------------------------------------------------------------

#: §9's suggested default bin widths — "a sensible default, not a hard requirement — make them
#: easy to change during implementation". Module-level constants so a future tweak is one line,
#: and keyword arguments of :func:`build_exam_statistics` below so a caller can override them
#: (e.g. for a very small exam) without touching this module. Kept as two separate constants even
#: though both are currently ``1.0``: they are conceptually independent (a very fine-grained
#: exercise might still warrant a narrower bin than the exam's total) and either could change
#: again on its own.
TOTAL_POINTS_BIN_WIDTH = Decimal("1.0")
#: §9's literal text suggests ``0.5`` here. Changed to ``1.0`` at the user's explicit request
#: (2026-07-29) to match the total-points histogram — §9 itself calls its bin widths "a sensible
#: default, not a hard requirement", so this is a sanctioned deviation, not a bug. Do **not**
#: "fix" this back to ``0.5`` to match the spec text without checking with the user first.
EXERCISE_BIN_WIDTH = Decimal("1.0")

#: Every raw decimal value in the payload (as opposed to a pre-built display ``label``) is
#: rendered with this — Python's own canonical, dot-separated ``Decimal`` string, e.g. ``"29.5"``.
#: This matches the module docstring's "every decimal is a canonical string" and CLAUDE.md's rule
#: that decimals cross the HTTP boundary as JSON strings a client can parse back into a ``Decimal``
#: unambiguously; only the ``label``/``percent`` fields documented as *display* text use German
#: (comma) formatting, via :func:`~app.formatting.format_german_decimal`, because a renderer must
#: never reformat these itself (this module's docstring: "every number is rounded here, once").
def _canonical(value: Decimal) -> str:
    return str(value)


def _format_german_date(value: date | None) -> str | None:
    """``DD.MM.YYYY`` (§14 #6).

    Copied from ``app.reports.attendance_list.format_german_date`` rather than imported: that
    module is a *report*, and reports depend on ``app.statistics`` (see
    ``app/reports/internal_report.py``), not the other way around — importing it from here would
    invert that dependency for the sake of two lines. See ``app/formatting.py``'s docstring for
    the broader note on why the two formatters aren't consolidated in this milestone.
    """
    return None if value is None else f"{value.day:02d}.{value.month:02d}.{value.year:04d}"


def _format_generated_at(value: datetime) -> str:
    """``DD.MM.YYYY HH:MM``, local time — §9's PDF timestamp (this module's docstring)."""
    return (
        f"{value.day:02d}.{value.month:02d}.{value.year:04d} "
        f"{value.hour:02d}:{value.minute:02d}"
    )


def _thresholds_or_none(exam: Exam) -> dict[str, Decimal] | None:
    """The exam's stored per-grade percentages, or ``None`` if the schema is absent/incomplete.

    Mirrors ``app.api.points._thresholds_or_none`` exactly (same check, same reasoning): all ten
    §7.1 grades must be present before any grade can be computed, and an exam may legitimately
    have zero (never configured) or, only transiently mid-edit, some other count. Duplicated
    rather than imported because this module must not depend on ``app.api`` (a core module
    importing an HTTP-layer one would invert the dependency direction the codebase otherwise
    keeps consistently one way).
    """
    if len(exam.grade_thresholds) != len(GRADES):
        return None
    return {threshold.grade: threshold.percentage for threshold in exam.grade_thresholds}


def _rate(numerator: int, denominator: int) -> Rate:
    """One §9 rate, rounded once here (this module's docstring) to one place, ``ROUND_HALF_UP``.

    ``percent`` is a canonical *dot* string, e.g. ``"84.6"`` — matching :class:`Rate`'s own
    docstring example — not a German comma string: it is a number a renderer may still need to
    compare or chart, not finished display text (unlike an :class:`HistogramBin`'s ``label``).
    """
    if denominator == 0:
        return Rate(numerator=numerator, denominator=denominator, percent=None)
    percent = (Decimal(numerator) / Decimal(denominator) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return Rate(numerator=numerator, denominator=denominator, percent=_canonical(percent))


# --------------------------------------------------------------------------------------------
# Per-registration classification
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Outcome:
    """One non-excluded registration's classification — see this module's docstring and the
    class docstring on :class:`ExamStatistics` for the rules this encodes.

    ``bucket`` is one of:

    * ``"attendance_not_recorded"`` — ``attended is None``.
    * ``"not_attended"`` — ``attended is False``.
    * ``"incomplete"`` — attended, but missing at least one exercise entry.
    * ``"graded"`` — attended, complete, and the exam has a full grading schema: a grade was
      actually computed (``grade``/``final_total``/``is_passing`` are meaningful).
    * ``"unclassified"`` — attended and complete, but the exam has **no** grading schema.
      Deliberately distinct from ``"incomplete"``: nothing is missing, there is simply nothing to
      compute yet. Counted in neither ``counts.graded`` nor ``counts.incomplete`` — see
      :func:`build_exam_statistics`'s docstring for why "invent nothing" applies here.
    """

    versuch: int
    attended: bool | None
    bucket: str
    grade: str | None
    final_total: Decimal | None
    is_passing: bool
    #: Exercise id -> entered points, for *this* registration, regardless of ``bucket`` — an
    #: individual exercise entry is "a complete, well-defined fact" (module docstring) even when
    #: the registration as a whole is incomplete, not attended, or ungraded for lack of a schema.
    entered_points: dict[int, Decimal] = field(default_factory=dict)


def _classify(
    registration: StudentRegistration,
    exercise_ids: Sequence[int],
    thresholds: Mapping[str, Decimal] | None,
    max_points: Decimal,
    bonus_mode: BonusMode,
) -> _Outcome:
    """Classify one non-excluded registration (see :class:`_Outcome`).

    :func:`~app.grading.engine.compute_grade` is only ever called for the ``"graded"`` bucket —
    exactly mirroring ``app.api.points.registration_points_row``'s ``if thresholds is not None``
    guard. Calling it with ``thresholds=None`` is not an option: ``compute_grade`` computes the
    4.0 point threshold unconditionally, before even looking at ``attended``, so a missing/
    incomplete schema makes it raise ``ValueError`` regardless of attendance — grade computation
    must be skipped entirely, not attempted and discarded.
    """
    entered = {points.exercise_id: points.points for points in registration.exercise_points}
    completeness = check_completeness(
        attended=registration.attended,
        exercise_ids=exercise_ids,
        entered_exercise_ids=entered.keys(),
    )

    bucket: str
    if registration.attended is None:
        bucket = "attendance_not_recorded"
    elif registration.attended is False:
        bucket = "not_attended"
    elif not completeness.is_complete:
        bucket = "incomplete"
    elif thresholds is None:
        bucket = "unclassified"
    else:
        bucket = "graded"

    grade: str | None = None
    final_total: Decimal | None = None
    is_passing = False
    if bucket == "graded":
        assert thresholds is not None  # for mypy; guaranteed by the branch above
        entered_in_order = [
            entered[exercise_id] for exercise_id in exercise_ids if exercise_id in entered
        ]
        result = compute_grade(
            exercise_points=entered_in_order,
            bonus_points=registration.bonus_points,
            attended=True,
            bonus_mode=bonus_mode,
            thresholds=thresholds,
            max_points=max_points,
        )
        grade = result.grade
        final_total = result.final_total
        is_passing = result.is_passing

    return _Outcome(
        versuch=registration.versuch,
        attended=registration.attended,
        bucket=bucket,
        grade=grade,
        final_total=final_total,
        is_passing=is_passing,
        entered_points=entered,
    )


@dataclass(frozen=True)
class _Counts:
    """The head counts every :class:`StatisticsCounts`/:class:`VersuchGroup` shares.

    ``graded``, ``incomplete``, ``awaiting_schema``, ``not_attended`` and
    ``attendance_not_recorded`` are mutually exclusive and together account for every non-excluded
    registration — see :attr:`StatisticsCounts.awaiting_schema`. ``attended`` overlaps them (it
    spans ``graded``, ``incomplete`` and ``awaiting_schema``) and ``passed``/``failed`` subdivide
    ``graded``; neither belongs in the partition.
    """

    attended: int
    not_attended: int
    attendance_not_recorded: int
    graded: int
    incomplete: int
    awaiting_schema: int
    passed: int
    failed: int


def _count(outcomes: Sequence[_Outcome]) -> _Counts:
    """Reduce a group of :class:`_Outcome` to :class:`_Counts`. Used for both the whole exam and
    each §9 Versuch group, so the two can never disagree about how a bucket is counted.
    """
    attended = sum(1 for outcome in outcomes if outcome.attended is True)
    not_attended = sum(1 for outcome in outcomes if outcome.bucket == "not_attended")
    attendance_not_recorded = sum(
        1 for outcome in outcomes if outcome.bucket == "attendance_not_recorded"
    )
    incomplete = sum(1 for outcome in outcomes if outcome.bucket == "incomplete")
    awaiting_schema = sum(1 for outcome in outcomes if outcome.bucket == "unclassified")
    passed = sum(1 for outcome in outcomes if outcome.bucket == "graded" and outcome.is_passing)
    failed = sum(
        1 for outcome in outcomes if outcome.bucket == "graded" and not outcome.is_passing
    )
    return _Counts(
        attended=attended,
        not_attended=not_attended,
        attendance_not_recorded=attendance_not_recorded,
        graded=passed + failed,
        incomplete=incomplete,
        awaiting_schema=awaiting_schema,
        passed=passed,
        failed=failed,
    )


def _grade_distribution(outcomes: Sequence[_Outcome]) -> GradeDistribution:
    """§9's "count per grade, plus mean and median grade among students with a numeric grade".

    Only ``"graded"``-bucket outcomes with ``is_passing`` contribute a numeric grade; a
    ``"graded"``-bucket outcome that is *not* passing contributes to ``failed_count`` instead, and
    a ``"not_attended"`` outcome to ``not_attended_count`` — matching :class:`GradeDistribution`'s
    own docstring, which excludes both from ``mean``/``median``.
    """
    numeric_counts: dict[str, int] = dict.fromkeys(GRADES, 0)
    failed_count = 0
    not_attended_count = 0
    numeric_values: list[Decimal] = []

    for outcome in outcomes:
        if outcome.bucket == "not_attended":
            not_attended_count += 1
        elif outcome.bucket == "graded":
            if outcome.is_passing:
                assert outcome.grade is not None
                numeric_counts[outcome.grade] += 1
                numeric_values.append(Decimal(outcome.grade))
            else:
                failed_count += 1

    numeric_values.sort()
    numeric_count = len(numeric_values)
    mean: str | None
    median: str | None
    if numeric_count == 0:
        mean = None
        median = None
    else:
        mean_raw = sum(numeric_values, Decimal(0)) / numeric_count
        mid = numeric_count // 2
        if numeric_count % 2 == 1:
            median_raw = numeric_values[mid]
        else:
            # Exact mean of the two middle values *before* rounding (this module's docstring).
            median_raw = (numeric_values[mid - 1] + numeric_values[mid]) / 2
        mean = _canonical(mean_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        median = _canonical(median_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return GradeDistribution(
        numeric=[GradeCount(grade=grade, count=numeric_counts[grade]) for grade in GRADES],
        numeric_count=numeric_count,
        failed_count=failed_count,
        not_attended_count=not_attended_count,
        mean=mean,
        median=median,
    )


# --------------------------------------------------------------------------------------------
# Histograms
# --------------------------------------------------------------------------------------------


def _build_histogram(
    title: str, values: Sequence[Decimal], reference_max: Decimal, bin_width: Decimal
) -> Histogram:
    """One §9 histogram — see :class:`Histogram`'s docstring for the range/closing rules.

    Bins are half-open ``[lower, upper)`` except the last, which is closed, achieved by computing
    each value's bin index as ``floor(value / bin_width)`` and clamping it to the last index
    rather than special-casing the top edge — a value exactly on an interior boundary opens the
    next bin (``floor`` puts it there directly), and a value at or above the top edge collapses
    into the final, closed bin by the same clamp.

    A negative value (not expected, but must not crash) is clamped to ``Decimal(0)`` before that
    same index computation, which places it in the first bin without a special case either.
    """
    max_observed = max(values) if values else None

    if max_observed is None and reference_max <= 0:
        # The one documented empty case: zero contributors and nothing to scale an axis to.
        return Histogram(
            title=title,
            bin_width=_canonical(bin_width),
            reference_max=_canonical(reference_max),
            max_observed=None,
            included_count=0,
            bins=[],
        )

    range_max = reference_max
    if max_observed is not None and max_observed > range_max:
        range_max = max_observed
    if range_max <= 0:
        # Only reachable if reference_max <= 0 and every observed value is <= 0 too (e.g. a
        # degenerate 0-point exercise with a stray zero entry) — still show one bin rather than
        # none, since there *is* a contributor.
        range_max = bin_width

    num_bins = max(
        1, int((range_max / bin_width).to_integral_value(rounding=ROUND_CEILING))
    )

    counts = [0] * num_bins
    for value in values:
        clamped = value if value >= 0 else Decimal(0)
        index = int((clamped / bin_width).to_integral_value(rounding=ROUND_FLOOR))
        index = max(0, min(index, num_bins - 1))
        counts[index] += 1

    bins: list[HistogramBin] = []
    for index in range(num_bins):
        lower = bin_width * index
        upper = bin_width * (index + 1)
        lower_label = format_german_decimal(lower, places=1)
        upper_label = format_german_decimal(upper, places=1)
        bins.append(
            HistogramBin(
                lower=_canonical(lower),
                upper=_canonical(upper),
                label=f"{lower_label}–{upper_label}",  # noqa: RUF001 -- EN DASH is the label's data, not a typo
                count=counts[index],
            )
        )

    return Histogram(
        title=title,
        bin_width=_canonical(bin_width),
        reference_max=_canonical(reference_max),
        max_observed=_canonical(max_observed) if max_observed is not None else None,
        included_count=len(values),
        bins=bins,
    )


def _versuch_breakdown(outcomes: Sequence[_Outcome]) -> list[VersuchGroup]:
    """§9's pass/fail-by-attempt breakdown — see :class:`VersuchGroup`'s docstring.

    Groups are emitted only for attempt numbers that actually occur, ascending, with no assumption
    of density or a cap at 3 (a ``dict`` keyed by the raw ``versuch`` int and ``sorted()`` over its
    keys naturally has both properties — nothing here special-cases "the usual" 1-3 range).
    """
    groups: dict[int, list[_Outcome]] = defaultdict(list)
    for outcome in outcomes:
        groups[outcome.versuch].append(outcome)

    result: list[VersuchGroup] = []
    for versuch in sorted(groups):
        group = groups[versuch]
        counts = _count(group)
        result.append(
            VersuchGroup(
                versuch=versuch,
                label=f"{versuch}. Versuch",
                registered=len(group),
                attended=counts.attended,
                not_attended=counts.not_attended,
                attendance_not_recorded=counts.attendance_not_recorded,
                graded=counts.graded,
                incomplete=counts.incomplete,
                awaiting_schema=counts.awaiting_schema,
                passed=counts.passed,
                failed=counts.failed,
                failure_rate=_rate(counts.failed, counts.graded),
            )
        )
    return result


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def build_exam_statistics(
    exam: Exam,
    *,
    now: datetime | None = None,
    total_points_bin_width: Decimal = TOTAL_POINTS_BIN_WIDTH,
    exercise_bin_width: Decimal = EXERCISE_BIN_WIDTH,
) -> ExamStatistics:
    """Build §9's full statistics payload for one exam — see this module's docstring.

    A pure function of the already-loaded ``exam`` ORM object: it reads ``exam.registrations`` and
    ``exam.exercises`` (and their own relationships) and issues no query of its own, exactly like
    ``app.reports.attendance_list.build_attendance_list_data``. Safe to call at any point during
    grading — there is no §8.1 completeness gate here, only the per-registration classification
    documented on :class:`ExamStatistics` and this module's own docstring.

    ``grading_configured`` is ``False`` when the exam's grading schema is absent or incomplete
    (:func:`_thresholds_or_none`). Every registration that is attended and complete then falls
    into :attr:`StatisticsCounts.awaiting_schema` — see :class:`_Outcome`'s ``"unclassified"``
    bucket — rather than into ``graded`` or ``incomplete``: there is no grade to compute and
    nothing missing from the student's data, so neither of those would be true. It is a bucket of
    its own precisely so the five buckets still partition ``counts.registered``; an instructor who
    enters points before configuring the schema must not watch students disappear from the counts.
    """
    exercises = list(exam.exercises)  # already position-ordered (Exam.exercises' relationship).
    exercise_ids = [exercise.id for exercise in exercises]
    max_points = sum((exercise.max_points for exercise in exercises), Decimal(0))
    thresholds = _thresholds_or_none(exam)

    all_registrations = list(exam.registrations)
    registrations = [
        registration for registration in all_registrations if not registration.excluded
    ]
    excluded_count = sum(1 for registration in all_registrations if registration.excluded)

    outcomes = [
        _classify(registration, exercise_ids, thresholds, max_points, exam.bonus_mode)
        for registration in registrations
    ]

    overall = _count(outcomes)
    counts = StatisticsCounts(
        registered=len(registrations),
        excluded=excluded_count,
        attended=overall.attended,
        not_attended=overall.not_attended,
        attendance_not_recorded=overall.attendance_not_recorded,
        graded=overall.graded,
        incomplete=overall.incomplete,
        awaiting_schema=overall.awaiting_schema,
        passed=overall.passed,
        failed=overall.failed,
    )

    rates = StatisticsRates(
        attendance=_rate(overall.attended, len(registrations)),
        passing=_rate(overall.passed, overall.graded),
        failure=_rate(overall.failed, overall.graded),
    )

    grade_distribution = _grade_distribution(outcomes)

    total_points_values = [
        outcome.final_total
        for outcome in outcomes
        if outcome.bucket == "graded" and outcome.final_total is not None
    ]
    total_points_histogram = _build_histogram(
        "Gesamtpunkte", total_points_values, max_points, total_points_bin_width
    )

    exercise_histograms = [
        _build_histogram(
            exercise.name,
            [
                outcome.entered_points[exercise.id]
                for outcome in outcomes
                # Everyone whose points could still count towards a grade: graded, incomplete, and
                # attendance-not-yet-recorded (entering points before ticking attendance is an
                # ordinary way to work). A student recorded as **absent** is excluded even when
                # stale points are still stored against them — §7.4 makes those points play no
                # part in any grade, so counting them would inflate the exercise's distribution
                # with data describing nobody who sat the exam. This state is reachable by design:
                # `docs/api-contract.md` guarantees that flipping `attended` to false keeps
                # previously entered points, so the instructor need not re-transcribe them.
                if outcome.attended is not False and exercise.id in outcome.entered_points
            ],
            exercise.max_points,
            exercise_bin_width,
        )
        for exercise in exercises
    ]

    versuch_breakdown = _versuch_breakdown(outcomes)

    passing_threshold: str | None = None
    if thresholds is not None:
        passing_threshold = _canonical(passing_threshold_points(thresholds, max_points))

    return ExamStatistics(
        exam_id=exam.id,
        lecture_name=exam.lecture.name,
        semester=exam.semester,
        termin=exam.termin,
        exam_date=_format_german_date(exam.exam_date),
        generated_at=_format_generated_at(now if now is not None else datetime.now()),
        max_points=_canonical(max_points),
        bonus_mode=exam.bonus_mode.value,
        grading_configured=thresholds is not None,
        passing_threshold=passing_threshold,
        counts=counts,
        rates=rates,
        grade_distribution=grade_distribution,
        total_points_histogram=total_points_histogram,
        exercise_histograms=exercise_histograms,
        versuch_breakdown=versuch_breakdown,
    )
