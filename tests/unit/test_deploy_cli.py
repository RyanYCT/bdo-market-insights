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

The last two sections widen from CLI mode to the process: ``main`` is the single
entry point that chooses between the two front-ends, and both front-ends route
through one ``Dispatcher`` from one composition root while restating no routing,
validation or rendering (Requirement 1.4). That second claim is structural, so it
is asserted against the front-ends' own source rather than by exercising cases.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from typer import rich_utils

from bdo_deploy import cli as cli_module
from bdo_deploy import presentation as presentation_module
from bdo_deploy import tui as tui_module
from bdo_deploy.cli import app, main
from bdo_deploy.core import assembly as assembly_module
from bdo_deploy.core.assembly import ControlPlane
from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.errors import ConfirmationRequired, ExecutorFailed
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import (
    Capability,
    Command,
    CommandResult,
    Op,
    Plan,
    PlanStep,
    Result,
    RunRef,
    RunStatus,
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


class FakeGitHub:
    """A ``GitHubExecutor`` that records what a front-end followed, and answers.

    Structurally satisfies the Protocol — the front-ends only ever call ``watch``
    and ``view`` on it — so a run can be "followed" with no ``gh`` process, no
    network and, crucially for a test suite, no blocking wait. ``conclusion`` is
    GitHub's own verdict string, which is what ``follow_run`` folds into the
    ``Result``.

    Shared with the TUI and the presentation suites, so all three exercise one
    fake and none of them can accidentally test a different GitHub.
    """

    def __init__(
        self,
        *,
        conclusion: str | None = "success",
        status: str | None = "completed",
        view_ok: bool = True,
        watch_ok: bool = True,
        watch_error: Exception | None = None,
    ) -> None:
        self.watched: list[RunRef] = []
        self.viewed: list[RunRef] = []
        self._conclusion = conclusion
        self._status = status
        self._view_ok = view_ok
        self._watch_ok = watch_ok
        self._watch_error = watch_error

    def watch(self, run: RunRef) -> CommandResult:
        self.watched.append(run)
        if self._watch_error is not None:
            raise self._watch_error
        return CommandResult(ok=self._watch_ok, output=f"watched {run.run_id}")

    def view(self, run: RunRef) -> RunStatus:
        self.viewed.append(run)
        return RunStatus(
            # A view reports the run's URL, as the real adapter reads it out of
            # `gh run view --json url` — which is how a run whose URL the dispatch
            # could not resolve acquires one.
            run=RunRef(workflow=run.workflow, run_id=run.run_id, url=run.url or RUN_URL),
            status=self._status,
            conclusion=self._conclusion,
            output=f"viewed {run.run_id}",
            ok=self._view_ok,
        )

    def run_step(self, step: PlanStep) -> CommandResult:
        raise AssertionError("no Op routes to this executor from a front-end")

    def run_workflow(
        self,
        *,
        stage: str,
        version: str | None = None,
        inputs: dict[str, str] | None = None,
    ) -> CommandResult:
        raise AssertionError("a front-end never dispatches; the Dispatcher does")

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
        allowed_refs: list[str] | None = None,
    ) -> CommandResult:
        raise AssertionError("a front-end never administers GitHub")

    def set_environment_secret(self, *, environment: str, name: str, value: str) -> CommandResult:
        raise AssertionError("a front-end never administers GitHub")


def dispatched_result(
    capability: Capability = Capability.RELEASE,
    *,
    run: RunRef | None = None,
) -> Result:
    """A successful ``Result`` for a command that dispatched a CI run."""
    return Result(
        capability=capability,
        ok=True,
        exit_code=ExitCode.SUCCESS,
        summary=f"{capability.value}: completed 1 step(s)",
        run_url=RUN_URL,
        run=run if run is not None else RunRef(workflow="deploy.yml", run_id="42", url=RUN_URL),
    )


class FakeTerminal:
    """A stream that claims to be a terminal without being one.

    Mode selection asks the streams themselves whether this is an interactive
    session, so a fake stream is enough to exercise both branches — no pty, and
    no real terminal, is needed. ``write``/``flush`` exist because whatever Click
    happens to emit has to go somewhere.
    """

    def __init__(self, *, tty: bool = True) -> None:
        self.tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self.tty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def _json_result(out: str) -> dict[str, Any]:
    """Parse stdout as the sole serialized ``Result`` it must contain."""
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1, f"stdout carried {len(lines)} line(s), expected exactly one Result"
    parsed: dict[str, Any] = json.loads(lines[0])
    return parsed


