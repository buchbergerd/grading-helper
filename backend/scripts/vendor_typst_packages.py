"""Fetch the Typst drawing packages the §9 report template imports.

SPECIFICATION.md §12 chose `cetz` + `cetz-plot` for charts in PDFs and §13 requires the running
app to make **no outbound network call**. Typst would otherwise fetch an `@preview` package from
its registry the first time a document imports one — during a report generation, on a machine
that by policy has no internet. This script resolves that by fetching the packages **ahead of
time**, into a local tree that ``app/reports/internal_report.py`` hands to Typst via
``package_path``. Typst then never looks at the registry at all.

Run it once after ``uv sync``, and again only if the pinned versions below change::

    cd backend && uv run python scripts/vendor_typst_packages.py

In the Docker image this runs as a **build** step (see ``deploy/Dockerfile``), so the finished
image contains the packages and the container needs no network to render a report.

The downloaded tree is deliberately **not** committed: it is third-party LGPL-3.0 source, and this
repository is otherwise proprietary first-party code. Downloading at build time is also what the
user asked for — the offline constraint is about never *uploading* exam data (names,
Matrikelnummern) to any external service, which nothing here does. Fetching a public package while
building an image is a different act entirely, and it happens on a build machine, not on the
department server holding student data.

Each archive is pinned by version **and** verified against a recorded SHA-256 before it is
unpacked, so a compromised or silently re-published upstream archive fails loudly instead of
executing as part of a report render.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

#: Where Typst expects to find them: ``<root>/<namespace>/<name>/<version>``. Kept inside the
#: ``app`` package so a wheel/`COPY` of the backend carries it, and so tests find it with no
#: environment configuration.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "app" / "reports" / "typst_packages"

#: Typst's public package registry. Read-only, and contacted by this script alone — never by the
#: running application.
BASE_URL = "https://packages.typst.org/preview"

DOWNLOAD_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Package:
    """One pinned package. ``sha256`` is of the downloaded ``.tar.gz``, before extraction."""

    name: str
    version: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.name}-{self.version}.tar.gz"

    @property
    def destination(self) -> Path:
        return PACKAGE_ROOT / "preview" / self.name / self.version


#: Bump a version here and update its checksum together — never one without the other. The
#: template's ``#import "@preview/cetz:..."`` lines must be updated to match in the same commit,
#: or the render fails at compile time (which is the desired behaviour: a loud failure).
PACKAGES = (
    Package(
        name="cetz",
        version="0.3.4",
        sha256="4f4b5a8d311d519e749940a766fe50521e40c041129e1c91af0c42e61f307514",
    ),
    Package(
        name="cetz-plot",
        version="0.1.1",
        sha256="f057107e2c4bbfc92e2494054a22c90e146a22e0136ee04f68a7728347685d75",
    ),
)

#: Dropped after extraction: example galleries and their build artefacts are a large share of the
#: tree and nothing imports them. The LICENSE and README stay — removing them while redistributing
#: LGPL-3.0 source inside an image would strip the attribution the licence requires.
PRUNED_DIRECTORIES = ("gallery", "tests")


def download(package: Package) -> bytes:
    """Fetch one archive and verify its checksum. Raises ``SystemExit`` with a usable message."""
    try:
        with urllib.request.urlopen(package.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            payload: bytes = response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise SystemExit(
            f"Could not download {package.name} {package.version} from {package.url}: {error}\n"
            "This script needs network access; the running application never does."
        ) from error

    digest = hashlib.sha256(payload).hexdigest()
    if digest != package.sha256:
        raise SystemExit(
            f"Checksum mismatch for {package.name} {package.version}.\n"
            f"  expected {package.sha256}\n"
            f"  got      {digest}\n"
            "Refusing to unpack. If the version was intentionally re-published, verify the new "
            "archive by hand before updating the pin in this file."
        )
    return payload


def extract(package: Package, payload: bytes) -> None:
    """Replace the destination with the archive's contents, then prune what nothing imports."""
    if package.destination.exists():
        shutil.rmtree(package.destination)
    package.destination.mkdir(parents=True)

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        # ``filter="data"`` refuses absolute paths, parent-directory escapes, symlinks and device
        # nodes — an archive cannot write outside the destination. Python 3.12 supports it; it
        # becomes the default in 3.14, so this is explicit rather than version-dependent.
        archive.extractall(package.destination, filter="data")

    for name in PRUNED_DIRECTORIES:
        shutil.rmtree(package.destination / name, ignore_errors=True)


def main() -> int:
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)
    for package in PACKAGES:
        print(f"Fetching {package.name} {package.version} …", flush=True)
        extract(package, download(package))
        print(f"  → {package.destination}")
    print(f"\nDone. {len(PACKAGES)} packages vendored under {PACKAGE_ROOT}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
