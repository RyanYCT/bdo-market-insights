"""Cross-platform SAM custom build for the seedTracked Lambda.

``Metadata.BuildMethod: makefile`` runs ``make build-SeedTrackedFunction`` (see
the Makefile), which calls this script with ``ARTIFACTS_DIR`` as ``argv[1]``. It
bundles ``app.py`` plus the committed tracked-set data files (from
``scripts/data/``) so the Lambda applies the same curated set the offline seed
scripts use -- mirroring how the migrator bundles ``migrations/``.

``bdo_common`` (tracking / dynamo) and Powertools ship in the shared layer
(ADR-0003), so nothing is pip-installed here. The repo root is located by
walking up from ``ARTIFACTS_DIR`` (SAM runs this from a scratch copy of the
function dir, so a CWD-relative ``scripts/data`` path would not resolve).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_DATA_FILES = (
    "tracked_items.json",
    "full_items.json",
    "categories.json",
    "track_sets.json",
)


def _find_repo_root(start: Path) -> Path:
    """Walk up from ARTIFACTS_DIR to the repo root (has template.yaml + scripts/data/)."""
    for candidate in (start, *start.parents):
        if (candidate / "template.yaml").is_file() and (candidate / "scripts" / "data").is_dir():
            return candidate
    raise FileNotFoundError(f"repo root (template.yaml + scripts/data/) not found above {start}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: build.py <artifacts_dir>")

    here = Path(__file__).resolve().parent
    artifacts = Path(sys.argv[1]).resolve()
    data_src = _find_repo_root(artifacts) / "scripts" / "data"

    artifacts.mkdir(parents=True, exist_ok=True)
    shutil.copy(here / "app.py", artifacts / "app.py")

    data_dst = artifacts / "data"
    data_dst.mkdir(exist_ok=True)
    for name in _DATA_FILES:
        shutil.copy(data_src / name, data_dst / name)


if __name__ == "__main__":
    main()
