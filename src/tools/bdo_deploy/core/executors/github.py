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

``RunRef`` and ``RunStatus`` are **defined in ``core.models``** and re-exported
here. They are this adapter's vocabulary, but ``Result.run`` and
``CommandResult.run`` carry a ``RunRef``, and a model defined here would make
``core.models`` import the executor module that already imports it. The
re-export keeps ``from ...executors.github import RunRef`` working, so nothing
else had to learn where the type moved.

Four things about this adapter are load-bearing and easy to undo by accident:

**The dispatched inputs are exactly the workflow's typed inputs.** A dispatch
sends ``-f stage=…`` and, when the command carries one, ``-f version=…`` — built
from ``step.params``, which the planner renders from the same fields as the
previewed ``gh workflow run`` line. No input is invented here, so the Actions UI
and the control plane dispatch the identical job with identical inputs
(Requirement 8.3). Nothing here executes a deploy: it triggers the
environment-protected run and stops (Requirements 5.3, 6.2).

**The dispatched run is authoritative, so its identity must come back.**
``gh workflow run`` prints nothing identifying the run it started, so the run is
resolved in a **second, read-only query** immediately afterwards — ``gh run list
--workflow <wf> --limit 1 --json url,databaseId`` — and returned **twice over**:
as ``CommandResult.run_url`` for the dispatcher to surface to a human, and as
``CommandResult.run`` — the ``RunRef`` the front-end's ``follow_run()`` needs to
call ``watch`` / ``view`` (Requirements 7.6, 10.6). Both come from the one query,
so following a run never means parsing an id back out of a display URL. That
query reads the newest run
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

**The ``gh --json`` payloads are validated, and the validation never raises.**
``gh``'s JSON is an I/O boundary, so it is validated against a Pydantic model
rather than hand-parsed with ``dict.get`` + ``isinstance`` (repo standard,
AGENTS.md). What is validated is ``CommandResult.stdout`` — the stream ``gh``
prints JSON to — and not the stdout+stderr join ``output`` carries: a warning on
stderr used to be concatenated onto the payload, which made it unparseable and
turned a *passing* run into a reported failure once ``follow_run`` folded the
unreadable status in. The reported output is still the combined ``output``, so a
failure surfaces everything ``gh`` said (Requirement 10.2); only the parsing is
narrowed. Every ``ValidationError`` is caught at that boundary and turned into
the tolerant outcome the callers already have: an unreadable ``gh run view``
payload is ``RunStatus(ok=False)`` carrying ``gh``'s own output with **no status
and no conclusion invented**, and an unreadable ``gh run list`` payload is a
``RunRef`` naming only the workflow, still a successful dispatch. A
``ValidationError`` escaping as an exception would reach the operator as a
traceback (Requirement 10.2) and would turn an unreadable run into a crash rather
than a reported failure — so the models are deliberately *permissive about
fields* and strict only about the payload being a run object at all.

**The deployment branch/tag policy takes two calls, in one order.** GitHub keeps
the *mode* on the Environment and each admitted *pattern* as its own resource, so
``set_environment`` sends the ``deployment_branch_policy`` mode first and only
then POSTs one ``deployment-branch-policies`` entry per pattern — the per-pattern
endpoint 404s while ``custom_branch_policies`` is false. Re-running ``bootstrap``
therefore cannot accumulate duplicates: the POST is keyed on the pattern and
GitHub answers a repeat with ``303 See Other`` at the existing entry instead of
creating a second one, which is why no read-then-diff is done here. The mode is
sent as ``protected_branches: false`` + ``custom_branch_policies: true`` because
the API requires both and refuses them equal (422). This policy is the boundary
that closes arbitrary-ref dispatch, enforced outside the repository (Requirement
6.5, ADR-0040).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Annotated, Final, Protocol, assert_never

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, RootModel, ValidationError

