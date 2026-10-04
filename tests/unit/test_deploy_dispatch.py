"""Unit tests for ``bdo_deploy.core.dispatch.Dispatcher``.

Two halves, both with executors faked — no ``sam`` / ``gh`` / ``git`` / AWS call
is ever made:

- ``plan()``: the exact plan produced for every capability + target routing the
  dispatcher supports (commands, ops, params, effects, confirmation flag), plus
  planning purity and determinism (Requirements 2.1, 2.2, 2.4).
- ``execute()``: the confirmation gate, step ordering, stop-at-first-failure, an
  unwired executor, and run-URL propagation (Requirements 10.2, 10.4, 10.6).
"""

from __future__ import annotations

import ast
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import pytest
import tomlkit
from pydantic import SecretStr

from bdo_deploy.core import constants
from bdo_deploy.core import validation as validation_module
from bdo_deploy.core.dispatch import (
    DEPLOY_ROLE_SECRET,
    DEPLOY_WORKFLOW,
    GIT_REMOTE,
    MASK,
    MASK_EFFECT,
    RELEASE_BASE_BRANCH,
    Dispatcher,
)
from bdo_deploy.core.errors import ConfirmationRequired, UsageError, exit_code_for
from bdo_deploy.core.executors import config as config_module
from bdo_deploy.core.executors import git as git_module
from bdo_deploy.core.executors import github as github_module
from bdo_deploy.core.executors.config import PARAMETER_OVERRIDES_KEY, PR_BODY, SAMCONFIG_FILE
from bdo_deploy.core.executors.github import SECRET_ENV_PREFIX, RunRef, RunStatus
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import (
    REVIEWERS_ARG,
    Capability,
    Command,
    CommandResult,
    ConfigDiff,
    ConfigView,
    Op,
    Plan,
    PlanStep,
    PrRef,
    Target,
)
from bdo_deploy.core.validation import PROD_STAGE, SSM_ROOT_SEGMENT, samconfig_parameter_names

SSM_KEY = "/bdo-market-insights/dev/domain/api-domain-name"

_SRC: Final = Path(__file__).resolve().parents[2] / "src"
_CONSTANTS_MODULE: Final = "tools/bdo_deploy/core/constants.py"
"""Where the values the core and the executors share are declared, and only there."""
RUN_URL = "https://github.com/RyanYCT/bdo-market-insights/actions/runs/42"
REVIEWER = "User:1234"
"""One required reviewer — the least a ``prod`` bootstrap is constructible with."""

CARRIED_SECRET_KEY: Final = "IconKeyPrefix"
"""A secret-shaped name that is nonetheless an ordinary deploy-time parameter.

Requirement 3.8's allowlist is about a key the target stage's ``samconfig.toml``
*already carries*, and no committed key is secret-shaped today — ``Stage``,
``BdoRegions`` and ``UseRdsProxy`` are the whole set, and task 11.6 is what adds
``EnableDemoKey``. So the allowed population is exercised against a fixture
samconfig that carries this name (``samconfig_carrying`` below) rather than by
waiting for the file to grow one.
"""


@contextmanager
def samconfig_carrying(stage: str, key: str, value: str = "carried") -> Iterator[Path]:
    """Point the validation layer at a ``samconfig.toml`` whose ``stage`` carries ``key``.

    A copy of the real file with one entry appended to ``stage``'s
    ``parameter_overrides``, so everything else about it — the other stage, the
    stage set itself, the SSM stage segments — stays exactly what the repository
    commits. Only the named stage gains the key, which is what makes the
    per-stage half of the allowlist observable.

    The cached parse is dropped on both sides of the swap: it is keyed by path, so
    a stale entry would otherwise let the fixture leak into a later test (or the
    real file into this one).
    """
    document = tomlkit.parse(validation_module.SAMCONFIG_PATH.read_text(encoding="utf-8"))
    parameters = document[stage]["deploy"]["parameters"]
    overrides = str(parameters[PARAMETER_OVERRIDES_KEY])
    parameters[PARAMETER_OVERRIDES_KEY] = f"{overrides} {key}={value}"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "samconfig.toml"
        path.write_text(tomlkit.dumps(document), encoding="utf-8")
        patch = pytest.MonkeyPatch()
        validation_module._samconfig_document.cache_clear()
        patch.setattr(validation_module, "SAMCONFIG_PATH", path)
        try:
            yield path
        finally:
            patch.undo()
            validation_module._samconfig_document.cache_clear()


# -- fakes -------------------------------------------------------------------


