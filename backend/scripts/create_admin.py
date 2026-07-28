"""Create an admin account (SPECIFICATION.md §3).

    uv run python -m scripts.create_admin --username <name>
    uv run python scripts/create_admin.py --username <name>

The password is read interactively with :func:`getpass.getpass` and is **never** accepted as a
command-line argument: argv lands in the shell history file and in ``ps`` output for every user
on the box.

This is deliberately a manual, operator-run step rather than an environment-variable-driven
auto-create on startup. An auto-create needs a default password baked into the compose file,
which then survives untouched into production because nothing ever forces anyone to change it.
Bootstrapping happens once per deployment; make it explicit.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Run as a plain file (`python scripts/create_admin.py`), sys.path[0] is scripts/ and `app` is
# not importable. Run as `-m scripts.create_admin`, __package__ is set and this is a no-op.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.auth.passwords import hash_password, validate_password_strength
from app.db import SessionLocal, init_db
from app.models import User


def prompt_for_password() -> str:
    """Ask for the password twice and validate it; ``SystemExit`` on mismatch or policy failure."""
    password = getpass.getpass("Passwort: ")
    confirmation = getpass.getpass("Passwort wiederholen: ")
    if password != confirmation:
        raise SystemExit("Die Passwörter stimmen nicht überein — abgebrochen.")
    errors = validate_password_strength(password)
    if errors:
        raise SystemExit("\n".join(errors))
    return password


def create_admin(username: str, password: str) -> int:
    """Insert the admin account; return its id. ``SystemExit`` if the username is taken."""
    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is not None:
            raise SystemExit(f"Benutzername {username!r} ist bereits vergeben — abgebrochen.")
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Legt ein Administratorkonto für GradingHelper an."
    )
    parser.add_argument("--username", required=True, help="Benutzername des neuen Kontos")
    args = parser.parse_args(argv)

    username: str = args.username.strip()
    if not username:
        raise SystemExit("Der Benutzername darf nicht leer sein.")

    # A fresh deployment starts with an empty volume; create the schema before touching it.
    # (Also binds SessionLocal to the configured engine.)
    init_db()

    password = prompt_for_password()
    user_id = create_admin(username, password)
    print(f"Administratorkonto {username!r} angelegt (id={user_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
