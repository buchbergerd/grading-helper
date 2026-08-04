"""Guards the repo README's versioning policy: one semantic version for the whole app.

``backend/pyproject.toml`` and ``frontend/package.json`` are two independent files with no
tooling link between them — nothing stops one from being bumped without the other except this
test. The frontend footer (``frontend/src/components/Footer.tsx``) only ever reads its own
``package.json`` copy (baked in at build time by ``frontend/vite.config.ts``), so a drift here
would silently show a version that no longer matches the backend actually running behind it.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_frontend_and_backend_versions_match() -> None:
    backend_pyproject = tomllib.loads((_REPO_ROOT / "backend" / "pyproject.toml").read_text())
    frontend_package_json = json.loads((_REPO_ROOT / "frontend" / "package.json").read_text())

    backend_version = backend_pyproject["project"]["version"]
    frontend_version = frontend_package_json["version"]

    assert backend_version == frontend_version, (
        f"backend/pyproject.toml ({backend_version}) and frontend/package.json "
        f"({frontend_version}) must carry the same version — see README.md#versioning."
    )