class Recorder:
    """The shared call log, so ordering *across* executors is observable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, PlanStep]] = []

    @property
    def ops(self) -> list[Op]:
        return [step.op for _, step in self.calls]

    @property
    def executors(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeExecutor:
    """A ``StepExecutor`` that records its steps and returns scripted outcomes."""

    def __init__(
        self,
        name: str,
        recorder: Recorder,
        *,
        results: list[CommandResult] | None = None,
    ) -> None:
        self.name = name
        self.recorder = recorder
        self.results = list(results or [])

    def run_step(self, step: PlanStep) -> CommandResult:
        self.recorder.calls.append((self.name, step))
        if self.results:
            return self.results.pop(0)
        return CommandResult(ok=True, output=f"{self.name}: ok")


class FakeSamExecutor(FakeExecutor):
    """A ``FakeExecutor`` that also satisfies ``SamExecutor``'s domain methods.

    Only ``run_step`` is ever called through the dispatcher's seam; these exist
    so the fake structurally matches the Protocol the ``sam=`` keyword is typed
    against.
    """

    def validate(self) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: validate")

    def build(self) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: build")

    def deploy(self, config_env: str) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: deploy {config_env}")

    def sync(self, config_env: str) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: sync {config_env}")

    def pipeline_bootstrap(self, stage: str) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: bootstrap {stage}")


class FakeGitHubExecutor(FakeExecutor):
    """A ``FakeExecutor`` that also satisfies ``GitHubExecutor``'s domain methods.

    Only ``run_step`` is ever called through the dispatcher's seam; these exist
    so the fake structurally matches the Protocol the ``github=`` keyword is
    typed against — and they touch no real ``gh``, repository or environment.
    """

    def run_workflow(
        self,
        *,
        stage: str,
        version: str | None = None,
        inputs: dict[str, str] | None = None,
    ) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: dispatch {stage}", run_url=RUN_URL)

    def watch(self, run: RunRef) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: watch {run.workflow}")

    def view(self, run: RunRef) -> RunStatus:
        return RunStatus(run=run, status="completed", conclusion="success")

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
        allowed_refs: list[str] | None = None,
    ) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: environment {name}")

    def set_environment_secret(
        self,
        *,
        environment: str,
        name: str,
        value: str,
    ) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: secret {name} on {environment}")


class FakeGitExecutor(FakeExecutor):
    """A ``FakeExecutor`` that also satisfies ``GitExecutor``'s release methods.

    Only ``run_step`` is ever called through the dispatcher's seam; these two
    exist so the fake structurally matches the Protocol the ``git=`` keyword is
    typed against — and they touch no real git state.
    """

    def release_preconditions(self, version: str) -> list[str]:
        return []

    def tag_and_push(self, version: str) -> CommandResult:
        return CommandResult(ok=True, output=f"{self.name}: tag and push {version}")


class FakeConfigStore(FakeExecutor):
    """A ``FakeExecutor`` that also satisfies ``ConfigStore``'s domain methods.

    Only ``run_step`` is ever called through the dispatcher's seam; these exist
    so the fake structurally matches the Protocol the ``config=`` keyword is
    typed against — and they touch no real SSM parameter and no tracked file.
    """

    def read_merged(self, stage: str) -> ConfigView:
        return ConfigView(stage=stage)

    def open_config_pr(
        self,
        stage: str,
        changes: list[ConfigDiff],
        *,
        branch: str | None = None,
        base: str | None = None,
        title: str | None = None,
    ) -> PrRef:
        return PrRef(
            branch=branch or f"config/{stage}",
            base=base or "main",
            title=title or f"config({stage})",
        )

    def put_ssm(self, path: str, value: str) -> ConfigDiff:
        return ConfigDiff(source="ssm", key=path, before=None, after=value)


def _wired(
    recorder: Recorder,
    *,
    sam: FakeSamExecutor | None = None,
    github: FakeGitHubExecutor | None = None,
    git: FakeGitExecutor | None = None,
    config: FakeConfigStore | None = None,
) -> Dispatcher:
    """A ``Dispatcher`` with all four executors faked unless one is overridden."""
    return Dispatcher(
        sam=sam if sam is not None else FakeSamExecutor("sam", recorder),
        github=github if github is not None else FakeGitHubExecutor("github", recorder),
        git=git if git is not None else FakeGitExecutor("git", recorder),
        config=config if config is not None else FakeConfigStore("config", recorder),
    )


def _plan(cmd: Command) -> Plan:
    """Plan ``cmd`` through a fully faked dispatcher."""
    return _wired(Recorder()).plan(cmd)


def _all_commands() -> list[Command]:
    """One command per routing the dispatcher supports."""
    return [
        Command(capability=Capability.CONFIG),
        Command(
            capability=Capability.CONFIG, args={"action": "set", "key": SSM_KEY, "value": "x"}
        ),
        Command(
            capability=Capability.CONFIG,
            args={"action": "set", "key": "BdoRegions", "value": "NA,EU"},
        ),
        Command(capability=Capability.BOOTSTRAP),
        Command(capability=Capability.DEPLOY, target=Target.LOCAL),
        Command(capability=Capability.DEPLOY, target=Target.LOCAL, args={"sync": True}),
        Command(capability=Capability.DEPLOY, target=Target.CI),
        Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod", version="v1.4.0"),
        Command(capability=Capability.RELEASE, version="v1.4.0"),
        Command(capability=Capability.RELEASE, version="v1.4.0", args={"dispatch": True}),
    ]


# -- A. plan() per capability + target ---------------------------------------


class TestPlanConfigShow:
    """``config show`` is the read-only plan: one merged read, no confirmation."""

    def test_plan(self) -> None:
        plan = _plan(Command(capability=Capability.CONFIG))
        assert plan.capability is Capability.CONFIG
        assert plan.target is Target.LOCAL
        assert plan.steps == [
            PlanStep(
                description=(
                    "read the merged dev config view (samconfig.toml + SSM, secret values masked)"
                ),
                command=(
                    "aws ssm get-parameters-by-path --path /bdo-market-insights/dev/ --recursive"
                ),
                executor="config",
                op=Op.CONFIG_SHOW,
                params={"stage": "dev", "ssm_path": "/bdo-market-insights/dev/"},
            )
        ]
        assert plan.effects == ["nothing changes: reads the dev config and renders it"]
        assert plan.requires_confirmation is False

    def test_show_is_the_default_action(self) -> None:
        explicit = _plan(Command(capability=Capability.CONFIG, args={"action": "show"}))
        assert explicit == _plan(Command(capability=Capability.CONFIG))

    def test_it_is_the_only_plan_that_needs_no_confirmation(self) -> None:
        unconfirmed = [
            (cmd.capability, cmd.args.get("action", "show"))
            for cmd in _all_commands()
            if not _plan(cmd).requires_confirmation
        ]
        assert unconfirmed == [(Capability.CONFIG, "show")]


class TestPlanConfigSet:
    """A write goes to exactly one sanctioned location: SSM, or a samconfig PR."""

    def test_ssm_path_becomes_an_audited_put(self) -> None:
        plan = _plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": SSM_KEY, "value": "api.example.test"},
            )
        )
        assert plan.steps == [
            PlanStep(
                description=f"write the operational value at {SSM_KEY} (audited)",
                command=f"aws ssm put-parameter --name {SSM_KEY} --value {MASK} --overwrite",
                executor="config",
                op=Op.SSM_PUT,
                params={
                    "path": SSM_KEY,
                    "value": SecretStr("api.example.test"),
                    "overwrite": True,
                },
            )
        ]
        assert plan.effects == [
            f"sets {SSM_KEY} in SSM Parameter Store to {MASK_EFFECT}",
            "records an audit entry for the write",
        ]
        assert plan.requires_confirmation is True

    def test_the_executor_still_gets_the_real_operational_value(self) -> None:
        plan = _plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": SSM_KEY, "value": "api.example.test"},
            )
        )
        value = plan.steps[0].params["value"]
        assert isinstance(value, SecretStr)
        assert value.get_secret_value() == "api.example.test"

    def test_deploy_time_config_becomes_a_pull_request(self) -> None:
        plan = _plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": "BdoRegions", "value": "NA,EU"},
            )
        )
        assert plan.steps == [
            PlanStep(
                description=(
                    "set BdoRegions in [dev.deploy.parameters] of samconfig.toml "
                    "on branch config/dev-BdoRegions and open a pull request"
                ),
                command=(
                    "gh api --method POST repos/{owner}/{repo}/pulls "
                    '-f title="config(dev): set BdoRegions=NA,EU" '
                    "-f head=config/dev-BdoRegions -f base=main "
                    f'-f body="{PR_BODY}"'
                ),
                executor="config",
                op=Op.SAMCONFIG_PR,
                params={
                    "stage": "dev",
                    "key": "BdoRegions",
                    "value": "NA,EU",
                    "branch": "config/dev-BdoRegions",
                    "base": "main",
                    "title": "config(dev): set BdoRegions=NA,EU",
                },
            )
        ]
        assert plan.effects == [
            "opens a pull request setting BdoRegions='NA,EU' in samconfig.toml",
            "changes nothing in the deployed stack until that pull request is merged and deployed",
        ]
        assert plan.requires_confirmation is True


class TestPlanBootstrap:
    """The one-time helper: SAM bootstrap plus the GitHub Environment wiring."""

    def test_plan(self) -> None:
        plan = _plan(Command(capability=Capability.BOOTSTRAP))
        assert plan.steps == [
            PlanStep(
                description="provision the dev OIDC deploy role and artifact bucket (one-time)",
                command="sam pipeline bootstrap --stage dev",
                executor="sam",
                op=Op.SAM_PIPELINE_BOOTSTRAP,
                params={"stage": "dev"},
            ),
            PlanStep(
                description="create or update the dev GitHub Environment",
                command="gh api --method PUT repos/{owner}/{repo}/environments/dev",
                executor="github",
                op=Op.GITHUB_ENVIRONMENT_SET,
                params={"environment": "dev"},
            ),
            PlanStep(
                description=(
                    "record the OIDC deploy role ARN as the environment's "
                    f"{DEPLOY_ROLE_SECRET} secret, read from "
                    f"{SECRET_ENV_PREFIX}{DEPLOY_ROLE_SECRET}"
                ),
                command=f"gh secret set {DEPLOY_ROLE_SECRET} --env dev",
                executor="github",
                op=Op.GITHUB_SECRET_SET,
                params={"environment": "dev", "name": DEPLOY_ROLE_SECRET},
            ),
        ]
        assert plan.effects == [
            "creates the dev OIDC deploy role and the SAM artifact bucket",
            "creates or updates the dev GitHub Environment and its AWS_DEPLOY_ROLE_ARN secret",
            f"reads the {DEPLOY_ROLE_SECRET} value from the "
            f"{SECRET_ENV_PREFIX}{DEPLOY_ROLE_SECRET} environment variable, "
            "which must be set before confirming",
            "one-time, out-of-band step: not part of the routine deploy path",
        ]
        assert plan.requires_confirmation is True

    def test_a_prod_bootstrap_plans_the_admitted_refs(self) -> None:
        # Requirements 4.1, 6.5: the policy is planned by the step that creates
        # the Environment, and named in the preview — a ref pattern is not
        # secret, so it is rendered rather than masked.
        step = self._environment_step(PROD_STAGE)
        assert step.params == {
            "environment": PROD_STAGE,
            "reviewers": [REVIEWER],
            "allowed_refs": ["tag:v*", f"branch:{RELEASE_BASE_BRANCH}"],
        }
        assert step.description == (
            f"create or update the {PROD_STAGE} GitHub Environment with required reviewers "
            f"{REVIEWER}, admitting deploys only from tag v* and branch main"
        )
        assert "deployment-branch-policies" in step.command
        assert "tag v* and branch main" in step.command

    def test_the_admitted_refs_are_an_effect_of_a_prod_bootstrap(self) -> None:
        plan = _plan(self._bootstrap(PROD_STAGE))
        assert (
            f"admits only tag v* and branch main as a {PROD_STAGE} deploy ref, "
            "enforced by GitHub outside the repository"
        ) in plan.effects

    def test_a_non_prod_bootstrap_plans_no_policy(self) -> None:
        # The policy restricts prod alone: "deploy this branch to dev" stays
        # expressible, and an absent param leaves any existing policy untouched.
        step = self._environment_step("dev")
        assert "allowed_refs" not in step.params
        plan = _plan(self._bootstrap("dev"))
        assert all("admits only" not in effect for effect in plan.effects)

    @staticmethod
    def _bootstrap(stage: str) -> Command:
        return Command(
            capability=Capability.BOOTSTRAP,
            stage=stage,
            args={REVIEWERS_ARG: [REVIEWER]},
        )

    def _environment_step(self, stage: str) -> PlanStep:
        plan = _plan(self._bootstrap(stage))
        return next(step for step in plan.steps if step.op is Op.GITHUB_ENVIRONMENT_SET)


class TestPlanDeployLocal:
    """LOCAL deploys go to the SAM CLI, and select only ``--config-env``."""

    def test_build_then_deploy(self) -> None:
        plan = _plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        assert plan.steps == [
            PlanStep(
                description="build the deployment artifacts",
                command="sam build",
                executor="sam",
                op=Op.SAM_BUILD,
                params={},
            ),
            PlanStep(
                description="deploy the dev stack",
                command="sam deploy --config-env dev --no-confirm-changeset",
                executor="sam",
                op=Op.SAM_DEPLOY,
                params={"config_env": "dev"},
            ),
        ]
        assert plan.effects == [
            "updates the dev CloudFormation stack from samconfig.toml's [dev] parameter set",
            "the stack self-bootstraps: migrations and bootstrap run inside the deploy",
        ]
        assert plan.requires_confirmation is True

    def test_sync_replaces_the_full_deploy(self) -> None:
        plan = _plan(
            Command(capability=Capability.DEPLOY, target=Target.LOCAL, args={"sync": True})
        )
        assert plan.steps == [
            PlanStep(
                description="sync code changes into the dev stack (fast-loop)",
                command="sam sync --config-env dev",
                executor="sam",
                op=Op.SAM_SYNC,
                params={"config_env": "dev"},
            )
        ]
        assert plan.effects == [
            "updates the dev stack's function code and resources in place",
            "skips a full CloudFormation deploy: a fast-loop, not a release",
        ]
        assert plan.requires_confirmation is True

    def test_sync_false_is_the_full_deploy(self) -> None:
        assert _plan(
            Command(capability=Capability.DEPLOY, target=Target.LOCAL, args={"sync": False})
        ) == _plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))

    @pytest.mark.parametrize("args", [{}, {"sync": True}])
    def test_never_composes_parameter_overrides(self, args: dict[str, str | bool]) -> None:
        """Requirement 2.2: samconfig.toml owns the parameter set."""
        plan = _plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL, args=dict(args)))
        for step in plan.steps:
            assert "--parameter-overrides" not in step.command
            assert "parameter_overrides" not in step.params
        selected = [step.params.get("config_env") for step in plan.steps if step.params]
        assert selected == ["dev"]


class TestPlanDeployCi:
    """CI deploys only trigger the protected ``deploy.yml`` run."""

    def test_without_a_version(self) -> None:
        plan = _plan(Command(capability=Capability.DEPLOY, target=Target.CI))
        assert plan.steps == [
            PlanStep(
                description="trigger the protected dev deploy job",
                command=f"gh workflow run {DEPLOY_WORKFLOW} -f stage=dev",
                executor="github",
                op=Op.GITHUB_RUN_WORKFLOW,
                params={"workflow": DEPLOY_WORKFLOW, "stage": "dev"},
            )
        ]
        assert plan.effects == [
            f"dispatches the {DEPLOY_WORKFLOW} run that deploys dev",
            "the dispatched run is authoritative: its URL and status are surfaced",
        ]
        assert plan.requires_confirmation is True

    def test_with_a_version_and_the_prod_gate(self) -> None:
        plan = _plan(
            Command(
                capability=Capability.DEPLOY,
                target=Target.CI,
                stage="prod",
                version="v1.4.0",
            )
        )
        assert plan.steps == [
            PlanStep(
                description="trigger the protected prod deploy job",
                command=f"gh workflow run {DEPLOY_WORKFLOW} -f stage=prod -f version=v1.4.0",
                executor="github",
                op=Op.GITHUB_RUN_WORKFLOW,
                params={
                    "workflow": DEPLOY_WORKFLOW,
                    "stage": "prod",
                    "version": "v1.4.0",
                },
            )
        ]
        assert plan.effects == [
            f"dispatches the {DEPLOY_WORKFLOW} run that deploys prod",
            "deploys nothing until the prod GitHub Environment's required reviewers "
            "approve the run",
            "the dispatched run is authoritative: its URL and status are surfaced",
        ]
        assert plan.requires_confirmation is True


class TestPlanRelease:
    """A release fires one of the two sanctioned pipeline triggers."""

    def test_tag_push(self) -> None:
        plan = _plan(Command(capability=Capability.RELEASE, version="v1.4.0"))
        assert plan.steps == [
            PlanStep(
                description="create the v1.4.0 release tag (clean tree, on main, tag absent)",
                command="git tag v1.4.0",
                executor="git",
                op=Op.GIT_TAG,
                params={"version": "v1.4.0", "base_branch": "main", "remote": "origin"},
            ),
            PlanStep(
                description="push v1.4.0 so the tag-triggered pipeline runs",
                command="git push origin v1.4.0",
                executor="git",
                op=Op.GIT_PUSH,
                params={"version": "v1.4.0", "remote": "origin"},
            ),
        ]
        assert plan.effects == [
            "creates the v1.4.0 tag and pushes it to origin",
            f"the pushed tag triggers the {DEPLOY_WORKFLOW} run, and v1.4.0 is the source "
            "of ApiVersion for what it deploys (ADR-0037)",
            "ignores the requested stage (dev): a tag run's scope is "
            f"{DEPLOY_WORKFLOW}'s to decide, not this plan's",
            "the prod deploy still waits on that environment's required reviewers",
        ]
        assert plan.requires_confirmation is True

    def test_dispatch(self) -> None:
        plan = _plan(
            Command(capability=Capability.RELEASE, version="v1.4.0", args={"dispatch": True})
        )
        assert plan.steps == [
            PlanStep(
                description=f"dispatch the {DEPLOY_WORKFLOW} run for v1.4.0 to dev",
                command=f"gh workflow run {DEPLOY_WORKFLOW} -f stage=dev -f version=v1.4.0",
                executor="github",
                op=Op.GITHUB_RUN_WORKFLOW,
                params={"workflow": DEPLOY_WORKFLOW, "stage": "dev", "version": "v1.4.0"},
            )
        ]
        assert plan.effects == [
            f"dispatches the {DEPLOY_WORKFLOW} run that deploys v1.4.0 to dev",
            "creates no tag; the dispatched run's URL and status are surfaced",
        ]
        assert plan.requires_confirmation is True

    def test_dispatch_and_tag_push_are_different_plans(self) -> None:
        tag = _plan(Command(capability=Capability.RELEASE, version="v1.4.0"))
        dispatch = _plan(
            Command(capability=Capability.RELEASE, version="v1.4.0", args={"dispatch": True})
        )
        assert tag != dispatch


class TestReleaseTargetNormalisation:
    """Requirement 7.5: a release deploys in CI however it was invoked."""

    @pytest.mark.parametrize("args", [{}, {"dispatch": True}])
    def test_a_release_plan_records_target_ci(self, args: dict[str, bool]) -> None:
        for target in (Target.LOCAL, Target.CI):
            plan = _plan(
                Command(
                    capability=Capability.RELEASE,
                    target=target,
                    version="v1.4.0",
                    args=dict(args),
                )
            )
            assert plan.target is Target.CI

    def test_a_local_release_is_normalised_not_rejected(self) -> None:
        # The tag push and the workflow dispatch happen locally; the deploy does
        # not — so a default target=LOCAL release is normalised, not refused.
        local = _plan(
            Command(capability=Capability.RELEASE, target=Target.LOCAL, version="v1.4.0")
        )
        explicit = _plan(
            Command(capability=Capability.RELEASE, target=Target.CI, version="v1.4.0")
        )
        assert local == explicit

    @pytest.mark.parametrize(
        "cmd_args",
        [
            {"capability": Capability.CONFIG},
            {
                "capability": Capability.CONFIG,
                "args": {"action": "set", "key": "BdoRegions", "value": "NA"},
            },
            {"capability": Capability.BOOTSTRAP},
        ],
    )
    def test_config_and_bootstrap_plan_the_same_steps_for_either_target(
        self, cmd_args: dict[str, object]
    ) -> None:
        local = _plan(Command(target=Target.LOCAL, **cmd_args))
        ci = _plan(Command(target=Target.CI, **cmd_args))
        assert local.steps == ci.steps
        assert local.effects == ci.effects
        assert local.requires_confirmation == ci.requires_confirmation

    def test_deploy_is_the_one_capability_whose_target_routes(self) -> None:
        local = _plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        ci = _plan(Command(capability=Capability.DEPLOY, target=Target.CI))
        assert local.target is Target.LOCAL
        assert ci.target is Target.CI
        assert local.steps != ci.steps


class TestPlanMasking:
    """Requirements 3.7 / 4.4: an operational value is not recoverable from a plan."""

    SECRET = "sup3r-s3cret-value"

    def _ssm_set_plan(self) -> Plan:
        return _plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": SSM_KEY, "value": self.SECRET},
            )
        )

    def test_the_rendered_command_and_effects_are_masked(self) -> None:
        plan = self._ssm_set_plan()
        assert MASK in plan.steps[0].command
        assert self.SECRET not in plan.steps[0].command
        assert MASK_EFFECT in plan.effects[0]
        assert all(self.SECRET not in effect for effect in plan.effects)

    def test_a_serialized_plan_does_not_leak_the_value(self) -> None:
        # The sanity check Requirement 3.7 asks for: --dry-run and --json
        # serialize this model, so the value must be absent from it — not merely
        # omitted by a renderer.
        dumped = self._ssm_set_plan().model_dump_json()
        assert self.SECRET not in dumped
        assert "s3cret" not in dumped
        assert "**********" in dumped

    def test_deploy_time_config_is_not_masked(self) -> None:
        # A samconfig.toml value is bound for a public pull request, so masking
        # it would only make the plan a worse preview of the diff it opens.
        plan = _plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": "BdoRegions", "value": "NA,EU"},
            )
        )
        assert "NA,EU" in plan.steps[0].command
        assert "NA,EU" in plan.model_dump_json()

    def test_a_secret_shaped_deploy_time_key_is_refused_not_rendered(self) -> None:
        # Requirement 3.8: this branch renders key=value into the command, the
        # effects, the PR title and eventually a tracked file, so masking the
        # preview would hide the leak instead of closing it. The reported defect,
        # pinned at the exact invocation that leaked.
        with pytest.raises(UsageError) as raised:
            _plan(
                Command(
                    capability=Capability.CONFIG,
                    args={"action": "set", "key": "ApiToken", "value": self.SECRET},
                )
            )
        error = raised.value
        assert error.exit_code == ExitCode.USAGE_ERROR
        assert self.SECRET not in str(error)
        assert "ApiToken" in str(error)
        assert f"/{SSM_ROOT_SEGMENT}/dev/" in str(error)

    def test_the_predicates_false_positives_are_refused_legibly(self) -> None:
        # The residual trade after the allowlist (Requirement 3.8): the substring
        # test still catches a legitimate parameter name that the stage does not
        # carry yet — ``IconKeyPrefix`` is in no stage's committed set — and
        # introducing it here would put key=value in a PR title and a tracked
        # file. A refusal costs one re-run, a committed secret costs a rotation,
        # but the operator must be able to see *why* it was refused, so the
        # message names samconfig.toml and the substring that matched.
        assert CARRIED_SECRET_KEY not in samconfig_parameter_names("dev"), (
            "this test states the *absent* case; a carried key is planned, not refused"
        )
        with pytest.raises(UsageError) as raised:
            _plan(
                Command(
                    capability=Capability.CONFIG,
                    args={"action": "set", "key": CARRIED_SECRET_KEY, "value": "icons/"},
                )
            )
        summary = str(raised.value)
        assert CARRIED_SECRET_KEY in summary
        assert "'key'" in summary, "the matched substring is what makes the refusal legible"
        assert SAMCONFIG_FILE in summary

    def test_no_plan_renders_a_secret_value(self) -> None:
        for cmd in _all_commands():
            plan = _plan(cmd)
            for step in plan.steps:
                assert MASK not in step.description
                if step.op is Op.SSM_PUT:
                    assert isinstance(step.params["value"], SecretStr)

    def test_the_bootstrap_secret_plan_renders_only_the_secret_name(self) -> None:
        plan = _plan(Command(capability=Capability.BOOTSTRAP))
        secret_step = next(step for step in plan.steps if step.op is Op.GITHUB_SECRET_SET)
        assert secret_step.params == {"environment": "dev", "name": DEPLOY_ROLE_SECRET}
        assert "value" not in secret_step.params
        assert secret_step.command == f"gh secret set {DEPLOY_ROLE_SECRET} --env dev"


class TestSecretShapedAllowlist:
    """Requirement 3.8: a key the stage already carries is planned, not refused.

    The refusal exists to stop a *new* secret-shaped key reaching a plan, a pull
    request title and a tracked file. A key already committed in the stage's
    ``samconfig.toml`` parameter set is already public, so refusing to change it
    protects nothing while blocking an ordinary parameter change
    (``IconKeyPrefix``, whose name merely contains "key").
    """

    VALUE: Final = "icons/v3-9f3a2c1d"

    def _set(self, stage: str, key: str, value: str) -> Plan:
        return _plan(
            Command(
                capability=Capability.CONFIG,
                stage=stage,
                args={"action": "set", "key": key, "value": value},
            )
        )

    def test_a_carried_secret_shaped_key_is_planned_as_a_pull_request(self) -> None:
        with samconfig_carrying("dev", CARRIED_SECRET_KEY):
            plan = self._set("dev", CARRIED_SECRET_KEY, self.VALUE)
        branch = f"config/dev-{CARRIED_SECRET_KEY}"
        title = f"config(dev): set {CARRIED_SECRET_KEY}={self.VALUE}"
        assert [step.op for step in plan.steps] == [Op.SAMCONFIG_PR]
        step = plan.steps[0]
        assert step.executor == "config"
        assert step.params == {
            "stage": "dev",
            "key": CARRIED_SECRET_KEY,
            "value": self.VALUE,
            "branch": branch,
            "base": RELEASE_BASE_BRANCH,
            "title": title,
        }
        assert title in step.command, "the preview is the pull request it opens"
        assert plan.effects[0] == (
            f"opens a pull request setting {CARRIED_SECRET_KEY}={self.VALUE!r} in samconfig.toml"
        )

    def test_an_absent_secret_shaped_key_is_still_refused(self) -> None:
        # Same fixture samconfig, so the difference is the allowlist and nothing
        # else: ``IconKeyPrefix`` is carried, ``ApiToken`` is not.
        with samconfig_carrying("dev", CARRIED_SECRET_KEY), pytest.raises(UsageError) as raised:
            self._set("dev", "ApiToken", self.VALUE)
        error = raised.value
        assert error.exit_code == ExitCode.USAGE_ERROR, "exit 2, before any executor call"
        assert self.VALUE not in str(error), "the refused value must not be quoted back"
        assert "ApiToken" in str(error)

    def test_the_allowlist_is_read_per_stage(self) -> None:
        # The two stages' parameter sets can differ, so a key committed for dev
        # says nothing about prod: the same command against prod is still refused.
        with samconfig_carrying("dev", CARRIED_SECRET_KEY):
            assert self._set("dev", CARRIED_SECRET_KEY, self.VALUE).steps[0].op is Op.SAMCONFIG_PR
            with pytest.raises(UsageError):
                self._set("prod", CARRIED_SECRET_KEY, self.VALUE)

    def test_the_allowlist_adds_no_input_output_of_its_own(self) -> None:
        # Planning stays pure (Requirement 2.4): the allowlist is derived from the
        # samconfig parse validation already cached, so planning under a cache
        # that is already warm reads no file at all.
        with samconfig_carrying("dev", CARRIED_SECRET_KEY) as path:
            samconfig_parameter_names("dev")  # warm the cache, as Command construction does
            reads: list[Path] = []
            original = Path.read_bytes

            def recorded(self: Path) -> bytes:
                reads.append(self)
                return original(self)

            patch = pytest.MonkeyPatch()
            patch.setattr(Path, "read_bytes", recorded)
            try:
                plan = self._set("dev", CARRIED_SECRET_KEY, self.VALUE)
            finally:
                patch.undo()
        assert plan.steps[0].op is Op.SAMCONFIG_PR
        assert path not in reads, "the allowlist must not re-read samconfig.toml while planning"
        assert reads == [], "planning must read no file at all"


class TestSingleExecutorRouting:
    """Requirement 2.1: a plan routes to one executor — bootstrap's documented pair aside."""

    @pytest.mark.parametrize(
        ("index", "primary"),
        [
            (0, "config"),  # config show
            (1, "config"),  # config set -> ssm.put
            (2, "config"),  # config set -> samconfig.pr
            (3, "sam"),  # bootstrap
            (4, "sam"),  # deploy LOCAL
            (5, "sam"),  # deploy LOCAL --sync
            (6, "github"),  # deploy CI
            (7, "github"),  # deploy CI prod
            (8, "git"),  # release via tag push
            (9, "github"),  # release via dispatch
        ],
    )
    def test_primary_executor(self, index: int, primary: str) -> None:
        plan = _plan(_all_commands()[index])
        assert plan.steps[0].executor == primary

    def test_only_bootstrap_spans_two_executors(self) -> None:
        # The design's capability mapping pairs SamExecutor with GitHubExecutor
        # for the one-time bootstrap; every other capability is single-executor.
        spans = {
            cmd.capability
            for cmd in _all_commands()
            if len({step.executor for step in _plan(cmd).steps}) > 1
        }
        assert spans == {Capability.BOOTSTRAP}
        assert {
            step.executor for step in _plan(Command(capability=Capability.BOOTSTRAP)).steps
        } == {
            "sam",
            "github",
        }

    def test_every_step_names_a_known_executor(self) -> None:
        for cmd in _all_commands():
            for step in _plan(cmd).steps:
                assert step.executor in {"sam", "github", "git", "config"}
                assert step.op
                assert step.command


