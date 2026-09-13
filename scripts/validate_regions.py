"""CI guard: validate the ``BdoRegions`` toggle before deploy (Req 5.3).

Every region configured in ``samconfig.toml`` (the authoritative deploy source,
ADR-0036) must be a member of the canonical marketQuery ``Region`` enum -- the
single source of valid regions -- and must be unique. Running this before deploy
turns a typo or an out-of-enum region into a build failure rather than a
generated schedule that feeds an invalid ``region`` into the pipelines.

    uv run python scripts/validate_regions.py            # checks dev + prod
    uv run python scripts/validate_regions.py prod       # checks one stage
"""

from __future__ import annotations

import importlib.util
import re
import sys
import typing
from collections import defaultdict
from pathlib import Path

from samconfig_regions import regions_for_stage

_ROOT = Path(__file__).resolve().parent.parent
_MARKET_QUERY = _ROOT / "src" / "functions" / "market_query" / "app.py"
_LAYER = _ROOT / "src" / "layer" / "python"


def canonical_regions() -> set[str]:
    """Return the marketQuery ``Region`` enum members (the single source)."""
    if str(_LAYER) not in sys.path:
        sys.path.insert(0, str(_LAYER))
    spec = importlib.util.spec_from_file_location("bdo_market_query_app", _MARKET_QUERY)
    if spec is None or spec.loader is None:  # pragma: no cover - import wiring
        raise SystemExit(f"cannot load {_MARKET_QUERY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return set(typing.get_args(module.Region))


def _logical_id_fragment(region: str) -> str:
    """Mirror the ``&{RegionName}`` substitution: strip non-alphanumerics.

    The per-region schedules use ``&{RegionName}`` in their CloudFormation
    logical ids (ADR-0036), which drops every non-alphanumeric character (so
    ``console_eu`` -> ``consoleeu``). Two distinct regions that reduce to the
    same fragment would generate colliding logical ids and fail at deploy.
    """
    return re.sub(r"[^0-9A-Za-z]+", "", region)


def find_problems(regions: list[str], canonical: set[str]) -> list[str]:
    """Return human-readable problems with a configured region list (empty = ok)."""
    problems: list[str] = []
    if not regions:
        problems.append("no regions configured")
    duplicates = sorted({r for r in regions if regions.count(r) > 1})
    if duplicates:
        problems.append(f"duplicate region(s): {', '.join(duplicates)}")
    unknown = sorted(set(regions) - canonical)
    if unknown:
        problems.append(f"region(s) not in the canonical marketQuery enum: {', '.join(unknown)}")
    # Distinct regions that collapse to the same CloudFormation logical-id
    # fragment (via &{RegionName}) would collide at deploy; catch it here.
    by_fragment: dict[str, set[str]] = defaultdict(set)
    for region in regions:
        by_fragment[_logical_id_fragment(region)].add(region)
    collisions = sorted(
        ", ".join(sorted(members)) for members in by_fragment.values() if len(members) > 1
    )
    if collisions:
        problems.append(
            "region(s) collide to the same CloudFormation logical id: " + "; ".join(collisions)
        )
    return problems


def main(argv: list[str]) -> None:
    stages = argv[1:] or ["dev", "prod"]
    canonical = canonical_regions()
    failures: list[str] = []
    for stage in stages:
        regions = regions_for_stage(stage)
        for problem in find_problems(regions, canonical):
            failures.append(f"[{stage}] {problem}")
        if not find_problems(regions, canonical):
            print(f"[{stage}] BdoRegions OK: {', '.join(regions)}")
    if failures:
        print("Region validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv)