# -- reading help text back ---------------------------------------------------
#
# Typer renders help through Rich, so the bytes on stdout are a *rendering* of
# the declared help and not the help itself. Two artefacts of that rendering will
# break a naive `phrase in out`, and both did:
#
#   * ANSI escape sequences. Typer forces a terminal (and therefore colour) when
#     GITHUB_ACTIONS / FORCE_COLOR / PY_COLORS is set -- see
#     `typer.rich_utils.FORCE_TERMINAL` -- which is always true on a GitHub
#     runner and normally false on a developer's machine under pytest capture.
#     The codes land *inside* the phrases: an option switch is emitted as
#     `ESC[1;36m-ESC[0mESC[1;36m-watch`, so even `--watch` is not a substring.
#   * hard wrapping at the console width, with the paragraph's dim style
#     re-opened on every wrapped line -- so `ESC[0m ESC[2m` lands wherever the
#     wrap happens to fall, which depends on the terminal width.
#
# `plain()` undoes exactly those two and nothing else: it removes the escape
# sequences and collapses the wrap back into single-spaced text. What is left is
# the operator-visible words, so the assertions still fail if the help text is
# weakened or deleted.

_ANSI_ESCAPE: Final = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")

HELP_WIDTHS: Final = (40, 200)
"""Two widths either side of any plausible terminal, asserted to be equivalent.

A single width would only prove the tests pass *there*. Rendering the same claim
narrow and wide is what makes a future change of runner width -- or of Rich's
wrapping -- unable to resurrect this failure.
"""


def plain(rendered: str) -> str:
    """Rendered help as the operator reads it: no ANSI, no wrapping artefacts."""
    return " ".join(_ANSI_ESCAPE.sub("", rendered).split())


