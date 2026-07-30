"""Fill an exam's registrations with random attendance/points/bonus for manual testing.

    uv run python -m scripts.generate_demo_data
    uv run python scripts/generate_demo_data.py --exam-id 1 --seed 42

Picks the exam to fill via ``--exam-id``, or the single exam in the database if there is exactly
one. For every non-excluded registration (§5.3 — excluded registrations never receive points, so
they are left untouched) it randomly assigns:

* **attendance** — mostly ``True``, a few ``False`` ("n.e.", §7.4), a few left ``None`` ("not yet
  recorded", §4) so the §8.1 completeness gate has something to actually block on;
* **points per exercise** — a per-student "ability" drives all of their exercise scores, quantized
  to the nearest 0.5 like a real transcription would be, occasionally exceeding ``max_points`` to
  exercise the over-max warning path (``app/api/points.py::_apply_points_save``); attended
  students occasionally miss one exercise on purpose, again for the completeness gate;
* **bonus points** — a minority of students get a small bonus (§7.3).

This is a throwaway local-data generator, not a fixture: nothing it writes is committed, and it
is not deterministic unless ``--seed`` is given (see `test_data/README.md` for the *committed*,
byte-reproducible synthetic PDFs this repo does check in — a different concern from this script).
"""

from __future__ import annotations

import argparse
import random
import sys
from decimal import Decimal
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.points import exam_completeness
from app.db import SessionLocal, init_db
from app.models import Exam, Exercise, ExercisePoints, StudentRegistration

HALF = Decimal("0.5")

# Tunable but deliberately not exposed as CLI flags (CLAUDE.md: no knobs beyond what's needed) —
# adjust here if a different demo shape is ever needed.
ATTENDED_RATE = 0.85
NOT_ATTENDED_RATE = 0.10
# remainder (0.05) is left as attendance-not-recorded (None)
INCOMPLETE_RATE = 0.10  # fraction of attended students missing one random exercise's points
OVERFLOW_RATE = 0.05  # fraction of entered exercise scores that deliberately exceed max_points
BONUS_RATE = 0.35


def quantize_to_half(raw: float, cap: Decimal) -> Decimal:
    """Round ``raw`` to the nearest 0.5, clamped to ``[0, cap]``."""
    steps = round(raw / 0.5)
    value = Decimal(steps) * HALF
    return max(Decimal(0), min(value, cap))


def pick_exam(db: Session, exam_id: int | None) -> Exam:
    if exam_id is not None:
        exam = db.get(Exam, exam_id)
        if exam is None:
            raise SystemExit(f"Keine Prüfung mit id={exam_id} gefunden.")
        return exam

    exams = db.execute(select(Exam)).scalars().all()
    if not exams:
        raise SystemExit("Keine Prüfung in der Datenbank gefunden.")
    if len(exams) > 1:
        options = ", ".join(f"{exam.id} ({exam.semester}, {exam.termin})" for exam in exams)
        raise SystemExit(
            f"Mehrere Prüfungen gefunden — bitte --exam-id angeben. Verfügbar: {options}"
        )
    return exams[0]


def fill_registration(
    registration: StudentRegistration, exercises: list[Exercise], rng: random.Random
) -> None:
    """Randomly assign attendance, points and bonus to one non-excluded registration in place."""
    registration.exercise_points.clear()

    roll = rng.random()
    if roll < NOT_ATTENDED_RATE:
        registration.attended = False
        registration.bonus_points = Decimal(0)
        return
    if roll < NOT_ATTENDED_RATE + (1 - ATTENDED_RATE - NOT_ATTENDED_RATE):
        registration.attended = None
        registration.bonus_points = Decimal(0)
        return

    registration.attended = True
    ability = rng.uniform(0.35, 0.95)
    skip_exercise_id = rng.choice(exercises).id if rng.random() < INCOMPLETE_RATE else None
    for exercise in exercises:
        if exercise.id == skip_exercise_id:
            continue
        max_points = exercise.max_points
        noise = rng.gauss(0, float(max_points) * 0.15)
        raw = float(max_points) * ability + noise
        cap = max_points * (Decimal("1.1") if rng.random() < OVERFLOW_RATE else Decimal(1))
        points = quantize_to_half(raw, cap)
        registration.exercise_points.append(ExercisePoints(exercise_id=exercise.id, points=points))

    registration.bonus_points = (
        quantize_to_half(rng.uniform(0.5, 3.0), Decimal(10))
        if rng.random() < BONUS_RATE
        else Decimal(0)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Füllt eine Prüfung mit zufälligen Anwesenheits-/Punktedaten zum Testen."
    )
    parser.add_argument("--exam-id", type=int, default=None, help="ID der zu füllenden Prüfung")
    parser.add_argument(
        "--seed", type=int, default=None, help="Zufalls-Seed für Reproduzierbarkeit"
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)

    init_db()
    with SessionLocal() as db:
        exam = pick_exam(db, args.exam_id)
        exercises = list(exam.exercises)
        if not exercises:
            raise SystemExit(f"Prüfung {exam.id} hat keine Aufgaben — nichts zu befüllen.")

        registrations = [item for item in exam.registrations if not item.excluded]
        if not registrations:
            raise SystemExit(f"Prüfung {exam.id} hat keine (nicht ausgeschlossenen) Anmeldungen.")

        for registration in registrations:
            fill_registration(registration, exercises, rng)
        db.commit()

        db.refresh(exam)
        completeness = exam_completeness(exam)
        print(
            f"Prüfung {exam.id} ({exam.semester}, {exam.termin}): "
            f"{len(registrations)} Anmeldungen befüllt."
        )
        status = "vollständig" if completeness.is_complete else "unvollständig"
        print(f"Vollständigkeit (§8.1): {status} ({completeness.incomplete_count} unvollständig)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
