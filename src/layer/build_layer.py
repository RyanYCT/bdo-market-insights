"""Cross-platform SAM build for the bdo-common Lambda layer.

``Metadata.BuildMethod: makefile`` runs ``make build-CommonLayer`` (see the
Makefile), which calls this script with ``ARTIFACTS_DIR`` as ``argv[1]``. It
populates ``{ARTIFACTS_DIR}/python`` with both:

1. the hand-authored ``bdo_common`` package, and
2. the runtime dependencies from ``requirements.txt``,

so ``/opt/python/bdo_common`` and the deps are importable at runtime.

The previous ``BuildMethod: python3.12`` installed only ``requirements.txt`` and
did **not** carry ``bdo_common`` into the layer, so every function failed at
init with ``No module named 'bdo_common'``. Everything this needs lives inside
the layer's ContentUri, so it works from SAM's scratch build copy.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - build-time pip invocation only (fixed argv, no shell, no user input)
import sys
from pathlib import Path

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: build_layer.py <artifacts_dir>")

    here = Path(__file__).resolve().parent
    python_dir = Path(sys.argv[1]).resolve() / "python"
    python_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        here / "python" / "bdo_common",
        python_dir / "bdo_common",
        dirs_exist_ok=True,
        ignore=_IGNORE,
    )
    subprocess.run(  # noqa: S603  # nosec B603 - fixed argv (sys.executable + repo-internal paths), no shell, no user input
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(here / "requirements.txt"),
            "-t",
            str(python_dir),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
