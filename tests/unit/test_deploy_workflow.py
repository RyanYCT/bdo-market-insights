"""Structural tests for ``deploy.yml``'s prod ref guard and the dispatched ref.

The guard is defence in depth behind the prod Environment's deployment branch/tag
policy (Requirement 6.6 / ADR-0040 decision 4), so what has to be true of it is
structural: it exists, it runs *before* any AWS credential is assumed, it admits
exactly what the Environment policy admits, and it leaves a dev deploy alone.

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
be reworded, and rather than by an index, which would move whenever a step is
added above it.
"""

REF_VAR: Final = "REF"
"""The guard's env key carrying the ref under test, as ``<type>:<name>``."""

CREDENTIAL_ACTION: Final = "aws-actions/configure-aws-credentials"


def _steps() -> list[dict[str, object]]:
    """The deploy job's steps, in order, from the real workflow file."""
    jobs = workflow_document(DEPLOY_WORKFLOW)["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[DEPLOY_JOB]
    assert isinstance(job, dict)
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


def _guard() -> dict[str, object]:
    steps = _steps()
    return steps[_index_of(_is_guard, steps)]


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

    def test_the_guard_runs_before_any_aws_credential_is_assumed(self) -> None:
        """Position, by index, is the whole point of "leaves AWS untouched".

        A guard that failed *after* ``configure-aws-credentials`` would have
        assumed the deploy role before deciding the ref was not allowed to deploy.
        """
        steps = _steps()
        assert _index_of(_is_guard, steps) < _index_of(_is_credential_step, steps)

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
