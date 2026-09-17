"""TUI front-end (interactive mode) for the deploy control plane.

A **convenience skin, not the system of record.** GitHub's own
``workflow_dispatch`` form and the Actions run dashboard remain the canonical
human trigger/observe surface, so this front-end is deliberately shallow: it
collects intent into the same typed ``Command`` the CLI builds, hands it to the
same shared ``Dispatcher`` from ``core.assembly.build_dispatcher()``, and renders
the ``Plan`` and the ``Result`` with the same vocabulary from ``presentation``
(Requirement 1.4). No routing, validation or capability logic is restated here —
if it were, the two front-ends could disagree about the same intent, which is
exactly what Requirement 1.5 forbids.

**The confirmation step is the core's gate, surfaced — not a second gate.**
The guided flow submits every command *unconfirmed*: ``execute(plan,
confirmed=False)``. For a mutating plan the core raises
``ConfirmationRequired`` carrying the ``Plan``, and that raise is what puts the
plan on screen (Requirement 1.3); only the human pressing *Confirm and run*
produces ``execute(plan, confirmed=True)``. Structuring it this way means the TUI
holds no opinion about which commands need confirming — a read-only ``config
show`` simply never raises and never asks — so the set of gated operations
cannot drift from the CLI's. The alternative, checking
``plan.requires_confirmation`` here and deciding for ourselves, would be a
second copy of the safety rule.

**Run status is deliberately not followed.** ``Result.run_url`` is surfaced as
text and nothing more: following a dispatched run (``gh run watch`` /
``gh run view``) is presentation performed after ``execute()`` returns and is a
separate concern from this flow.

``run_tui()`` returns an ``int`` from the same ``ExitCode`` vocabulary as the CLI
(Requirement 10.7), so a caller cannot tell from the exit status which front-end
ran.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Final, assert_never, cast

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static

from bdo_deploy.core.assembly import build_dispatcher
from bdo_deploy.core.dispatch import (
    ACTION_ARG,
    CONFIG_SET,
    CONFIG_SHOW,
    DISPATCH_ARG,
    KEY_ARG,
    SYNC_ARG,
    VALUE_ARG,
    Dispatcher,
)
from bdo_deploy.core.errors import ConfirmationRequired, UsageError
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import (
    REVIEWERS_ARG,
    Capability,
    Command,
    Plan,
    Result,
    Target,
)
from bdo_deploy.presentation import failed_result, plan_lines, result_lines

DEFAULT_STAGE: Final = "dev"

CAPABILITY_HELP: Final[dict[Capability, str]] = {
    Capability.CONFIG: (
        "Read the merged samconfig.toml + SSM view for a stage, or change one value "
        "in its sanctioned location (a reviewed pull request, or an audited SSM write)."
    ),
    Capability.BOOTSTRAP: (
        "ONE-TIME, out-of-band step — NOT part of the routine deploy path. Stands up a "
        "stage's OIDC deploy role, artifact bucket and GitHub Environment with its "
        "required reviewers."
    ),
    Capability.DEPLOY: (
        "Deploy a stage: locally via the SAM CLI (dev/personal only), or in GitHub "
        "Actions. A production deploy has no local path — use release, or target=ci."
    ),
    Capability.RELEASE: (
        "Start a release: verify the git preconditions, then tag and push (or dispatch "
        "deploy.yml). The deploy itself always runs in Actions, behind its "
        "Environment's required reviewers."
    ),
}
"""One line per capability, shown while the operator is choosing.

