"""Unit tests for the CLI front-end, ``bdo_deploy.cli``.

Every test drives the real front-end in-process via ``main([...])`` with a faked
``Dispatcher`` injected, so no ``sam`` / ``gh`` / ``git`` / AWS call is ever made
and no subprocess is spawned. The fake follows the pattern of
``test_deploy_dispatch.py``: it records what it was asked to do and returns
scripted outcomes.

What is asserted is the front-end's contract, not the core's behaviour: the
subcommand set, intent collection into a ``Command``, the ``--json`` / ``--yes``
/ ``--dry-run`` flags, that nothing is ever prompted for, and the exit codes
(Requirements 1.1, 1.2, 10.1, 10.2, 10.4, 10.7).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from bdo_deploy.cli import app, main
from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.errors import ConfirmationRequired, ExecutorFailed
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import (
    Capability,
    Command,
    Op,
    Plan,
    PlanStep,
    Result,
    Target,
)

SSM_KEY = "/bdo-market-insights/dev/domain/api-domain-name"
RUN_URL = "https://github.com/RyanYCT/bdo-market-insights/actions/runs/42"


# -- fakes -------------------------------------------------------------------


class FakeDispatcher(Dispatcher):
    """A ``Dispatcher`` whose ``plan`` / ``execute`` are recorded and scripted.

    Subclasses the real class rather than duck-typing it because the front-end is
    typed against ``Dispatcher``; overriding the two methods it calls is enough,
    and no executor is injected, so a mistaken call into the base implementation
    would fail loudly instead of reaching a tool.

    ``execute`` reproduces the one behaviour of the core the CLI is defined
    against: an unconfirmed mutating plan **raises** ``ConfirmationRequired``.
    """

    def __init__(
        self,
        *,
        result: Result | None = None,
        error: Exception | None = None,
        plan_error: Exception | None = None,
        requires_confirmation: bool = True,
    ) -> None:
        super().__init__()
        self.planned: list[Command] = []
        self.executed: list[tuple[Plan, bool]] = []
        self._result = result
        self._error = error
        self._plan_error = plan_error
        self._requires_confirmation = requires_confirmation

    def plan(self, cmd: Command) -> Plan:
        self.planned.append(cmd)
        if self._plan_error is not None:
            raise self._plan_error
        return Plan(
            capability=cmd.capability,
            target=cmd.target,
            steps=[
                PlanStep(
                    description="build the deployment artifacts",
                    command="sam build",
                    executor="sam",
                    op=Op.SAM_BUILD,
                )
            ],
            effects=["changes the dev stack"],
            requires_confirmation=self._requires_confirmation,
        )

    def execute(self, plan: Plan, *, confirmed: bool) -> Result:
        self.executed.append((plan, confirmed))
        if plan.requires_confirmation and not confirmed:
            raise ConfirmationRequired(
                f"{plan.capability.value}: confirmation required, nothing has run",
                plan=plan,
            )
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return Result(
            capability=plan.capability,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary=f"{plan.capability.value}: completed {len(plan.steps)} step(s)",
        )


def _json_result(out: str) -> dict[str, Any]:
    """Parse stdout as the sole serialized ``Result`` it must contain."""
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1, f"stdout carried {len(lines)} line(s), expected exactly one Result"
    parsed: dict[str, Any] = json.loads(lines[0])
    return parsed


# -- A. the subcommand surface (Requirement 1.1) ------------------------------


class TestSubcommands:
    """One subcommand per capability, and no subcommand without an executor."""

    def test_one_subcommand_per_capability(self) -> None:
        registered = {command.name for command in app.registered_commands}
        registered |= {group.name for group in app.registered_groups}
        assert registered == {capability.value for capability in Capability}

    def test_config_exposes_show_and_set(self) -> None:
        (config_group,) = app.registered_groups
        nested = config_group.typer_instance
        assert nested is not None
        assert {command.name for command in nested.registered_commands} == {"show", "set"}

    def test_help_labels_bootstrap_as_one_time(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Requirement 4.2: help output says bootstrap is a one-time step."""
        assert main(["bootstrap", "--help"]) == ExitCode.SUCCESS
        rendered = " ".join(capsys.readouterr().out.split())
        assert "ONE-TIME, out-of-band step" in rendered
        assert "NOT part of the routine deploy path" in rendered


# -- B. intent collection ----------------------------------------------------


