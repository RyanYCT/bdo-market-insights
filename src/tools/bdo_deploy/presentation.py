"""The one rendering vocabulary both front-ends share.

A ``Plan`` and a ``Result`` are rendered for a human in exactly one place, here,
and the two front-ends differ only in *where* they put the lines: the CLI prints
them to stdout or stderr depending on ``--json``, the TUI writes them into a
widget. Nothing about *what* a plan or a result looks like is decided twice.

That matters more than it sounds. Requirement 1.5 makes the two front-ends
behaviourally equivalent, and the ``Plan`` they route through the shared
``Dispatcher`` is already identical by construction. If each front-end then
described that identical plan in its own words, the operator would be looking at
two different accounts of the same pending mutation — and the confirmation step
the TUI exists to provide (Requirement 1.3) would be confirming a *description*
that no CLI dry run could be checked against. One vocabulary keeps "the same
intent behaves the same way" true of what a person actually reads.

``follow_run`` lives here because following a dispatched CI run *is* presentation
— it happens after ``execute()`` has returned, reaches the ``GitHubExecutor``
directly rather than through a plan, and produces nothing but a verdict to
report. Sharing it is what stops CLI mode and TUI mode from growing two different
notions of "did the run pass".

``failed_result`` lives here for the same reason: turning a raised failure into a
``Result`` is how both front-ends honour "no Python traceback reaches the
operator" (Requirement 10.2), and the exit code it carries comes from
``core.errors.exit_code_for`` so neither front-end can invent a fifth outcome.

These functions render; they never decide. No routing, validation or capability
logic appears in this module — that all lives in the core, which is what keeps
both front-ends thin (Requirement 1.4).
"""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError as PydanticValidationError

from bdo_deploy.core.errors import ControlPlaneError, ExecutorFailed, exit_code_for
from bdo_deploy.core.executors.github import GitHubExecutor
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import Capability, Plan, Result, RunStatus

SUCCESS_CONCLUSION: Final = "success"
"""The one ``gh run view`` conclusion that means the run passed.

GitHub's own string, compared rather than translated: the run is authoritative
(Requirement 10.6), so mapping its vocabulary onto a local one would create a
second verdict that can disagree with it.
"""


def plan_lines(plan: Plan) -> list[str]:
    """Render a ``Plan`` as the lines that show what will run and what changes.

    Returned as a list rather than printed so the caller owns the stream (or the
    widget): the CLI's ``--json`` mode has to divert these to stderr, and the TUI
    has no stream at all.

    ``PlanStep.command`` is shown verbatim because it is the step's own display
    rendering, produced once by ``plan()`` alongside the structured intent the
    executor acts on — so the line the operator confirms cannot drift from the
    call that is made. Secret-shaped and operational values are already absent
    from it (Requirement 3.7): they travel as ``SecretStr`` in ``params`` and were
    never rendered into ``command``, so no masking is needed — or possible —
    here.
    """
    lines = [f"plan: {plan.capability.value} -> {plan.target.value}"]
    for position, step in enumerate(plan.steps, start=1):
        lines.append(f"  {position}. {step.description}")
        lines.append(f"     $ {step.command}")
    lines.extend(f"  * {effect}" for effect in plan.effects)
    return lines


def result_lines(result: Result, *, confirmation_hint: str | None = None) -> list[str]:
    """Render a ``Result`` as the lines a human reads about the outcome.

    ``confirmation_hint`` is the one line that legitimately differs between the
    front-ends, because ``Result.plan`` is set only when a mutating plan was
    refused and *how you proceed from there* is mode-specific: the CLI tells the
    caller to re-invoke with ``--yes``, while the TUI has already shown the plan
    on a confirmation screen and has nothing to add. It is a parameter rather
    than a branch on the mode so this module needs no notion of which front-end
    called it.

    ``raw_output`` is appended as one element, unreformatted, so the failing
    executor's own output reaches the operator verbatim (Requirement 10.2).
    """
    lines = [f"{'ok' if result.ok else 'failed'}: {result.summary}"]
    lines.extend(
        f"  {change.source} {change.key}: {change.before!r} -> {change.after!r}"
        for change in result.changes
    )
    if result.run_url is not None:
        lines.append(f"  run: {result.run_url}")
    if result.plan is not None and confirmation_hint is not None:
        lines.append(f"  {confirmation_hint}")
    if result.raw_output:
        lines.append(result.raw_output)
    return lines


