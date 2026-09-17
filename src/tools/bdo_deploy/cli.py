"""CLI front-end (non-interactive mode) for the deploy control plane.

A **thin** front-end (Requirement 1.4): each subcommand collects intent into a
typed ``Command``, hands it to the shared ``Dispatcher`` from
``core.assembly.build_dispatcher()``, and renders the ``Result``. It contains no
routing, no validation and no capability logic — those live in the core, so the
TUI front-end inherits exactly the same behaviour instead of a second copy that
could drift.

**It never prompts.** Not for a stage, not for a confirmation, not for anything:
agents, automation and CI have no terminal to answer with, and a front-end that
prompted "only sometimes" would hang a pipeline. ``--yes`` *is* how confirmation
is given; without it a mutating command exits ``3`` with the refused ``Plan``
rendered, which is the caller's cue to inspect the effects and re-invoke
(Requirement 10.4).

**Streams.** With ``--json`` the sole content of stdout is one serialized
``Result`` and every human-readable line goes to stderr (Requirement 1.2), so a
caller can pipe stdout straight into a parser. Without ``--json`` the same human
lines go to stdout, where a person reading a terminal expects them.

**Why ``--dry-run`` renders the plan but does not put it in the ``Result``.** A
dry run stops at ``plan()`` — it holds no executor reference and so cannot mutate
anything (Requirement 10.5) — and its plan is rendered for a human. It is *not*
folded into ``Result.plan``, which the models reserve for a plan that was
*refused* for want of confirmation. An agent that wants a machine-readable
preview already has a better path than ``--dry-run``: invoke without ``--yes``
and read ``Result.plan`` out of the exit-``3`` JSON. Keeping the two distinct
means an exit-``3`` ``Result`` carrying a plan always means "this was refused,
re-invoke with --yes" and never "this was a preview you asked for".

**Exit codes** come from one place, ``core.errors.exit_code_for``, so the CLI
cannot invent a fifth outcome (Requirement 10.7). No error path emits a Python
traceback: every failure is rendered as its own message plus the executor's
verbatim output (Requirement 10.2).
"""

from __future__ import annotations

import sys
from typing import Annotated, Final

import typer

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
from bdo_deploy.core.errors import ConfirmationRequired, exit_code_for
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

PROG_NAME: Final = "bdo-deploy"
DEFAULT_STAGE: Final = "dev"

EXIT_CODE_HELP: Final = (
    "Exit codes: 0 success, 1 executor or action failed, "
    "2 usage/validation error before any executor call, 3 confirmation required."
)

app = typer.Typer(
    name=PROG_NAME,
    no_args_is_help=True,
    add_completion=False,
    help=(
        "Deploy control plane: translate intent into a typed command and dispatch it to "
        "the SAM CLI, GitHub Actions or git. Never interactive — pass --yes to confirm a "
        f"mutating command. {EXIT_CODE_HELP}"
    ),
)

config_app = typer.Typer(
    no_args_is_help=True,
    help="Read or change configuration in its one sanctioned location (samconfig.toml or SSM).",
)
app.add_typer(config_app, name=Capability.CONFIG.value)


# -- shared flags -------------------------------------------------------------
#
# Declared once and reused by every subcommand, so the three contract flags mean
# the same thing everywhere and are accepted *after* the subcommand — which is
# where a caller writing `bdo-deploy deploy --json --yes` expects them.

JsonFlag = Annotated[
    bool,
    typer.Option(
        "--json",
        help=(
            "Emit exactly one serialized Result as the sole content of stdout; logs go to stderr."
        ),
    ),
]

YesFlag = Annotated[
    bool,
    typer.Option(
        "--yes",
        "-y",
        help=(
            "Confirm a mutating command. Nothing is ever prompted for: without this flag a "
            "mutating command exits 3 with the plan it refused to run."
        ),
    ),
]

DryRunFlag = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Stop after planning: render the plan and change nothing anywhere.",
    ),
]

StageOption = Annotated[
    str,
    typer.Option("--stage", help="The samconfig.toml environment to act on."),
]


# -- subcommands, one per capability ------------------------------------------


