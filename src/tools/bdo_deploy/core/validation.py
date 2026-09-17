"""Validation rules for the command core.

These rules live in the core so **both** front-ends inherit them: the CLI and the
TUI only collect intent, and a ``Command`` that violates a rule cannot be
constructed at all. Every check here is pure — it reads ``samconfig.toml`` and
nothing else, performs no AWS/git/subprocess work, and mutates nothing — so
validation failures are caught before any executor call and return exit ``2``
(Requirement 10.3).

Rules:

- ``stage`` must be one of the environments actually defined in
  ``samconfig.toml``. The stage set is read from the file rather than hardcoded
  so it cannot drift from the config that owns the parameter sets.
- ``version`` must match the release-tag format ``^v\\d+\\.\\d+\\.\\d+$``; it
  becomes ``ApiVersion`` (ADR-0037).
- An SSM name that is written must be a repo-scoped
  ``/bdo-market-insights/<stage>/<category>/<key>`` path whose ``<stage>`` is one
  of the environments defined in ``samconfig.toml``; a bare ``/bdo/...`` path and
  a mistyped stage (``/bdo-market-insights/prd/...``) are both rejected
  (Requirement 9.1).
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Final

from bdo_deploy.core.errors import UsageError

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
"""Repository root: ``src/tools/bdo_deploy/core/validation.py`` -> four levels up."""

SAMCONFIG_PATH: Final = REPO_ROOT / "samconfig.toml"

PROD_STAGE: Final = "prod"
"""The stage that no LOCAL path may deploy (Requirement 5.2)."""

RELEASE_VERSION_PATTERN: Final = re.compile(r"^v\d+\.\d+\.\d+$")
RELEASE_VERSION_FORMAT: Final = "vX.Y.Z"

SSM_ROOT_SEGMENT: Final = "bdo-market-insights"
SSM_PATH_FORMAT: Final = "/bdo-market-insights/<stage>/<category>/<key>"
_SSM_PATH_SEGMENTS: Final = 4


@lru_cache(maxsize=1)
def samconfig_stages(path: Path = SAMCONFIG_PATH) -> frozenset[str]:
    """Return the deploy environments defined in ``samconfig.toml``.

    Each top-level table other than ``[default]`` is a ``--config-env`` the SAM
    CLI can be pointed at (currently ``dev`` and ``prod``). Reading the set from
    the file keeps stage validation from drifting from the config that owns the
    parameter sets.

    Raises ``UsageError`` (exit ``2``) if the file is missing, unreadable, or not
    valid TOML — the operator gets a named cause, not a traceback.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(
            field="stage",
            value=str(path),
            problem=f"samconfig.toml could not be read ({exc.strerror or exc})",
            hint="the deploy control plane must run from a checkout of this repository",
        ) from exc
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(
            field="stage",
            value=str(path),
            problem=f"samconfig.toml is not valid TOML ({exc})",
            hint="fix samconfig.toml before deploying",
        ) from exc
    return frozenset(
        name for name, table in document.items() if isinstance(table, dict) and name != "default"
    )


def validate_stage(stage: str) -> str:
    """Return ``stage`` if it is an environment defined in ``samconfig.toml``."""
    stages = samconfig_stages()
    if stage not in stages:
        raise UsageError(
            field="stage",
            value=stage,
            problem=f"{stage!r} is not an environment defined in samconfig.toml",
            hint=f"expected one of: {', '.join(sorted(stages))}",
        )
    return stage


def validate_version(version: str) -> str:
    """Return ``version`` if it matches the release-tag format (Requirement 7.2)."""
    # fullmatch, not match: Python's ``$`` also matches just before a trailing
    # newline, so ``match`` would accept "v1.4.0\n" as a tag name.
    if RELEASE_VERSION_PATTERN.fullmatch(version) is None:
        raise UsageError(
            field="version",
            value=version,
            problem=f"{version!r} is not a release tag",
            hint=f"expected the format {RELEASE_VERSION_FORMAT} (regex ^v\\d+\\.\\d+\\.\\d+$)",
        )
    return version


def validate_ssm_path(name: str) -> str:
    """Return ``name`` if it is a repo-scoped SSM path (Requirement 9.1).

    Accepts exactly ``/bdo-market-insights/<stage>/<category>/<key>``: four
    non-empty segments under that one root, whose ``<stage>`` segment is an
    environment defined in ``samconfig.toml``. A bare ``/bdo/...`` path, a path
    with an empty segment, and a path with the wrong depth are all rejected.

    The ``<stage>`` segment is checked against the real environment set rather
    than merely being required non-empty, because a typo
    (``/bdo-market-insights/prd/...``) would otherwise create a parameter nothing
    ever reads — a silent misconfiguration instead of an exit ``2``.
    """
    problem: str | None = None
    if not name.startswith("/"):
        problem = f"{name!r} is not an absolute SSM path"
    else:
        segments = name[1:].split("/")
        if len(segments) != _SSM_PATH_SEGMENTS:
            problem = f"{name!r} has {len(segments)} path segments, expected {_SSM_PATH_SEGMENTS}"
        elif any(not segment for segment in segments):
            problem = f"{name!r} has an empty path segment"
        elif segments[0] != SSM_ROOT_SEGMENT:
            problem = f"{name!r} is not scoped to /{SSM_ROOT_SEGMENT}"
        elif segments[1] not in (stages := samconfig_stages()):
            problem = (
                f"stage segment {segments[1]!r} of {name!r} is not an environment "
                f"defined in samconfig.toml (expected one of: {', '.join(sorted(stages))})"
            )
    if problem is not None:
        raise UsageError(
            field="ssm_path",
            value=name,
            problem=problem,
            hint=f"required format {SSM_PATH_FORMAT}",
        )
    return name


__all__ = [
    "PROD_STAGE",
    "RELEASE_VERSION_FORMAT",
    "RELEASE_VERSION_PATTERN",
    "SAMCONFIG_PATH",
    "SSM_PATH_FORMAT",
    "SSM_ROOT_SEGMENT",
    "samconfig_stages",
    "validate_ssm_path",
    "validate_stage",
    "validate_version",
]
