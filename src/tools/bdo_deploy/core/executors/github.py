"""Adapter over the GitHub CLI: everything the control plane does through ``gh``.

Its remit is three groups: **workflow dispatch** (trigger the ``deploy.yml`` run
that performs a shared-env or prod deploy), **run status** (surface that run back
to the operator), and **repository / environment administration** (create or
update a GitHub Environment and set an Environment secret — used only by the
one-time ``bootstrap`` capability).

Named ``GitHubExecutor`` rather than ``ActionsDispatcher`` because its remit is
broader than Actions, and because "Dispatcher" collided with the core
``Dispatcher`` that routes *to* it.

Two types carry the behaviour, mirroring ``executors/sam.py`` and
``executors/git.py``:

- ``GitHubExecutor`` — the Protocol the design documents and ``Dispatcher``
  type-hints against. It is the interface; it prescribes no invocation.
- ``GitHubCli`` — the concrete adapter that satisfies it by shelling out to
  ``gh`` through the shared ``run_command`` runner. The runner is injected, so
  every argument list an op produces can be asserted without a real ``gh``, a
  real repository or a real GitHub Environment.

``run_step`` switches on ``step.op`` and reads typed values out of
``step.params``; it never parses ``step.command``, which is the display rendering
only.

Three things about this adapter are load-bearing and easy to undo by accident:

**The dispatched inputs are exactly the workflow's typed inputs.** A dispatch
sends ``-f stage=…`` and, when the command carries one, ``-f version=…`` — built
from ``step.params``, which the planner renders from the same fields as the
previewed ``gh workflow run`` line. No input is invented here, so the Actions UI
and the control plane dispatch the identical job with identical inputs
(Requirement 8.3). Nothing here executes a deploy: it triggers the
environment-protected run and stops (Requirements 5.3, 6.2).

**The dispatched run is authoritative, so its URL must come back.**
``gh workflow run`` prints no run URL, so the URL is resolved in a **second,
read-only query** immediately afterwards — ``gh run list --workflow <wf> --limit
1 --json url,databaseId`` — and put in ``CommandResult.run_url`` for the
dispatcher to surface (Requirements 7.6, 10.6). That query reads the newest run
of the workflow, which is the run just dispatched unless something else
dispatched the same workflow in the same instant; the window is small, it is not
worth a polling loop, and the failure mode is a URL pointing at a neighbouring
run of the same workflow rather than a wrong outcome. A dispatch that succeeded
but whose URL could not be resolved is still reported as a **success** with the
reason the URL is missing — the run is already moving, and failing the step would
describe the deploy as not-started when it has started.

**A secret value never touches argv.** ``_process`` documents why: argv is
visible to other processes and is quoted back in the timeout / missing-tool
messages. So ``set_environment_secret`` writes the value to ``gh secret set`` on
**stdin** and passes no ``--body``. The planner puts only ``environment`` and
``name`` in ``params`` (Requirement 4.4), so ``run_step`` reads the value from
the process environment instead — from ``BDO_DEPLOY_SECRET_<NAME>``, e.g.
``BDO_DEPLOY_SECRET_AWS_DEPLOY_ROLE_ARN`` — and fails naming that variable when
it is absent or empty. The value is never echoed: not into an argument, not into
a returned message, not into an error.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Final, Protocol, assert_never

from pydantic import BaseModel

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import CommandRunner, run_command
from bdo_deploy.core.executors.base import StepExecutor
from bdo_deploy.core.models import CommandResult, Op, PlanStep

GH: Final = "gh"
"""The sanctioned executable; every invocation below starts with it."""

DEFAULT_WORKFLOW: Final = "deploy.yml"
"""The dedicated CD workflow a dispatch targets (ADR-0038, Requirement 8.3).