from bdo_deploy.core.constants import DEPLOY_WORKFLOW
from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import CommandRunner, run_command
from bdo_deploy.core.executors.base import (
    StepExecutor,
    optional_str_param,
    str_param,
)
from bdo_deploy.core.models import CommandResult, Op, PlanStep, RunRef, RunStatus

GH: Final = "gh"
"""The sanctioned executable; every invocation below starts with it."""

DEFAULT_WORKFLOW: Final = DEPLOY_WORKFLOW
"""The dedicated CD workflow a dispatch targets (ADR-0038, Requirement 8.3).

``core.constants.DEPLOY_WORKFLOW`` under the name this module already published:
the same value ``core.dispatch`` plans with, not a second source of truth. It is a
*default* here because a planned step carries ``workflow`` in ``params`` and
``run_step`` passes that through, so this name is reached only when a caller
invokes ``run_workflow`` directly.
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
REVIEWERS_PARAM: Final = "reviewers"
ALLOWED_REFS_PARAM: Final = "allowed_refs"

BRANCH_REF_TYPE: Final = "branch"
"""The ``type`` an ``allowed_refs`` entry carrying no ``<Type>:`` prefix is sent as.

``reviewers`` reads a bare entry as a ``User`` for the same reason: the common
case needs no prefix, and the prefix is what distinguishes the other kind
(``tag:v*``).
"""

_RUN_LIST_FIELDS: Final = "url,databaseId"
"""The two fields the run-URL query asks for; ``url`` is what ends up surfaced."""


def _str_or_none(value: object) -> object:
    """Keep a string field, drop anything else — the tolerance, in one place.

    ``gh`` documents these fields as strings, so a non-string is a payload this
    adapter cannot read. Reading it as *absent* rather than raising is what keeps
    an odd field from escalating into a ``ValidationError``: ``view`` must report
    "no status" for an unreadable payload, and a status of ``None`` is exactly
    that, whereas a raise would reach the operator as a traceback (Requirement
    10.2). This replaces the ``isinstance`` test the hand-parsed ``_str_field``
    performed, in the model where Pydantic can apply it to every such field.
    """
    return value if isinstance(value, str) else None


def _run_id_or_none(value: object) -> object:
    """A run id as ``gh`` sends it — a JSON number — rendered as a ``str``.

    The ``bool`` check comes first and is load-bearing: ``True`` is an ``int`` in
    Python *and* Pydantic's lax mode coerces a ``bool`` into an ``int | str``
    field (``True`` arrives as ``1``), so without this the payload
    ``{"databaseId": true}`` would yield the run id ``"1"`` — a real run number
    invented out of a boolean. The hand-parsed ``_id_field`` guarded the same case
    with an explicit ``isinstance(value, bool)`` test; the guard has to live in a
    ``BeforeValidator`` because the coercion Pydantic would otherwise apply
    happens inside validation, not before it.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) else None