# -- B. planning purity ------------------------------------------------------


class TestPlanningPurity:
    """Requirement 2.4: ``plan()`` reaches no executor, and is deterministic."""

    def test_planning_every_capability_touches_no_executor(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder)
        for cmd in _all_commands():
            dispatcher.plan(cmd)
        assert recorder.calls == []

    def test_a_usage_error_also_touches_no_executor(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder)
        with pytest.raises(UsageError):
            dispatcher.plan(Command(capability=Capability.RELEASE))
        assert recorder.calls == []

    def test_planning_is_deterministic(self) -> None:
        dispatcher = _wired(Recorder())
        for cmd in _all_commands():
            first = dispatcher.plan(cmd).model_dump_json()
            second = dispatcher.plan(cmd).model_dump_json()
            assert first == second

    def test_two_dispatchers_plan_identically(self) -> None:
        for cmd in _all_commands():
            assert (
                _wired(Recorder()).plan(cmd).model_dump_json()
                == Dispatcher().plan(cmd).model_dump_json()
            )


# -- C. plan() usage errors (exit 2) -----------------------------------------


class TestPlanUsageErrors:
    """Intent no executor can serve is rejected at planning, with exit ``2``."""

    def _rejects(self, cmd: Command, field: str) -> UsageError:
        with pytest.raises(UsageError) as excinfo:
            _plan(cmd)
        error = excinfo.value
        assert error.field == field
        assert error.exit_code is ExitCode.USAGE_ERROR
        assert exit_code_for(error) is ExitCode.USAGE_ERROR
        return error

    def test_unknown_config_action(self) -> None:
        error = self._rejects(
            Command(capability=Capability.CONFIG, args={"action": "delete"}), "args.action"
        )
        assert error.value == "delete"
        assert "set" in str(error)

    def test_config_set_without_key(self) -> None:
        error = self._rejects(
            Command(capability=Capability.CONFIG, args={"action": "set", "value": "NA"}),
            "args.key",
        )
        assert error.value is None

    def test_config_set_without_value(self) -> None:
        error = self._rejects(
            Command(capability=Capability.CONFIG, args={"action": "set", "key": "BdoRegions"}),
            "args.value",
        )
        assert error.value is None

    def test_config_set_with_a_non_string_key(self) -> None:
        error = self._rejects(
            Command(
                capability=Capability.CONFIG, args={"action": "set", "key": True, "value": "x"}
            ),
            "args.key",
        )
        assert "string" in str(error)

    def test_config_set_to_a_non_repo_scoped_ssm_path(self) -> None:
        error = self._rejects(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": "/bdo/dev/domain/api", "value": "x"},
            ),
            "ssm_path",
        )
        assert error.value == "/bdo/dev/domain/api"

    def test_config_set_to_an_ssm_path_with_an_undefined_stage(self) -> None:
        error = self._rejects(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": "/bdo-market-insights/prd/domain/x", "value": "x"},
            ),
            "ssm_path",
        )
        assert error.value == "/bdo-market-insights/prd/domain/x"
        assert "'prd'" in str(error)
        assert "prod" in str(error)

    def test_non_boolean_sync(self) -> None:
        error = self._rejects(
            Command(capability=Capability.DEPLOY, target=Target.LOCAL, args={"sync": "yes"}),
            "args.sync",
        )
        assert "boolean" in str(error)

    def test_non_boolean_dispatch(self) -> None:
        error = self._rejects(
            Command(capability=Capability.RELEASE, version="v1.4.0", args={"dispatch": "yes"}),
            "args.dispatch",
        )
        assert "boolean" in str(error)

    def test_sync_on_a_ci_deploy_is_refused_not_dropped(self) -> None:
        """A fast-loop the CI job cannot run is named, never silently widened."""
        error = self._rejects(
            Command(capability=Capability.DEPLOY, target=Target.CI, args={"sync": True}),
            "args.sync",
        )
        assert error.value is True
        assert "cannot be combined with target=ci" in str(error)
        assert "target=local" in str(error)

    def test_release_without_a_version(self) -> None:
        error = self._rejects(Command(capability=Capability.RELEASE), "version")
        assert error.value is None
        assert "vX.Y.Z" in str(error)


