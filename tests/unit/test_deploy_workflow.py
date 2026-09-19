"""Structural tests for ``deploy.yml``'s prod ref guard and the dispatched ref.

The guard is defence in depth behind the prod Environment's deployment branch/tag
policy (Requirement 6.6 / ADR-0040 decision 4), so what has to be true of it is
structural: it lives in a job referencing **no** Environment that ``deploy``
depends on via ``needs:`` — the boundary that lets it fail before the approval
wait rather than after it — it admits exactly what the Environment policy admits,
and it leaves a dev deploy alone.

Two things are read rather than restated. The admitted refs come from
``PROD_ALLOWED_REFS`` — the same constant the bootstrap planner sends to the
Environment — so the two layers cannot drift apart about what a prod deploy may
run from. And the guard's decision is exercised by **running its own shell body**
over accepted and rejected refs instead of matching its text: a test that grepped
for a literal would still pass if the condition were inverted.

``bash`` is the only subprocess here; no ``gh`` / ``sam`` / ``git`` / AWS call is
made, and the dispatch half runs through the recording runner.
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs the workflow's own shell body, nothing else
import sys
from collections.abc import Callable
from typing import Final

import pytest

from bdo_deploy.core.dispatch import DEPLOY_WORKFLOW, PROD_ALLOWED_REFS
from bdo_deploy.core.executors.github import GH, GitHubCli
from bdo_deploy.core.validation import PROD_STAGE
from tests.unit.test_deploy_executors import FakeRunner
from tests.unit.test_deploy_properties import workflow_document

DEPLOY_JOB: Final = "deploy"

ALLOWED_REFS_VAR: Final = "ALLOWED_REFS"
"""The guard's env key naming the admitted refs; how the step is identified.

Located by the data it carries rather than by its ``name``, which is prose and may
be reworded, and rather than by the job or step it sits in, either of which may be
renamed or reordered.
"""

REF_VAR: Final = "REF"
"""The guard's env key carrying the ref under test, as ``<type>:<name>``."""

CREDENTIAL_ACTION: Final = "aws-actions/configure-aws-credentials"


def _jobs() -> dict[str, dict[str, object]]:
    """Every job in the real workflow file, keyed by job id."""
    jobs = workflow_document(DEPLOY_WORKFLOW)["jobs"]
    assert isinstance(jobs, dict)
    return {str(name): job for name, job in jobs.items() if isinstance(job, dict)}


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    """``job``'s steps, in order."""
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def _index_of(
    predicate: Callable[[dict[str, object]], bool], steps: list[dict[str, object]]
) -> int:
    """The single index in ``steps`` satisfying ``predicate``."""
    found = [index for index, step in enumerate(steps) if predicate(step)]
    assert len(found) == 1, f"expected exactly one matching step, found {found}"
    return found[0]


def _step_env(step: dict[str, object]) -> dict[str, str]:
    env = step.get("env", {})
    assert isinstance(env, dict)
    return {str(key): str(value) for key, value in env.items()}


def _is_guard(step: dict[str, object]) -> bool:
    return ALLOWED_REFS_VAR in _step_env(step)


def _is_credential_step(step: dict[str, object]) -> bool:
    uses = step.get("uses")
    return isinstance(uses, str) and uses.startswith(CREDENTIAL_ACTION)


def _guard_job() -> tuple[str, dict[str, object]]:
    """The job id and body of the one job carrying the guard step."""
    found = [
        (name, job)
        for name, job in _jobs().items()
        if any(_is_guard(step) for step in _steps(job))
    ]
    assert len(found) == 1, f"expected exactly one job to carry the guard, found {found}"
    return found[0]


def _guard() -> dict[str, object]:
    _, job = _guard_job()
    steps = _steps(job)
    return steps[_index_of(_is_guard, steps)]


def _needs(job: dict[str, object]) -> list[str]:
    """``job``'s ``needs:``, normalised — the key accepts a scalar or a list."""
    declared = job.get("needs", [])
    if isinstance(declared, str):
        return [declared]
    assert isinstance(declared, list)
    return [str(one) for one in declared]


def _run_guard(ref: str) -> subprocess.CompletedProcess[str]:
    """Run the guard's own ``run:`` body against ``ref``.

    The body reads only its two env values, so the workflow-expression half
    (``github.ref_type``/``github.ref_name``, asserted separately) is simply
    supplied here as the ref being judged. Nothing else is provided: the body is
    pure pattern matching and must not depend on the wider environment.
    """
    guard = _guard()
    body = guard["run"]
    assert isinstance(body, str)
    return subprocess.run(  # nosec B603 B607 - a literal argv, the body from our own repo
        ["bash", "-c", body],
        env={ALLOWED_REFS_VAR: _step_env(guard)[ALLOWED_REFS_VAR], REF_VAR: ref},
        capture_output=True,
        text=True,
        check=False,
    )


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the guard body is POSIX shell")