``core.dispatch`` declares the same value for the planner and imports this
module, so importing it from there would be a cycle. It is a *default* here
rather than a second source of truth: a planned step carries ``workflow`` in
``params`` and ``run_step`` passes that through, so this constant is only reached
when a caller invokes ``run_workflow`` directly.
"""

SECRET_ENV_PREFIX: Final = "BDO_DEPLOY_SECRET_"
"""Prefix of the environment variable a secret's value is read from.

``AWS_DEPLOY_ROLE_ARN`` is therefore supplied as
``BDO_DEPLOY_SECRET_AWS_DEPLOY_ROLE_ARN``.
"""

WORKFLOW_PARAM: Final = "workflow"
STAGE_PARAM: Final = "stage"
VERSION_PARAM: Final = "version"
ENVIRONMENT_PARAM: Final = "environment"
NAME_PARAM: Final = "name"

_RUN_LIST_FIELDS: Final = "url,databaseId"
"""The two fields the run-URL query asks for; ``url`` is what ends up surfaced."""


class RunRef(BaseModel):
    """A reference to one dispatched workflow run.

    ``url`` and ``run_id`` are optional because they are resolved *after* the
    dispatch by a separate query (see the module docstring): a reference to a run
    that was certainly dispatched but could not be located yet is still a useful
    thing to return, and it is not a failure.
    """

    workflow: str
    run_id: str | None = None
    url: str | None = None


class RunStatus(BaseModel):
    """What ``gh run view`` reported about a run.

    ``status`` / ``conclusion`` are GitHub's own strings, kept verbatim rather
    than mapped onto a local vocabulary — the CI run is authoritative
    (Requirement 10.6), so re-encoding its verdict would only create a second
    one that can disagree.
    """

    run: RunRef
    status: str | None = None
    conclusion: str | None = None
    output: str = ""
    ok: bool = True


class GitHubExecutor(StepExecutor, Protocol):
    """Protocol for the GitHub CLI adapter — the interface, not an invocation."""

    def run_workflow(
        self,
        *,
        stage: str,
        version: str | None = None,
        inputs: dict[str, str] | None = None,
    ) -> CommandResult:
        """``gh workflow run deploy.yml -f stage=… -f version=…``.

        Targets the dedicated CD workflow (ADR-0038) and sends exactly the
        workflow's typed inputs (Requirement 8.3). The design sketches a
        ``RunRef`` return; the shipped signature returns ``CommandResult``
        because that is what the dispatcher consumes and because a dispatch can
        fail — the run reference travels in ``CommandResult.run_url``, which is
        the field the dispatcher already surfaces (Requirement 10.6).
        """
        ...

    def watch(self, run: RunRef) -> CommandResult:
        """``gh run watch`` — block until the dispatched run finishes."""
        ...

    def view(self, run: RunRef) -> RunStatus:
        """``gh run view`` — report the run's status without waiting."""
        ...

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
    ) -> CommandResult:
        """Create or update a GitHub Environment, e.g. ``prod`` with reviewers.

        ``gh api repos/{owner}/{repo}/environments/{name}``. Required reviewers
        are what makes the prod gate a *platform* control rather than application
        code (Requirement 6.3).
        """
        ...

    def set_environment_secret(
        self,
        *,
        environment: str,
        name: str,
        value: str,
    ) -> CommandResult:
        """Set an Environment secret, e.g. ``AWS_DEPLOY_ROLE_ARN``.

        Only ``name`` is ever rendered in a ``Plan``; the role ARN is
        account-identifying, so ``value`` is supplied at execution time and never
        appears in a plan, in ``--json`` output, or in any tracked file
        (Requirement 4.4). It is handed to ``gh`` on **stdin**, never as an
        argument.
        """
        ...


