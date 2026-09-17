"""Adapter over ``git``: the release path that fires the tag-triggered pipeline.

A release is the one control-plane action whose failure mode is a *published*
mistake: once ``vX.Y.Z`` is on the origin, the tag-triggered ``deploy.yml`` run is
already moving and ``ApiVersion`` is already decided (ADR-0037). So this adapter
is built around two guarantees rather than around ``git`` convenience:

- **Nothing is created before the preconditions are checked.** ``tag()`` runs the
  four release preconditions — clean tree, on ``main``, tag absent locally, tag
  absent on the origin — and returns a failure naming the specific one that
  blocked, without having invoked ``git tag`` (Requirements 7.1, 7.3). The checks
  themselves are read-only queries (``status``, ``branch``, ``tag --list``,
  ``ls-remote``), so a blocked release leaves the working tree, the current
  branch and the existing tags exactly as they were.
- **A failed push leaves no dangling local tag.** The planner emits ``git.tag``
  and ``git.push`` as two steps, so a push that fails after a successful tag
  would otherwise strand ``vX.Y.Z`` locally — the next attempt would then fail
  its own "tag absent locally" precondition on debris from the first. ``push()``
  deletes the local tag it could not publish and says so (Requirement 7.3's
  intent: a failed release leaves git as it was).

Two types live here, mirroring ``executors/sam.py``:

- ``GitExecutor`` — the Protocol the design documents and ``Dispatcher``
  type-hints against. It is the interface; it prescribes no invocation.
- ``Git`` — the concrete adapter that satisfies it by shelling out to ``git``
  through the shared ``run_command`` runner. The runner is injected, so every
  argument list an op produces — including the read-only precondition queries —
  can be asserted without touching a real repository's git state.

``run_step`` switches on ``step.op`` and reads typed values out of
``step.params``; it never parses ``step.command``, which is the display rendering
only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, assert_never

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import CommandRunner, run_command
from bdo_deploy.core.executors.base import StepExecutor
from bdo_deploy.core.models import CommandResult, Op, PlanStep

GIT: Final = "git"
"""The sanctioned executable; every invocation below starts with it."""

RELEASE_BASE_BRANCH: Final = "main"
"""The only branch a release may be cut from (Requirement 7.1)."""

GIT_REMOTE: Final = "origin"
"""The remote a release tag is published to (Requirement 7.4)."""

# ``core.dispatch`` declares the same two values for the planner and imports this
# module, so importing them from there would be a cycle. They are *defaults* here
# rather than a second source of truth: a planned step carries ``base_branch`` and
# ``remote`` in ``params``, and ``run_step`` passes those through, so the
# constants below are only reached when a caller invokes a domain method
# directly. Relocating the planner's copies would mean editing ``dispatch.py``,
# which this task does not own.

VERSION_PARAM: Final = "version"
BASE_BRANCH_PARAM: Final = "base_branch"
REMOTE_PARAM: Final = "remote"


class GitExecutor(StepExecutor, Protocol):
    """Protocol for the ``git`` adapter — the interface, not an invocation."""

    def release_preconditions(self, version: str) -> list[str]:
        """The blocking issues for releasing ``version``; empty means clear.

        Each string names the specific precondition that failed (Requirement
        7.3). Read-only: nothing about the repository is changed by asking.
        """
        ...

    def tag_and_push(self, version: str) -> CommandResult:
        """Verify the preconditions, create ``vX.Y.Z``, then push it to the origin.

        Creates nothing when a precondition blocks, and deletes the local tag
        again if the push fails (Requirements 7.1, 7.3, 7.4).
        """
        ...


class Git:
    """``GitExecutor`` implemented by shelling out to the ``git`` CLI.

    Satisfies the Protocol **structurally** rather than by inheritance — the
    ``TYPE_CHECKING`` binding at the end of this module is what makes mypy prove
    it — so ``Dispatcher``'s ``GitExecutor``-typed parameter accepts it without
    ``dispatch.py`` knowing this class exists.
    """

    def __init__(self, *, runner: CommandRunner = run_command) -> None:
        self._run = runner

    # -- typed domain methods ----------------------------------------------

    def release_preconditions(
        self,
        version: str,
        *,
        base_branch: str = RELEASE_BASE_BRANCH,
        remote: str = GIT_REMOTE,
    ) -> list[str]:
        """Return every blocking issue for releasing ``version``, in check order.

        All four checks run even once one has failed, so an operator sees the
        whole picture in one report instead of fixing them one round-trip at a
        time. Every query is read-only; a query that could not be answered is
        itself blocking — an unverifiable precondition is not a passed one.
        """
        return [
            issue
            for issue in (
                self._check_tree_clean(),
                self._check_on_base_branch(base_branch),
                self._check_tag_absent_locally(version),
                self._check_tag_absent_on_remote(version, remote),
            )
            if issue is not None
        ]

    def tag(
        self,
        version: str,
        *,
        base_branch: str = RELEASE_BASE_BRANCH,
        remote: str = GIT_REMOTE,
    ) -> CommandResult:
        """Create the ``version`` tag, but only if the preconditions are clear.

        The precondition check happens **here**, before ``git tag`` is invoked,
        because this is the first step that would mutate anything: a blocked
        release reports which precondition failed and has created no tag
        (Requirements 7.1, 7.3).
        """
        issues = self.release_preconditions(version, base_branch=base_branch, remote=remote)
        if issues:
            return CommandResult(ok=False, output=_blocked_message(version, issues))
        return self._run([GIT, "tag", version])

    def push(self, version: str, *, remote: str = GIT_REMOTE) -> CommandResult:
        """Push the ``version`` tag to ``remote``, cleaning up if it fails.

        On failure the local tag is deleted, so the repository is left as it was
        before the release attempt and a retry is not blocked by its own debris.
        The report says both that the push failed and what became of the tag.
        """
        pushed = self._run([GIT, "push", remote, version])
        if pushed.ok:
            return pushed
        return CommandResult(
            ok=False,
            output=(
                f"pushing {version} to {remote} failed; the local tag was not published\n"
                f"{pushed.output.rstrip()}\n"
                f"{self._delete_local_tag(version)}"
            ),
        )

    def tag_and_push(
        self,
        version: str,
        *,
        base_branch: str = RELEASE_BASE_BRANCH,
        remote: str = GIT_REMOTE,
    ) -> CommandResult:
        """Create ``version`` and publish it, so the tag-triggered pipeline runs.

        Stops at the first failure: a blocked precondition means nothing is
        created, and a failed push means the tag this call created is deleted
        again (Requirement 7.4, and 7.3's "leaves git as it was").
        """
        tagged = self.tag(version, base_branch=base_branch, remote=remote)
        if not tagged.ok:
            return tagged
        pushed = self.push(version, remote=remote)
        return CommandResult(
            ok=pushed.ok,
            output="\n".join(text for text in (tagged.output, pushed.output) if text),
        )

    # -- the StepExecutor seam ---------------------------------------------

    def run_step(self, step: PlanStep) -> CommandResult:
        """Dispatch ``step``'s op onto the domain method that performs it.

        Switches on ``step.op`` and reads typed values out of ``step.params``; it
        **never parses ``step.command``**, which is the display rendering only.
        The match covers every ``Op`` member — the git ops onto a method, every
        other op onto a named refusal — so ``assert_never`` makes adding an op
        without handling it a type error rather than a silent fallthrough.
        """
        match step.op:
            case Op.GIT_TAG:
                return self.tag(
                    _str_param(step, VERSION_PARAM),
                    base_branch=_str_param(step, BASE_BRANCH_PARAM),
                )
            case Op.GIT_PUSH:
                return self.push(
                    _str_param(step, VERSION_PARAM),
                    remote=_str_param(step, REMOTE_PARAM),
                )
            case (
                Op.SAM_BUILD
                | Op.SAM_DEPLOY
                | Op.SAM_SYNC
                | Op.SAM_PIPELINE_BOOTSTRAP
                | Op.GITHUB_RUN_WORKFLOW
                | Op.GITHUB_ENVIRONMENT_SET
                | Op.GITHUB_SECRET_SET
                | Op.CONFIG_SHOW
                | Op.SSM_PUT
                | Op.SAMCONFIG_PR
            ):
                raise UsageError(
                    field="op",
                    value=step.op.value,
                    problem=f"{step.op.value!r} is not a git operation",
                    hint=f"route it to the {step.executor!r} executor instead",
                )
            case _:  # pragma: no cover - exhaustive over Op
                assert_never(step.op)

    # -- the four preconditions, one read-only query each -------------------

    def _check_tree_clean(self) -> str | None:
        """Blocking unless ``git status --porcelain`` reports nothing at all.

        ``--porcelain`` lists untracked files too, and deliberately so: a release
        is cut from what is committed, and a tree with stray files is not the
        tree the operator thinks they are tagging.
        """
        status = self._run([GIT, "status", "--porcelain"])
        if not status.ok:
            return _unverifiable("the working tree is clean", status.output)
        if status.output.strip():
            return (
                "the working tree is not clean; commit or stash the changes first:\n"
                f"{status.output.rstrip()}"
            )
        return None

    def _check_on_base_branch(self, base_branch: str) -> str | None:
        """Blocking unless the current branch is exactly ``base_branch``.

        ``git branch --show-current`` prints nothing on a detached HEAD, which is
        reported as such rather than as a mismatch against an empty name.
        """
        branch = self._run([GIT, "branch", "--show-current"])
        if not branch.ok:
            return _unverifiable(f"the current branch is {base_branch}", branch.output)
        current = branch.output.strip()
        if not current:
            return (
                f"HEAD is detached, so the release is not on {base_branch}; "
                f"check out {base_branch} first"
            )
        if current != base_branch:
            return (
                f"the current branch is {current!r}, but a release is cut from "
                f"{base_branch!r}; check out {base_branch} first"
            )
        return None

    def _check_tag_absent_locally(self, version: str) -> str | None:
        """Blocking if ``version`` already names a local tag.

        ``git tag --list <version>`` is an exact-name query that exits ``0`` and
        prints nothing when the tag is absent, so the absence is read from the
        output rather than from an exit status that never signals it.
        """
        listed = self._run([GIT, "tag", "--list", version])
        if not listed.ok:
            return _unverifiable(f"the {version} tag does not exist locally", listed.output)
        if listed.output.strip():
            return (
                f"the {version} tag already exists locally; releases are immutable, "
                "so pick the next version"
            )
        return None

    def _check_tag_absent_on_remote(self, version: str, remote: str) -> str | None:
        """Blocking if ``version`` already exists on ``remote``.

        Checked separately from the local tag because the origin is what the
        tag-triggered pipeline watches: a version already published there has
        already been released, whether or not this checkout knows about it.
        """
        ref = f"refs/tags/{version}"
        remote_tags = self._run([GIT, "ls-remote", "--tags", remote, ref])
        if not remote_tags.ok:
            return _unverifiable(
                f"the {version} tag does not exist on {remote}", remote_tags.output
            )
        if remote_tags.output.strip():
            return (
                f"the {version} tag already exists on {remote}; that version has already "
                "been released, so pick the next one"
            )
        return None

    def _delete_local_tag(self, version: str) -> str:
        """Delete the unpublished local tag, reporting either outcome.

        A cleanup that itself fails is reported rather than swallowed: the
        operator has to know a stray local tag is there, since it would block
        their next attempt's "tag absent locally" precondition.
        """
        deleted = self._run([GIT, "tag", "--delete", version])
        if deleted.ok:
            return f"the local {version} tag has been deleted; git is as it was before the release"
        return (
            f"the local {version} tag could not be deleted either, so it is still present; "
            f"remove it with `git tag --delete {version}` before retrying\n"
            f"{deleted.output.rstrip()}"
        )


def _blocked_message(version: str, issues: list[str]) -> str:
    """Report every failed precondition, naming what was *not* done."""
    listed = "\n".join(f"- {issue}" for issue in issues)
    return (
        f"the release preconditions for {version} are not met, so no tag was created "
        f"and nothing was pushed:\n{listed}"
    )


def _unverifiable(precondition: str, output: str) -> str:
    """Treat an unanswerable query as blocking, quoting what ``git`` said.

    A precondition that could not be checked is not a precondition that passed —
    reporting it as clear would let a release proceed on an assumption.
    """
    return f"could not verify that {precondition}: {output.rstrip()}"


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


if TYPE_CHECKING:  # pragma: no cover - a type-check-time assertion, not runtime code
    # ``Dispatcher`` type-hints against the Protocol, so the concrete adapter has
    # to be structurally compatible with it. Binding one to the other here makes
    # mypy fail this module if a method's name or signature ever drifts.
    _satisfies_protocol: GitExecutor = Git()


__all__ = ["GIT", "GIT_REMOTE", "RELEASE_BASE_BRANCH", "Git", "GitExecutor"]
