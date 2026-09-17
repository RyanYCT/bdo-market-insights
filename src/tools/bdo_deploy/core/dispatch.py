"""The dispatcher: the one place a ``Command`` becomes a ``Plan``.

``plan()`` is **pure** (Requirement 2.4, design Property 5): it starts no
subprocess, makes no AWS/``gh``/``git``/``sam`` call, writes no file, and touches
no network. The only I/O it may cause is the cached read of ``samconfig.toml``
performed by ``core.validation`` when a ``Command`` is constructed. That makes it
safe to call for ``--dry-run`` and for TUI preview, and it makes planning
deterministic: the same ``Command`` yields the same ``Plan`` — same steps, same
order, same strings — so CLI mode and TUI mode serialize byte-for-byte identical
plans (design Property 1).

Every ``PlanStep`` carries both the structured intent its executor acts on
(``op`` + ``params``, from the closed ``Op`` vocabulary in ``core.models``) and
the ``command`` rendering shown by ``--dry-run`` and the TUI. That rendering is
faithful except in one respect: secret-shaped and operational values are
**masked** in it and in ``Plan.effects``, and travel in ``params`` as a
``SecretStr`` instead — so neither ``--dry-run`` nor ``--json`` can print such a
value (Requirements 3.7, 4.4). The ``config`` executor's SSM
operations are *rendered* as their equivalent AWS CLI invocation: the
``ConfigStore`` calls the SSM API through boto3 off ``params``, and the CLI form
is the faithful, copy-pasteable rendering of that one API call. No executor parses
``command``.

**No first-party prod deploy** (Requirement 6.1, design Property 2): the local
SAM branch is reached only for ``target == LOCAL``, and a ``DEPLOY`` + ``LOCAL``
+ ``prod`` ``Command`` cannot be constructed at all (``Command`` rejects it in
``core.models``). There is therefore no routing path here that can emit
``sam deploy --config-env prod``, and no second check that could drift from that
invariant: production is reached only by dispatching the environment-protected
``deploy.yml`` run.

``execute()`` is the **only** method here that can reach an executor: ``plan()``
never touches ``self._sam`` and friends. A ``--dry-run`` (or TUI preview) run
therefore stops at ``plan()`` and no mutation is even expressible — dry-run
purity is a property of which methods hold executor references, not of a flag
checked at run time. ``Plan`` deliberately carries no ``dry_run`` field: the
preview and the applied plan are the same value, and only calling ``execute()``
applies it.

The executors themselves land in tasks 3.1-3.4.
"""

from __future__ import annotations

from typing import Final, assert_never

from pydantic import SecretStr

from bdo_deploy.core.errors import (
    ConfirmationRequired,
    ControlPlaneError,
    ExecutorFailed,
    ExecutorUnavailable,
    UsageError,
    exit_code_for,
)
from bdo_deploy.core.executors.base import StepExecutor
from bdo_deploy.core.executors.config import ConfigStore
from bdo_deploy.core.executors.git import GitExecutor
from bdo_deploy.core.executors.github import GitHubExecutor
from bdo_deploy.core.executors.sam import SamExecutor
from bdo_deploy.core.models import (
    Capability,
    Command,
    ConfigDiff,
    Op,
    Plan,
    PlanStep,
    Result,
    Target,
)
from bdo_deploy.core.validation import PROD_STAGE, SSM_ROOT_SEGMENT, validate_ssm_path

DEPLOY_WORKFLOW: Final = "deploy.yml"
"""The dedicated CD workflow every CI deploy is dispatched against (ADR-0038)."""

RELEASE_BASE_BRANCH: Final = "main"
GIT_REMOTE: Final = "origin"

ACTION_ARG: Final = "action"
"""``config`` sub-action: ``show`` (default) or ``set``."""

KEY_ARG: Final = "key"
VALUE_ARG: Final = "value"
SYNC_ARG: Final = "sync"
"""``deploy`` toggle selecting the ``sam sync`` dev fast-loop."""

DISPATCH_ARG: Final = "dispatch"
"""``release`` toggle selecting ``workflow_dispatch`` instead of a pushed tag."""

CONFIG_SHOW: Final = "show"
CONFIG_SET: Final = "set"

DEPLOY_ROLE_SECRET: Final = "AWS_DEPLOY_ROLE_ARN"
"""The environment secret holding the OIDC deploy role ARN."""