def follow_run(github: GitHubExecutor, result: Result) -> Result:
    """Follow ``result.run`` to completion and fold the run's verdict into it.

    Blocks in ``gh run watch`` until the dispatched run finishes, then reads its
    conclusion with ``gh run view`` and returns a ``Result`` whose ``ok`` and
    ``exit_code`` are **the run's**, not the dispatch's. Requirements 7.6 and 10.6
    make the CI run the authoritative pass or fail: the wizard only pressed the
    button, so a green dispatch of a run that then failed has to be reported as a
    failure. A ``target=CI`` plan's dispatch is its last step, so there is no local
    outcome left for the run's verdict to overwrite.

    Both front-ends call this one function, which is why it lives here rather than
    in either of them: CLI mode and TUI mode then cannot grow two different
    notions of "did the run pass". It is a **no-op when ``result.run is None``** —
    every local command, and every failure that never reached a dispatch — so a
    caller needs no notion of which commands produce a run.

    The run's URL is filled in from the view when the dispatch could not resolve
    one, since by then the run has certainly been located.

    Following is the front-ends' decision, not this function's: it is default-on
    for human output and gated on ``--watch`` under ``--json``, because a blocking
    watch cannot coexist with "exactly one serialized ``Result`` is the sole
    content of stdout" (Requirement 1.2). Both callers surface the URL either way.
    """
    if result.run is None:
        return result
    watched = github.watch(result.run)
    status = github.view(result.run)
    ok = _run_passed(status, watched_ok=watched.ok)
    followed = status.run if status.ok else result.run
    return result.model_copy(
        update={
            "ok": ok,
            "exit_code": ExitCode.SUCCESS if ok else ExitCode.EXECUTOR_FAILED,
            "summary": f"{result.summary}; the run {_verdict(status)}",
            "run": followed,
            "run_url": followed.url if followed.url is not None else result.run_url,
            "raw_output": "\n".join(
                text for text in (result.raw_output, watched.output, status.output) if text
            )
            or None,
        }
    )


def _run_passed(status: RunStatus, *, watched_ok: bool) -> bool:
    """Whether the followed run passed, preferring its own reported conclusion.

    A conclusion is GitHub's verdict on the run and is used as-is. When none could
    be read — an unreadable ``gh run view``, or a run still without a conclusion —
    the fallback is whether ``gh run watch`` itself exited cleanly, which is the
    only other thing that observed the run. An unreadable status is *not* treated
    as a pass.
    """
    if status.ok and status.conclusion is not None:
        return status.conclusion == SUCCESS_CONCLUSION
    return status.ok and watched_ok


def _verdict(status: RunStatus) -> str:
    """One clause describing how the followed run ended, in GitHub's own words."""
    if status.ok and status.conclusion is not None:
        return f"concluded {status.conclusion}"
    if status.ok and status.status is not None:
        return f"reported no conclusion (status {status.status})"
    return "could not be read, so it is not reported as a pass"


def failed_result(capability: Capability, exc: BaseException) -> Result:
    """Render any raised failure as a ``Result``, never as a traceback.

    A ``ControlPlaneError`` already names the offending field or step, so its own
    summary is used verbatim. A Pydantic ``ValidationError`` — a malformed
    ``Command`` — is reduced to its messages for the same reason: an operator
    needs the field that is wrong, not the frames that discovered it.

    Shared by both front-ends so a given failure reads the same way and carries
    the same exit code whichever mode provoked it.
    """
    if isinstance(exc, ControlPlaneError):
        summary = exc.summary
    elif isinstance(exc, PydanticValidationError):
        summary = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
    else:
        summary = str(exc) or type(exc).__name__
    return Result(
        capability=capability,
        ok=False,
        exit_code=exit_code_for(exc),
        summary=summary,
        raw_output=exc.output if isinstance(exc, ExecutorFailed) else None,
    )


__all__ = ["failed_result", "follow_run", "plan_lines", "result_lines"]
