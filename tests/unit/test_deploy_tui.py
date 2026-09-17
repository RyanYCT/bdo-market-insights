"""Unit tests for the TUI front-end, ``bdo_deploy.tui``.

Every test drives the real Textual application through its own test pilot, in
process and with no terminal: clicks land on real buttons, real widgets hold the
intent, and the flow runs the same code an operator would. The ``Dispatcher`` is
the fake from ``test_deploy_cli`` — reused deliberately, because the point of
these tests is that the TUI is the *same* front-end contract driven a different
way, so it should be provable against the same stand-in for the core. No ``sam``
/ ``gh`` / ``git`` / AWS call is ever made.

What is asserted is the front-end's contract, not the core's behaviour: intent
collection into one typed ``Command``, the explicit confirmation step rendered
before any mutating execution (Requirement 1.3), routing through the shared
``Dispatcher`` (Requirement 1.4), and exit codes from the one shared vocabulary
(Requirement 10.7).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import NamedTuple

import pytest
from textual.widgets import Checkbox, Input, Select, Static

from bdo_deploy.core.assembly import ControlPlane
from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import Capability, Command, Result, Target
from bdo_deploy.tui import DeployApp, run_tui
from tests.unit.test_deploy_cli import (
    RUN_URL,
    FakeDispatcher,
    FakeGitHub,
    dispatched_result,
)

Step = str | Callable[[DeployApp], None]
"""One action in a driven flow: a selector to click, or a mutation of the form."""


class Driven(NamedTuple):
    """A finished flow: the app, plus a snapshot of the screen it had reached.

    A snapshot rather than the screen itself, because shutting the app down
    empties the screen stack and unmounts its widgets — so by the time a test
    body runs there is nothing left to query. What the operator would have been
    looking at is therefore captured while the app is still running: the text of
    every identified ``Static`` on the reached screen, and the ids present on it.
    """

    app: DeployApp
    texts: dict[str, str]
    """The reached screen's ``Static`` widgets, by id."""

    ids: set[str]
    """Every id on the reached screen, so a test can assert a widget's absence."""


def drive(
    dispatcher: Dispatcher,
    *steps: Step,
    github: FakeGitHub | None = None,
) -> Driven:
    """Run ``steps`` against a fresh app and return it for inspection.

    Textual's pilot is async, so the loop lives here rather than in every test;
    the tests stay plain synchronous functions and the suite needs no async
    plugin. A pause after each step lets the app mount whatever the step pushed,
    which is what makes the *next* selector resolvable.

    ``github`` substitutes the executor a dispatched run is followed with, which
    the app receives the only way it can: as a whole ``ControlPlane``, exactly as
    the composition root would have handed it one.
    """
    app = (
        DeployApp(dispatcher=dispatcher)
        if github is None
        else DeployApp(plane=ControlPlane(dispatcher=dispatcher, github=github))
    )
    snapshot = Driven(app=app, texts={}, ids=set())

    async def _run() -> None:
        async with app.run_test() as pilot:
            for step in steps:
                if callable(step):
                    step(app)
                else:
                    await pilot.click(step)
                # Twice: a step that pushes a screen has only *scheduled* the
                # mount after one idle pass, and the next selector has to resolve
                # against a composed screen.
                await pilot.pause()
                await pilot.pause()
            nonlocal snapshot
            snapshot = Driven(
                app=app,
                texts={
                    widget.id: str(widget.content)
                    for widget in app.screen.query(Static)
                    if widget.id is not None
                },
                ids={node.id for node in app.screen.query("*") if node.id is not None},
            )

    asyncio.run(_run())
    return snapshot


def set_input(field: str, value: str) -> Step:
    """Type ``value`` into the named ``Input``."""

    def _step(app: DeployApp) -> None:
        app.screen.query_one(f"#{field}", Input).value = value

    return _step


def set_select(field: str, value: object) -> Step:
    """Choose ``value`` in the named ``Select``."""

    def _step(app: DeployApp) -> None:
        app.screen.query_one(f"#{field}", Select).value = value

    return _step


def check(field: str) -> Step:
    """Tick the named ``Checkbox``."""

    def _step(app: DeployApp) -> None:
        app.screen.query_one(f"#{field}", Checkbox).value = True

    return _step


def choose(capability: Capability) -> list[Step]:
    """The opening two steps: pick a capability and continue to its form."""
    return [set_select("capability", capability), "#continue"]


def rendered(driven: Driven, field: str) -> str:
    """Read back what a ``Static`` on the reached screen was showing."""
    return driven.texts[field]


# -- A. intent collection (Requirement 1.4) -----------------------------------