# -- D. execute() with faked executors ---------------------------------------


class TestExecuteConfirmationGate:
    """Requirement 10.4: a mutating plan runs nothing until it is confirmed."""

    def test_unconfirmed_mutating_plan_raises_and_runs_nothing(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder)
        plan = dispatcher.plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        with pytest.raises(ConfirmationRequired) as excinfo:
            dispatcher.execute(plan, confirmed=False)
        error = excinfo.value
        assert error.plan == plan
        assert error.exit_code is ExitCode.CONFIRMATION_REQUIRED
        assert exit_code_for(error) is ExitCode.CONFIRMATION_REQUIRED
        assert "nothing has run" in str(error)
        assert recorder.calls == []

    def test_read_only_plan_executes_without_confirmation(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder)
        plan = dispatcher.plan(Command(capability=Capability.CONFIG))
        result = dispatcher.execute(plan, confirmed=False)
        assert result.ok is True
        assert result.exit_code is ExitCode.SUCCESS
        assert recorder.ops == [Op.CONFIG_SHOW]


class TestExecuteStepOrdering:
    """Confirmed execution calls each step's executor once, in plan order."""

    def test_bootstrap_runs_its_three_steps_in_order(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder)
        plan = dispatcher.plan(Command(capability=Capability.BOOTSTRAP))
        result = dispatcher.execute(plan, confirmed=True)
        assert recorder.ops == [
            Op.SAM_PIPELINE_BOOTSTRAP,
            Op.GITHUB_ENVIRONMENT_SET,
            Op.GITHUB_SECRET_SET,
        ]
        assert recorder.executors == ["sam", "github", "github"]
        assert [step for _, step in recorder.calls] == plan.steps
        assert result.ok is True
        assert result.exit_code is ExitCode.SUCCESS
        assert "3 step(s)" in result.summary

    def test_changes_from_every_step_are_collected(self) -> None:
        recorder = Recorder()
        diff = ConfigDiff(source="ssm", key=SSM_KEY, before=None, after="api.example.test")
        config = FakeConfigStore(
            "config",
            recorder,
            results=[CommandResult(ok=True, output="put", changes=[diff])],
        )
        dispatcher = _wired(recorder, config=config)
        plan = dispatcher.plan(
            Command(
                capability=Capability.CONFIG,
                args={"action": "set", "key": SSM_KEY, "value": "api.example.test"},
            )
        )
        result = dispatcher.execute(plan, confirmed=True)
        assert result.changes == [diff]
        assert result.raw_output == "put"