@config_app.command(CONFIG_SHOW)
def config_show(
    ctx: typer.Context,
    stage: StageOption = DEFAULT_STAGE,
    json_output: JsonFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Render the merged samconfig.toml + SSM view for a stage, secrets masked.

    The only read-only capability, so it takes no ``--yes``: there is nothing to
    confirm, and offering the flag would suggest otherwise.
    """
    raise typer.Exit(
        _run(
            ctx,
            capability=Capability.CONFIG,
            stage=stage,
            args={ACTION_ARG: CONFIG_SHOW},
            json_output=json_output,
            assume_yes=True,
            dry_run=dry_run,
        )
    )


@config_app.command(CONFIG_SET)
def config_set(
    ctx: typer.Context,
    key: Annotated[
        str,
        typer.Argument(
            help=(
                "An absolute /bdo-market-insights/<stage>/<category>/<key> SSM path for "
                "operational config, or a samconfig.toml parameter name (e.g. BdoRegions) "
                "for deploy-time config, which is changed by a pull request."
            )
        ),
    ],
    value: Annotated[str, typer.Argument(help="The value to set.")],
    stage: StageOption = DEFAULT_STAGE,
    json_output: JsonFlag = False,
    yes: YesFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Change one configuration value in its sanctioned location."""
    raise typer.Exit(
        _run(
            ctx,
            capability=Capability.CONFIG,
            stage=stage,
            args={ACTION_ARG: CONFIG_SET, KEY_ARG: key, VALUE_ARG: value},
            json_output=json_output,
            assume_yes=yes,
            dry_run=dry_run,
        )
    )


@app.command(Capability.BOOTSTRAP.value)
def bootstrap(
    ctx: typer.Context,
    stage: StageOption = DEFAULT_STAGE,
    reviewer: Annotated[
        list[str] | None,
        typer.Option(
            "--reviewer",
            help=(
                "A required reviewer of the stage's GitHub Environment, as <Type>:<id> "
                "(e.g. User:1234, Team:56). Repeatable. A prod bootstrap needs at least one, "
                "so production cannot be bootstrapped unprotected."
            ),
        ),
    ] = None,
    json_output: JsonFlag = False,
    yes: YesFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """ONE-TIME, out-of-band step: stand up a stage's deploy plumbing.

    Provisions the OIDC deploy role and artifact bucket and creates the stage's
    GitHub Environment with its required reviewers. This is NOT part of the
    routine deploy path — a routine deploy is a single declarative `deploy`,
    and the stack self-bootstraps.
    """
    raise typer.Exit(
        _run(
            ctx,
            capability=Capability.BOOTSTRAP,
            stage=stage,
            args={REVIEWERS_ARG: list(reviewer or [])},
            json_output=json_output,
            assume_yes=yes,
            dry_run=dry_run,
        )
    )


@app.command(Capability.DEPLOY.value)
def deploy(
    ctx: typer.Context,
    stage: StageOption = DEFAULT_STAGE,
    target: Annotated[
        Target,
        typer.Option(
            "--target",
            help=(
                "Where the deploy executes: 'local' runs the SAM CLI on this machine "
                "(dev/personal only), 'ci' triggers the environment-protected deploy.yml run."
            ),
        ),
    ] = Target.LOCAL,
    sync: Annotated[
        bool,
        typer.Option("--sync", help="Use the local `sam sync` dev fast-loop instead of a deploy."),
    ] = False,
    json_output: JsonFlag = False,
    yes: YesFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Deploy a stage, locally via the SAM CLI or in CI via GitHub Actions.

    A production deploy has no local path: use `release`, or --target ci.
    """
    raise typer.Exit(
        _run(
            ctx,
            capability=Capability.DEPLOY,
            target=target,
            stage=stage,
            args={SYNC_ARG: sync},
            json_output=json_output,
            assume_yes=yes,
            dry_run=dry_run,
        )
    )


@app.command(Capability.RELEASE.value)
def release(
    ctx: typer.Context,
    version: Annotated[str, typer.Argument(help="The release tag, in the format vX.Y.Z.")],
    stage: StageOption = DEFAULT_STAGE,
    dispatch: Annotated[
        bool,
        typer.Option(
            "--dispatch",
            help="Dispatch deploy.yml for an existing version instead of creating a tag.",
        ),
    ] = False,
    json_output: JsonFlag = False,
    yes: YesFlag = False,
    dry_run: DryRunFlag = False,
) -> None:
    """Start a release: verify the preconditions, then tag and push (or dispatch).

    The deploy itself always runs in GitHub Actions, and a production run still
    waits on its Environment's required reviewers.
    """
    raise typer.Exit(
        _run(
            ctx,
            capability=Capability.RELEASE,
            stage=stage,
            version=version,
            args={DISPATCH_ARG: dispatch},
            json_output=json_output,
            assume_yes=yes,
            dry_run=dry_run,
        )
    )


# -- the one shared path from intent to exit code -----------------------------


def _run(
    ctx: typer.Context,
    *,
    capability: Capability,
    json_output: bool,
    assume_yes: bool,
    dry_run: bool,
    stage: str = DEFAULT_STAGE,
    target: Target = Target.LOCAL,
    version: str | None = None,
    args: dict[str, str | bool | list[str]] | None = None,
) -> int:
    """Build the ``Command``, dispatch it, render the ``Result``, return the code.

    Every subcommand funnels through here, so the flag contract — the streams,
    the confirmation gate, the error rendering, the exit code — is implemented
    exactly once and cannot differ per capability.
    """
    console = _Console(json_output=json_output)
    try:
        cmd = Command(
            capability=capability,
            target=target,
            stage=stage,
            version=version,
            args=args or {},
            dry_run=dry_run,
            assume_yes=assume_yes,
        )
        dispatcher = _dispatcher(ctx)
        plan = dispatcher.plan(cmd)
        console.render_plan(plan)
        if dry_run:
            # Stop at planning: `execute()` is the only method that can reach an
            # executor, so not calling it is what makes a dry run inert.
            result = Result(
                capability=capability,
                ok=True,
                exit_code=ExitCode.SUCCESS,
                summary=(
                    f"dry run: {len(plan.steps)} planned step(s), nothing executed, "
                    "nothing changed"
                ),
            )
        else:
            result = dispatcher.execute(plan, confirmed=assume_yes)
    except ConfirmationRequired as exc:
        # The core raises so a caller cannot silently ignore the gate; the CLI is
        # where that becomes the documented contract — exit 3 with the refused
        # plan in `Result.plan` (Requirement 10.4). Nothing has run.
        result = Result(
            capability=capability,
            ok=False,
            exit_code=exit_code_for(exc),
            summary=exc.summary,
            plan=exc.plan,
        )
    except Exception as exc:  # noqa: BLE001 - the front-end is the traceback boundary
        # Deliberately broad: this is the last frame before the process exits, and
        # Requirement 10.2 forbids leaking a traceback to the operator. Every
        # outcome still maps through `exit_code_for`, which knows only four codes,
        # so breadth here cannot invent a fifth.
        result = failed_result(capability, exc)
    console.render_result(result)
    return int(result.exit_code)


def _dispatcher(ctx: typer.Context) -> Dispatcher:
    """Return the ``Dispatcher`` to route through, building the real one by default.

    A ``Dispatcher`` placed on the Click context (``main(argv, dispatcher=...)``)
    wins, which is how a test drives the whole front-end in-process against faked
    executors. Otherwise the composition root is asked for the real wiring — the
    same ``build_dispatcher()`` the TUI calls, so both front-ends demonstrably
    route through one core (Requirement 1.4).
    """
    if isinstance(ctx.obj, Dispatcher):
        return ctx.obj
    return build_dispatcher()


# -- rendering ----------------------------------------------------------------


class _Console:
    """Writes the two kinds of output to the two streams the mode dictates.

    With ``--json`` the serialized ``Result`` owns stdout outright and every human
    line is diverted to stderr (Requirement 1.2); without it the human lines go to
    stdout. One object holds that decision so no call site has to remember it.
    """

    def __init__(self, *, json_output: bool) -> None:
        self._json = json_output

    def _log(self, line: str = "") -> None:
        print(line, file=sys.stderr if self._json else sys.stdout)

    def render_plan(self, plan: Plan) -> None:
        """Show what will run, in order, and what it will change.

        The lines come from ``presentation.plan_lines``, the vocabulary the TUI
        renders too, so a dry run and a TUI confirmation step describe the same
        pending mutation in the same words.
        """
        for line in plan_lines(plan):
            self._log(line)

    def render_result(self, result: Result) -> None:
        """Emit the outcome: one JSON object, or human lines."""
        if self._json:
            # Exactly one Result, and nothing else, on stdout. Secret-shaped
            # values travel as SecretStr, so they are absent from this
            # serialization rather than merely skipped by a renderer.
            print(result.model_dump_json(), file=sys.stdout)
            return
        # `--yes` is this front-end's confirmation channel, so the hint naming it
        # is supplied here rather than baked into the shared renderer: the TUI
        # confirms by a keypress and has no flag to point at.
        for line in result_lines(
            result, confirmation_hint="re-invoke with --yes to run the plan above"
        ):
            self._log(line)


def main(argv: list[str] | None = None, *, dispatcher: Dispatcher | None = None) -> int:
    """Process entry point for the ``bdo-deploy`` console script.

    Returns the exit code instead of exiting, so the whole front-end is testable
    in-process without spawning a subprocess. Typer/Click signal their own
    outcomes — ``--help``, an unknown flag, a subcommand's ``typer.Exit`` — by
    raising ``SystemExit``; catching it here is what turns those into the same
    returned ``int``. Click has already printed its own message by then, so no
    traceback reaches the operator either way.

    ``dispatcher`` overrides the core the subcommands route through, for tests.
    """
    args = sys.argv[1:] if argv is None else argv
    command = typer.main.get_command(app)
    try:
        command(args=args, prog_name=PROG_NAME, obj=dispatcher)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return int(ExitCode.SUCCESS)
        if isinstance(code, int):
            return code
        # A string payload is a message, not a code: report it as a failure
        # rather than crashing on int() (the contract admits no fifth outcome).
        print(code, file=sys.stderr)
        return int(ExitCode.EXECUTOR_FAILED)
    return int(ExitCode.SUCCESS)


__all__ = ["app", "main"]
