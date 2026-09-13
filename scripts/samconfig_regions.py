"""Read the ``BdoRegions`` toggle out of ``samconfig.toml`` (ADR-0036).

``samconfig.toml`` is the single authoritative home of the active-region toggle
(Req 1.5). A ``sam deploy --parameter-overrides`` on the command line replaces
the whole parameter set (unspecified params revert to template defaults), so
both ``make deploy`` and the CI prod deploy re-thread ``BdoRegions`` from here
rather than hardcoding it inline. The CI region-enum guard
(``scripts/validate_regions.py``) reuses :func:`regions_for_stage` so it checks
exactly what will be deployed.

Usage (prints the comma-joined list for a stage, e.g. ``tw`` or ``tw,na``)::

    uv run python scripts/samconfig_regions.py prod
"""

from __future__ import annotations

import shlex
import sys
import tomllib
from pathlib import Path

_SAMCONFIG = Path(__file__).resolve().parent.parent / "samconfig.toml"


def regions_for_stage(stage: str, samconfig: Path = _SAMCONFIG) -> list[str]:
    """Return the ``BdoRegions`` entries configured for ``stage`` in samconfig.

    Parses ``[<stage>.deploy.parameters].parameter_overrides`` (a
    space-delimited ``Key=Value`` string) and splits the ``BdoRegions`` value on
    commas. Raises ``SystemExit`` with a clear message when the stage or the
    ``BdoRegions`` override is absent, so a misconfigured deploy fails loudly.
    """
    data = tomllib.loads(samconfig.read_text(encoding="utf-8"))
    try:
        overrides = data[stage]["deploy"]["parameters"]["parameter_overrides"]
    except KeyError as exc:
        raise SystemExit(
            f"[{stage}.deploy.parameters].parameter_overrides missing in {samconfig}"
        ) from exc
    for token in shlex.split(overrides):
        key, _, value = token.partition("=")
        if key == "BdoRegions":
            return [region.strip() for region in value.split(",") if region.strip()]
    raise SystemExit(f"BdoRegions not set in [{stage}.deploy.parameters].parameter_overrides")


def main(argv: list[str]) -> None:
    stage = argv[1] if len(argv) > 1 else "dev"
    print(",".join(regions_for_stage(stage)))


if __name__ == "__main__":
    main(sys.argv)