class TestExecuteFailure:
    """Requirement 10.2: stop at the first failure, surface its output verbatim."""

    def test_failure_at_step_two_of_three_skips_the_remainder(self) -> None:
        recorder = Recorder()
        github = FakeGitHubExecutor(
            "github",
            recorder,
            results=[CommandResult(ok=False, output="gh: HTTP 403 forbidden")],
        )
        dispatcher = _wired(recorder, github=github)
        plan = dispatcher.plan(Command(capability=Capability.BOOTSTRAP))
        result = dispatcher.execute(plan, confirmed=True)
        assert recorder.ops == [Op.SAM_PIPELINE_BOOTSTRAP, Op.GITHUB_ENVIRONMENT_SET]
        assert result.ok is False
        assert result.exit_code is ExitCode.EXECUTOR_FAILED
        assert result.raw_output == "gh: HTTP 403 forbidden"
        assert "step 2/3 failed (github)" in result.summary
        assert "1 remaining step(s) skipped" in result.summary

    def test_a_raising_executor_surfaces_its_message_not_a_traceback(self) -> None:
        recorder = Recorder()

        class Raising(FakeSamExecutor):
            def run_step(self, step: PlanStep) -> CommandResult:
                super().run_step(step)
                raise RuntimeError("sam: build failed")

        dispatcher = _wired(recorder, sam=Raising("sam", recorder))
        plan = dispatcher.plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        result = dispatcher.execute(plan, confirmed=True)
        assert recorder.ops == [Op.SAM_BUILD]
        assert result.ok is False
        assert result.exit_code is ExitCode.EXECUTOR_FAILED
        assert result.raw_output == "sam: build failed"
        assert "Traceback" not in result.summary

    def test_an_uninjected_executor_fails_before_any_step_runs(self) -> None:
        recorder = Recorder()
        dispatcher = Dispatcher(config=FakeConfigStore("config", recorder))
        plan = dispatcher.plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        result = dispatcher.execute(plan, confirmed=True)
        assert recorder.calls == []
        assert result.ok is False
        assert result.exit_code is ExitCode.EXECUTOR_FAILED
        assert "'sam'" in result.summary
        assert "not injected" in result.summary
        assert "nothing has run" in result.summary
        assert result.changes == []


