"""Typed command-core models (Pydantic v2).

Both front-ends build a ``Command``, the ``Dispatcher``
resolves it into a ``Plan``, and execution returns a ``Result``. Pydantic v2 gives
free JSON serialization for the CLI's ``--json`` mode, which emits exactly one
serialized ``Result``.

The validation rules the models enforce (stage membership, the release-version
regex, the LOCAL prod deploy rejection, repo-scoped SSM paths) live in
``core.validation`` so both front-ends inherit them; a violating ``Command``
cannot be constructed. They raise ``UsageError`` (exit ``2``) before any executor
is reached.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.validation import (
    PROD_STAGE,
    validate_ssm_path,
    validate_stage,
    validate_version,
)


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


class ConfigDiff(BaseModel):
    """A single config change, in one of the two sanctioned locations.

    AppConfig is rejected as a store, so ``source`` admits no third value. An
    ``ssm`` diff names a repo-scoped path, so writes cannot escape the repo's
    namespace (Requirement 9.1).
    """

    model_config = ConfigDict(validate_assignment=True)

    source: Literal["samconfig", "ssm"]
    key: str
    before: str | None
    after: str | None

    @model_validator(mode="after")
    def _check_ssm_key_is_repo_scoped(self) -> ConfigDiff:
        if self.source == "ssm":
            validate_ssm_path(self.key)
        return self


class Command(BaseModel):
    """The typed request object both front-ends build.

    Validation runs on construction *and* on assignment, so there is no way to
    hold an invalid command — in particular no way to hold a LOCAL prod deploy
    (design Property 2: no first-party prod deploy).
    """

    model_config = ConfigDict(validate_assignment=True)

    capability: Capability
    target: Target = Target.LOCAL
    stage: str = "dev"
    version: str | None = None
    args: dict[str, str | bool | list[str]] = Field(default_factory=dict)
    dry_run: bool = False
    assume_yes: bool = False

    @field_validator("stage")
    @classmethod
    def _check_stage(cls, stage: str) -> str:
        return validate_stage(stage)

    @field_validator("version")
    @classmethod
    def _check_version(cls, version: str | None) -> str | None:
        return None if version is None else validate_version(version)

    @model_validator(mode="after")
    def _reject_local_prod_deploy(self) -> Command:
        """Reject a LOCAL prod deploy (Requirement 5.2).

        Production is reachable only by dispatching the environment-protected CI
        job, so this combination has no executor behind it by design.
        """
        if (
            self.capability is Capability.DEPLOY
            and self.target is Target.LOCAL
            and self.stage == PROD_STAGE
        ):
            raise UsageError(
                field="target",
                value=Target.LOCAL.value,
                problem=(
                    f"a {PROD_STAGE} deploy cannot run with target={Target.LOCAL.value}; "
                    "the control plane has no local production deploy path"
                ),
                hint=(
                    "use `release` to tag a version and let the environment-protected "
                    f"CI job deploy, or re-run with target={Target.CI.value}"
                ),
            )
        return self


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


class CommandResult(BaseModel):
    """What one executor call reported: the design's executor return type.

    ``output`` is the sanctioned tool's own output, kept verbatim so the
    dispatcher can surface it without reformatting (Requirement 10.2).
    """

    ok: bool
    output: str = ""
    run_url: str | None = None
    """Set by an actions/CI step that dispatched a run (Requirement 10.6)."""

    changes: list[ConfigDiff] = Field(default_factory=list)


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
    "CommandResult",
    "ConfigDiff",
    "ExitCode",
    "Plan",
    "PlanStep",
    "Result",
    "Target",
]