The bootstrap entry repeats the CLI's "one-time, out-of-band" labelling because
Requirement 4.2 asks for it in *menus* as well as in help output — a human
picking from a list is precisely who might otherwise mistake it for the routine
deploy path.
"""


def _select_value[T](screen: Screen[None], *, field: str, expected: type[T]) -> T:
    """Read the ``Select`` named ``field`` as the type the ``Command`` needs.

    Textual types a ``Select``'s value as "the parameterised type *or* the blank
    sentinel", and a runtime ``isinstance`` cannot be given a parameterised
    class — so the expected type is passed in and checked here. Every ``Select``
    in this module is built with ``allow_blank=False`` and an initial value, so
    the blank branch is unreachable; it exists because a blank silently becoming
    a ``Command`` field would surface as a baffling failure much further
    downstream. Raising a ``UsageError`` names the field and maps to exit ``2``,
    the same as any other unusable intent.
    """
    value = screen.query_one(f"#{field}", Select).value
    if not isinstance(value, expected):  # pragma: no cover - allow_blank=False
        raise UsageError(field=field, value=None, problem=f"no {field} was selected")
    return value


def _deploy_app(screen: Screen[None]) -> DeployApp:
    """Return the running ``DeployApp`` a screen belongs to.

    Textual types ``Screen.app`` as the generic ``App``, and every screen in this
    module is only ever mounted by ``DeployApp``, so the cast is narrowing a type
    the framework cannot express rather than asserting anything new.
    """
    return cast("DeployApp", screen.app)


class _CapabilityScreen(Screen[None]):
    """Step one of the guided flow: which capability, and what it does."""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            yield Label("Capability")
            yield Select[Capability](
                [(capability.value, capability) for capability in Capability],
                value=Capability.DEPLOY,
                allow_blank=False,
                id="capability",
            )
            yield Static(CAPABILITY_HELP[Capability.DEPLOY], id="capability-help")
        with Horizontal(id="actions"):
            yield Button("Continue", id="continue", variant="primary")
        yield Footer()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Describe the highlighted capability as the operator moves through them."""
        capability = event.value
        if isinstance(capability, Capability):
            self.query_one("#capability-help", Static).update(CAPABILITY_HELP[capability])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        capability = _select_value(self, field="capability", expected=Capability)
        _deploy_app(self).push_screen(_IntentScreen(capability))