class TestExecuteRunUrl:
    """Requirement 10.6: the dispatched run is the authoritative reference."""

    def test_run_url_propagates_from_a_github_step(self) -> None:
        recorder = Recorder()
        github = FakeGitHubExecutor(
            "github",
            recorder,
            results=[CommandResult(ok=True, output="dispatched", run_url=RUN_URL)],
        )
        dispatcher = _wired(recorder, github=github)
        plan = dispatcher.plan(
            Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod", version="v1.4.0")
        )
        result = dispatcher.execute(plan, confirmed=True)
        assert result.ok is True
        assert result.run_url == RUN_URL
        assert RUN_URL in result.summary

    def test_the_structured_run_reference_propagates_too(self) -> None:
        """``Result.run`` carries the executor's own ``RunRef``, unparsed.

        This is what ``follow_run()`` needs: run identity travels from the
        executor that dispatched it, so nothing has to re-derive an id from the
        display URL.
        """
        recorder = Recorder()
        github = FakeGitHubExecutor(
            "github",
            recorder,
            results=[
                CommandResult(
                    ok=True,
                    output="dispatched",
                    run_url=RUN_URL,
                    run=RunRef(workflow=DEPLOY_WORKFLOW, run_id="42", url=RUN_URL),
                )
            ],
        )
        dispatcher = _wired(recorder, github=github)
        plan = dispatcher.plan(
            Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod", version="v1.4.0")
        )
        result = dispatcher.execute(plan, confirmed=True)
        assert result.run == RunRef(workflow=DEPLOY_WORKFLOW, run_id="42", url=RUN_URL)

    def test_a_run_dispatched_before_a_failure_is_still_reported(self) -> None:
        """A failing later step must not lose the run that was already dispatched.

        The operator has a run to look at either way, and the front-end can still
        follow it — dropping the reference on failure would hide a deploy that is
        already moving.
        """
        recorder = Recorder()
        github = FakeGitHubExecutor(
            "github",
            recorder,
            results=[
                CommandResult(
                    ok=False,
                    output="gh: dispatched, then the step reported a failure",
                    run_url=RUN_URL,
                    run=RunRef(workflow=DEPLOY_WORKFLOW, run_id="42", url=RUN_URL),
                )
            ],
        )
        dispatcher = _wired(recorder, github=github)
        plan = dispatcher.plan(
            Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod", version="v1.4.0")
        )
        result = dispatcher.execute(plan, confirmed=True)
        assert result.ok is False
        assert result.run is not None
        assert result.run.run_id == "42"

    def test_a_local_command_carries_no_run(self) -> None:
        recorder = Recorder()
        dispatcher = _wired(recorder, sam=FakeSamExecutor("sam", recorder))
        plan = dispatcher.plan(Command(capability=Capability.DEPLOY, target=Target.LOCAL))
        result = dispatcher.execute(plan, confirmed=True)
        assert result.run is None and result.run_url is None