class GitHubCli:
    """``GitHubExecutor`` implemented by shelling out to the ``gh`` CLI.

    Satisfies the Protocol **structurally** rather than by inheritance — the
    ``TYPE_CHECKING`` binding at the end of this module is what makes mypy prove
    it — so ``Dispatcher``'s ``GitHubExecutor``-typed parameter accepts it
    without ``dispatch.py`` knowing this class exists.
    """

    def __init__(self, *, runner: CommandRunner = run_command) -> None:
        self._run = runner

    # -- workflow dispatch --------------------------------------------------

    def run_workflow(
        self,
        *,
        stage: str,
        version: str | None = None,
        inputs: dict[str, str] | None = None,
        workflow: str = DEFAULT_WORKFLOW,
    ) -> CommandResult:
        """Dispatch ``workflow`` for ``stage``, then resolve the run's URL.

        The inputs sent are ``stage``, ``version`` when one was given, and any
        extra ``inputs`` the caller passes — nothing is invented here, so the
        Actions UI and the control plane dispatch the identical job (Requirement
        8.3). Only the run is triggered; the deploy itself is CI's (Requirements
        5.3, 6.2).

        A failed dispatch is returned as-is with no URL. A successful one is
        returned with ``run_url`` set, or — when the follow-up query could not
        find it — still as a success, saying the URL was unavailable rather than
        reporting a started deploy as a failure.
        """
        argv = [GH, "workflow", "run", workflow, *_input_flags(stage, version, inputs)]
        dispatched = self._run(argv)
        if not dispatched.ok:
            return dispatched
        run = self._latest_run(workflow)
        if run.url is None:
            return CommandResult(
                ok=True,
                output=(
                    f"{dispatched.output.rstrip()}\n"
                    f"the {workflow} run was dispatched, but its URL could not be resolved; "
                    f"find it with `gh run list --workflow {workflow}`"
                ).lstrip(),
            )
        return CommandResult(
            ok=True,
            output=f"{dispatched.output.rstrip()}\n{run.url}".lstrip(),
            run_url=run.url,
        )

    # -- run status ---------------------------------------------------------

    def watch(self, run: RunRef) -> CommandResult:
        """Follow the run until it finishes; the run's own verdict is the result."""
        return self._run([GH, "run", "watch", *_run_selector(run)])

    def view(self, run: RunRef) -> RunStatus:
        """Report ``run``'s status and conclusion without waiting for it.

        A run whose JSON could not be read is reported as ``ok=False`` with
        ``gh``'s own output and no status, rather than as a run with a guessed
        one: an unreadable status is not a passing status.
        """
        viewed = self._run(
            [
                GH,
                "run",
                "view",
                *_run_selector(run),
                "--json",
                "status,conclusion,url",
            ]
        )
        if not viewed.ok:
            return RunStatus(run=run, output=viewed.output, ok=False)
        fields = _json_object(viewed.output)
        if fields is None:
            return RunStatus(run=run, output=viewed.output, ok=False)
        return RunStatus(
            run=RunRef(
                workflow=run.workflow,
                run_id=run.run_id,
                url=_str_field(fields, "url") or run.url,
            ),
            status=_str_field(fields, "status"),
            conclusion=_str_field(fields, "conclusion"),
            output=viewed.output,
            ok=True,
        )

    # -- repository / environment administration (bootstrap only) -----------

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
    ) -> CommandResult:
        """Create or update the ``name`` Environment, optionally with reviewers.

        ``gh api`` substitutes ``{owner}`` / ``{repo}`` from the repository the
        command runs in, which is the repository root ``_process`` always uses —
        so the owner and repo are never hard-coded or derived here.

        Reviewers, when given, are sent as a JSON body on **stdin** (``--input
        -``) rather than as flags: the reviewer list is a nested structure the
        GitHub API wants as JSON, and building it on stdin avoids composing that
        structure out of repeated arguments. Each entry is ``<Type>:<id>``, e.g.
        ``User:1234`` or ``Team:56``; a bare id is read as a ``User``.
        """
        argv = [GH, "api", "--method", "PUT", f"repos/{{owner}}/{{repo}}/environments/{name}"]
        if reviewers is None:
            return self._run(argv)
        return self._run([*argv, "--input", "-"], stdin=_reviewers_body(reviewers))

    def set_environment_secret(
        self,
        *,
        environment: str,
        name: str,
        value: str,
    ) -> CommandResult:
        """Set the ``name`` Environment secret on ``environment`` from stdin.

        ``value`` is written to ``gh secret set``'s standard input and no
        ``--body`` flag is passed, so the value appears in no argument list and
        in no message this call can return (Requirement 4.4). An empty value is
        refused before ``gh`` is invoked: it would otherwise store an empty
        secret and a deploy would fail much later with a blank role ARN.
        """
        if not value:
            raise UsageError(
                field="value",
                value="",
                problem=f"the {name} secret value is empty, so nothing was written",
                hint=f"supply it in {SECRET_ENV_PREFIX}{name}",
            )
        return self._run(
            [GH, "secret", "set", name, "--env", environment],
            stdin=value,
        )

    # -- the StepExecutor seam ---------------------------------------------

    def run_step(self, step: PlanStep) -> CommandResult:
        """Dispatch ``step``'s op onto the domain method that performs it.

        Switches on ``step.op`` and reads typed values out of ``step.params``; it
        **never parses ``step.command``**, which is the display rendering only.
        The match covers every ``Op`` member — the github ops onto a method,
        every other op onto a named refusal — so ``assert_never`` makes adding an
        op without handling it a type error rather than a silent fallthrough.
        """
        match step.op:
            case Op.GITHUB_RUN_WORKFLOW:
                return self.run_workflow(
                    stage=_str_param(step, STAGE_PARAM),
                    version=_optional_str_param(step, VERSION_PARAM),
                    workflow=_str_param(step, WORKFLOW_PARAM),
                )
            case Op.GITHUB_ENVIRONMENT_SET:
                return self.set_environment(name=_str_param(step, ENVIRONMENT_PARAM))
            case Op.GITHUB_SECRET_SET:
                name = _str_param(step, NAME_PARAM)
                return self.set_environment_secret(
                    environment=_str_param(step, ENVIRONMENT_PARAM),
                    name=name,
                    value=_secret_from_environment(name),
                )
            case (
                Op.SAM_BUILD
                | Op.SAM_DEPLOY
                | Op.SAM_SYNC
                | Op.SAM_PIPELINE_BOOTSTRAP
                | Op.GIT_TAG
                | Op.GIT_PUSH
                | Op.CONFIG_SHOW
                | Op.SSM_PUT
                | Op.SAMCONFIG_PR
            ):
                raise UsageError(
                    field="op",
                    value=step.op.value,
                    problem=f"{step.op.value!r} is not a GitHub operation",
                    hint=f"route it to the {step.executor!r} executor instead",
                )
            case _:  # pragma: no cover - exhaustive over Op
                assert_never(step.op)

    # -- run-URL resolution -------------------------------------------------

    def _latest_run(self, workflow: str) -> RunRef:
        """The newest run of ``workflow``, as far as one read-only query can tell.

        ``gh workflow run`` prints nothing identifying the run it started, so
        this is how a dispatch gets a URL at all. One query, no polling loop: a
        reference that could not be resolved is reported as such by the caller
        rather than waited for.
        """
        listed = self._run(
            [GH, "run", "list", "--workflow", workflow, "--limit", "1", "--json", _RUN_LIST_FIELDS]
        )
        if not listed.ok:
            return RunRef(workflow=workflow)
        runs = _json_array(listed.output)
        if not runs:
            return RunRef(workflow=workflow)
        newest = runs[0]
        return RunRef(
            workflow=workflow,
            run_id=_id_field(newest, "databaseId"),
            url=_str_field(newest, "url"),
        )