MASK: Final = "***"
"""What a masked value is *rendered* as in ``PlanStep.command`` (Requirement 3.7)."""

MASK_EFFECT: Final = "(masked)"
"""What a masked value is *described* as in ``Plan.effects`` (Requirement 3.7)."""


def _plan_for(
    cmd: Command,
    steps: list[PlanStep],
    effects: list[str],
    *,
    confirm: bool,
) -> Plan:
    """Wrap ``cmd``'s resolved steps and effects in the ``Plan`` it produces.

    Every ``_plan_*`` method funnels through here, so the fields a plan carries
    besides its steps — the capability, the target, the confirmation flag — are
    derived in one place rather than restated per capability.

    A ``release`` is normalised to ``target = CI``: the deploy always runs in
    GitHub Actions, whether it got there by a pushed tag or by a
    ``workflow_dispatch``, so recording the target the command happened to carry
    would describe a local prod deploy that no plan can express (Requirement 7.5,
    design Property 2). ``config`` and ``bootstrap`` plan identical steps
    whichever target they carried, and ``deploy`` is the one capability for which
    the target is a genuine routing choice — both simply pass it through.
    """
    return Plan(
        capability=cmd.capability,
        target=Target.CI if cmd.capability is Capability.RELEASE else cmd.target,
        steps=steps,
        effects=effects,
        requires_confirmation=confirm,
    )


def _ssm_stage_prefix(stage: str) -> str:
    """The repo-scoped SSM prefix a ``config show`` reads, built from one root.

    ``SSM_ROOT_SEGMENT`` is the same constant ``core.validation`` enforces writes
    against and ``ConfigStore`` reads under, so the previewed ``aws ssm`` line
    cannot name a prefix the executor would not actually read.
    """
    return f"/{SSM_ROOT_SEGMENT}/{stage}/"


def _str_arg(cmd: Command, name: str) -> str:
    """Return a required string entry of ``cmd.args``, or raise ``UsageError``."""
    value = cmd.args.get(name)
    if value is None:
        raise UsageError(
            field=f"args.{name}",
            value=None,
            problem=f"{name!r} is required for {cmd.capability.value}",
        )
    if not isinstance(value, str):
        raise UsageError(
            field=f"args.{name}",
            value=value,
            problem=f"{name!r} must be a string",
        )
    return value


def _bool_arg(cmd: Command, name: str) -> bool:
    """Return an optional boolean entry of ``cmd.args`` (absent means false)."""
    value = cmd.args.get(name, False)
    if not isinstance(value, bool):
        raise UsageError(
            field=f"args.{name}",
            value=value,
            problem=f"{name!r} must be a boolean",
        )
    return value


def _step_failed(step: PlanStep, *, position: int, total: int) -> str:
    """Name the step that failed, so the operator knows where the run stopped."""
    return f"step {position}/{total} failed ({step.executor}): {step.command}"


def _raised_output(exc: BaseException) -> str:
    """The executor's own words for a raised failure — never a traceback.

    An ``ExecutorFailed`` already carries the tool's verbatim output; anything
    else is rendered as its message, falling back to the exception's type name
    when the message is empty (a bare ``NotImplementedError``, say).
    """
    if isinstance(exc, ExecutorFailed) and exc.output is not None:
        return exc.output
    return str(exc) or type(exc).__name__