# -- E. execute() per capability + target ------------------------------------


class TestExecuteEveryRouting:
    """Every supported capability + target executes exactly the plan it produced.

    The plan-side suite above pins each plan's shape; these walk the same
    commands through ``execute()`` so routing, the confirmation gate and failure
    reporting are asserted for *each* capability rather than for one
    representative of it.
    """

    @pytest.mark.parametrize("index", range(len(_all_commands())))
    def test_confirmed_execution_runs_the_planned_steps_in_order(self, index: int) -> None:
        cmd = _all_commands()[index]
        recorder = Recorder()
        dispatcher = _wired(recorder)
        plan = dispatcher.plan(cmd)
        result = dispatcher.execute(plan, confirmed=True)
        assert [step for _, step in recorder.calls] == plan.steps
        assert recorder.executors == [step.executor for step in plan.steps]
        assert result.capability is cmd.capability
        assert result.ok is True
        assert result.exit_code is ExitCode.SUCCESS
        assert f"{len(plan.steps)} step(s)" in result.summary

    @pytest.mark.parametrize("index", range(len(_all_commands())))
    def test_the_confirmation_gate_holds_for_every_mutating_capability(self, index: int) -> None:
        cmd = _all_commands()[index]
        recorder = Recorder()
        dispatcher = _wired(recorder)
        plan = dispatcher.plan(cmd)
        if not plan.requires_confirmation:
            assert dispatcher.execute(plan, confirmed=False).ok is True
            return
        with pytest.raises(ConfirmationRequired) as excinfo:
            dispatcher.execute(plan, confirmed=False)
        assert excinfo.value.plan == plan
        assert recorder.calls == []

    @pytest.mark.parametrize("index", range(len(_all_commands())))
    def test_a_failing_first_step_is_reported_not_raised(self, index: int) -> None:
        cmd = _all_commands()[index]
        recorder = Recorder()
        failure = [CommandResult(ok=False, output="executor: refused")]
        dispatcher = _wired(
            recorder,
            sam=FakeSamExecutor("sam", recorder, results=list(failure)),
            github=FakeGitHubExecutor("github", recorder, results=list(failure)),
            git=FakeGitExecutor("git", recorder, results=list(failure)),
            config=FakeConfigStore("config", recorder, results=list(failure)),
        )
        plan = dispatcher.plan(cmd)
        result = dispatcher.execute(plan, confirmed=True)
        assert len(recorder.calls) == 1
        assert result.ok is False
        assert result.exit_code is ExitCode.EXECUTOR_FAILED
        assert result.raw_output == "executor: refused"
        assert "step 1/" in result.summary