def _input_flags(
    stage: str,
    version: str | None,
    inputs: dict[str, str] | None,
) -> list[str]:
    """The ``-f key=value`` flags for a dispatch, in a deterministic order.

    ``stage`` first, then ``version`` when set — the same fields, in the same
    order, as the planner's rendered ``gh workflow run`` line, so the previewed
    command and the dispatched inputs cannot disagree. Extra ``inputs`` follow in
    sorted order so the invocation is reproducible.
    """
    values: list[tuple[str, str]] = [(STAGE_PARAM, stage)]
    if version is not None:
        values.append((VERSION_PARAM, version))
    for key in sorted(inputs or {}):
        if key not in {STAGE_PARAM, VERSION_PARAM}:
            values.append((key, (inputs or {})[key]))
    return [flag for key, value in values for flag in ("-f", f"{key}={value}")]


def _run_selector(run: RunRef) -> list[str]:
    """How ``gh run watch`` / ``gh run view`` are pointed at ``run``.

    A known run id selects that run exactly; without one the argument is omitted,
    which is ``gh``'s "the most recent run" behaviour — the only thing left to
    look at when the dispatch's URL could not be resolved.
    """
    return [] if run.run_id is None else [run.run_id]


def _reviewers_body(reviewers: list[str]) -> str:
    """The JSON body that sets ``reviewers`` on an Environment.

    Each entry is ``<Type>:<id>``; a bare id is read as a ``User``. The ids stay
    strings here — the API accepts them either way, and parsing them into ints
    would only add a failure mode for a value this adapter does not interpret.
    """
    entries = []
    for reviewer in reviewers:
        kind, _, identifier = reviewer.rpartition(":")
        entries.append({"type": kind or "User", "id": identifier})
    return json.dumps({"reviewers": entries})


