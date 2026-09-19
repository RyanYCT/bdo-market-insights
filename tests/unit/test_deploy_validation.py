"""Unit tests for ``bdo_deploy.core.validation`` and the exit-code contract.

Pure tests: no AWS, no subprocess, no network. They exercise the rules the
command core enforces before any executor is reached (Requirements 7.2, 9.1,
10.3, 10.7).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from bdo_deploy.core.errors import (
    ConfirmationRequired,
    ExecutorFailed,
    UsageError,
    exit_code_for,
)
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.validation import (
    SAMCONFIG_PATH,
    samconfig_stages,
    validate_ssm_path,
    validate_stage,
    validate_version,
)


class TestReleaseVersion:
    """The release-tag regex ``^v\\d+\\.\\d+\\.\\d+$`` (Requirement 7.2)."""

    @pytest.mark.parametrize("version", ["v1.4.0", "v0.0.0", "v10.20.30"])
    def test_accepts_release_tag(self, version: str) -> None:
        assert validate_version(version) == version

    @pytest.mark.parametrize(
        "version",
        [
            "1.4.0",  # no leading v
            "v1.4",  # too few components
            "v1.4.0.1",  # too many components
            "v1.4.0-rc1",  # pre-release suffix
            "V1.4.0",  # wrong case
            " v1.4.0",  # leading whitespace
            "v1.4.0\n",  # trailing newline (anchored $ must not admit it)
            "",
        ],
    )
    def test_rejects_non_release_tag(self, version: str) -> None:
        with pytest.raises(UsageError) as excinfo:
            validate_version(version)
        assert excinfo.value.field == "version"
        assert excinfo.value.exit_code is ExitCode.USAGE_ERROR
        assert "vX.Y.Z" in str(excinfo.value)


class TestStageMembership:
    """``stage`` must name an environment defined in ``samconfig.toml``."""

    def test_stages_come_from_samconfig_and_exclude_default(self) -> None:
        stages = samconfig_stages()
        assert stages
        assert "default" not in stages
        # Read independently of the module under test: every top-level table in
        # the file except [default] is a --config-env.
        raw = SAMCONFIG_PATH.read_text(encoding="utf-8")
        expected = {
            line[1:].split(".", 1)[0].split("]", 1)[0]
            for line in raw.splitlines()
            if line.startswith("[")
        } - {"default"}
        assert stages == frozenset(expected)

    def test_accepts_every_stage_defined_in_samconfig(self) -> None:
        for stage in samconfig_stages():
            assert validate_stage(stage) == stage

    def test_rejects_unknown_stage_naming_field_and_allowed_set(self) -> None:
        with pytest.raises(UsageError) as excinfo:
            validate_stage("staging")
        error = excinfo.value
        assert error.field == "stage"
        assert error.value == "staging"
        assert error.exit_code is ExitCode.USAGE_ERROR
        message = str(error)
        assert "stage" in message
        assert "'staging'" in message
        for stage in samconfig_stages():
            assert stage in message


class TestRepoScopedSsmPath:
    """Writes must name ``/bdo-market-insights/<stage>/<category>/<key>`` (Req 9.1)."""

    @pytest.mark.parametrize(
        "path",
        [
            "/bdo-market-insights/dev/domain/api-domain-name",
            "/bdo-market-insights/prod/discord/webhook-url",
        ],
    )
    def test_accepts_repo_scoped_path(self, path: str) -> None:
        assert validate_ssm_path(path) == path

    @pytest.mark.parametrize(
        "path",
        [
            "/bdo/dev/domain/api-domain-name",  # bare /bdo/... root
            "/bdo-market-insights/dev//api-domain-name",  # empty segment
            "/bdo-market-insights/dev/domain",  # too few segments
            "/bdo-market-insights/dev/domain/api/extra",  # too many segments
            "bdo-market-insights/dev/domain/api-domain-name",  # not absolute
            "/bdo-market-insights/prd/domain/api-domain-name",  # mistyped stage
            "/bdo-market-insights/staging/domain/api-domain-name",  # undefined stage
            "",
        ],
    )
    def test_rejects_non_repo_scoped_path(self, path: str) -> None:
        with pytest.raises(UsageError) as excinfo:
            validate_ssm_path(path)
        error = excinfo.value
        assert error.field == "ssm_path"
        assert error.value == path
        assert error.exit_code is ExitCode.USAGE_ERROR
        assert "/bdo-market-insights/<stage>/<category>/<key>" in str(error)

    def test_stage_segment_must_be_a_samconfig_environment(self) -> None:
        """Requirement 9.1: a typo'd stage would create a parameter nothing reads."""
        with pytest.raises(UsageError) as excinfo:
            validate_ssm_path("/bdo-market-insights/prd/domain/api-domain-name")
        message = str(excinfo.value)
        assert "'prd'" in message
        for stage in samconfig_stages():
            assert stage in message

    def test_every_samconfig_stage_is_accepted_as_the_stage_segment(self) -> None:
        for stage in samconfig_stages():
            path = f"/bdo-market-insights/{stage}/domain/api-domain-name"
            assert validate_ssm_path(path) == path


class TestExitCodeMapping:
    """``exit_code_for`` is the single outcome -> exit-code mapping (Req 10.7)."""

    def test_exit_code_value_set_is_exactly_zero_to_three(self) -> None:
        assert {code.value for code in ExitCode} == {0, 1, 2, 3}

    def test_success_maps_to_zero(self) -> None:
        assert exit_code_for(None) is ExitCode.SUCCESS

    def test_executor_failure_maps_to_one(self) -> None:
        assert exit_code_for(ExecutorFailed("sam deploy failed")) is ExitCode.EXECUTOR_FAILED

    def test_usage_error_maps_to_two(self) -> None:
        outcome = UsageError(field="stage", value="staging", problem="unknown stage")
        assert exit_code_for(outcome) is ExitCode.USAGE_ERROR

    def test_confirmation_required_maps_to_three(self) -> None:
        assert exit_code_for(ConfirmationRequired("needs --yes")) is ExitCode.CONFIRMATION_REQUIRED

    def test_pydantic_validation_error_maps_to_two(self) -> None:
        class _Model(BaseModel):
            count: int

        with pytest.raises(ValidationError) as excinfo:
            _Model(count="not-an-int")
        assert exit_code_for(excinfo.value) is ExitCode.USAGE_ERROR

    @pytest.mark.parametrize(
        "outcome",
        [RuntimeError("boom"), KeyError("missing"), ValueError("bad")],
    )
    def test_unexpected_exception_maps_to_one(self, outcome: BaseException) -> None:
        assert exit_code_for(outcome) is ExitCode.EXECUTOR_FAILED
