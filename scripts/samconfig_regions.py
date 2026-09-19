"""Read a stage's deploy parameter set out of ``samconfig.toml``.

``samconfig.toml`` is the single authoritative home of the **stage-static**
CloudFormation parameter set (Req 2.5), ``BdoRegions`` included (ADR-0036,
Req 1.5). A ``sam deploy --parameter-overrides`` on the command line **replaces**
that whole set rather than merging with it (unspecified parameters revert to
template defaults, aws-sam-cli#2380), so a caller that needs to supply even one
derived value has to restate every static one. This module is what restates them
*from the file* instead of by hand, so ``make deploy``, the CI prod deploy and a
``sam deploy --config-env <stage>`` with no overrides all converge one state.

Two of ``template.yaml``'s parameters are deliberately not in the file and are
supplied by the caller, because they are derived at deploy time: ``ApiVersion``
(the release tag, ADR-0037) and ``MigrationsFingerprint`` (a content hash of
``migrations/versions``, ADR-0025).

The module keeps its ``_regions`` name because ``scripts/validate_regions.py``,
``.github/workflows/ci.yml``, ``.github/workflows/deploy.yml`` and the ``Makefile``
all already reach for it there; :func:`regions_for_stage` is unchanged.

Usage::

    # The comma-joined active-region list for a stage (e.g. `tw` or `tw,na`).
    uv run python scripts/samconfig_regions.py prod

    # The stage's full Key=Value set, with the caller's own entries merged in.
    uv run python scripts/samconfig_regions.py --parameters prod \
        ApiVersion=v1.2.3 MigrationsFingerprint=abc123

An override for a key the file already carries **replaces** it in place rather
than being appended: which of two duplicate ``Key=`` entries in a single
``--parameter-overrides`` value wins is not documented by SAM (and is not
observable without the CLI), so the merge happens here where it is defined. That
is what keeps ``make deploy STAGE=dev AUTO_MIGRATE=false`` -- step 2 of a
first-time bring-up (ADR-0025, docs/runbook.md) -- actually reaching the deploy.
"""

from __future__ import annotations

import shlex
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

_SAMCONFIG = Path(__file__).resolve().parent.parent / "samconfig.toml"

_PARAMETERS_FLAG = "--parameters"


def parameters_for_stage(stage: str, samconfig: Path = _SAMCONFIG) -> dict[str, str]:
    """Return ``stage``'s ``parameter_overrides`` as an ordered ``Key -> Value`` map.

    Reads ``[<stage>.deploy.parameters].parameter_overrides``. SAM accepts that
    entry in two equally-valid spellings and both are handled: a single
    space-delimited ``Key=Value`` string, or a TOML array of ``Key=Value``
    strings. File order is preserved (a ``dict`` is insertion-ordered) so a
    rendered set reads the way the file does and a diff of two renderings is
    about values, not order.

    Raises ``SystemExit`` with a clear message when the stage or its table is
    absent, so a misconfigured deploy fails loudly rather than falling back to
    template defaults.
    """
    data = tomllib.loads(samconfig.read_text(encoding="utf-8"))
    try:
        overrides = data[stage]["deploy"]["parameters"]["parameter_overrides"]
    except KeyError as exc:
        raise SystemExit(
            f"[{stage}.deploy.parameters].parameter_overrides missing in {samconfig}"
        ) from exc
    # String form -> shlex-split into Key=Value tokens; the TOML-array form is
    # already a list of Key=Value strings.
    tokens = overrides if isinstance(overrides, list) else shlex.split(overrides)
    return parse_parameters(token for token in tokens if isinstance(token, str))


def regions_for_stage(stage: str, samconfig: Path = _SAMCONFIG) -> list[str]:
    """Return the ``BdoRegions`` entries configured for ``stage`` in samconfig.

    The single active-region toggle (ADR-0036), split on commas. Behaviour is
    unchanged from before this module was widened: ``scripts/validate_regions.py``
    reuses it so the CI region-enum guard checks exactly what will be deployed.
    """
    value = parameters_for_stage(stage, samconfig).get("BdoRegions")
    if value is None:
        raise SystemExit(f"BdoRegions not set in [{stage}.deploy.parameters].parameter_overrides")
    return [region.strip() for region in value.split(",") if region.strip()]


def merged_parameters(
    stage: str, overrides: Mapping[str, str], samconfig: Path = _SAMCONFIG
) -> dict[str, str]:
    """``stage``'s set with ``overrides`` applied: replace in place, else append.

    A key the file already carries keeps its position and takes the caller's
    value; a key it does not carry (``ApiVersion``, ``MigrationsFingerprint``, or
    a deliberate command-line override) is appended. Replacement rather than
    duplication is the point -- see the module docstring.
    """
    parameters = parameters_for_stage(stage, samconfig)
    parameters.update(overrides)
    return parameters


def parse_parameters(entries: Iterable[str]) -> dict[str, str]:
    """Parse ``Key=Value`` tokens into an ordered map, rejecting malformed ones.

    A token without a ``=`` (or with an empty key) is a caller mistake worth
    naming: silently dropping it would hand SAM a set missing a parameter the
    operator believed they had passed.
    """
    parameters: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not separator or not key:
            raise SystemExit(f"expected a Key=Value parameter override, got {entry!r}")
        parameters[key] = value
    return parameters


def render_parameters(parameters: Mapping[str, str]) -> str:
    """Render a parameter map as the space-delimited string SAM's CLI takes.

    A value containing whitespace cannot survive this spelling, so say so rather
    than emitting a string that would silently split into bogus parameters.
    """
    for key, value in parameters.items():
        if value != value.strip() or any(character.isspace() for character in value):
            raise SystemExit(
                f"{key}={value!r} contains whitespace, which a space-delimited "
                "--parameter-overrides string cannot carry"
            )
    return " ".join(f"{key}={value}" for key, value in parameters.items())


def main(argv: list[str]) -> None:
    arguments = argv[1:]
    full_set = bool(arguments) and arguments[0] == _PARAMETERS_FLAG
    if full_set:
        arguments = arguments[1:]
    stage = arguments[0] if arguments else "dev"
    if not full_set:
        # The original, unchanged contract: the comma-joined region list.
        print(",".join(regions_for_stage(stage)))
        return
    overrides = parse_parameters(arguments[1:])
    print(render_parameters(merged_parameters(stage, overrides)))


if __name__ == "__main__":
    main(sys.argv)