def _secret_from_environment(name: str) -> str:
    """Read the ``name`` secret's value out of the process environment.

    The planner puts only ``environment`` and ``name`` in ``params``, never a
    value (Requirement 4.4), so execution is where the value has to come from.
    An absent or empty variable is a named failure quoting the variable's *name*
    — the value is never echoed, not even on the error path.
    """
    variable = f"{SECRET_ENV_PREFIX}{name}"
    value = os.environ.get(variable, "")
    if not value:
        raise UsageError(
            field=variable,
            value=None,
            problem=(f"the {name} secret value is not in the environment, so nothing was written"),
            hint=f"export {variable} and re-run",
        )
    return value


def _json_object(output: str) -> dict[str, object] | None:
    """Parse ``output`` as a JSON object, or ``None`` if it is not one.

    ``gh`` writes machine-readable JSON on ``--json``, but its output is captured
    together with stderr, so a warning line can precede it. A body that does not
    parse is reported as unreadable rather than guessed at.
    """
    try:
        parsed: object = json.loads(output)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_array(output: str) -> list[dict[str, object]]:
    """Parse ``output`` as a JSON array of objects, or an empty list."""
    try:
        parsed: object = json.loads(output)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _str_field(fields: dict[str, object], name: str) -> str | None:
    """A string field of a parsed ``gh --json`` object, or ``None``."""
    value = fields.get(name)
    return value if isinstance(value, str) else None


def _id_field(fields: dict[str, object], name: str) -> str | None:
    """A run id, which ``gh`` reports as a number, rendered as a string."""
    value = fields.get(name)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) else None


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


def _optional_str_param(step: PlanStep, name: str) -> str | None:
    """Read an optional string out of ``step.params``.

    ``version`` is present only when the command carried one, so its absence is
    normal; a present-but-wrongly-typed value is still a planning fault.
    """
    if name not in step.params:
        return None
    return _str_param(step, name)


if TYPE_CHECKING:  # pragma: no cover - a type-check-time assertion, not runtime code
    # ``Dispatcher`` type-hints against the Protocol, so the concrete adapter has
    # to be structurally compatible with it. Binding one to the other here makes
    # mypy fail this module if a method's name or signature ever drifts.
    _satisfies_protocol: GitHubExecutor = GitHubCli()


__all__ = [
    "DEFAULT_WORKFLOW",
    "GH",
    "SECRET_ENV_PREFIX",
    "GitHubCli",
    "GitHubExecutor",
    "RunRef",
    "RunStatus",
]
