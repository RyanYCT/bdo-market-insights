"""Typed command-core models (Pydantic v2).

Shapes and defaults only: both front-ends build a ``Command``, the ``Dispatcher``
resolves it into a ``Plan``, and execution returns a ``Result``. Pydantic v2 gives
free JSON serialization for the CLI's ``--json`` mode, which emits exactly one
serialized ``Result``.

Validation rules (stage membership, the release-version regex, the LOCAL prod
deploy rejection, repo-scoped SSM paths) are added in task 1.3.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Capability(StrEnum):
    """The capabilities the control plane exposes, one per CLI subcommand.

    Runtime feature flags are deferred to a follow-up spec, so ``FLAG`` is
    deliberately absent: the CLI never advertises a subcommand with no executor
    behind it.
    """

    CONFIG = "config"
    BOOTSTRAP = "bootstrap"
    DEPLOY = "deploy"
    RELEASE = "release"


class Target(StrEnum):
    """Where a command executes; selects the executor."""

    LOCAL = "local"
    """SamExecutor — dev/personal stacks only."""

    CI = "ci"
    """ActionsDispatcher — shared-env and prod."""


class ExitCode(IntEnum):
    """The exit-code contract (Requirement 10.7).

    Every terminating outcome maps to exactly one of these.
    """

    SUCCESS = 0
    EXECUTOR_FAILED = 1
    USAGE_ERROR = 2
    CONFIRMATION_REQUIRED = 3


class ConfigDiff(BaseModel):
    """A single config change, in one of the two sanctioned locations.

    AppConfig is rejected as a store, so ``source`` admits no third value.
    """

    source: Literal["samconfig", "ssm"]
    key: str
    before: str | None
    after: str | None


class Command(BaseModel):
    """The typed request object both front-ends build."""

    capability: Capability
    target: Target = Target.LOCAL
    stage: str = "dev"
    version: str | None = None
    args: dict[str, str | bool | list[str]] = Field(default_factory=dict)
    dry_run: bool = False
    assume_yes: bool = False


class PlanStep(BaseModel):
    """One executor call in a plan."""

    description: str
    command: str
    """The exact command line, e.g. ``sam deploy --config-env dev``."""

    executor: Literal["sam", "actions", "git", "config"]


class Plan(BaseModel):
    """The resolved, side-effect-free preview of a ``Command``."""

    capability: Capability
    target: Target
    steps: list[PlanStep]
    effects: list[str]
    """Human-readable "what will change"."""

    requires_confirmation: bool


class Result(BaseModel):
    """The typed outcome returned after execution."""

    capability: Capability
    ok: bool
    exit_code: ExitCode
    summary: str
    changes: list[ConfigDiff] = Field(default_factory=list)
    run_url: str | None = None
    """CI run reference when ``target == CI``."""

    raw_output: str | None = None


__all__ = [
    "Capability",
    "Command",
    "ConfigDiff",
    "ExitCode",
    "Plan",
    "PlanStep",
    "Result",
    "Target",
]