@pytest.fixture(params=HELP_WIDTHS, ids=lambda width: f"cols{width}")
def help_width(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> int:
    """Render help at a pinned width with colour forced *on*.

    Pinning the width makes the rendering deterministic instead of inheriting
    whatever terminal the test happens to run under, and parametrising it proves
    the assertions do not depend on the value. Forcing colour on reproduces the
    CI environment unconditionally, so a rendering-sensitive assertion fails for
    everyone rather than only on the runner.

    Both are patched on ``typer.rich_utils``, whose module globals
    ``_get_rich_console()`` reads at render time; ``FORCE_TERMINAL`` is decided
    from the environment at *import*, so setting an env var here would be too
    late.
    """
    width: int = request.param
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setattr(rich_utils, "MAX_WIDTH", width)
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", True)
    return width


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

    def test_help_labels_bootstrap_as_one_time(
        self, capsys: pytest.CaptureFixture[str], help_width: int
    ) -> None:
        """Requirement 4.2: help output says bootstrap is a one-time step.

        Asserted at both ends of ``HELP_WIDTHS``: the labelling has to reach the
        operator whatever terminal they are on, so the rendering must not be able
        to hide it.
        """
        assert main(["bootstrap", "--help"]) == ExitCode.SUCCESS
        rendered = plain(capsys.readouterr().out)
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


# -- F2. run following (Requirements 7.6, 10.6) ------------------------------


def _followed(
    github: FakeGitHub,
    *,
    result: Result | None = None,
) -> ControlPlane:
    """A ``ControlPlane`` whose core is faked and whose GitHub records the follow."""
    return ControlPlane(
        dispatcher=FakeDispatcher(result=result if result is not None else dispatched_result()),
        github=github,
    )


class TestRunFollowing:
    """The CI run is authoritative, and ``--json`` has to ask before we wait."""

    def test_human_output_follows_the_run_by_default(self) -> None:
        github = FakeGitHub()
        code = main(["release", "v1.4.0", "--yes"], plane=_followed(github))
        assert code == ExitCode.SUCCESS
        assert [run.run_id for run in github.watched] == ["42"]

    def test_the_runs_verdict_overrides_a_green_dispatch(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A dispatch that succeeded but whose run failed is reported as a failure."""
        github = FakeGitHub(conclusion="failure")
        code = main(["release", "v1.4.0", "--yes"], plane=_followed(github))
        assert code == ExitCode.EXECUTOR_FAILED
        rendered = capsys.readouterr().out
        assert "the run concluded failure" in rendered
        assert RUN_URL in rendered, "the URL is surfaced whatever the verdict"

    def test_json_alone_does_not_wait(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Requirement 1.2: a blocking watch cannot precede the sole Result."""
        github = FakeGitHub()
        code = main(["release", "v1.4.0", "--yes", "--json"], plane=_followed(github))
        assert code == ExitCode.SUCCESS
        assert github.watched == [] and github.viewed == []
        payload = _json_result(capsys.readouterr().out)
        assert payload["run_url"] == RUN_URL, "the run is still reachable without waiting"
        assert payload["run"]["run_id"] == "42"

    def test_json_with_watch_follows_the_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        github = FakeGitHub(conclusion="failure")
        code = main(["release", "v1.4.0", "--yes", "--json", "--watch"], plane=_followed(github))
        assert code == ExitCode.EXECUTOR_FAILED
        payload = _json_result(capsys.readouterr().out)
        assert payload["ok"] is False
        assert [run.run_id for run in github.watched] == ["42"]

    def test_deploy_follows_its_run_too(self) -> None:
        """Following is wired once, for every capability that can dispatch a run."""
        github = FakeGitHub()
        result = dispatched_result(Capability.DEPLOY)
        code = main(
            ["deploy", "--stage", "prod", "--target", "ci", "--yes"],
            plane=_followed(github, result=result),
        )
        assert code == ExitCode.SUCCESS
        assert [run.run_id for run in github.watched] == ["42"]

    def test_a_command_that_dispatched_nothing_is_never_followed(self) -> None:
        """No run, no watch — so no capability needs to know whether it makes one."""
        github = FakeGitHub()
        local = Result(
            capability=Capability.DEPLOY,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary="deploy: completed 2 step(s)",
        )
        assert main(["deploy", "--yes"], plane=_followed(github, result=local)) == ExitCode.SUCCESS
        assert github.watched == []

    def test_a_dry_run_follows_nothing(self) -> None:
        github = FakeGitHub()
        assert (
            main(["release", "v1.4.0", "--dry-run"], plane=_followed(github)) == ExitCode.SUCCESS
        )
        assert github.watched == []

    def test_watch_is_offered_only_where_a_run_can_be_dispatched(
        self, capsys: pytest.CaptureFixture[str], help_width: int
    ) -> None:
        """The two capabilities that can trigger a CI run offer ``--watch``; none else.

        A flag on ``config show`` would advertise a wait that nothing there can
        produce.

        The absence half is why the ANSI stripping matters twice over: Rich splits
        a switch as ``-`` + ``-watch``, so a raw-text ``not in`` would hold even
        where the flag *is* offered, and the assertion would pass for the wrong
        reason.
        """
        for argv in (["deploy", "--help"], ["release", "--help"]):
            assert main(argv) == ExitCode.SUCCESS
            assert "--watch" in plain(capsys.readouterr().out)
        assert main(["config", "show", "--help"]) == ExitCode.SUCCESS
        assert "--watch" not in plain(capsys.readouterr().out)


# -- G. the console entry point ----------------------------------------------


class TestEntryPoint:
    """``main`` returns an exit code; it is never the one that exits."""

    def test_no_arguments_renders_help_as_a_usage_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No capability named is a usage error (exit ``2``), with help shown."""
        assert main([]) == ExitCode.USAGE_ERROR
        assert "Usage: bdo-deploy" in plain(capsys.readouterr().out)

    def test_argv_is_read_when_no_arguments_are_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher = FakeDispatcher()
        monkeypatch.setattr(sys, "argv", ["bdo-deploy", "deploy", "--dry-run"])
        assert main(dispatcher=dispatcher) == ExitCode.SUCCESS
        assert [cmd.capability for cmd in dispatcher.planned] == [Capability.DEPLOY]


# -- H. mode selection: one entry point, two front-ends (Requirement 1.4) ----


@pytest.fixture
def launched_tui(monkeypatch: pytest.MonkeyPatch) -> list[Dispatcher | None]:
    """Record every ``run_tui`` launch instead of starting a real Textual app.

    Patched on ``bdo_deploy.tui`` — the module the CLI imports it from at call
    time — so what is recorded is the launch the real entry point performed, with
    the dispatcher it chose to hand over.
    """
    launched: list[Dispatcher | None] = []

    def fake_run_tui(*, dispatcher: Dispatcher | None = None) -> int:
        launched.append(dispatcher)
        return int(ExitCode.SUCCESS)

    monkeypatch.setattr(tui_module, "run_tui", fake_run_tui)
    return launched


def _terminal(monkeypatch: pytest.MonkeyPatch, *, stdin: bool, stdout: bool) -> None:
    """Present streams that claim (or deny) being a terminal."""
    monkeypatch.setattr(sys, "stdin", FakeTerminal(tty=stdin))
    if stdout:
        # Left alone when it must be a pipe: pytest's captured stdout already is
        # one, and keeping it is what lets a test read the help text back.
        monkeypatch.setattr(sys, "stdout", FakeTerminal(tty=True))


class TestModeSelection:
    """``main`` is the only entry point, and it chooses the front-end."""

    def test_a_terminal_with_no_subcommand_launches_the_tui(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        """The TUI is reachable by an operator, not only programmatically."""
        dispatcher = FakeDispatcher()
        _terminal(monkeypatch, stdin=True, stdout=True)
        assert main([], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert launched_tui == [dispatcher], "the injected core is handed to the TUI too"

    def test_the_tui_flag_launches_the_tui(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        _terminal(monkeypatch, stdin=True, stdout=True)
        assert main(["--tui"]) == ExitCode.SUCCESS
        assert launched_tui == [None], "no override: the TUI asks the composition root"

    @pytest.mark.parametrize(
        ("stdin", "stdout"),
        [(False, False), (True, False), (False, True)],
        ids=["neither", "stdin-only", "stdout-only"],
    )
    def test_without_both_streams_on_a_terminal_nothing_interactive_starts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        launched_tui: list[Dispatcher | None],
        capsys: pytest.CaptureFixture[str],
        *,
        stdin: bool,
        stdout: bool,
    ) -> None:
        """A pipeline cannot be hijacked into a full-screen app it cannot answer."""
        _terminal(monkeypatch, stdin=stdin, stdout=stdout)
        assert main([]) == ExitCode.USAGE_ERROR
        assert launched_tui == []
        if not stdout:
            assert "Usage: bdo-deploy" in plain(capsys.readouterr().out)

    def test_the_tui_flag_without_a_terminal_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        """Explicit intent still cannot make a pipe interactive."""
        _terminal(monkeypatch, stdin=False, stdout=False)
        assert main(["--tui"]) == ExitCode.USAGE_ERROR
        assert launched_tui == []

    def test_the_tui_flag_cannot_be_combined_with_a_subcommand(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        """The two select opposite modes, so asking for both is a usage error."""
        dispatcher = FakeDispatcher()
        _terminal(monkeypatch, stdin=True, stdout=True)
        assert main(["--tui", "deploy", "--yes"], dispatcher=dispatcher) == ExitCode.USAGE_ERROR
        assert launched_tui == []
        assert dispatcher.planned == [], "neither mode ran"

    def test_a_subcommand_on_a_terminal_still_runs_cli_mode(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        """A named capability is unambiguous: a tty must not divert it to the TUI."""
        dispatcher = FakeDispatcher()
        _terminal(monkeypatch, stdin=True, stdout=True)
        assert main(["deploy", "--yes"], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert launched_tui == []
        assert [cmd.capability for cmd in dispatcher.planned] == [Capability.DEPLOY]


# -- I. the shared-core property, asserted (Requirement 1.4) -----------------

FRONT_ENDS: Final = (cli_module, tui_module)
"""The two front-end modules. Every test below holds for both, or fails."""

CORE_VOCABULARY: Final = frozenset({"Dispatcher"})
"""The only non-constant name a front-end may import from ``core.dispatch``.

The type, so a front-end can be annotated against the core it routes through —
and nothing else. Everything else it may import from that module is an
UPPER_CASE argument-name constant, which is shared *vocabulary* rather than
behaviour: both front-ends spelling ``args`` keys the same way is precisely how
they avoid restating anything.
"""

RENDERING: Final = frozenset({"failed_result", "follow_run", "plan_lines", "result_lines"})
"""The shared rendering vocabulary, which lives in ``presentation`` only.

``follow_run`` is part of it: following a dispatched run happens after
``execute()`` has returned and produces nothing but a verdict to report, so it is
presentation — and being *shared* is the point, since it is what stops the two
front-ends from growing two notions of "did the run pass" (Requirements 7.6,
10.6). Note what this allow-list therefore still forbids: a front-end that
implemented its own following, or that reached ``GitHubExecutor.watch`` itself,
would fail the ban below and the redefinition check here.
"""

WIRING: Final = frozenset({"ControlPlane", "build_control_plane"})
"""What a front-end may take from the composition root.

``build_control_plane`` is the one way to obtain a core, and ``ControlPlane`` is
the type of what it returns — needed to annotate the injected override and to tell
it apart from a bare ``Dispatcher``. Neither carries behaviour.
"""


def _source(module: ModuleType) -> ast.Module:
    """Parse a module's own source, so the assertions are about what is written."""
    assert module.__file__ is not None
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every module the source imports, however it imports it."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def _imported_from(tree: ast.Module, module: str) -> set[str]:
    """The names the source imports from one module."""
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _called_names(tree: ast.Module) -> set[str]:
    """Every name the source calls, whether bare or through an attribute."""
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def _defined_names(tree: ast.Module) -> set[str]:
    """Every function or class the source defines, at any nesting depth."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


class TestOneSharedCore:
    """Both front-ends route through one ``Dispatcher`` from one composition root.

    Requirement 1.4 is a structural claim — each front-end is *limited to*
    collecting intent and rendering the ``Result`` — so it is asserted
    structurally, against the front-ends' own source. A behavioural test can show
    that the two agree on the cases it thought to try; these show that a
    disagreement has nowhere to come from, because neither module contains any
    routing, validation or rendering to disagree with.

    (That the two produce *identical* plans for the same intent is Requirement
    1.5, and is asserted separately.)
    """

    def test_both_take_their_wiring_from_the_one_composition_root(self) -> None:
        """The name each front-end imported is the composition root's own function.

        Read out of the module namespaces rather than as attributes, because
        ``build_control_plane`` is an import into a front-end and not part of its
        public surface — which is the point being asserted.
        """
        for module in FRONT_ENDS:
            assert vars(module)["build_control_plane"] is assembly_module.build_control_plane
            imported = _imported_from(_source(module), "bdo_deploy.core.assembly")
            assert imported <= WIRING, f"{module.__name__} takes more than its wiring"

    def test_neither_assembles_a_core_of_its_own(self) -> None:
        """Nothing but ``build_control_plane`` (or an injection) can produce a core.

        Together with the executor ban below, this is what makes "the same
        ``Dispatcher``" true by construction rather than by convention: a front-end
        that cannot name a concrete executor and does not call ``Dispatcher(...)``
        has exactly one way to obtain a core — and now one way to obtain the
        ``GitHubExecutor`` it follows a run with, which is the same object the core
        dispatches through.
        """
        for module in FRONT_ENDS:
            called = _called_names(_source(module))
            assert "build_control_plane" in called, (
                f"{module.__name__} must use the composition root"
            )
            assert "Dispatcher" not in called, f"{module.__name__} assembles its own core"
            assert "ControlPlane" not in called, f"{module.__name__} assembles its own wiring"

    def test_neither_can_reach_an_executor(self) -> None:
        """No front-end names a concrete adapter, so none can reach a tool directly.

        Unchanged by run-following: the front-ends hand the ``GitHubExecutor`` the
        composition root gave them to the shared ``follow_run()`` and never name an
        adapter, or even the Protocol, themselves.
        """
        for module in FRONT_ENDS:
            executors = {
                imported
                for imported in _imported_modules(_source(module))
                if imported.startswith("bdo_deploy.core.executors")
            }
            assert executors == set(), f"{module.__name__} imports {executors}"

    def test_neither_restates_routing_or_validation(self) -> None:
        """Routing lives in ``core.dispatch``, validation in ``core.validation``.

        A front-end imports the ``Dispatcher`` type and the shared argument-name
        constants from the former and nothing at all from the latter, so neither
        module can hold a second opinion about which executor an intent reaches or
        whether an intent is usable.
        """
        for module in FRONT_ENDS:
            tree = _source(module)
            assert "bdo_deploy.core.validation" not in _imported_modules(tree)
            behaviour = {
                name
                for name in _imported_from(tree, "bdo_deploy.core.dispatch")
                if name not in CORE_VOCABULARY and not name.isupper()
            }
            assert behaviour == set(), f"{module.__name__} imports core behaviour: {behaviour}"

    def test_neither_restates_rendering(self) -> None:
        """One vocabulary describes a plan and a result, and it lives elsewhere."""
        assert set(presentation_module.__all__) >= RENDERING
        for module in FRONT_ENDS:
            tree = _source(module)
            imported = _imported_from(tree, "bdo_deploy.presentation")
            assert imported, f"{module.__name__} renders without the shared vocabulary"
            assert imported <= RENDERING
            redefined = RENDERING & _defined_names(tree)
            assert redefined == set(), f"{module.__name__} redefines {redefined}"

    def test_one_entry_point_reaches_both_front_ends(
        self, monkeypatch: pytest.MonkeyPatch, launched_tui: list[Dispatcher | None]
    ) -> None:
        """One process, one injected core, either front-end — the operator's view."""
        dispatcher = FakeDispatcher()
        _terminal(monkeypatch, stdin=True, stdout=True)
        assert main(["deploy", "--yes"], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert main([], dispatcher=dispatcher) == ExitCode.SUCCESS
        assert [cmd.capability for cmd in dispatcher.planned] == [Capability.DEPLOY]
        assert launched_tui == [dispatcher]