class Dispatcher:
    """Resolves a ``Command`` into a ``Plan`` and routes it to one executor.

    Executors are injected so ``execute()`` can be tested against recorded
    invocations; ``plan()`` never uses them, which is what keeps planning pure.
    """

    def __init__(
        self,
        *,
        sam: SamExecutor | None = None,
        github: GitHubExecutor | None = None,
        git: GitExecutor | None = None,
        config: ConfigStore | None = None,
    ) -> None:
        self._sam = sam
        self._github = github
        self._git = git
        self._config = config

    def plan(self, cmd: Command) -> Plan:
        """Resolve ``cmd`` into the ordered executor calls plus their effects.

        Pure and side-effect-free. Raises ``UsageError`` (exit ``2``) for intent
        that no executor can serve — before any executor is reached.
        """
        match cmd.capability:
            case Capability.CONFIG:
                return self._plan_config(cmd)
            case Capability.BOOTSTRAP:
                return self._plan_bootstrap(cmd)
            case Capability.DEPLOY:
                return self._plan_deploy(cmd)
            case Capability.RELEASE:
                return self._plan_release(cmd)
            case _:  # pragma: no cover - exhaustive over Capability
                assert_never(cmd.capability)

    # -- execution ----------------------------------------------------------

    def execute(self, plan: Plan, *, confirmed: bool) -> Result:
        """Run ``plan``'s steps in order through their injected executors.

        Raises ``ConfirmationRequired`` — carrying ``plan`` — when the plan
        mutates and ``confirmed`` is false; the gate is checked before any step
        is resolved, so nothing has run. The core raises rather than returning a
        "refused" ``Result`` because a raise **cannot be silently ignored by a
        caller**, which is what a safety gate needs: a caller that forgot to
        inspect a returned status would proceed as though the mutation had been
        approved. The **CLI front-end** catches ``ConfirmationRequired``, renders
        a ``Result`` carrying the refused ``Plan`` in ``Result.plan``, and exits
        ``3`` — which is how Requirement 10.4 is satisfied at the CLI_Mode
        boundary it describes.

        Every other outcome is a ``Result``. Execution stops at the first failing
        step, the remaining steps are skipped, and the failing executor's output
        is surfaced verbatim with exit ``1`` and no traceback (Requirement 10.2).
        """
        if plan.requires_confirmation and not confirmed:
            raise ConfirmationRequired(
                f"{plan.capability.value}: confirmation required, nothing has run",
                plan=plan,
            )

        total = len(plan.steps)
        try:
            wired = self._wire(plan)
        except ExecutorUnavailable as exc:
            # Resolution precedes execution, so no step ran and nothing mutated;
            # the error says so itself, hence no "steps skipped" tail.
            return self._failure(plan, error=exc, skipped=0)

        changes: list[ConfigDiff] = []
        outputs: list[str] = []
        run_url: str | None = None

        for position, (step, executor) in enumerate(wired, start=1):
            try:
                outcome = executor.run_step(step)
            except Exception as exc:
                return self._failure(
                    plan,
                    error=ExecutorFailed(
                        _step_failed(step, position=position, total=total),
                        output=_raised_output(exc),
                    ),
                    skipped=total - position,
                    changes=changes,
                    run_url=run_url,
                )
            changes.extend(outcome.changes)
            outputs.append(outcome.output)
            if outcome.run_url is not None:
                run_url = outcome.run_url
            if not outcome.ok:
                return self._failure(
                    plan,
                    error=ExecutorFailed(
                        _step_failed(step, position=position, total=total),
                        output=outcome.output,
                    ),
                    skipped=total - position,
                    changes=changes,
                    run_url=run_url,
                )

        summary = f"{plan.capability.value}: completed {total} step(s)"
        if run_url is not None:
            summary = f"{summary}; the dispatched run is authoritative: {run_url}"
        return Result(
            capability=plan.capability,
            ok=True,
            exit_code=exit_code_for(None),
            summary=summary,
            changes=changes,
            run_url=run_url,
            raw_output="\n".join(text for text in outputs if text) or None,
        )

    def _wire(self, plan: Plan) -> list[tuple[PlanStep, StepExecutor]]:
        """Pair every step with its injected executor, or raise.

        Resolving the whole plan up front means an unwired executor is reported
        as a named ``ExecutorUnavailable`` before the first step runs, rather
        than as an ``AttributeError`` on ``None`` halfway through.
        """
        injected: dict[str, StepExecutor | None] = {
            "sam": self._sam,
            "github": self._github,
            "git": self._git,
            "config": self._config,
        }
        wired: list[tuple[PlanStep, StepExecutor]] = []
        for position, step in enumerate(plan.steps, start=1):
            executor = injected[step.executor]
            if executor is None:
                raise ExecutorUnavailable(
                    f"step {position}/{len(plan.steps)} needs the {step.executor!r} executor, "
                    "which was not injected into the Dispatcher; nothing has run"
                )
            wired.append((step, executor))
        return wired

    @staticmethod
    def _failure(
        plan: Plan,
        *,
        error: ControlPlaneError,
        skipped: int,
        changes: list[ConfigDiff] | None = None,
        run_url: str | None = None,
    ) -> Result:
        """Build the ``Result`` for a stopped run; ``exit_code_for`` maps the code."""
        summary = error.summary
        if skipped > 0:
            summary = f"{summary}; {skipped} remaining step(s) skipped"
        return Result(
            capability=plan.capability,
            ok=False,
            exit_code=exit_code_for(error),
            summary=summary,
            changes=changes if changes is not None else [],
            run_url=run_url,
            raw_output=error.output if isinstance(error, ExecutorFailed) else None,
        )

    # -- config -------------------------------------------------------------

    def _plan_config(self, cmd: Command) -> Plan:
        """Route ``config`` to the ConfigStore: a merged read, or one write.

        A write goes to exactly one of the two sanctioned locations and no third
        (design Property 3): an SSM path (operational config) becomes an audited
        ``PutParameter``; anything else is deploy-time config held in
        ``samconfig.toml`` and becomes a pull request against that tracked file.
        """
        action = cmd.args.get(ACTION_ARG, CONFIG_SHOW)
        if action == CONFIG_SHOW:
            return self._plan_config_show(cmd)
        if action == CONFIG_SET:
            return self._plan_config_set(cmd)
        raise UsageError(
            field=f"args.{ACTION_ARG}",
            value=action,
            problem=f"{action!r} is not a config action",
            hint=f"expected one of: {CONFIG_SET}, {CONFIG_SHOW}",
        )

    def _plan_config_show(self, cmd: Command) -> Plan:
        return _plan_for(
            cmd,
            [
                PlanStep(
                    description=(
                        f"read the merged {cmd.stage} config view "
                        "(samconfig.toml + SSM, secret values masked)"
                    ),
                    command=(
                        "aws ssm get-parameters-by-path "
                        f"--path {_ssm_stage_prefix(cmd.stage)} --recursive"
                    ),
                    executor="config",
                    op=Op.CONFIG_SHOW,
                    params={
                        "stage": cmd.stage,
                        "ssm_path": _ssm_stage_prefix(cmd.stage),
                    },
                )
            ],
            [f"nothing changes: reads the {cmd.stage} config and renders it"],
            confirm=False,
        )

    def _plan_config_set(self, cmd: Command) -> Plan:
        key = _str_arg(cmd, KEY_ARG)
        value = _str_arg(cmd, VALUE_ARG)
        if key.startswith("/"):
            # Operational config: an audited write to a repo-scoped SSM path.
            #
            # The value is masked in the rendering and carried as a ``SecretStr``
            # in ``params``, so it is absent from the serialized plan rather than
            # merely omitted by a renderer: neither ``--dry-run`` nor ``--json``
            # can print it (Requirement 3.7). The executor recovers it with
            # ``.get_secret_value()``.
            validate_ssm_path(key)
            step = PlanStep(
                description=f"write the operational value at {key} (audited)",
                command=f"aws ssm put-parameter --name {key} --value {MASK} --overwrite",
                executor="config",
                op=Op.SSM_PUT,
                params={"path": key, "value": SecretStr(value), "overwrite": True},
            )
            effects = [
                f"sets {key} in SSM Parameter Store to {MASK_EFFECT}",
                "records an audit entry for the write",
            ]
        else:
            # Deploy-time config: version-controlled, changed through review. The
            # value is not masked — it is bound for a public pull request against
            # a tracked file, so hiding it would only make the plan a worse
            # preview of the diff it opens.
            branch = f"config/{cmd.stage}-{key}"
            title = f"config({cmd.stage}): set {key}={value}"
            step = PlanStep(
                description=(
                    f"set {key} in [{cmd.stage}.deploy.parameters] of samconfig.toml "
                    f"on branch {branch} and open a pull request"
                ),
                command=(
                    f'gh pr create --base {RELEASE_BASE_BRANCH} --head {branch} --title "{title}"'
                ),
                executor="config",
                op=Op.SAMCONFIG_PR,
                params={
                    "stage": cmd.stage,
                    "key": key,
                    "value": value,
                    "branch": branch,
                    "base": RELEASE_BASE_BRANCH,
                    "title": title,
                },
            )
            effects = [
                f"opens a pull request setting {key}={value!r} in samconfig.toml",
                "changes nothing in the deployed stack until that pull request "
                "is merged and deployed",
            ]
        return _plan_for(cmd, [step], effects, confirm=True)

    # -- bootstrap ----------------------------------------------------------

    def _plan_bootstrap(self, cmd: Command) -> Plan:
        """Route ``bootstrap`` to the one-time SAM + GitHub Environment helper.

        Deliberately out-of-band (Requirement 4.2): the routine deploy path is a
        single declarative deploy and never plans these steps.
        """
        return _plan_for(
            cmd,
            [
                PlanStep(
                    description=(
                        f"provision the {cmd.stage} OIDC deploy role and artifact bucket "
                        "(one-time)"
                    ),
                    command=f"sam pipeline bootstrap --stage {cmd.stage}",
                    executor="sam",
                    op=Op.SAM_PIPELINE_BOOTSTRAP,
                    params={"stage": cmd.stage},
                ),
                # GitHub administration is the GitHubExecutor's remit, not the
                # ConfigStore's: ConfigStore stays strictly config-as-data over
                # samconfig.toml + SSM.
                PlanStep(
                    description=f"create or update the {cmd.stage} GitHub Environment",
                    command=(
                        f"gh api --method PUT repos/{{owner}}/{{repo}}/environments/{cmd.stage}"
                    ),
                    executor="github",
                    op=Op.GITHUB_ENVIRONMENT_SET,
                    params={"environment": cmd.stage},
                ),
                PlanStep(
                    description=(
                        "record the OIDC deploy role ARN as the environment's "
                        f"{DEPLOY_ROLE_SECRET} secret"
                    ),
                    command=f"gh secret set {DEPLOY_ROLE_SECRET} --env {cmd.stage}",
                    executor="github",
                    op=Op.GITHUB_SECRET_SET,
                    params={"environment": cmd.stage, "name": DEPLOY_ROLE_SECRET},
                ),
            ],
            [
                f"creates the {cmd.stage} OIDC deploy role and the SAM artifact bucket",
                f"creates or updates the {cmd.stage} GitHub Environment and its "
                "AWS_DEPLOY_ROLE_ARN secret",
                "one-time, out-of-band step: not part of the routine deploy path",
            ],
            confirm=True,
        )

    # -- deploy -------------------------------------------------------------

    def _plan_deploy(self, cmd: Command) -> Plan:
        """Route ``deploy`` by target: LOCAL to the SAM CLI, CI to ``deploy.yml``."""
        match cmd.target:
            case Target.LOCAL:
                return self._plan_local_deploy(cmd)
            case Target.CI:
                return self._plan_ci_deploy(cmd)
            case _:  # pragma: no cover - exhaustive over Target
                assert_never(cmd.target)

    def _plan_local_deploy(self, cmd: Command) -> Plan:
        """Plan a local SAM deploy.

        Reached only for ``target == LOCAL``, and a LOCAL prod deploy ``Command``
        does not exist, so this branch structurally cannot emit
        ``sam deploy --config-env prod``. ``samconfig.toml`` owns the parameter
        set: only ``--config-env`` is selected, never ``--parameter-overrides``
        (Requirement 2.2).
        """
        if _bool_arg(cmd, SYNC_ARG):
            return _plan_for(
                cmd,
                [
                    PlanStep(
                        description=f"sync code changes into the {cmd.stage} stack (fast-loop)",
                        command=f"sam sync --config-env {cmd.stage}",
                        executor="sam",
                        op=Op.SAM_SYNC,
                        params={"config_env": cmd.stage},
                    )
                ],
                [
                    f"updates the {cmd.stage} stack's function code and resources in place",
                    "skips a full CloudFormation deploy: a fast-loop, not a release",
                ],
                confirm=True,
            )
        return _plan_for(
            cmd,
            [
                PlanStep(
                    description="build the deployment artifacts",
                    command="sam build",
                    executor="sam",
                    op=Op.SAM_BUILD,
                ),
                PlanStep(
                    description=f"deploy the {cmd.stage} stack",
                    command=f"sam deploy --config-env {cmd.stage}",
                    executor="sam",
                    op=Op.SAM_DEPLOY,
                    params={"config_env": cmd.stage},
                ),
            ],
            [
                f"updates the {cmd.stage} CloudFormation stack from samconfig.toml's "
                f"[{cmd.stage}] parameter set",
                "the stack self-bootstraps: migrations and bootstrap run inside the deploy",
            ],
            confirm=True,
        )

    def _plan_ci_deploy(self, cmd: Command) -> Plan:
        """Plan the CI deploy: trigger the protected ``deploy.yml`` run.

        The control plane only triggers; CI executes the deploy (Requirement
        5.3). For prod this is the only producible plan (Requirement 6.1).
        """
        effects = [
            f"dispatches the {DEPLOY_WORKFLOW} run that deploys {cmd.stage}",
            "the dispatched run is authoritative: its URL and status are surfaced",
        ]
        if cmd.stage == PROD_STAGE:
            effects.insert(
                1,
                f"deploys nothing until the {PROD_STAGE} GitHub Environment's "
                "required reviewers approve the run",
            )
        return _plan_for(
            cmd,
            [
                PlanStep(
                    description=f"trigger the protected {cmd.stage} deploy job",
                    command=self._workflow_run_command(cmd),
                    executor="github",
                    op=Op.GITHUB_RUN_WORKFLOW,
                    params=self._workflow_run_params(cmd),
                )
            ],
            effects,
            confirm=True,
        )

    # -- release ------------------------------------------------------------

    def _plan_release(self, cmd: Command) -> Plan:
        """Route ``release`` to ``git`` (push a tag) or ``github`` (dispatch).

        Both are sanctioned pipeline triggers of ``deploy.yml`` (Requirement
        7.5); the tag is also the source of ``ApiVersion`` (ADR-0037).
        """
        version = cmd.version
        if version is None:
            raise UsageError(
                field="version",
                value=None,
                problem="a release needs a version, none was given",
                hint="pass a release tag in the format vX.Y.Z",
            )
        if _bool_arg(cmd, DISPATCH_ARG):
            return _plan_for(
                cmd,
                [
                    PlanStep(
                        description=(
                            f"dispatch the {DEPLOY_WORKFLOW} run for {version} to {cmd.stage}"
                        ),
                        command=self._workflow_run_command(cmd),
                        executor="github",
                        op=Op.GITHUB_RUN_WORKFLOW,
                        params=self._workflow_run_params(cmd),
                    )
                ],
                [
                    f"dispatches the {DEPLOY_WORKFLOW} run that deploys {version} to {cmd.stage}",
                    "creates no tag; the dispatched run's URL and status are surfaced",
                ],
                confirm=True,
            )
        return _plan_for(
            cmd,
            [
                PlanStep(
                    description=(
                        f"create the {version} release tag "
                        f"(clean tree, on {RELEASE_BASE_BRANCH}, tag absent)"
                    ),
                    command=f"git tag {version}",
                    executor="git",
                    op=Op.GIT_TAG,
                    params={"version": version, "base_branch": RELEASE_BASE_BRANCH},
                ),
                PlanStep(
                    description=f"push {version} so the tag-triggered pipeline runs",
                    command=f"git push {GIT_REMOTE} {version}",
                    executor="git",
                    op=Op.GIT_PUSH,
                    params={"version": version, "remote": GIT_REMOTE},
                ),
            ],
            [
                f"creates the {version} tag and pushes it to {GIT_REMOTE}",
                f"the pushed tag triggers the {DEPLOY_WORKFLOW} run; {version} becomes ApiVersion",
                f"the {PROD_STAGE} deploy still waits on that environment's required reviewers",
            ],
            confirm=True,
        )

    # -- shared -------------------------------------------------------------

    @staticmethod
    def _workflow_run_command(cmd: Command) -> str:
        """Render the one ``deploy.yml`` dispatch both CI paths use.

        A single renderer keeps the control plane's inputs identical to the
        typed ``workflow_dispatch`` inputs the Actions UI offers (design
        Property 4).
        """
        line = f"gh workflow run {DEPLOY_WORKFLOW} -f stage={cmd.stage}"
        if cmd.version is not None:
            line = f"{line} -f version={cmd.version}"
        return line

    @staticmethod
    def _workflow_run_params(cmd: Command) -> dict[str, str | bool | list[str] | SecretStr]:
        """The typed intent behind that dispatch, for the ``GitHubExecutor``.

        Rendered from the same fields as ``_workflow_run_command`` so the
        dispatched inputs and the previewed line cannot disagree (design
        Property 4). ``version`` is present only when the command carries one.
        """
        params: dict[str, str | bool | list[str] | SecretStr] = {
            "workflow": DEPLOY_WORKFLOW,
            "stage": cmd.stage,
        }
        if cmd.version is not None:
            params["version"] = cmd.version
        return params


__all__ = [
    "DEPLOY_ROLE_SECRET",
    "DEPLOY_WORKFLOW",
    "MASK",
    "MASK_EFFECT",
    "Dispatcher",
    "Op",
]
