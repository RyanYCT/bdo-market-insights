"""Unit tests for the ``bdo_deploy.core.models`` command-core models.

Covers the JSON wire shape both front-ends share, the validators the models
inherit from ``core.validation``, and the structural guarantees the design
relies on (no LOCAL prod deploy, no ``FLAG`` capability).
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.exit_codes import ExitCode
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


def _command() -> Command:
    return Command(
        capability=Capability.DEPLOY,
        target=Target.CI,
        stage="prod",
        version="v1.4.0",
        args={"guided": True, "stacks": ["api", "etl"], "profile": "default"},
        dry_run=True,
        assume_yes=False,
    )


def _config_diff() -> ConfigDiff:
    return ConfigDiff(
        source="ssm",
        key="/bdo-market-insights/dev/domain/api-domain-name",
        before=None,
        after="api.example.test",
    )


def _plan() -> Plan:
    return Plan(
        capability=Capability.DEPLOY,
        target=Target.LOCAL,
        steps=[
            PlanStep(
                description="deploy the dev stack",
                command="sam deploy --config-env dev",
                executor="sam",
                op=Op.SAM_DEPLOY,
                params={"config_env": "dev"},
            )
        ],
        effects=["updates the dev CloudFormation stack"],
        requires_confirmation=True,
    )


def _result() -> Result:
    return Result(
        capability=Capability.CONFIG,
        ok=False,
        exit_code=ExitCode.USAGE_ERROR,
        summary="stage: 'staging' is not an environment defined in samconfig.toml",
        changes=[_config_diff()],
        run_url="https://github.com/RyanYCT/bdo-market-insights/actions/runs/1",
        raw_output="sam: error",
    )


class TestJsonRoundTrip:
    """``--json`` mode emits serialized models; they must round-trip exactly."""

    def test_command_round_trips(self) -> None:
        command = _command()
        assert Command.model_validate_json(command.model_dump_json()) == command

    def test_config_diff_round_trips(self) -> None:
        diff = _config_diff()
        assert ConfigDiff.model_validate_json(diff.model_dump_json()) == diff

    def test_plan_round_trips(self) -> None:
        plan = _plan()
        assert Plan.model_validate_json(plan.model_dump_json()) == plan

    def test_result_round_trips(self) -> None:
        result = _result()
        assert Result.model_validate_json(result.model_dump_json()) == result

    def test_result_exit_code_serializes_as_plain_int(self) -> None:
        payload = json.loads(_result().model_dump_json())
        assert payload["exit_code"] == 2
        assert isinstance(payload["exit_code"], int)
        assert not isinstance(payload["exit_code"], str)

    def test_capability_and_target_serialize_as_strings(self) -> None:
        payload = json.loads(_command().model_dump_json())
        assert payload["capability"] == "deploy"
        assert payload["target"] == "ci"


class TestOpVocabulary:
    """``Op`` is the closed operation vocabulary executors switch on."""

    def test_members_are_exactly_the_designed_vocabulary(self) -> None:
        assert {op.value for op in Op} == {
            "sam.build",
            "sam.deploy",
            "sam.sync",
            "sam.pipeline_bootstrap",
            "github.run_workflow",
            "github.environment_set",
            "github.secret_set",
            "git.tag",
            "git.push",
            "config.show",
            "ssm.put",
            "samconfig.pr",
        }

    def test_github_ops_are_not_spelled_gh_or_actions(self) -> None:
        # They name the renamed GitHubExecutor, not the old ActionsDispatcher.
        assert {op.value for op in Op if op.value.startswith("github.")}
        assert not [op for op in Op if op.value.startswith(("gh.", "actions."))]

    def test_op_serializes_as_its_string_value(self) -> None:
        payload = json.loads(_plan().model_dump_json())
        assert payload["steps"][0]["op"] == "sam.deploy"

    def test_an_unknown_op_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="sam.destroy"):
            PlanStep(
                description="destroy the stack",
                command="sam destroy",
                executor="sam",
                op="sam.destroy",
            )


class TestPlanStepExecutors:
    """``PlanStep.executor`` names one of the four injected adapters."""

    @pytest.mark.parametrize("executor", ["sam", "github", "git", "config"])
    def test_accepts_every_known_executor(self, executor: str) -> None:
        step = PlanStep(
            description="do the thing",
            command="gh --version",
            executor=executor,
            op=Op.CONFIG_SHOW,
        )
        assert step.executor == executor

    def test_rejects_the_former_actions_executor(self) -> None:
        with pytest.raises(ValueError, match="actions"):
            PlanStep(
                description="dispatch the run",
                command="gh workflow run deploy.yml",
                executor="actions",
                op=Op.GITHUB_RUN_WORKFLOW,
            )


class TestPlanStepSecretParams:
    """Requirements 3.7 / 4.4: a secret param is absent from the serialized model."""

    def _step(self) -> PlanStep:
        return PlanStep(
            description="write the operational value",
            command="aws ssm put-parameter --name /bdo-market-insights/dev/domain/x --value ***",
            executor="config",
            op=Op.SSM_PUT,
            params={"path": "/bdo-market-insights/dev/domain/x", "value": SecretStr("s3cret")},
        )

    def test_a_secret_str_param_survives_as_a_secret_str(self) -> None:
        value = self._step().params["value"]
        assert isinstance(value, SecretStr)
        assert value.get_secret_value() == "s3cret"

    def test_the_secret_is_not_in_the_serialized_step(self) -> None:
        dumped = self._step().model_dump_json()
        assert "s3cret" not in dumped
        assert "**********" in dumped

    def test_plain_string_params_are_untouched(self) -> None:
        assert self._step().params["path"] == "/bdo-market-insights/dev/domain/x"


class TestResultPlan:
    """``Result.plan`` carries the plan a confirmation gate refused (Req 10.4)."""

    def test_defaults_to_none(self) -> None:
        assert _result().plan is None

    def test_carries_the_refused_plan_and_round_trips(self) -> None:
        plan = _plan()
        result = Result(
            capability=Capability.DEPLOY,
            ok=False,
            exit_code=ExitCode.CONFIRMATION_REQUIRED,
            summary="deploy: confirmation required, nothing has run",
            plan=plan,
        )
        assert result.plan == plan
        assert Result.model_validate_json(result.model_dump_json()) == result

    def test_the_refused_plan_is_serialized_with_its_effects(self) -> None:
        result = Result(
            capability=Capability.DEPLOY,
            ok=False,
            exit_code=ExitCode.CONFIRMATION_REQUIRED,
            summary="deploy: confirmation required, nothing has run",
            plan=_plan(),
        )
        payload = json.loads(result.model_dump_json())
        assert payload["plan"]["effects"] == ["updates the dev CloudFormation stack"]
        assert payload["plan"]["requires_confirmation"] is True


class TestCapabilityMembers:
    """Runtime flags are deferred, so no ``FLAG`` capability may be advertised."""

    def test_exactly_four_capabilities(self) -> None:
        assert {capability.value for capability in Capability} == {
            "config",
            "bootstrap",
            "deploy",
            "release",
        }

    def test_no_flag_capability(self) -> None:
        assert not hasattr(Capability, "FLAG")
        with pytest.raises(ValueError, match="flag"):
            Capability("flag")


class TestCommandStageAndVersion:
    """Stage membership and the release-tag regex are enforced on ``Command``."""

    def test_accepts_stages_defined_in_samconfig(self) -> None:
        for stage in ("dev", "prod"):
            assert Command(capability=Capability.CONFIG, stage=stage).stage == stage

    def test_rejects_unknown_stage(self) -> None:
        with pytest.raises(UsageError) as excinfo:
            Command(capability=Capability.CONFIG, stage="staging")
        assert excinfo.value.field == "stage"
        assert "staging" in str(excinfo.value)

    def test_version_defaults_to_none(self) -> None:
        assert Command(capability=Capability.CONFIG).version is None

    def test_accepts_release_tag_version(self) -> None:
        command = Command(capability=Capability.RELEASE, version="v1.4.0")
        assert command.version == "v1.4.0"

    @pytest.mark.parametrize("version", ["1.4.0", "v1.4", "v1.4.0-rc1", ""])
    def test_rejects_malformed_version(self, version: str) -> None:
        with pytest.raises(UsageError) as excinfo:
            Command(capability=Capability.RELEASE, version=version)
        assert excinfo.value.field == "version"
        assert excinfo.value.exit_code is ExitCode.USAGE_ERROR


class TestNoLocalProdDeploy:
    """Design Property 2 / Requirement 5.2: no first-party prod deploy."""

    def test_construction_is_rejected(self) -> None:
        with pytest.raises(UsageError) as excinfo:
            Command(capability=Capability.DEPLOY, target=Target.LOCAL, stage="prod")
        error = excinfo.value
        assert error.field == "target"
        assert error.value == "local"
        assert error.exit_code is ExitCode.USAGE_ERROR
        message = str(error)
        assert "target" in message
        assert "prod" in message
        assert "release" in message

    def test_local_is_the_default_target_so_the_default_prod_deploy_is_rejected(self) -> None:
        with pytest.raises(UsageError):
            Command(capability=Capability.DEPLOY, stage="prod")

    def test_mutation_path_is_closed(self) -> None:
        # validate_assignment=True is deliberate: the combination cannot be
        # reached by mutating an existing command either. (Pydantic applies the
        # new value before the after-validator runs, so the raising instance is
        # left dirty and callers must discard it rather than reuse it.)
        command = Command(capability=Capability.DEPLOY, target=Target.LOCAL, stage="dev")
        with pytest.raises(UsageError) as excinfo:
            command.stage = "prod"
        assert excinfo.value.field == "target"

    def test_mutating_target_onto_a_prod_deploy_is_closed(self) -> None:
        command = Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod")
        with pytest.raises(UsageError) as excinfo:
            command.target = Target.LOCAL
        assert excinfo.value.field == "target"

    def test_prod_deploy_is_allowed_with_target_ci(self) -> None:
        command = Command(capability=Capability.DEPLOY, target=Target.CI, stage="prod")
        assert command.stage == "prod"
        assert command.target is Target.CI

    def test_local_dev_deploy_is_allowed(self) -> None:
        command = Command(capability=Capability.DEPLOY, target=Target.LOCAL, stage="dev")
        assert command.stage == "dev"

    @pytest.mark.parametrize(
        ("capability", "args"),
        [
            (Capability.CONFIG, {}),
            # A prod bootstrap needs its reviewers (Requirement 4.5); that is a
            # separate rule, and passing them shows this one still lets it through.
            (Capability.BOOTSTRAP, {"reviewers": ["User:1234"]}),
            (Capability.RELEASE, {}),
        ],
    )
    def test_non_deploy_capabilities_are_unaffected(
        self, capability: Capability, args: dict[str, str | bool | list[str]]
    ) -> None:
        command = Command(capability=capability, target=Target.LOCAL, stage="prod", args=args)
        assert command.stage == "prod"
        assert command.target is Target.LOCAL


class TestConfigDiffSsmScoping:
    """Requirement 9.1 is enforced by ``ConfigDiff``, and only for ``ssm``."""

    def test_accepts_repo_scoped_ssm_key(self) -> None:
        diff = ConfigDiff(
            source="ssm",
            key="/bdo-market-insights/dev/domain/api-domain-name",
            before=None,
            after="api.example.test",
        )
        assert diff.key == "/bdo-market-insights/dev/domain/api-domain-name"

    @pytest.mark.parametrize(
        "key",
        [
            "/bdo/dev/domain/api-domain-name",
            "/bdo-market-insights/dev//api-domain-name",
            "/bdo-market-insights/dev/domain",
            "/bdo-market-insights/dev/domain/api/extra",
            "bdo-market-insights/dev/domain/api-domain-name",
        ],
    )
    def test_rejects_non_repo_scoped_ssm_key(self, key: str) -> None:
        with pytest.raises(UsageError) as excinfo:
            ConfigDiff(source="ssm", key=key, before=None, after="x")
        assert excinfo.value.field == "ssm_path"
        assert excinfo.value.value == key

    def test_samconfig_source_is_not_ssm_scoped(self) -> None:
        diff = ConfigDiff(
            source="samconfig",
            key="dev.deploy.parameters.BdoRegions",
            before="NA",
            after="NA,EU",
        )
        assert diff.key == "dev.deploy.parameters.BdoRegions"

    def test_ssm_scoping_survives_assignment(self) -> None:
        diff = ConfigDiff(
            source="samconfig",
            key="dev.deploy.parameters.BdoRegions",
            before=None,
            after="NA",
        )
        with pytest.raises(UsageError) as excinfo:
            diff.source = "ssm"
        assert excinfo.value.field == "ssm_path"