def _objects_only(value: object) -> object:
    """Drop non-object entries of a ``gh --json`` array, leaving the rest to validate.

    ``_json_array`` filtered the same way. A non-array payload is passed through
    untouched so it fails validation — ``gh run list`` answering with something
    that is not a list of runs is a payload this adapter reports as unreadable,
    not one it reinterprets.
    """
    return (
        [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else value
    )


GhString = Annotated[str | None, BeforeValidator(_str_or_none)]
"""A string field of a ``gh --json`` payload: present as a ``str``, or ``None``."""

GhRunId = Annotated[str | None, BeforeValidator(_run_id_or_none)]
"""``databaseId`` as carried here: a ``str``, never coerced out of a ``bool``."""


class _GhRunView(BaseModel):
    """The ``gh run view --json status,conclusion,url`` payload.

    Private to this module rather than in ``core.models`` because it is the *wire
    shape of ``gh``*, consumed entirely inside this adapter and reaching no public
    signature: ``view`` returns the domain ``RunStatus``. ``RunRef`` / ``RunStatus``
    live in ``core.models`` only because ``Result`` carries them; putting a ``gh``
    payload model beside them would widen the shared vocabulary with a type
    nothing outside this file can use.

    Every field defaults to ``None`` and tolerates a non-string, so a *partial*
    payload validates and reports what it actually carried. The failure that
    remains — and the only one — is a payload that is not a JSON object at all,
    which ``view`` reports as unreadable.
    """

    model_config = ConfigDict(extra="ignore")

    status: GhString = None
    conclusion: GhString = None
    url: GhString = None


class _GhRunListEntry(BaseModel):
    """One run of the ``gh run list --json url,databaseId`` array."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    url: GhString = None
    run_id: GhRunId = Field(default=None, alias="databaseId")


class _GhRunList(RootModel[list[_GhRunListEntry]]):
    """The ``gh run list --json`` array, newest run first."""

    root: Annotated[list[_GhRunListEntry], BeforeValidator(_objects_only)] = Field(
        default_factory=list
    )


def _viewed_run(stdout: str) -> _GhRunView | None:
    """Validate ``gh run view``'s payload, or ``None`` when it cannot be read.

    ``model_validate_json`` is the ``model_validate`` of a JSON string, which is
    what the boundary actually hands over: it makes the two ways this read can
    fail — a body that does not parse, and a body that parses into something that
    is not a run object — one ``ValidationError`` caught in one place, instead of
    a parse step and a shape step that could disagree about which is tolerated.

    The argument is ``CommandResult.stdout`` — the stream ``gh`` writes JSON to —
    and **not** the stdout+stderr join. A warning line on stderr is therefore no
    longer part of what is validated: it used to make a well-formed payload
    unparseable, which ``view`` reported as an unreadable status and
    ``presentation.follow_run`` folded in as a *failed* run, so a warning could
    fail a passing deploy. A body that still does not parse is reported as
    unreadable rather than guessed at, and the ``ValidationError`` is **caught**,
    never raised at the caller: an unreadable status has to arrive as
    ``ok=False``, not as a traceback (Requirement 10.2).
    """
    try:
        return _GhRunView.model_validate_json(stdout)
    except ValidationError:
        return None


def _listed_runs(stdout: str) -> list[_GhRunListEntry]:
    """Validate ``gh run list``'s payload, or an empty list when it cannot be read.

    Reads ``CommandResult.stdout`` for the same reason ``_viewed_run`` does: a
    warning ``gh`` wrote to stderr is not part of the JSON array it printed.

    An empty list is what the caller already treats as "the run could not be
    located", so a non-array, an empty array and an unparseable body all reach
    the same tolerant outcome: a ``RunRef`` naming only the workflow, still a
    successful dispatch.
    """
    try:
        return _GhRunList.model_validate_json(stdout).root
    except ValidationError:
        return []


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
        workflow's typed inputs (Requirement 8.3). Returns a ``CommandResult``
        rather than a bare ``RunRef`` because that is the one value every
        executor call returns and because a dispatch can fail — the run reference
        travels inside it, in ``CommandResult.run``, alongside the display-only
        ``run_url`` the dispatcher surfaces (Requirement 10.6).
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
        allowed_refs: list[str] | None = None,
    ) -> CommandResult:
        """Create or update a GitHub Environment, e.g. ``prod`` with reviewers.

        ``gh api repos/{owner}/{repo}/environments/{name}``. Required reviewers
        are what makes the prod gate a *platform* control rather than application
        code (Requirement 6.3).

        ``allowed_refs`` sets the deployment branch/tag policy — entries spelled
        ``branch:main`` / ``tag:v*``, as ``reviewers`` entries are ``<Type>:<id>``.
        For ``prod`` that is ``tag:v*`` + ``branch:main``, the boundary that
        closes arbitrary-ref dispatch (Requirement 6.5). ``None`` leaves any
        existing policy untouched, exactly as an absent ``reviewers`` leaves the
        reviewers untouched.
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

        A failed dispatch is returned as-is with no run. A successful one carries
        the ``RunRef`` in ``run`` and its URL in ``run_url``, or — when the
        follow-up query could not find the run — still a success carrying a
        ``RunRef`` that names only the workflow, saying the URL was unavailable
        rather than reporting a started deploy as a failure. The reference is
        returned even then, because ``gh run watch`` can still follow "the most
        recent run of this workflow" (see ``_run_selector``), which is the whole
        of what is left to look at.
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
                run=run,
            )
        return CommandResult(
            ok=True,
            output=f"{dispatched.output.rstrip()}\n{run.url}".lstrip(),
            run_url=run.url,
            run=run,
        )

    # -- run status ---------------------------------------------------------

    def watch(self, run: RunRef) -> CommandResult:
        """Follow the run until it finishes; the run's own verdict is the result."""
        return self._run([GH, "run", "watch", *_run_selector(run)])

    def view(self, run: RunRef) -> RunStatus:
        """Report ``run``'s status and conclusion without waiting for it.

        The payload is read from ``stdout`` alone, so a warning ``gh`` wrote to
        stderr does not make a well-formed status unreadable. A run whose JSON
        genuinely could not be read is reported as ``ok=False`` with ``gh``'s own
        output — **both** streams, so the warning the operator needs to see is
        still there — and no status, rather than as a run with a guessed one: an
        unreadable status is not a passing status.
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
        fields = _viewed_run(viewed.stdout)
        if fields is None:
            return RunStatus(run=run, output=viewed.output, ok=False)
        return RunStatus(
            run=RunRef(
                workflow=run.workflow,
                run_id=run.run_id,
                url=fields.url or run.url,
            ),
            status=fields.status,
            conclusion=fields.conclusion,
            output=viewed.output,
            ok=True,
        )

    # -- repository / environment administration (bootstrap only) -----------

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
        allowed_refs: list[str] | None = None,
    ) -> CommandResult:
        """Create or update the ``name`` Environment and the protection it carries.

        ``gh api`` substitutes ``{owner}`` / ``{repo}`` from the repository the
        command runs in, which is the repository root ``_process`` always uses —
        so the owner and repo are never hard-coded or derived here.

        Reviewers and the branch/tag policy's mode, when given, are sent as one
        JSON body on **stdin** (``--input -``) rather than as flags: both are
        nested structures the GitHub API wants as JSON, and building them on stdin
        avoids composing that structure out of repeated arguments. Each reviewer
        is ``<Type>:<id>``, e.g. ``User:1234`` or ``Team:56``; a bare id is read
        as a ``User``.

        The admitted ref patterns then follow as one POST each, because GitHub
        holds them as separate resources and rejects them until the environment's
        mode enables custom policies — see this module's docstring for that
        ordering and for why a repeat run adds no duplicate. A failed policy call
        is returned as-is, with ``gh``'s own output (Requirement 10.2): the
        environment was already updated, and describing that as untouched would be
        a worse report than the tool's own.
        """
        argv = [GH, "api", "--method", "PUT", f"repos/{{owner}}/{{repo}}/environments/{name}"]
        body = _environment_body(reviewers, allowed_refs)
        updated = (
            self._run(argv) if body is None else self._run([*argv, "--input", "-"], stdin=body)
        )
        if not updated.ok or not allowed_refs:
            return updated
        return self._add_branch_policies(name, allowed_refs, updated)

    def _add_branch_policies(
        self,
        name: str,
        allowed_refs: list[str],
        updated: CommandResult,
    ) -> CommandResult:
        """POST one ``deployment-branch-policies`` entry per pattern, in order.

        Stops at the first failure and returns it. Each pattern's body goes on
        stdin for the same reason the environment's does; the outputs are
        concatenated so the operator sees every call the step made, as
        ``run_workflow`` does with its dispatch and its follow-up query.
        """
        outputs = [updated.output.rstrip()]
        for pattern in allowed_refs:
            created = self._run(
                [
                    GH,
                    "api",
                    "--method",
                    "POST",
                    f"repos/{{owner}}/{{repo}}/environments/{name}/deployment-branch-policies",
                    "--input",
                    "-",
                ],
                stdin=_branch_policy_body(pattern),
            )
            if not created.ok:
                return created
            outputs.append(created.output.rstrip())
        return CommandResult(ok=True, output="\n".join(line for line in outputs if line))

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
                    stage=str_param(step, STAGE_PARAM),
                    version=optional_str_param(step, VERSION_PARAM),
                    workflow=str_param(step, WORKFLOW_PARAM),
                )
            case Op.GITHUB_ENVIRONMENT_SET:
                return self.set_environment(
                    name=str_param(step, ENVIRONMENT_PARAM),
                    reviewers=_list_param(step, REVIEWERS_PARAM),
                    allowed_refs=_list_param(step, ALLOWED_REFS_PARAM),
                )
            case Op.GITHUB_SECRET_SET:
                name = str_param(step, NAME_PARAM)
                return self.set_environment_secret(
                    environment=str_param(step, ENVIRONMENT_PARAM),
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
        runs = _listed_runs(listed.stdout)
        if not runs:
            return RunRef(workflow=workflow)
        newest = runs[0]
        return RunRef(workflow=workflow, run_id=newest.run_id, url=newest.url)


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


def _list_param(step: PlanStep, name: str) -> list[str] | None:
    """Read an optional list param — ``reviewers``, ``allowed_refs`` — out of a step.

    ``None`` when the planned step named none, which for both of the Environment's
    lists means *leave that protection as it is*: the Environment is created or
    updated without touching the reviewers or the branch/tag policy, rather than
    having either cleared by an empty list. A present-but-wrongly-typed value is a
    planning fault and is named as such, in keeping with ``str_param``; both lists
    matter too much to be quietly dropped, since they *are* the prod gate
    (Requirements 6.4, 6.5).
    """
    if name not in step.params:
        return None
    value = step.params[name]
    if not isinstance(value, list):
        raise UsageError(
            field=f"params.{name}",
            value=value,
            problem=f"{step.op.value} needs a list {name!r} param",
        )
    return value


def _environment_body(
    reviewers: list[str] | None,
    allowed_refs: list[str] | None,
) -> str | None:
    """The JSON body for the Environment PUT, or ``None`` when there is nothing to send.

    Each list contributes its key only when it was given, so an absent one leaves
    that protection untouched while a given one is taken literally — an empty
    ``reviewers`` clears the reviewers, and an empty ``allowed_refs`` enables
    custom policies with no pattern, which admits no ref at all. The planner sends
    neither empty list; they mean what they say rather than being second-guessed
    here.

    Reviewer ids stay strings — the API accepts them either way, and parsing them
    into ints would only add a failure mode for a value this adapter does not
    interpret. The policy mode carries ``protected_branches`` too because the API
    requires both keys and refuses them equal.
    """
    body: dict[str, object] = {}
    if reviewers is not None:
        body["reviewers"] = [_reviewer_entry(reviewer) for reviewer in reviewers]
    if allowed_refs is not None:
        body["deployment_branch_policy"] = {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
    return json.dumps(body) if body else None


def _reviewer_entry(reviewer: str) -> dict[str, str]:
    """One ``<Type>:<id>`` reviewer as the API's ``{type, id}``; a bare id is a ``User``."""
    kind, _, identifier = reviewer.rpartition(":")
    return {"type": kind or "User", "id": identifier}


def _branch_policy_body(pattern: str) -> str:
    """The JSON body creating one admitted ref pattern.

    ``tag:v*`` becomes ``{"name": "v*", "type": "tag"}``; an unprefixed pattern is
    a branch. The prefix is read exactly as ``reviewers`` reads ``<Type>:<id>``,
    and it is not validated here for the same reason the reviewer type is not: a
    misspelled kind is GitHub's 422 to describe, and inventing a second
    vocabulary for it would only hide which value the API rejected.
    """
    kind, _, name = pattern.rpartition(":")
    return json.dumps({"name": name, "type": kind or BRANCH_REF_TYPE})


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
