"""Typed command-core models (Pydantic v2).

Both front-ends build a ``Command``, the ``Dispatcher``
resolves it into a ``Plan``, and execution returns a ``Result``. Pydantic v2 gives
free JSON serialization for the CLI's ``--json`` mode, which emits exactly one
serialized ``Result``.

The validation rules the models enforce (stage membership, the release-version
regex, the LOCAL prod deploy rejection, the required reviewers a ``prod``
bootstrap must carry, repo-scoped SSM paths) are enforced here, on the models
themselves, off the pure checks in ``core.validation`` — so both front-ends
inherit them and a violating ``Command`` cannot be constructed. They raise
``UsageError`` (exit ``2``) before any executor is reached.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

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
    """Where a *deploy* executes; only meaningful for ``capability == DEPLOY``.

    A ``release`` plan always records ``CI`` — the deploy runs in Actions however
    it was triggered, so a ``release`` command is normalised during planning —
    and ``config`` / ``bootstrap`` plan the same steps whichever target they
    carried.
    """

    LOCAL = "local"
    """SamExecutor — dev/personal stacks only."""

    CI = "ci"
    """GitHubExecutor — shared-env and prod."""


class Op(StrEnum):
    """The closed vocabulary of planned operations; executors switch on it.

    Closed rather than a bare ``str`` because every executor's ``run_step``
    switches on it: with a ``StrEnum`` the type checker can verify the switch is
    exhaustive, so adding an op without handling it is a type error at check
    time instead of a silent fallthrough discovered mid-deploy.

    The ``params`` each op expects are documented on the member. Keys are stable
    names in the executor's own vocabulary (``config_env``, ``version``,
    ``path``, …), so an executor reads a typed value rather than re-deriving one
    by parsing ``PlanStep.command``.
    """

    SAM_BUILD = "sam.build"
    """Build the deployment artifacts. No params."""

    SAM_DEPLOY = "sam.deploy"
    """Deploy a stack. Params: ``config_env`` (the ``samconfig.toml`` environment)."""

    SAM_SYNC = "sam.sync"
    """Dev fast-loop sync. Params: ``config_env``."""

    SAM_PIPELINE_BOOTSTRAP = "sam.pipeline_bootstrap"
    """One-time OIDC role + artifact bucket. Params: ``stage``."""

    GITHUB_RUN_WORKFLOW = "github.run_workflow"
    """Dispatch a workflow run. Params: ``workflow``, ``stage``, ``version`` (when set)."""

    GITHUB_ENVIRONMENT_SET = "github.environment_set"
    """Create/update a GitHub Environment. Params: ``environment``, an optional
    ``reviewers`` — the required reviewers to configure on it — and an optional
    ``allowed_refs``, the deployment branch/tag policy's admitted patterns
    (``branch:main`` / ``tag:v*``).

    Both lists are carried in ``params`` (and rendered in the step) because
    neither is secret: they are *what protection will be configured*, which is
    exactly what a plan preview exists to show."""

    GITHUB_SECRET_SET = "github.secret_set"  # nosec B105 - op name; no secret value in source
    """Set an environment secret. Params: ``environment``, ``name``.

    Only the secret's name is ever planned; the value is supplied at execution
    and, when one is carried at all, it is carried as a ``SecretStr``.
    """

    GIT_TAG = "git.tag"
    """Create a release tag. Params: ``version``, ``base_branch``, ``remote``.

    ``remote`` is carried even though creating a tag is purely local: the
    executor verifies the release preconditions before it creates anything, and
    one of them asks whether the tag already exists on the remote the matching
    ``git.push`` step will publish to.
    """

    GIT_PUSH = "git.push"
    """Push a release tag. Params: ``version``, ``remote``."""

    CONFIG_SHOW = "config.show"
    """Merged samconfig + SSM read. Params: ``stage``, ``ssm_path`` (the path prefix)."""

    SSM_PUT = "ssm.put"
    """Audited ``PutParameter``. Params: ``path``, ``value`` (a ``SecretStr``), ``overwrite``."""

    SAMCONFIG_PR = "samconfig.pr"
    """Open a samconfig.toml PR. Params: ``stage``, ``key``, ``value``, ``branch``,
    ``base``, ``title``. The value is deploy-time config bound for a public pull
    request, so it is not secret and stays rendered."""


class RunRef(BaseModel):
    """A reference to one dispatched workflow run.

    Lives here, in the shared vocabulary, rather than in ``core.executors.github``
    where the ``gh`` adapter that produces it lives: ``Result`` and
    ``CommandResult`` carry a ``RunRef``, and a model in the executor module would
    make ``core.models`` import an executor that already imports it. The adapter
    re-exports the name, so ``gh``-side code still reads as if it owned it.

    ``url`` and ``run_id`` are optional because they are resolved *after* the
    dispatch by a separate read-only query: a reference to a run that was
    certainly dispatched but could not be located yet is still a useful thing to
    return, and it is not a failure.
    """

    workflow: str
    run_id: str | None = None
    url: str | None = None


class RunStatus(BaseModel):
    """What ``gh run view`` reported about a run.

    ``status`` / ``conclusion`` are GitHub's own strings, kept verbatim rather
    than mapped onto a local vocabulary — the CI run is authoritative
    (Requirement 10.6), so re-encoding its verdict would only create a second one
    that can disagree.
    """

    run: RunRef
    status: str | None = None
    conclusion: str | None = None
    output: str = ""
    ok: bool = True


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


class ConfigView(BaseModel):
    """The merged, mask-applied config read behind ``config show``.

    Two dicts rather than one flat namespace, because *where* a value lives is
    the point: ``samconfig`` is deploy-time config that changes only through a
    reviewed pull request, ``ssm`` is operational config changed by an audited
    write. Collapsing them would hide which of the two sanctioned locations an
    operator has to go to (design Property 3).

    A value that must not be rendered is held as a ``SecretStr`` rather than
    pre-rendered as ``"***"``: the mask then survives ``model_dump_json()``, so
    ``--json`` cannot print it either (Requirement 3.2). The un-masked entries
    stay plain ``str`` so a read remains useful.
    """

    stage: str
    samconfig: dict[str, str | SecretStr] = Field(default_factory=dict)
    """``[<stage>.deploy.parameters]``, with ``parameter_overrides`` expanded into
    its individual CloudFormation parameters (``BdoRegions``, ``Stage``, …)."""

    ssm: dict[str, str | SecretStr] = Field(default_factory=dict)
    """Repo-scoped SSM parameters for the stage, keyed by full path."""

    masked: list[str] = Field(default_factory=list)
    """The keys whose values were masked, so a reader can tell a masked value
    from a literally-absent one without inspecting the dicts' value types."""