class TestTheSharedConstantsHaveOneHome:
    """The four values the core and its executors both need are declared once.

    ``core.dispatch`` imports every executor module, so an executor cannot import
    it back; each of these values used to be declared twice to dodge that cycle,
    with a comment on the executor copy admitting the clone. ``core.constants`` is
    a leaf both sides import, and these tests are what keeps a copy from coming
    back — equality alone would not, since two modules declaring the same literal
    are equal by construction. The literal count is the assertion that bites: a
    re-cloned value adds a second occurrence of the literal in ``src``.
    """

    def test_the_planner_and_the_executors_read_the_same_values(self) -> None:
        assert (DEPLOY_WORKFLOW, RELEASE_BASE_BRANCH, GIT_REMOTE, MASK) == (
            constants.DEPLOY_WORKFLOW,
            constants.RELEASE_BASE_BRANCH,
            constants.GIT_REMOTE,
            constants.MASK,
        )
        assert git_module.RELEASE_BASE_BRANCH == constants.RELEASE_BASE_BRANCH
        assert git_module.GIT_REMOTE == constants.GIT_REMOTE
        assert github_module.DEFAULT_WORKFLOW == constants.DEPLOY_WORKFLOW
        assert config_module.MASK == constants.MASK

    @pytest.mark.parametrize(
        "name", ["DEPLOY_WORKFLOW", "RELEASE_BASE_BRANCH", "GIT_REMOTE", "MASK"]
    )
    def test_each_value_is_written_out_exactly_once_under_src(self, name: str) -> None:
        declaration = f'{name}: Final = "{getattr(constants, name)}"'
        declaring = [
            str(path.relative_to(_SRC))
            for path in sorted(_SRC.rglob("*.py"))
            if declaration in path.read_text(encoding="utf-8")
        ]
        assert declaring == [_CONSTANTS_MODULE], (
            f"{name} is declared in {declaring}; it belongs only in core.constants"
        )

    def test_the_constants_module_imports_nothing_from_the_package(self) -> None:
        """A leaf is what makes it importable from both sides of the cycle.

        Read off the module's own import statements rather than its text: the
        docstring names ``bdo_deploy`` modules when it explains which sides import
        it, and prose is not an import.
        """
        tree = ast.parse((_SRC / _CONSTANTS_MODULE).read_text(encoding="utf-8"))
        imported = {
            name
            for node in ast.walk(tree)
            for name in (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
        }
        assert not [name for name in imported if name.startswith("bdo_deploy")], imported
