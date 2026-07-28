"""Small helpers shared by the model modules."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC 'now'.

    Used as a Python-side column default instead of ``func.now()``: SQLite's
    ``CURRENT_TIMESTAMP`` produces a naive string in local-ish time, which would make
    ``expires_at`` comparisons in the session layer (§3) subtly wrong.
    """
    return datetime.now(UTC)
