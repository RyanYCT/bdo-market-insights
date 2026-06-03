"""Cross-platform SAM custom build for the migrator Lambda.

``Metadata.BuildMethod: makefile`` runs ``make build-MigratorFunction`` (see the
Makefile), which calls this script with ``ARTIFACTS_DIR`` as ``argv[1]``. It
reproduces the previous POSIX recipe (mkdir / cp / cp -R / pip install) in
Python so the build runs identically on Windows, macOS and Linux.

It locates the repo-root ``migrations/`` tree by walking up from
``ARTIFACTS_DIR`` (always ``<repo>/.aws-sam/build/.../MigratorFunction``) rather
than a CWD-relative path: SAM runs this recipe from a scratch *copy* of the
function directory, so a ``../../../migrations`` path would not resolve.

psycopg v3 ships in the shared bdo-common layer (ADR-0003), so only the Alembic
engine (``requirements.txt``) is installed here.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - build-time pip invocation only (fixed argv, no shell, no user input)
import sys
from pathlib import Path

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def _find_repo_root(start: Path) -> Path:
    """Walk up from ARTIFACTS_DIR to the repo root (has template.yaml + migrations/)."""
    for candidate in (start, *start.parents):
        if (candidate / "template.yaml").is_file() and (candidate / "migrations").is_dir():
            return candidate
    raise FileNotFoundError(f"repo root (template.yaml + migrations/) not found above {start}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: build.py <artifacts_dir>")

    here = Path(__file__).resolve().parent
    artifacts = Path(sys.argv[1]).resolve()
    repo_root = _find_repo_root(artifacts)

    artifacts.mkdir(parents=True, exist_ok=True)
    shutil.copy(here / "app.py", artifacts / "app.py")
    shutil.copytree(
        repo_root / "migrations",
        artifacts / "migrations",
        dirs_exist_ok=True,
        ignore=_IGNORE,
    )
    # Install for the Lambda runtime target (Amazon Linux x86_64, cp312), not
    # the build host -- otherwise a Windows/macOS build pulls host-native wheels
    # (e.g. greenlet) whose binaries Lambda's Linux runtime cannot load.
    subprocess.run(  # noqa: S603  # nosec B603 - fixed argv (sys.executable + repo-internal paths), no shell, no user input
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(here / "requirements.txt"),
            "--target",
            str(artifacts),
            "--platform",
            "manylinux2014_x86_64",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--only-binary=:all:",
            "--upgrade",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