class TestIntentCollection:
    """The form becomes one typed ``Command``; the TUI decides nothing else."""

    def test_deploy_collects_stage_target_and_the_sync_toggle(self) -> None:
        dispatcher = FakeDispatcher()
        drive(
            dispatcher,
            *choose(Capability.DEPLOY),
            set_input("stage", "prod"),
            set_select("target", Target.CI),
            "#review",
        )
        (cmd,) = dispatcher.planned
        assert cmd == Command(
            capability=Capability.DEPLOY,
            target=Target.CI,
            stage="prod",
            args={"sync": False},
        )

    def test_release_collects_the_version_and_dispatch_toggle(self) -> None:
        dispatcher = FakeDispatcher()
        drive(
            dispatcher,
            *choose(Capability.RELEASE),
            set_input("version", "v1.4.0"),
            check("dispatch"),
            "#review",
        )
        (cmd,) = dispatcher.planned
        assert cmd.capability is Capability.RELEASE
        assert cmd.version == "v1.4.0"
        assert cmd.args == {"dispatch": True}

    def test_bootstrap_splits_the_reviewer_list(self) -> None:
        """A human types a list; the ``Command`` needs the entries the API wants."""
        dispatcher = FakeDispatcher()
        drive(
            dispatcher,
            *choose(Capability.BOOTSTRAP),
            set_input("stage", "prod"),
            set_input("reviewers", " User:1 , Team:2 , "),
            "#review",
        )
        (cmd,) = dispatcher.planned
        assert cmd.args == {"reviewers": ["User:1", "Team:2"]}

    def test_config_collects_its_action_and_only_then_the_key_and_value(self) -> None:
        show = FakeDispatcher(requires_confirmation=False)
        drive(show, *choose(Capability.CONFIG), "#review")
        assert [cmd.args for cmd in show.planned] == [{"action": "show"}]

        write = FakeDispatcher()
        drive(
            write,
            *choose(Capability.CONFIG),
            set_select("action", "set"),
            set_input("key", "BdoRegions"),
            set_input("value", "NA,EU"),
            "#review",
        )
        assert [cmd.args for cmd in write.planned] == [
            {"action": "set", "key": "BdoRegions", "value": "NA,EU"}
        ]

    def test_the_menu_labels_bootstrap_as_a_one_time_step(self) -> None:
        """Requirement 4.2: the labelling is in menus, not only in CLI help."""
        driven = drive(FakeDispatcher(), set_select("capability", Capability.BOOTSTRAP))
        described = " ".join(rendered(driven, "capability-help").split())
        assert "ONE-TIME, out-of-band step" in described
        assert "NOT part of the routine deploy path" in described


# -- B. the explicit confirmation step (Requirement 1.3) ---------------------


class TestConfirmationStep:
    """A mutating plan is rendered and answered before anything executes."""

    def test_a_mutating_plan_is_rendered_and_nothing_is_executed(self) -> None:
        dispatcher = FakeDispatcher()
        driven = drive(dispatcher, *choose(Capability.DEPLOY), "#review")
        assert "$ sam build" in rendered(driven, "plan")
        assert "changes the dev stack" in rendered(driven, "plan")
        assert [confirmed for _, confirmed in dispatcher.executed] == [False], (
            "the plan must reach the core unconfirmed, so the core's gate stops it"
        )

    def test_confirming_executes_the_very_plan_that_was_shown(self) -> None:
        dispatcher = FakeDispatcher()
        driven = drive(dispatcher, *choose(Capability.DEPLOY), "#review", "#confirm")
        assert [confirmed for _, confirmed in dispatcher.executed] == [False, True]
        refused, confirmed = (plan for plan, _ in dispatcher.executed)
        assert refused is confirmed, "the confirmed plan must be the one on screen"
        assert driven.app.exit_code == ExitCode.SUCCESS
        assert "ok:" in rendered(driven, "result")

    def test_cancelling_runs_nothing_and_returns_to_the_form(self) -> None:
        dispatcher = FakeDispatcher()
        driven = drive(dispatcher, *choose(Capability.DEPLOY), "#review", "#cancel")
        assert [confirmed for _, confirmed in dispatcher.executed] == [False]
        assert "review" in driven.ids, "cancelling returns to the intent form"
        assert driven.app.last_result is None

    def test_a_read_only_command_is_never_gated(self) -> None:
        """``config show`` mutates nothing, so the core never asks — nor does the TUI."""
        dispatcher = FakeDispatcher(requires_confirmation=False)
        driven = drive(dispatcher, *choose(Capability.CONFIG), "#review")
        assert "plan" not in driven.ids
        assert driven.app.exit_code == ExitCode.SUCCESS


# -- C. exit codes from the one shared vocabulary (Requirement 10.7) ---------


