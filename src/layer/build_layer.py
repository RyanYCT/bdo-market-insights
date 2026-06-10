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

# Top-level packages that MUST exist under the layer after a successful build.
# pip can exit 0 having vendored nothing usable (e.g. when --target points at a
# filesystem it cannot write to, or a wheel resolution that drops deps), which
# silently publishes a source-only layer and breaks every function at init with
# "No module named 'aws_lambda_powertools'". Asserting here fails the build
# loudly instead of shipping a broken layer.
_REQUIRED_PACKAGES = (
    "bdo_common",  # hand-authored source (copytree step)
    "aws_lambda_powertools",  # aws-lambda-powertools[tracer]
    "pydantic",
    "pydantic_core",  # native wheel; the cross-platform canary
    "psycopg",  # psycopg[binary]
)


def _verify_layer(python_dir: Path) -> None:
    """Fail the build if any required package is absent from the layer."""
    missing = [pkg for pkg in _REQUIRED_PACKAGES if not (python_dir / pkg).is_dir()]
    if missing:
        raise SystemExit(
            "Layer build is incomplete - missing package(s): "
            + ", ".join(missing)
            + f"\nunder {python_dir}\n"
            "Refusing to produce a layer without its runtime dependencies. "
            "Build on a native Linux filesystem (not a Windows-mounted /mnt/* "
            "path) and ensure pip could write to the target directory."
        )


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
    # Install for the Lambda runtime target (Amazon Linux x86_64, cp312), not
    # the build host. Without this, a Windows/macOS build pulls native wheels
    # (pydantic_core, psycopg_binary) whose binaries Lambda's Linux runtime
    # cannot load -> "No module named 'pydantic_core._pydantic_core'".
    subprocess.run(  # noqa: S603  # nosec B603 - fixed argv (sys.executable + repo-internal paths), no shell, no user input
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(here / "requirements.txt"),
            "--target",
            str(python_dir),
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

    # Guardrail: never let an incomplete layer reach `sam deploy` (see
    # _REQUIRED_PACKAGES). A bad build now fails here instead of silently
    # publishing a source-only layer that breaks all functions at init.
    _verify_layer(python_dir)


if __name__ == "__main__":
    main()