class TestIntentCollection:
    """Flags become fields of one typed ``Command``; the CLI decides nothing else."""

    def test_deploy_collects_target_stage_and_flags(self) -> None:
        dispatcher = FakeDispatcher()
        argv = ["deploy", "--stage", "prod", "--target", "ci", "--yes"]
        assert main(argv, dispatcher=dispatcher) == ExitCode.SUCCESS
        (cmd,) = dispatcher.planned
        assert cmd == Command(
            capability=Capability.DEPLOY,
            target=Target.CI,
            stage="prod",
            args={"sync": False},
            assume_yes=True,
        )

    def test_release_collects_version_and_dispatch_toggle(self) -> None:
        dispatcher = FakeDispatcher()
        main(["release", "v1.4.0", "--dispatch", "--yes"], dispatcher=dispatcher)
        (cmd,) = dispatcher.planned
        assert cmd.capability is Capability.RELEASE
        assert cmd.version == "v1.4.0"
        assert cmd.args == {"dispatch": True}

    def test_bootstrap_collects_repeatable_reviewers(self) -> None:
        dispatcher = FakeDispatcher()
        main(
            ["bootstrap", "--stage", "prod", "--reviewer", "User:1", "--reviewer", "Team:2", "-y"],
            dispatcher=dispatcher,
        )
        (cmd,) = dispatcher.planned
        assert cmd.args == {"reviewers": ["User:1", "Team:2"]}

    def test_config_show_and_set_collect_their_action(self) -> None:
        dispatcher = FakeDispatcher(requires_confirmation=False)
        main(["config", "show"], dispatcher=dispatcher)
        main(["config", "set", "BdoRegions", "NA,EU", "--yes"], dispatcher=dispatcher)
        assert [cmd.args for cmd in dispatcher.planned] == [
            {"action": "show"},
            {"action": "set", "key": "BdoRegions", "value": "NA,EU"},
        ]


# -- C. the exit-code contract (Requirements 10.1, 10.2, 10.4, 10.7) ---------