class _IntentScreen(Screen[None]):
    """Step two: collect the chosen capability's intent into one ``Command``.

    The fields offered per capability mirror the CLI's flags for the same
    subcommand, and ``_command()`` populates ``Command.args`` using the same arg
    names from ``core.dispatch`` — which is what makes the two front-ends produce
    identical ``Plan``s for the same intent (Requirement 1.5) rather than merely
    similar ones.
    """

    def __init__(self, capability: Capability) -> None:
        super().__init__()
        self._capability = capability

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            yield Static(CAPABILITY_HELP[self._capability], id="capability-help")
            yield Label("Stage")
            yield Input(value=DEFAULT_STAGE, id="stage")
            yield from self._capability_fields()
        with Horizontal(id="actions"):
            yield Button("Review plan", id="review", variant="primary")
            yield Button("Back", id="back")
        yield Footer()

    def _capability_fields(self) -> ComposeResult:
        """Yield only the fields the chosen capability can act on."""
        match self._capability:
            case Capability.CONFIG:
                yield Label("Action")
                yield Select[str](
                    [(CONFIG_SHOW, CONFIG_SHOW), (CONFIG_SET, CONFIG_SET)],
                    value=CONFIG_SHOW,
                    allow_blank=False,
                    id="action",
                )
                yield Label("Key — an SSM path, or a samconfig.toml parameter (set only)")
                yield Input(id="key")
                yield Label("Value (set only)")
                yield Input(id="value")
            case Capability.BOOTSTRAP:
                yield Label("Required reviewers, comma-separated — e.g. User:1234, Team:56")
                yield Input(id="reviewers")
            case Capability.DEPLOY:
                yield Label("Target — where the deploy executes")
                yield Select[Target](
                    [(target.value, target) for target in Target],
                    value=Target.LOCAL,
                    allow_blank=False,
                    id="target",
                )
                yield Checkbox("Use the local `sam sync` dev fast-loop", id=SYNC_ARG)
            case Capability.RELEASE:
                yield Label("Version — the release tag, vX.Y.Z")
                yield Input(id="version")
                yield Checkbox("Dispatch deploy.yml for an existing tag", id=DISPATCH_ARG)
            case _:  # pragma: no cover - exhaustive over Capability
                assert_never(self._capability)

    def _command(self) -> Command:
        """Build the typed ``Command`` from what the operator entered.

        ``dry_run`` and ``assume_yes`` stay false: this front-end has no dry-run
        mode — reviewing the plan *is* the preview — and its confirmation channel
        is the confirmation screen, not a flag. Construction validates, so an
        unusable stage or version is rejected here (exit ``2``) before the core is
        reached at all.
        """
        stage = self.query_one("#stage", Input).value.strip() or DEFAULT_STAGE
        target = Target.LOCAL
        version: str | None = None
        args: dict[str, str | bool | list[str]] = {}
        match self._capability:
            case Capability.CONFIG:
                action = _select_value(self, field="action", expected=str)
                args[ACTION_ARG] = action
                if action == CONFIG_SET:
                    args[KEY_ARG] = self.query_one("#key", Input).value.strip()
                    args[VALUE_ARG] = self.query_one("#value", Input).value
            case Capability.BOOTSTRAP:
                raw = self.query_one("#reviewers", Input).value
                args[REVIEWERS_ARG] = [entry.strip() for entry in raw.split(",") if entry.strip()]
            case Capability.DEPLOY:
                target = _select_value(self, field="target", expected=Target)
                args[SYNC_ARG] = self.query_one(f"#{SYNC_ARG}", Checkbox).value
            case Capability.RELEASE:
                version = self.query_one("#version", Input).value.strip()
                args[DISPATCH_ARG] = self.query_one(f"#{DISPATCH_ARG}", Checkbox).value
            case _:  # pragma: no cover - exhaustive over Capability
                assert_never(self._capability)
        return Command(
            capability=self._capability,
            target=target,
            stage=stage,
            version=version,
            args=args,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = _deploy_app(self)
        if event.button.id == "back":
            app.pop_screen()
            return
        app.submit(self._capability, self._command)


class _ConfirmScreen(Screen[None]):
    """Step three: the explicit confirmation step (Requirement 1.3).

    Reached only because the core refused to run a mutating plan unconfirmed, so
    the plan on screen is the one the core itself declined — not a preview built
    for display. *Confirm and run* re-submits that same ``Plan`` value, which is
    why what the operator read cannot differ from what executes.
    """

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self._plan = plan

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            yield Static(
                "This will change things. Nothing has run yet — review the plan, "
                "then confirm or go back.",
                id="warning",
            )
            yield Static("\n".join(plan_lines(self._plan)), id="plan")
        with Horizontal(id="actions"):
            yield Button("Confirm and run", id="confirm", variant="error")
            yield Button("Cancel", id="cancel")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = _deploy_app(self)
        if event.button.id == "cancel":
            # Back to the form with nothing run. The exit code stays
            # CONFIRMATION_REQUIRED, which is the honest description of a session
            # that ends here: a mutating plan was produced and not confirmed.
            app.pop_screen()
            return
        app.attempt(self._plan, confirmed=True)


class _ResultScreen(Screen[None]):
    """The final step: what happened, in the vocabulary the CLI also prints."""

    def __init__(self, result: Result) -> None:
        super().__init__()
        self._result = result

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            # No confirmation hint: `--yes` is the CLI's channel and would be
            # meaningless advice here. A dispatched run's URL is surfaced as text
            # by the shared renderer; this front-end does not follow the run.
            yield Static("\n".join(result_lines(self._result)), id="result")
        with Horizontal(id="actions"):
            yield Button("New command", id="again", variant="primary")
            yield Button("Quit", id="quit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = _deploy_app(self)
        if event.button.id == "again":
            app.pop_screen()
            return
        app.action_quit_app()


class DeployApp(App[None]):
    """The Textual application: a guided flow over the shared command core.

    Holds the only two calls into the core — ``plan()`` and ``execute()`` — so
    every screen stays a view. ``exit_code`` is the process exit code the flow has
    earned so far, in the same ``ExitCode`` vocabulary the CLI exits with: it
    starts at ``SUCCESS`` (quitting before submitting anything changed nothing),
    becomes ``CONFIRMATION_REQUIRED`` while a refused plan is awaiting a decision,
    and is finally whatever the ``Result`` carried. A later command overwrites an
    earlier one's code, so the status describes how the session actually ended.
    """

    CSS = """
    #form {
        height: auto;
        max-height: 1fr;
        padding: 1 2;
    }
    #actions {
        height: auto;
        padding: 0 2 1 2;
    }
    #actions Button {
        margin-right: 1;
    }
    #warning {
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self, *, dispatcher: Dispatcher | None = None) -> None:
        """Build the app, optionally against an already-wired ``Dispatcher``.

        ``dispatcher`` exists for tests, which drive the whole flow in-process
        against faked executors. Left unset, the composition root is asked for
        the real wiring — the same ``build_dispatcher()`` the CLI calls, so both
        front-ends demonstrably route through one core (Requirement 1.4) — and it
        is built lazily, on the first submission, so merely opening the app
        reaches no tool.
        """
        super().__init__()
        self._dispatcher = dispatcher
        self.exit_code: ExitCode = ExitCode.SUCCESS
        self.last_result: Result | None = None

    def dispatcher(self) -> Dispatcher:
        """Return the shared ``Dispatcher``, building the real wiring on demand."""
        if self._dispatcher is None:
            self._dispatcher = build_dispatcher()
        return self._dispatcher

    def on_mount(self) -> None:
        self.push_screen(_CapabilityScreen())

    def submit(self, capability: Capability, collect: Callable[[], Command]) -> None:
        """Collect the intent, plan it, and offer it to the core unconfirmed.

        ``collect`` is passed as a callable rather than an already-built
        ``Command`` so that a rejected intent — a stage that is not a
        ``samconfig.toml`` environment, a malformed version — is caught by the
        same handler as every other failure and rendered as a ``Result``. A
        ``Command`` cannot be constructed in a state the core would refuse, so
        building it is itself a fallible step.

        ``confirmed=False`` is deliberate: the core decides whether this plan
        needs a human, and its refusal is what raises the confirmation step.
        """
        try:
            command = collect()
            plan = self.dispatcher().plan(command)
        except Exception as exc:  # noqa: BLE001 - the front-end is the traceback boundary
            # Requirement 10.2: an operator sees the failure, never a traceback.
            # `failed_result` maps every outcome through `exit_code_for`, which
            # knows only four codes, so breadth here cannot invent a fifth.
            self._finish(failed_result(capability, exc))
            return
        self.attempt(plan, confirmed=False)

    def attempt(self, plan: Plan, *, confirmed: bool) -> None:
        """Execute ``plan``, or surface the core's refusal as the confirm step."""
        try:
            result = self.dispatcher().execute(plan, confirmed=confirmed)
        except ConfirmationRequired as exc:
            # Nothing has run. The CLI turns this into exit 3; the TUI turns it
            # into the confirmation step the human answers (Requirement 1.3).
            self.exit_code = ExitCode.CONFIRMATION_REQUIRED
            self.push_screen(_ConfirmScreen(exc.plan if exc.plan is not None else plan))
            return
        except Exception as exc:  # noqa: BLE001 - the front-end is the traceback boundary
            result = failed_result(plan.capability, exc)
        self._finish(result)

    def _finish(self, result: Result) -> None:
        """Record the outcome and show it.

        A spent confirmation step is dropped on the way: once the plan it guarded
        has run, leaving it on the stack would send *New command* back to a plan
        that has already executed — an invitation to run it twice.
        """
        if isinstance(self.screen, _ConfirmScreen):
            self.pop_screen()
        self.last_result = result
        self.exit_code = result.exit_code
        self.push_screen(_ResultScreen(result))

    def action_quit_app(self) -> None:
        """Leave, reporting what the session earned."""
        self.exit()


def run_tui(*, dispatcher: Dispatcher | None = None) -> int:
    """Launch the Textual application and return the process exit code.

    Returns rather than exits, for the same reason ``cli.main`` does: the
    front-end is then testable in-process. The code comes from the same
    ``ExitCode`` vocabulary as CLI mode (Requirement 10.7), read off the app
    rather than off Textual's own return value so an operator closing the
    terminal still reports what the session did.
    """
    app = DeployApp(dispatcher=dispatcher)
    app.run()
    return int(app.exit_code)


__all__ = ["DeployApp", "run_tui"]