class TestTheProdRefGuard:
    """``deploy.yml`` refuses a prod run from a ref prod does not admit.

    **Validates: Requirements 6.6**
    """

    def test_the_guard_lives_outside_the_deploy_job_in_no_environment(self) -> None:
        """The job boundary is the guard's whole reason to work at all.

        A job referencing an Environment with required reviewers waits for approval
        before it starts, so a guard *inside* ``deploy`` could only run after the
        wait. What has to hold structurally: the guard is not in ``deploy``, and its
        own job references no Environment — while ``deploy`` still does, so this is
        a real boundary rather than both jobs being unprotected.
        """
        guard_job_id, guard_job = _guard_job()
        assert guard_job_id != DEPLOY_JOB
        assert "environment" not in guard_job
        assert "environment" in _jobs()[DEPLOY_JOB]

    def test_the_deploy_job_needs_the_guard_job(self) -> None:
        """Without the ``needs:`` edge the two jobs would simply run in parallel.

        The pre-gate only gets to name a disallowed ref *before* the approval wait
        because ``deploy`` waits for it.
        """
        guard_job_id, _ = _guard_job()
        assert guard_job_id in _needs(_jobs()[DEPLOY_JOB])

    def test_the_guard_job_is_never_skipped_so_dev_still_deploys(self) -> None:
        """A ``needs:`` on a *skipped* job skips the dependent job by default.

        So the prod scoping must stay on the step (asserted below) and the job must
        carry no condition of its own: it always runs, no-ops on dev, and succeeds,
        which is what keeps ``deploy``'s ``needs:`` from blocking a dev deploy.
        """
        _, guard_job = _guard_job()
        assert "if" not in guard_job

    def test_the_deploy_job_still_assumes_credentials_after_the_gate(self) -> None:
        """The "leaves AWS untouched" claim now rests on the job graph.

        Once the guard moved out, nothing orders it against
        ``configure-aws-credentials`` by index any more — the credential step lives
        in the gated job, which cannot start until the pre-gate has passed.
        """
        deploy_steps = _steps(_jobs()[DEPLOY_JOB])
        assert not any(_is_guard(step) for step in deploy_steps)
        _index_of(_is_credential_step, deploy_steps)

    def test_the_guard_applies_only_to_prod(self) -> None:
        """A dev deploy from a feature branch stays legitimate (Req 6.5's scoping).

        Only GitHub can evaluate the expression, so the scoping is asserted on the
        condition itself: it must test the job's derived stage against prod, which
        is what stops the guard failing a dev dispatch from any branch.
        """
        condition = _guard()["if"]
        assert isinstance(condition, str)
        assert "env.STAGE" in condition
        assert f"'{PROD_STAGE}'" in condition
        # The guard now sits in its own job, so `STAGE` must be workflow-level env
        # rather than the deploy job's, or the condition would read as empty here.
        workflow_env = workflow_document(DEPLOY_WORKFLOW)["env"]
        assert isinstance(workflow_env, dict)
        assert "STAGE" in workflow_env

    def test_the_guard_admits_exactly_what_the_environment_policy_admits(self) -> None:
        """One list, two layers: the guard's patterns are ``PROD_ALLOWED_REFS``.

        Read from the constant the bootstrap planner sends to the Environment, so
        editing either side alone fails here rather than leaving the in-workflow
        guard and the platform-enforced boundary quietly disagreeing about which
        refs may deploy prod.
        """
        patterns = _step_env(_guard())[ALLOWED_REFS_VAR].split()
        assert set(patterns) == set(PROD_ALLOWED_REFS)

    def test_the_guard_judges_the_ref_of_either_trigger(self) -> None:
        """``REF`` must be built from the two fields both triggers populate.

        A tag push reports ``ref_type: tag`` with the tag in ``ref_name``; a
        ``workflow_dispatch`` reports whichever ref was selected. Reading the type
        as well as the name is what keeps a *branch* called ``v1.2.3`` from
        satisfying the ``tag:v*`` pattern.
        """
        ref = _step_env(_guard())[REF_VAR]
        assert "github.ref_type" in ref
        assert "github.ref_name" in ref

    @pytest.mark.parametrize("ref", ["tag:v1.2.3", "tag:v10.0.0-rc1", "branch:main"])
    def test_an_admitted_ref_passes(self, ref: str) -> None:
        """The ``v*``-tag and ``main`` cases the policy names, run through the body."""
        assert _run_guard(ref).returncode == 0

    @pytest.mark.parametrize(
        "ref",
        [
            "branch:feat/deploy-control-plane-impl",  # the dispatch hole Req 6.6 closes
            "branch:mainline",  # `main` is exact, not a prefix
            "branch:v1.2.3",  # the v* pattern is a TAG pattern
            "tag:release-1.2.3",  # a tag outside v*
        ],
    )
    def test_a_ref_prod_does_not_admit_fails_and_is_named(self, ref: str) -> None:
        """A rejection must fail the run *and* say which ref was rejected.

        Naming the ref is the guard's entire reason to exist — the Environment
        policy already refuses the deploy, it just does not say what it refused.
        """
        completed = _run_guard(ref)
        assert completed.returncode != 0
        assert ref in completed.stdout + completed.stderr


class TestTheDispatchedRef:
    """The control plane dispatches no explicit ref.

    **Validates: Requirements 6.7**
    """

    def test_run_workflow_passes_no_ref_so_the_default_branch_runs(self) -> None:
        """``gh workflow run`` without ``--ref`` runs the repository default branch.

        Asserted on the argv the real ``GitHubCli`` builds, not on a reading of the
        source: adding a ``--ref`` anywhere on the dispatch path fails this.
        """
        runner = FakeRunner()
        GitHubCli(runner=runner).run_workflow(stage=PROD_STAGE, version="v1.2.3")
        dispatch = runner.argvs[0]
        assert dispatch[:4] == [GH, "workflow", "run", DEPLOY_WORKFLOW]
        # Both spellings `gh workflow run` accepts, including `--ref=<ref>`.
        assert "-r" not in runner.flat_argv
        assert not any(argument.startswith("--ref") for argument in runner.flat_argv)