class TestExitCodes:
    """Every terminating outcome maps to exactly one of the four codes."""

    def test_success_exits_zero_with_ok_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["deploy", "--yes", "--json"], dispatcher=FakeDispatcher())
        assert code == ExitCode.SUCCESS
        assert _json_result(capsys.readouterr().out)["ok"] is True

    def test_executor_failure_exits_one_with_verbatim_output(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        failed = Result(
            capability=Capability.DEPLOY,
            ok=False,
            exit_code=ExitCode.EXECUTOR_FAILED,
            summary="step 1/2 failed (sam): sam build",
            raw_output="Error: Template file not found",
        )
        code = main(["deploy", "--yes", "--json"], dispatcher=FakeDispatcher(result=failed))
        assert code == ExitCode.EXECUTOR_FAILED
        payload = _json_result(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["raw_output"] == "Error: Template file not found"

    def test_a_raised_executor_failure_is_rendered_without_a_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dispatcher = FakeDispatcher(error=ExecutorFailed("sam build failed", output="boom"))
        code = main(["deploy", "--yes"], dispatcher=dispatcher)
        captured = capsys.readouterr()
        assert code == ExitCode.EXECUTOR_FAILED
        assert "failed: sam build failed" in captured.out
        assert "boom" in captured.out
        assert "Traceback" not in captured.out + captured.err

    def test_validation_error_exits_two_naming_the_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dispatcher = FakeDispatcher()
        code = main(["release", "1.4.0", "--yes", "--json"], dispatcher=dispatcher)
        assert code == ExitCode.USAGE_ERROR
        payload = _json_result(capsys.readouterr().out)
        assert payload["summary"].startswith("version:")
        assert dispatcher.planned == [], "a rejected command must not reach the dispatcher"

    def test_local_prod_deploy_is_rejected_before_planning(self) -> None:
        dispatcher = FakeDispatcher()
        assert (
            main(["deploy", "--stage", "prod", "--yes"], dispatcher=dispatcher)
            == ExitCode.USAGE_ERROR
        )
        assert dispatcher.planned == []

    def test_unknown_flag_exits_two_without_a_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["deploy", "--nope"], dispatcher=FakeDispatcher())
        captured = capsys.readouterr()
        assert code == ExitCode.USAGE_ERROR
        assert "Traceback" not in captured.out + captured.err

    def test_exit_codes_are_deterministic(self) -> None:
        codes = [main(["deploy", "--json"], dispatcher=FakeDispatcher()) for _ in range(3)]
        assert codes == [ExitCode.CONFIRMATION_REQUIRED] * 3


# -- D. the confirmation gate (Requirement 10.4) -----------------------------


class TestConfirmationGate:
    """Without ``--yes`` a mutating command exits 3 carrying the refused plan."""

    def test_exits_three_with_the_plan_in_the_result(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["deploy", "--json"], dispatcher=FakeDispatcher())
        assert code == ExitCode.CONFIRMATION_REQUIRED
        payload = _json_result(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["plan"]["steps"][0]["command"] == "sam build"
        assert payload["plan"]["requires_confirmation"] is True

    def test_yes_is_how_confirmation_is_given(self) -> None:
        dispatcher = FakeDispatcher()
        assert main(["deploy", "--yes"], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert [confirmed for _, confirmed in dispatcher.executed] == [True]

    def test_a_read_only_command_needs_no_confirmation(self) -> None:
        dispatcher = FakeDispatcher(requires_confirmation=False)
        assert main(["config", "show"], dispatcher=dispatcher) == ExitCode.SUCCESS

    def test_nothing_is_ever_prompted_for(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unreadable stdin changes nothing: the front-end never reads it."""

        class ExplodingStdin:
            def read(self, *args: object) -> str:
                raise AssertionError("the CLI must never prompt")

            def readline(self, *args: object) -> str:
                raise AssertionError("the CLI must never prompt")

            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", ExplodingStdin())
        assert (
            main(["deploy", "--json"], dispatcher=FakeDispatcher())
            == ExitCode.CONFIRMATION_REQUIRED
        )


# -- E. --dry-run stops at planning (Requirement 10.5) -----------------------


class TestDryRun:
    """A dry run renders the plan and never calls ``execute()``."""

    def test_it_does_not_execute(self, capsys: pytest.CaptureFixture[str]) -> None:
        dispatcher = FakeDispatcher()
        code = main(["deploy", "--dry-run"], dispatcher=dispatcher)
        assert code == ExitCode.SUCCESS
        assert dispatcher.planned and dispatcher.executed == []
        assert "$ sam build" in capsys.readouterr().out

    def test_it_needs_no_confirmation(self) -> None:
        dispatcher = FakeDispatcher()
        assert main(["release", "v1.4.0", "--dry-run"], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert dispatcher.executed == []

    def test_it_is_inert_against_the_real_wiring(self) -> None:
        """No injected dispatcher: the real composition root plans and stops.

        Guards the front-end's default path — ``build_dispatcher()`` — which
        creates no AWS client and runs no subprocess, so planning reaches nothing.
        """
        assert main(["deploy", "--dry-run"]) == ExitCode.SUCCESS


# -- F. the --json stream contract (Requirement 1.2) -------------------------


class TestJsonMode:
    """One serialized ``Result`` on stdout; every human line on stderr."""

    def test_stdout_carries_only_the_result_and_logs_go_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["deploy", "--json"], dispatcher=FakeDispatcher())
        captured = capsys.readouterr()
        payload = _json_result(captured.out)
        assert payload["capability"] == "deploy"
        assert "$ sam build" in captured.err
        assert "changes the dev stack" in captured.err

    def test_human_mode_writes_those_lines_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["deploy", "--json"], dispatcher=FakeDispatcher())
        json_mode = capsys.readouterr()
        main(["deploy"], dispatcher=FakeDispatcher())
        human = capsys.readouterr()
        assert "$ sam build" in human.out
        assert human.err == ""
        assert json_mode.out != human.out

    def test_a_run_url_is_surfaced(self, capsys: pytest.CaptureFixture[str]) -> None:
        dispatched = Result(
            capability=Capability.RELEASE,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary="release: completed 1 step(s)",
            run_url=RUN_URL,
        )
        main(["release", "v1.4.0", "--yes"], dispatcher=FakeDispatcher(result=dispatched))
        assert RUN_URL in capsys.readouterr().out

    def test_a_secret_shaped_value_is_absent_from_the_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verified against the real planner, whose params carry a ``SecretStr``.

        The refusal happens before any executor is resolved, so a ``Dispatcher``
        with no executors injected is enough — and nothing can be reached.
        """
        code = main(
            ["config", "set", SSM_KEY, "s3cret-value", "--json"],
            dispatcher=Dispatcher(),
        )
        captured = capsys.readouterr()
        assert code == ExitCode.CONFIRMATION_REQUIRED
        assert "s3cret-value" not in captured.out + captured.err
        params = _json_result(captured.out)["plan"]["steps"][0]["params"]
        assert params["path"] == SSM_KEY
        assert params["value"] == "**********"


# -- G. the console entry point ----------------------------------------------


class TestEntryPoint:
    """``main`` returns an exit code; it is never the one that exits."""

    def test_no_arguments_renders_help_as_a_usage_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No capability named is a usage error (exit ``2``), with help shown."""
        assert main([]) == ExitCode.USAGE_ERROR
        assert "Usage:" in capsys.readouterr().out

    def test_argv_is_read_when_no_arguments_are_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher = FakeDispatcher()
        monkeypatch.setattr(sys, "argv", ["bdo-deploy", "deploy", "--dry-run"])
        assert main(dispatcher=dispatcher) == ExitCode.SUCCESS
        assert [cmd.capability for cmd in dispatcher.planned] == [Capability.DEPLOY]