class PrRef(BaseModel):
    """A reference to the pull request a deploy-time config change opened.

    ``url`` is optional because the PR is opened by ``gh``, which is the
    authoritative record: a PR that certainly exists but whose URL could not be
    parsed out of ``gh``'s output is still a successful outcome, and reporting it
    as a failure would describe a change as un-proposed when it has been
    proposed.
    """

    branch: str
    base: str
    title: str
    url: str | None = None


REVIEWERS_ARG: Final = "reviewers"
"""``bootstrap`` arg carrying the required reviewers of the GitHub Environment.

Each entry is ``<Type>:<id>`` — ``User:1234``, ``Team:56`` — the form
``GitHubExecutor.set_environment`` sends to the GitHub API; a bare id is read as
a ``User``.
"""


def bootstrap_reviewers(args: Mapping[str, str | bool | list[str]]) -> list[str]:
    """Return the required reviewers named in ``args``, validating their shape.

    Shared by ``Command``'s validator (which refuses a ``prod`` bootstrap that
    names none) and by the planner (which puts them in the
    ``github.environment_set`` step's params), so the reviewers a command is
    *accepted* for are exactly the reviewers that get *configured* — one reader,
    no second interpretation of the same arg.

    A missing arg is an empty list, which is legitimate for a non-prod stage. A
    present-but-mis-shaped arg (a bare string, an empty entry) is a
    ``UsageError``: silently reading ``"User:1234"`` as five reviewers, or
    sending an empty reviewer id to the API, would configure a gate other than
    the one the operator asked for.
    """
    value = args.get(REVIEWERS_ARG)
    if value is None:
        return []
    if not isinstance(value, list):
        raise UsageError(
            field=f"args.{REVIEWERS_ARG}",
            value=value,
            problem=f"{REVIEWERS_ARG!r} must be a list of required reviewers",
            hint='e.g. reviewers=["User:1234", "Team:56"]',
        )
    if any(not entry.strip() for entry in value):
        raise UsageError(
            field=f"args.{REVIEWERS_ARG}",
            value=value,
            problem=f"{REVIEWERS_ARG!r} contains an empty reviewer",
            hint='every entry names a reviewer as "<Type>:<id>", e.g. "User:1234"',
        )
    return list(value)


