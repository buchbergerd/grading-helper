"""SQLAlchemy ORM models (SPECIFICATION.md §4).

Importing this package registers every mapper with ``app.db.Base.metadata``.
"""

from app.models.common import utcnow
from app.models.exam import BonusMode, Exam, Exercise, GradeThreshold
from app.models.lecture import Lecture
from app.models.registration import ExercisePoints, StudentRegistration
from app.models.user import User, UserSession

__all__ = [
    "BonusMode",
    "Exam",
    "Exercise",
    "ExercisePoints",
    "GradeThreshold",
    "Lecture",
    "StudentRegistration",
    "User",
    "UserSession",
    "utcnow",
]
