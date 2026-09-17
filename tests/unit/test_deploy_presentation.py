"""Unit tests for ``bdo_deploy.presentation.follow_run``.

The shared helper both front-ends call after ``execute()`` has returned. What is
asserted is the one thing it decides: **whose verdict the returned ``Result``
carries**. Requirements 7.6 and 10.6 make the CI run authoritative, so a dispatch
that went fine and a run that then failed must come back as a failure.

Every test drives it against ``FakeGitHub`` — no ``gh`` process, no network, and
no blocking watch anywhere in the suite. The rendering functions beside it are
covered through the two front-end suites, which is where their output is read.
"""

from __future__ import annotations

from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import Capability, Result, RunRef
from bdo_deploy.presentation import follow_run, result_lines
from tests.unit.test_deploy_cli import RUN_URL, FakeGitHub, dispatched_result

RUN = RunRef(workflow="deploy.yml", run_id="42", url=RUN_URL)


class TestFollowRunIsANoOp:
    """Nothing to follow means nothing happens — not a failure, not a wait."""

    def test_a_result_without_a_run_is_returned_unchanged(self) -> None:
        github = FakeGitHub()
        local = Result(
            capability=Capability.DEPLOY,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary="deploy: completed 2 step(s)",
        )
        assert follow_run(github, local) == local
        assert github.watched == [] and github.viewed == []


class TestFollowRunFoldsTheVerdictIn:
    """The run's own pass/fail becomes the ``Result``'s."""

    def test_a_successful_run_stays_a_success(self) -> None:
        github = FakeGitHub(conclusion="success")
        followed = follow_run(github, dispatched_result(run=RUN))
        assert followed.ok is True
        assert followed.exit_code is ExitCode.SUCCESS
        assert "the run concluded success" in followed.summary

    def test_a_failed_run_fails_the_result(self) -> None:
        github = FakeGitHub(conclusion="failure")
        followed = follow_run(github, dispatched_result(run=RUN))
        assert followed.ok is False
        assert followed.exit_code is ExitCode.EXECUTOR_FAILED
        assert "the run concluded failure" in followed.summary

    def test_the_run_is_watched_to_completion_and_then_read(self) -> None:
        github = FakeGitHub()
        follow_run(github, dispatched_result(run=RUN))
        assert github.watched == [RUN], "the watch blocks until the run finishes"
        assert github.viewed == [RUN], "the conclusion is read from the run itself"

    def test_an_unreadable_status_is_not_reported_as_a_pass(self) -> None:
        github = FakeGitHub(view_ok=False, conclusion=None)
        followed = follow_run(github, dispatched_result(run=RUN))
        assert followed.ok is False
        assert "could not be read" in followed.summary

    def test_a_run_without_a_conclusion_falls_back_to_the_watch(self) -> None:
        """``gh run watch`` is the only other thing that observed the run."""
        assert (
            follow_run(FakeGitHub(conclusion=None, watch_ok=False), dispatched_result(run=RUN)).ok
            is False
        )
        assert (
            follow_run(FakeGitHub(conclusion=None, watch_ok=True), dispatched_result(run=RUN)).ok
            is True
        )

    def test_the_watch_and_view_output_reaches_the_operator(self) -> None:
        """Requirement 10.2: the tool's own output is surfaced, not summarised."""
        followed = follow_run(FakeGitHub(), dispatched_result(run=RUN))
        assert followed.raw_output is not None
        assert "watched 42" in followed.raw_output
        assert "viewed 42" in followed.raw_output

    def test_the_run_url_survives_and_is_rendered(self) -> None:
        followed = follow_run(FakeGitHub(), dispatched_result(run=RUN))
        assert followed.run_url == RUN_URL
        assert f"  run: {RUN_URL}" in result_lines(followed)

    def test_an_unresolved_url_is_filled_in_from_the_view(self) -> None:
        """By the time a run has been followed, it has certainly been located."""
        unresolved = Result(
            capability=Capability.RELEASE,
            ok=True,
            exit_code=ExitCode.SUCCESS,
            summary="release: completed 2 step(s)",
            run=RunRef(workflow="deploy.yml"),
        )
        followed = follow_run(FakeGitHub(), unresolved)
        assert followed.run is not None and followed.run.url == RUN_URL
        assert followed.run_url == RUN_URL