class Command(BaseModel):
    """The typed request object both front-ends build.

    Validation runs on construction *and* on assignment, so there is no way to
    hold an invalid command — in particular no way to hold a LOCAL prod deploy
    (design Property 2: no first-party prod deploy), and no way to hold a
    ``prod`` bootstrap that names no required reviewer (Requirements 4.5, 6.4).
    Both production invariants are enforced the same way and in the same place:
    the offending command is *unconstructable*, so no planner or executor needs a
    second check that could drift from it.
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

    @model_validator(mode="after")
    def _require_reviewers_for_prod_bootstrap(self) -> Command:
        """Refuse a ``prod`` bootstrap that names no required reviewer.

        Requirements 4.5 and 6.4: the prod gate is the GitHub Environment's
        required reviewers, so an Environment created without them is an
        *unprotected* production — and the bootstrap helper is the one code path
        that creates it. Refusing here, rather than in the planner or the
        executor, means the rejection happens before any executor call (exit
        ``2``), so no Environment is created and none is created-then-fixed. It
        also sits beside the LOCAL-prod-deploy rule, which is the other invariant
        of the same kind, enforced the same way.

        The reviewers' *shape* is checked for every bootstrap, prod or not: a
        mis-shaped list would otherwise configure a gate nobody asked for on a
        non-prod Environment.
        """
        if self.capability is not Capability.BOOTSTRAP:
            return self
        reviewers = bootstrap_reviewers(self.args)
        if self.stage == PROD_STAGE and not reviewers:
            raise UsageError(
                field=f"args.{REVIEWERS_ARG}",
                value=None,
                problem=(
                    f"a {PROD_STAGE} bootstrap needs at least one required reviewer; "
                    f"none was given, so no GitHub Environment was created"
                ),
                hint=(
                    f"pass the reviewers of the {PROD_STAGE} GitHub Environment, e.g. "
                    f'{REVIEWERS_ARG}=["User:1234", "Team:56"]'
                ),
            )
        return self


class PlanStep(BaseModel):
    """One executor call in a plan: structured intent plus its rendering.

    A step carries **both** because routing is decided exactly once, in
    ``plan()``, and the same value is then consumed for display and for
    execution — so the previewed plan cannot drift from what actually runs
    (design Property 5).

    ``op`` + ``params`` are the structured intent an executor acts on; ``command``
    is display only. **No executor ever parses ``command``**: ``run_step``
    switches on ``op`` and reads ``params``, which is what lets each adapter reach
    its tool natively — ``sam``/``git``/``gh`` steps shell out, while
    ``ConfigStore`` uses boto3 for SSM rather than re-parsing a shell string.
    """

    description: str
    command: str
    """The display rendering only — the line shown by ``--dry-run`` and the TUI
    preview, e.g. ``sam deploy --config-env dev``. Never parsed by an executor."""

    executor: Literal["sam", "github", "git", "config"]
    op: Op
    """The structured intent, from the closed ``Op`` vocabulary."""

    params: dict[str, str | bool | list[str] | SecretStr] = Field(default_factory=dict)
    """The typed values the executor needs, so it never has to parse ``command``.

    A secret-shaped or operational value is carried as a ``SecretStr``: the
    executor recovers it with ``.get_secret_value()``, while ``model_dump_json()``
    renders it as ``'**********'``. The value is therefore *absent from the
    serialized model*, not merely omitted by a renderer — which is what
    Requirements 3.7 and 4.4 require of ``--dry-run`` and ``--json``.
    """


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
    output: str
    run_url: str | None = None
    """Set by a github/CI step that dispatched a run (Requirement 10.6)."""

    run: RunRef | None = None
    """The dispatched run's identity, set by the same step that sets ``run_url``.

    The design's ``GitHubExecutor`` sketch has ``run_workflow`` return a bare
    ``RunRef``; the shipped signature returns a ``CommandResult`` — a dispatch can
    fail, and the dispatcher consumes one uniform value from every executor — so
    the reference travels *inside* it. That is what lets the run reach
    ``Result.run`` structurally: without this field the only route from executor to
    front-end would be parsing the id back out of the URL string, which would give
    run identity a second source of truth.
    """

    changes: list[ConfigDiff] = Field(default_factory=list)


class Result(BaseModel):
    """The typed outcome returned after execution."""

    capability: Capability
    ok: bool
    exit_code: ExitCode
    summary: str
    changes: list[ConfigDiff] = Field(default_factory=list)
    run_url: str | None = None
    """CI run reference when ``target == CI`` — display only.

    What a human is shown and what an agent reads out of ``--json``; it is not
    what the run is followed by. See ``run``.
    """

    run: RunRef | None = None
    """The structured reference to the dispatched run, set alongside ``run_url``.

    ``presentation.follow_run()`` needs a ``RunRef`` to call
    ``GitHubExecutor.watch`` / ``view``, and it gets it from here rather than by
    parsing one back out of ``run_url``: a dispatched run then has exactly one
    identity, carried from the executor that created it, instead of a second one
    re-derived from a display string whose shape ``gh`` is free to change.
    ``None`` when no run was dispatched, which is what makes ``follow_run()`` a
    no-op for every local command.
    """

    raw_output: str | None = None

    plan: Plan | None = None
    """Set when a mutating plan was refused for want of confirmation.

    ``Dispatcher.execute()`` *raises* ``ConfirmationRequired`` — a raise cannot
    be silently ignored, which is what a safety gate needs. The CLI front-end
    catches it and renders a ``Result`` carrying that refused ``Plan`` here, so
    the caller can inspect the effects and re-invoke with ``--yes`` (exit ``3``,
    Requirement 10.4). It is ``None`` for every other outcome.
    """


__all__ = [
    "REVIEWERS_ARG",
    "Capability",
    "Command",
    "CommandResult",
    "ConfigDiff",
    "ConfigView",
    "ExitCode",
    "Op",
    "Plan",
    "PlanStep",
    "PrRef",
    "Result",
    "RunRef",
    "RunStatus",
    "Target",
    "bootstrap_reviewers",
]