class TestExitCodes:
    """The TUI reports the same four outcomes the CLI does."""

    def test_an_unanswered_confirmation_leaves_the_session_at_three(self) -> None:
        driven = drive(FakeDispatcher(), *choose(Capability.DEPLOY), "#review")
        assert driven.app.exit_code == ExitCode.CONFIRMATION_REQUIRED

    def test_a_rejected_command_reports_two_and_never_reaches_the_core(self) -> None:
        dispatcher = FakeDispatcher()
        driven = drive(
            dispatcher,
            *choose(Capability.RELEASE),
            set_input("version", "1.4.0"),
            "#review",
        )
        assert driven.app.exit_code == ExitCode.USAGE_ERROR
        assert dispatcher.planned == []
        assert rendered(driven, "result").startswith("failed: version:")

    def test_a_local_prod_deploy_is_rejected_before_planning(self) -> None:
        dispatcher = FakeDispatcher()
        driven = drive(
            dispatcher, *choose(Capability.DEPLOY), set_input("stage", "prod"), "#review"
        )
        assert driven.app.exit_code == ExitCode.USAGE_ERROR
        assert dispatcher.planned == []

    def test_an_executor_failure_reports_one_with_verbatim_output(self) -> None:
        failed = Result(
            capability=Capability.DEPLOY,
            ok=False,
            exit_code=ExitCode.EXECUTOR_FAILED,
            summary="step 1/1 failed (sam): sam build",
            raw_output="Error: Template file not found",
        )
        driven = drive(
            FakeDispatcher(result=failed),
            *choose(Capability.DEPLOY),
            "#review",
            "#confirm",
        )
        assert driven.app.exit_code == ExitCode.EXECUTOR_FAILED
        shown = rendered(driven, "result")
        assert "Error: Template file not found" in shown
        assert "Traceback" not in shown

    def test_a_dispatched_run_url_is_surfaced_as_text(self) -> None:
        """Requirement 10.6: the URL reaches the operator in this mode too."""
        dispatched = Result(
            capability=Capability.RELEASE,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary="release: completed 1 step(s)",
            run_url=RUN_URL,
        )
        driven = drive(
            FakeDispatcher(result=dispatched),
            *choose(Capability.RELEASE),
            set_input("version", "v1.4.0"),
            "#review",
            "#confirm",
        )
        assert RUN_URL in rendered(driven, "result")


# -- C2. run following (Requirements 7.6, 10.6) ------------------------------


def _release(dispatcher: FakeDispatcher, github: FakeGitHub) -> Driven:
    """Drive a confirmed release through to its result screen."""
    return drive(
        dispatcher,
        *choose(Capability.RELEASE),
        set_input("version", "v1.4.0"),
        "#review",
        "#confirm",
        github=github,
    )


class TestRunFollowing:
    """Following is unconditional here, and it is the CLI's own helper doing it."""

    def test_a_dispatched_run_is_followed_without_being_asked(self) -> None:
        """No ``--json`` contract to protect, so there is no flag to gate it on."""
        github = FakeGitHub()
        driven = _release(FakeDispatcher(result=dispatched_result()), github)
        assert [run.run_id for run in github.watched] == ["42"]
        assert driven.app.exit_code == ExitCode.SUCCESS
        assert "the run concluded success" in rendered(driven, "result")

    def test_the_runs_verdict_is_the_sessions_outcome(self) -> None:
        github = FakeGitHub(conclusion="failure")
        driven = _release(FakeDispatcher(result=dispatched_result()), github)
        assert driven.app.exit_code == ExitCode.EXECUTOR_FAILED
        shown = rendered(driven, "result")
        assert "the run concluded failure" in shown
        assert RUN_URL in shown, "the URL is surfaced whatever the verdict"

    def test_a_command_that_dispatched_nothing_is_never_followed(self) -> None:
        github = FakeGitHub()
        driven = drive(
            FakeDispatcher(),
            *choose(Capability.DEPLOY),
            "#review",
            "#confirm",
            github=github,
        )
        assert github.watched == []
        assert driven.app.exit_code == ExitCode.SUCCESS

    def test_a_watch_that_fails_is_reported_rather_than_taken_as_a_pass(self) -> None:
        """Not having obtained the run's verdict is not the same as success."""
        github = FakeGitHub(watch_error=RuntimeError("gh: could not follow the run"))
        driven = _release(FakeDispatcher(result=dispatched_result()), github)
        shown = rendered(driven, "result")
        assert driven.app.exit_code != ExitCode.SUCCESS
        assert "could not follow the run" in shown
        assert "Traceback" not in shown


# -- D. one shared core, one entry point (Requirements 1.4, 10.7) ------------


class TestSharedCore:
    """The TUI routes through the same ``Dispatcher`` the CLI does."""

    def test_the_real_wiring_is_built_lazily_from_the_composition_root(self) -> None:
        """Opening the app reaches no tool: the wiring appears on first use."""
        app = DeployApp()
        assert isinstance(app.dispatcher(), Dispatcher)
        assert app.dispatcher() is app.dispatcher(), "the wiring is built once"
        assert app.plane().github is not None, "and it carries the executor to follow with"

    def test_run_tui_returns_the_sessions_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``run_tui`` returns an int from the shared vocabulary; it never exits."""
        seen: list[Dispatcher] = []

        def fake_run(app: DeployApp, *args: object, **kwargs: object) -> None:
            seen.append(app.dispatcher())
            app.exit_code = ExitCode.CONFIRMATION_REQUIRED

        monkeypatch.setattr(DeployApp, "run", fake_run)
        dispatcher = FakeDispatcher()
        code = run_tui(dispatcher=dispatcher)
        assert code == ExitCode.CONFIRMATION_REQUIRED
        assert type(code) is int
        assert seen == [dispatcher], "the injected dispatcher is the one used"
