"""Adapter over the SAM CLI for LOCAL, non-prod work.

``samconfig.toml`` environments own the parameter set: this executor selects only
``--config-env`` and **never composes ``--parameter-overrides``** (Requirement
2.2). There is therefore nothing here that could assemble a CloudFormation
parameter set by hand, and no place a parameter could be silently overridden.

Two types live here, and the split is deliberate:

- ``SamExecutor`` — the Protocol the design documents and ``Dispatcher`` type-hints
  against. It is the interface; it prescribes no invocation.
- ``SamCli`` — the concrete adapter that satisfies it by shelling out to ``sam``
  through the shared ``run_command`` runner. The runner is injected, so the
  argument list an op produces can be asserted without a real ``sam`` on the
  machine.

``deploy()`` refuses ``config_env == "prod"``. That refusal is **defence in depth**
behind the model invariant, not a replacement for it: a LOCAL prod deploy
``Command`` cannot be constructed (``core.models``) and no plan can emit
``sam deploy --config-env prod`` (``core.dispatch``), so this executor should never
see one. If it ever did — a hand-built ``PlanStep``, a future planning bug — the
last thing between that step and production says no (Requirement 6.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, assert_never

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import CommandRunner, run_command
from bdo_deploy.core.executors.base import StepExecutor
from bdo_deploy.core.models import CommandResult, Op, PlanStep
from bdo_deploy.core.validation import PROD_STAGE

SAM: Final = "sam"
"""The sanctioned executable; every invocation below starts with it."""

CONFIG_ENV_FLAG: Final = "--config-env"
"""The **only** deploy-shaping flag this executor selects (Requirement 2.2)."""

CONFIG_ENV_PARAM: Final = "config_env"
STAGE_PARAM: Final = "stage"


class SamExecutor(StepExecutor, Protocol):
    """Protocol for the SAM CLI adapter — the interface, not an invocation."""

    def validate(self) -> CommandResult:
        """``sam validate --lint`` — check the template without deploying."""
        ...

    def build(self) -> CommandResult:
        """``sam build`` — produce the deployment artifacts."""
        ...

    def deploy(self, config_env: str) -> CommandResult:
        """``sam deploy --config-env <env>``. Refuses ``config_env == "prod"``."""
        ...

    def sync(self, config_env: str) -> CommandResult:
        """``sam sync --config-env <env>`` — the dev fast-loop."""
        ...

    def pipeline_bootstrap(self, stage: str) -> CommandResult:
        """``sam pipeline bootstrap --stage <stage>`` — one-time, non-interactive."""
        ...


class SamCli:
    """``SamExecutor`` implemented by shelling out to the ``sam`` CLI.

    Satisfies the Protocol **structurally** rather than by inheritance — the
    ``TYPE_CHECKING`` binding at the end of this module is what makes mypy prove
    it — so ``Dispatcher``'s ``SamExecutor``-typed parameter accepts it without
    ``dispatch.py`` knowing this class exists.
    """

    def __init__(self, *, runner: CommandRunner = run_command) -> None:
        self._run = runner

    # -- typed domain methods ----------------------------------------------

    def validate(self) -> CommandResult:
        """Lint-validate the template; reads only, changes nothing."""
        return self._run([SAM, "validate", "--lint"])

    def build(self) -> CommandResult:
        """Build the deployment artifacts."""
        return self._run([SAM, "build"])

    def deploy(self, config_env: str) -> CommandResult:
        """Deploy the ``config_env`` stack from its ``samconfig.toml`` parameter set.

        Raises ``UsageError`` for ``config_env == "prod"`` before invoking
        anything, so no production CloudFormation call is ever made from here
        (Requirement 6.1).
        """
        self._refuse_prod(config_env)
        return self._run([SAM, "deploy", CONFIG_ENV_FLAG, config_env])

    def sync(self, config_env: str) -> CommandResult:
        """Sync code into the ``config_env`` stack in place (dev fast-loop)."""
        return self._run([SAM, "sync", CONFIG_ENV_FLAG, config_env])

    def pipeline_bootstrap(self, stage: str) -> CommandResult:
        """Provision ``stage``'s OIDC deploy role and artifact bucket (one-time).

        No confirmation prompt is answered here and no interactive flow is driven:
        the invocation is the plain one, so if ``sam`` would need input it fails
        visibly rather than being fed a guessed answer.
        """
        return self._run([SAM, "pipeline", "bootstrap", "--stage", stage])

    # -- the StepExecutor seam ---------------------------------------------

    def run_step(self, step: PlanStep) -> CommandResult:
        """Dispatch ``step``'s op onto the domain method that performs it.

        Switches on ``step.op`` and reads typed values out of ``step.params``; it
        **never parses ``step.command``**, which is the display rendering only.
        The match covers every ``Op`` member — the sam ops onto a method, every
        other op onto a named refusal — so ``assert_never`` makes adding an op
        without handling it a type error rather than a silent fallthrough.
        """
        match step.op:
            case Op.SAM_BUILD:
                return self.build()
            case Op.SAM_DEPLOY:
                return self.deploy(_str_param(step, CONFIG_ENV_PARAM))
            case Op.SAM_SYNC:
                return self.sync(_str_param(step, CONFIG_ENV_PARAM))
            case Op.SAM_PIPELINE_BOOTSTRAP:
                return self.pipeline_bootstrap(_str_param(step, STAGE_PARAM))
            case (
                Op.GITHUB_RUN_WORKFLOW
                | Op.GITHUB_ENVIRONMENT_SET
                | Op.GITHUB_SECRET_SET
                | Op.GIT_TAG
                | Op.GIT_PUSH
                | Op.CONFIG_SHOW
                | Op.SSM_PUT
                | Op.SAMCONFIG_PR
            ):
                raise UsageError(
                    field="op",
                    value=step.op.value,
                    problem=f"{step.op.value!r} is not a SAM operation",
                    hint=f"route it to the {step.executor!r} executor instead",
                )
            case _:  # pragma: no cover - exhaustive over Op
                assert_never(step.op)

    @staticmethod
    def _refuse_prod(config_env: str) -> None:
        """Refuse a production deploy, naming the field and the sanctioned path."""
        if config_env == PROD_STAGE:
            raise UsageError(
                field=CONFIG_ENV_PARAM,
                value=config_env,
                problem=(
                    f"the SAM executor does not deploy {PROD_STAGE}; production is reached "
                    "only by dispatching the environment-protected CI job"
                ),
                hint="use `release` to tag a version and let that CI job deploy",
            )


def _str_param(step: PlanStep, name: str) -> str:
    """Read a required string out of ``step.params``, or raise ``UsageError``.

    A step reaching an executor without the value its op documents is a planning
    fault; naming the missing param beats a ``KeyError`` or an invocation built
    around ``None``.
    """
    value = step.params.get(name)
    if not isinstance(value, str):
        raise UsageError(
            field=f"params.{name}",
            value=value,
            problem=f"{step.op.value} needs a string {name!r} param",
        )
    return value


if TYPE_CHECKING:  # pragma: no cover - a type-check-time assertion, not runtime code
    # ``Dispatcher`` type-hints against the Protocol, so the concrete adapter has
    # to be structurally compatible with it. Binding one to the other here makes
    # mypy fail this module if a method's name or signature ever drifts.
    _satisfies_protocol: SamExecutor = SamCli()


__all__ = ["CONFIG_ENV_FLAG", "SAM", "SamCli", "SamExecutor"]
