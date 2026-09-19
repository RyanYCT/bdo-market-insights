"""Unit tests for the four ``bdo_deploy`` executor adapters (task 3.5).

Nothing here touches a live tool or a live cloud, which is what the design's
Testing Strategy asks for: the ``sam`` / ``gh`` / ``git`` adapters are exercised
against **recorded command invocations** through an injected runner, and the
AWS-touching ``ConfigStore`` writes go to **``moto``**. No real ``sam`` or ``gh``
runs, no tag is created or pushed, and the repository's own ``samconfig.toml`` is
never modified — every samconfig test works on a ``tmp_path`` copy.

Five sections, one per unit:

- ``_process.run_command`` — the shared runner: verbatim combined output on a
  non-zero exit, a named (traceback-free) failure for a missing executable, and
  the stdin channel that a secret travels on *and never comes back out of*.
- ``SamCli`` — the exact argv per op, ``--config-env`` and nothing else, and the
  prod refusal that invokes nothing.
- ``Git`` — the four read-only precondition queries, each precondition blocking
  by name without creating a tag, and the failed push that deletes its own tag.
- ``GitHubCli`` — the dispatch argv and run-URL resolution, the Environment's
  deployment branch/tag policy as the two ordered calls GitHub needs, and the
  secret write whose value is on stdin and in no argument.
- ``SsmSamconfigStore`` — masked merged reads, audited SSM writes refused before
  AWS, and a comment-preserving samconfig edit proposed as a pull request.

Requirements: 3.4, 3.5, 9.1, 10.2.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final, cast

import boto3
import moto
import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import run_command
from bdo_deploy.core.executors.config import (
    MASK,
    PR_BODY,
    PULLS_PATH,
    SECURE_STRING,
    SsmClient,
    SsmSamconfigStore,
)
from bdo_deploy.core.executors.git import Git
from bdo_deploy.core.executors.github import (
    DEFAULT_WORKFLOW,
    SECRET_ENV_PREFIX,
    GitHubCli,
    RunRef,
)
from bdo_deploy.core.executors.sam import SamCli
from bdo_deploy.core.models import CommandResult, ConfigDiff, Op, PlanStep
from bdo_deploy.core.validation import SAMCONFIG_PATH

SECRET: Final = "sup3r-s3cret-value"
"""A value that must never appear in an argv or in a returned message."""

VERSION: Final = "v1.4.0"
RUN_URL: Final = "https://github.com/RyanYCT/bdo-market-insights/actions/runs/42"
DEPLOY_ROLE_SECRET: Final = "AWS_DEPLOY_ROLE_ARN"

ENVIRONMENT_PATH: Final = "repos/{owner}/{repo}/environments/prod"
POLICY_PATH: Final = f"{ENVIRONMENT_PATH}/deployment-branch-policies"
"""The two ``gh api`` paths the ``prod`` Environment's protection is written to."""

SSM_DSN: Final = "/bdo-market-insights/dev/db/dsn"
SSM_TOKEN: Final = "/bdo-market-insights/dev/api/token"  # noqa: S105
SSM_DOMAIN: Final = "/bdo-market-insights/dev/domain/api-domain-name"

DSN_VALUE: Final = "postgres://secure-dsn-plaintext"
TOKEN_VALUE: Final = "token-plaintext-value"  # noqa: S105


# -- fakes -------------------------------------------------------------------


class FakeRunner:
    """A ``CommandRunner`` that records every invocation and scripts replies.

    Exact-argv lookup first, then a prefix match (for an invocation with a long
    trailing argument such as ``gh api … -f body=…``), then a success with no
    output — which is what the read-only ``git`` queries mean by "nothing to
    report". So a test only scripts the invocation whose outcome it is about.
    """

    def __init__(
        self,
        *,
        responses: dict[tuple[str, ...], CommandResult] | None = None,
        prefixes: dict[tuple[str, ...], CommandResult] | None = None,
        default: CommandResult | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self._responses = dict(responses or {})
        self._prefixes = dict(prefixes or {})
        self._default = default if default is not None else CommandResult(ok=True, output="")

    def __call__(self, argv: Sequence[str], *, stdin: str | None = None) -> CommandResult:
        self.calls.append((list(argv), stdin))
        exact = self._responses.get(tuple(argv))
        if exact is not None:
            return exact
        for prefix, result in self._prefixes.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return self._default

    @property
    def argvs(self) -> list[list[str]]:
        """Every argument list this runner was asked to run, in order."""
        return [argv for argv, _ in self.calls]

    @property
    def stdins(self) -> list[str | None]:
        """What was written to each invocation's standard input, in order."""
        return [stdin for _, stdin in self.calls]

    @property
    def flat_argv(self) -> list[str]:
        """Every argument of every invocation, for "this never appears" checks."""
        return [argument for argv in self.argvs for argument in argv]


class ForbiddenSsmClient:
    """An ``SsmClient`` that fails the test if any AWS call is attempted.

    Used where the point is that a refusal happened *before* AWS was reached: a
    recorded absence of a call is weaker than a call that cannot happen at all.
    """

    def get_parameters_by_path(
        self,
        *,
        Path: str,
        Recursive: bool,
        WithDecryption: bool,
        NextToken: str = "",
    ) -> dict[str, Any]:
        raise AssertionError(f"GetParametersByPath was called for {Path}")

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, Any]:
        raise AssertionError(f"GetParameter was called for {Name}")

    def put_parameter(
        self,
        *,
        Name: str,
        Value: str,
        Type: str,
        Overwrite: bool,
        Description: str = "",
    ) -> dict[str, Any]:
        raise AssertionError(f"PutParameter was called for {Name}")


class PutFailsSsmClient:
    """A real (``moto``) client whose ``PutParameter`` fails, reads intact.

    Simulates a denied write so the "prior value is unchanged" claim can be
    checked against what is actually stored, rather than assumed.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get_parameters_by_path(
        self,
        *,
        Path: str,
        Recursive: bool,
        WithDecryption: bool,
        NextToken: str = "",
    ) -> dict[str, Any]:
        response: dict[str, Any] = self._inner.get_parameters_by_path(
            Path=Path, Recursive=Recursive, WithDecryption=WithDecryption
        )
        return response

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, Any]:
        response: dict[str, Any] = self._inner.get_parameter(
            Name=Name, WithDecryption=WithDecryption
        )
        return response

    def put_parameter(
        self,
        *,
        Name: str,
        Value: str,
        Type: str,
        Overwrite: bool,
        Description: str = "",
    ) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "not permitted"}},
            "PutParameter",
        )


def _step(op: Op, executor: str, **params: str | bool | list[str] | SecretStr) -> PlanStep:
    """A ``PlanStep`` carrying only what an executor reads: ``op`` and ``params``."""
    return PlanStep(
        description=f"{op.value} step",
        command=f"<display rendering of {op.value}>",
        executor=cast("Any", executor),
        op=op,
        params=dict(params),
    )


# =============================================================================
# A. the shared runner (``_process.run_command``)
# =============================================================================


class TestRunCommandOutput:
    """Requirement 10.2: the tool's own output, verbatim, and never a traceback."""

    def test_non_zero_exit_reports_combined_output_verbatim(self) -> None:
        result = run_command(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('stdout line\\n');"
                " sys.stderr.write('stderr line\\n'); sys.exit(3)",
            ]
        )
        assert result.ok is False
        assert "stdout line\n" in result.output
        assert "stderr line\n" in result.output
        assert "Traceback" not in result.output

    def test_zero_exit_is_ok(self) -> None:
        result = run_command([sys.executable, "-c", "print('fine')"])
        assert result.ok is True
        assert result.output.strip() == "fine"

    def test_missing_executable_reports_the_tool_by_name(self) -> None:
        result = run_command(["bdo-deploy-no-such-tool"])
        assert result.ok is False
        assert result.output.startswith("bdo-deploy-no-such-tool: command not found")
        assert "Traceback" not in result.output
        assert "FileNotFoundError" not in result.output

    def test_a_timeout_is_reported_not_raised(self) -> None:
        result = run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.2,
        )
        assert result.ok is False
        assert "timed out after 0.2s" in result.output
        assert "Traceback" not in result.output

    def test_an_empty_argv_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError, match="at least an executable"):
            run_command([])


BOTH_STREAMS: Final = (
    "import sys; sys.stdout.write('payload line\\n'); sys.stderr.write('warning line\\n');"
)
"""A child that writes to both streams — the shape that broke the ``gh`` boundary."""


class TestRunCommandStreamSplit:
    """Requirement 10.2: both streams for the operator, stdout alone for a parser.

    ``output`` is what a human reads on a failure, so it keeps everything the
    tool said; ``stdout`` is what a caller validates a machine-readable payload
    out of, so a warning the tool wrote to stderr must not be in it. Every path
    that reached the tool reports both.
    """

    def test_a_successful_command_separates_the_streams(self) -> None:
        result = run_command([sys.executable, "-c", BOTH_STREAMS])
        assert result.ok is True
        assert result.stdout == "payload line\n", "stderr is not part of stdout"
        assert result.output == "payload line\nwarning line\n", "both, in terminal order"

    def test_a_failed_command_separates_the_streams(self) -> None:
        result = run_command([sys.executable, "-c", f"{BOTH_STREAMS} sys.exit(3)"])
        assert result.ok is False
        assert result.stdout == "payload line\n"
        assert result.output == "payload line\nwarning line\n"

    def test_a_timed_out_command_still_reports_both(self) -> None:
        """A partial payload is as worth parsing as a completed one."""
        result = run_command(
            [
                sys.executable,
                "-c",
                f"{BOTH_STREAMS} sys.stdout.flush(); import time; time.sleep(30)",
            ],
            timeout=1.0,
        )
        assert result.ok is False
        assert result.stdout == "payload line\n", "the message about the timeout is not stdout"
        assert "timed out after 1s" in result.output
        assert "payload line\n" in result.output
        assert "warning line\n" in result.output

    def test_a_missing_executable_has_no_tool_output_at_all(self) -> None:
        """The explanation is this module's own; it is nobody's standard output."""
        result = run_command(["bdo-deploy-no-such-tool"])
        assert result.stdout == ""
        assert result.output.startswith("bdo-deploy-no-such-tool: command not found")


class TestRunCommandStdin:
    """``stdin`` is the channel a secret travels on, in one direction only."""

    def test_stdin_is_delivered_to_the_child(self) -> None:
        result = run_command(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            stdin=SECRET,
        )
        assert result.ok is True
        assert result.output == SECRET

    def test_a_missing_tool_does_not_echo_stdin(self) -> None:
        result = run_command(["bdo-deploy-no-such-tool"], stdin=SECRET)
        assert result.ok is False
        assert SECRET not in result.output

    def test_a_timeout_does_not_echo_stdin(self) -> None:
        result = run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=SECRET,
            timeout=0.2,
        )
        assert result.ok is False
        assert "timed out" in result.output
        assert SECRET not in result.output

    def test_nothing_is_run_through_a_shell(self) -> None:
        # A value can therefore not be re-interpreted by a shell: the "command"
        # below is one argument, not a pipeline, so it is simply not found.
        result = run_command(["bdo-deploy-no-such-tool; echo pwned"])
        assert result.ok is False
        assert "command not found" in result.output


# =============================================================================
# B. ``SamCli``
# =============================================================================


class TestSamInvocations:
    """The exact argv per op — ``--config-env`` selection and nothing more."""

    def test_validate(self) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).validate()
        assert runner.argvs == [["sam", "validate", "--lint"]]

    def test_build(self) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).build()
        assert runner.argvs == [["sam", "build"]]

    def test_deploy(self) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).deploy("dev")
        assert runner.argvs == [["sam", "deploy", "--config-env", "dev"]]

    def test_sync(self) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).sync("dev")
        assert runner.argvs == [["sam", "sync", "--config-env", "dev"]]

    def test_pipeline_bootstrap(self) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).pipeline_bootstrap("dev")
        assert runner.argvs == [["sam", "pipeline", "bootstrap", "--stage", "dev"]]

    def test_no_invocation_composes_parameter_overrides(self) -> None:
        """Requirement 2.2: ``samconfig.toml`` owns the parameter set."""
        runner = FakeRunner()
        sam = SamCli(runner=runner)
        sam.validate()
        sam.build()
        sam.deploy("dev")
        sam.sync("dev")
        sam.pipeline_bootstrap("dev")
        assert "--parameter-overrides" not in runner.flat_argv
        assert not any("parameter_overrides" in argument for argument in runner.flat_argv)

    def test_a_failing_sam_is_reported_verbatim(self) -> None:
        failure = CommandResult(ok=False, output="Error: Failed to create changeset\n")
        runner = FakeRunner(default=failure)
        result = SamCli(runner=runner).deploy("dev")
        assert result.ok is False
        assert result.output == "Error: Failed to create changeset\n"


class TestSamRefusesProd:
    """Requirement 6.1: the last thing between a step and production says no."""

    def test_deploy_prod_raises_before_invoking_anything(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            SamCli(runner=runner).deploy("prod")
        assert excinfo.value.field == "config_env"
        assert excinfo.value.value == "prod"
        assert "does not deploy prod" in excinfo.value.problem
        assert runner.calls == []

    def test_a_prod_deploy_step_is_refused_too(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError):
            SamCli(runner=runner).run_step(_step(Op.SAM_DEPLOY, "sam", config_env="prod"))
        assert runner.calls == []


class TestSamRunStep:
    """The ``StepExecutor`` seam: each sam op onto its method, by ``op`` alone."""

    @pytest.mark.parametrize(
        ("step", "argv"),
        [
            (_step(Op.SAM_BUILD, "sam"), ["sam", "build"]),
            (
                _step(Op.SAM_DEPLOY, "sam", config_env="dev"),
                ["sam", "deploy", "--config-env", "dev"],
            ),
            (
                _step(Op.SAM_SYNC, "sam", config_env="dev"),
                ["sam", "sync", "--config-env", "dev"],
            ),
            (
                _step(Op.SAM_PIPELINE_BOOTSTRAP, "sam", stage="dev"),
                ["sam", "pipeline", "bootstrap", "--stage", "dev"],
            ),
        ],
    )
    def test_each_sam_op_maps_to_its_invocation(self, step: PlanStep, argv: list[str]) -> None:
        runner = FakeRunner()
        SamCli(runner=runner).run_step(step)
        assert runner.argvs == [argv]

    def test_a_non_sam_op_is_refused_by_name(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            SamCli(runner=runner).run_step(_step(Op.GIT_TAG, "git", version=VERSION))
        assert excinfo.value.field == "op"
        assert excinfo.value.value == "git.tag"
        assert "not a SAM operation" in excinfo.value.problem
        assert runner.calls == []

    def test_a_step_missing_its_param_names_the_param(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            SamCli(runner=runner).run_step(_step(Op.SAM_DEPLOY, "sam"))
        assert excinfo.value.field == "params.config_env"
        assert runner.calls == []


# =============================================================================
# C. ``Git``
# =============================================================================

STATUS_ARGV: Final = ("git", "status", "--porcelain")
BRANCH_ARGV: Final = ("git", "branch", "--show-current")
TAG_LIST_ARGV: Final = ("git", "tag", "--list", VERSION)
LS_REMOTE_ARGV: Final = ("git", "ls-remote", "--tags", "origin", f"refs/tags/{VERSION}")
TAG_ARGV: Final = ["git", "tag", VERSION]
PUSH_ARGV: Final = ["git", "push", "origin", VERSION]
DELETE_ARGV: Final = ["git", "tag", "--delete", VERSION]

ON_MAIN: Final = CommandResult(ok=True, output="main\n")


def _git(**responses: CommandResult) -> tuple[Git, FakeRunner]:
    """A ``Git`` whose preconditions are clear unless a response overrides one."""
    scripted: dict[tuple[str, ...], CommandResult] = {BRANCH_ARGV: ON_MAIN}
    for name, result in responses.items():
        scripted[{"status": STATUS_ARGV, "branch": BRANCH_ARGV, "tags": TAG_LIST_ARGV}[name]] = (
            result
        )
    runner = FakeRunner(responses=scripted)
    return Git(runner=runner), runner


class TestGitPreconditionQueries:
    """The four checks are read-only queries, and all four always run."""

    def test_the_four_query_argv(self) -> None:
        git, runner = _git()
        assert git.release_preconditions(VERSION) == []
        assert runner.argvs == [
            list(STATUS_ARGV),
            list(BRANCH_ARGV),
            list(TAG_LIST_ARGV),
            list(LS_REMOTE_ARGV),
        ]

    def test_a_clear_check_creates_nothing(self) -> None:
        git, runner = _git()
        git.release_preconditions(VERSION)
        assert TAG_ARGV not in runner.argvs
        assert PUSH_ARGV not in runner.argvs

    def test_every_precondition_is_reported_in_one_pass(self) -> None:
        git, _ = _git(
            status=CommandResult(ok=True, output=" M src/app.py\n"),
            branch=CommandResult(ok=True, output="feature/x\n"),
            tags=CommandResult(ok=True, output=f"{VERSION}\n"),
        )
        issues = git.release_preconditions(VERSION)
        assert len(issues) == 3


class TestGitBlockedRelease:
    """Requirements 7.1 / 7.3: a blocked release names the precondition, tags nothing."""

    @pytest.mark.parametrize(
        ("responses", "expected"),
        [
            (
                {"status": CommandResult(ok=True, output=" M src/app.py\n")},
                "the working tree is not clean",
            ),
            (
                {"branch": CommandResult(ok=True, output="feature/x\n")},
                "the current branch is 'feature/x'",
            ),
            (
                {"branch": CommandResult(ok=True, output="")},
                "HEAD is detached",
            ),
            (
                {"tags": CommandResult(ok=True, output=f"{VERSION}\n")},
                f"the {VERSION} tag already exists locally",
            ),
        ],
    )
    def test_each_precondition_blocks_by_name_without_tagging(
        self,
        responses: dict[str, CommandResult],
        expected: str,
    ) -> None:
        git, runner = _git(**responses)
        result = git.tag_and_push(VERSION)
        assert result.ok is False
        assert expected in result.output
        assert "no tag was created and nothing was pushed" in result.output
        assert TAG_ARGV not in runner.argvs
        assert PUSH_ARGV not in runner.argvs

    def test_an_existing_remote_tag_blocks_without_tagging(self) -> None:
        runner = FakeRunner(
            responses={
                BRANCH_ARGV: ON_MAIN,
                LS_REMOTE_ARGV: CommandResult(ok=True, output=f"abc123\trefs/tags/{VERSION}\n"),
            }
        )
        result = Git(runner=runner).tag_and_push(VERSION)
        assert result.ok is False
        assert f"the {VERSION} tag already exists on origin" in result.output
        assert TAG_ARGV not in runner.argvs
        assert PUSH_ARGV not in runner.argvs

    def test_an_unanswerable_query_is_itself_blocking(self) -> None:
        git, runner = _git(
            status=CommandResult(ok=False, output="fatal: not a git repository\n"),
        )
        result = git.tag_and_push(VERSION)
        assert result.ok is False
        assert "could not verify that the working tree is clean" in result.output
        assert "fatal: not a git repository" in result.output
        assert TAG_ARGV not in runner.argvs


class TestGitTagAndPush:
    """Requirement 7.4, and 7.3's "a failed release leaves git as it was"."""

    def test_success_runs_tag_then_push_in_order(self) -> None:
        git, runner = _git()
        result = git.tag_and_push(VERSION)
        assert result.ok is True
        assert runner.argvs[-2:] == [TAG_ARGV, PUSH_ARGV]

    def test_a_failed_push_deletes_the_local_tag_and_says_so(self) -> None:
        runner = FakeRunner(
            responses={
                BRANCH_ARGV: ON_MAIN,
                tuple(PUSH_ARGV): CommandResult(ok=False, output="! [remote rejected]\n"),
            }
        )
        result = Git(runner=runner).tag_and_push(VERSION)
        assert result.ok is False
        assert runner.argvs[-3:] == [TAG_ARGV, PUSH_ARGV, DELETE_ARGV]
        assert "! [remote rejected]" in result.output
        assert f"the local {VERSION} tag has been deleted" in result.output

    def test_a_failed_cleanup_is_reported_not_swallowed(self) -> None:
        runner = FakeRunner(
            responses={
                BRANCH_ARGV: ON_MAIN,
                tuple(PUSH_ARGV): CommandResult(ok=False, output="! [remote rejected]\n"),
                tuple(DELETE_ARGV): CommandResult(ok=False, output="error: tag is in use\n"),
            }
        )
        result = Git(runner=runner).tag_and_push(VERSION)
        assert result.ok is False
        assert "could not be deleted either" in result.output
        assert "error: tag is in use" in result.output


class TestGitRunStep:
    """The ``StepExecutor`` seam: the two git ops, and a named refusal otherwise."""

    def test_tag_step(self) -> None:
        runner = FakeRunner(responses={BRANCH_ARGV: ON_MAIN})
        Git(runner=runner).run_step(
            _step(Op.GIT_TAG, "git", version=VERSION, base_branch="main", remote="origin")
        )
        assert runner.argvs[-1] == TAG_ARGV

    def test_push_step(self) -> None:
        runner = FakeRunner()
        Git(runner=runner).run_step(_step(Op.GIT_PUSH, "git", version=VERSION, remote="origin"))
        assert runner.argvs == [PUSH_ARGV]

    def test_a_non_git_op_is_refused_by_name(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            Git(runner=runner).run_step(_step(Op.SAM_BUILD, "sam"))
        assert excinfo.value.value == "sam.build"
        assert "not a git operation" in excinfo.value.problem
        assert runner.calls == []


# =============================================================================
# D. ``GitHubCli``
# =============================================================================

RUN_LIST_ARGV: Final = (
    "gh",
    "run",
    "list",
    "--workflow",
    DEFAULT_WORKFLOW,
    "--limit",
    "1",
    "--json",
    "url,databaseId",
)


def _gh_payload(body: str, *, stderr: str = "") -> CommandResult:
    """A ``gh --json`` reply shaped the way the real runner reports one.

    ``gh`` prints the payload to **stdout** and any warning to stderr, so the
    runner returns the payload alone in ``stdout`` and both streams, in terminal
    order, in ``output``. Scripting only ``output`` would hand the boundary a
    payload on a stream ``gh`` never writes JSON to.
    """
    return CommandResult(ok=True, output=f"{stderr}{body}", stdout=body)


def _run_list(url: str | None = RUN_URL) -> CommandResult:
    """What ``gh run list --json`` prints for the newest run of the workflow."""
    body = [] if url is None else [{"url": url, "databaseId": 42}]
    return _gh_payload(json.dumps(body))


class TestGitHubDispatch:
    """Requirement 8.3: exactly the workflow's typed inputs, nothing invented."""

    def test_dispatch_argv_without_a_version(self) -> None:
        runner = FakeRunner(responses={RUN_LIST_ARGV: _run_list()})
        GitHubCli(runner=runner).run_workflow(stage="dev")
        assert runner.argvs[0] == [
            "gh",
            "workflow",
            "run",
            DEFAULT_WORKFLOW,
            "-f",
            "stage=dev",
        ]

    def test_dispatch_argv_with_a_version(self) -> None:
        runner = FakeRunner(responses={RUN_LIST_ARGV: _run_list()})
        GitHubCli(runner=runner).run_workflow(stage="prod", version=VERSION)
        assert runner.argvs[0] == [
            "gh",
            "workflow",
            "run",
            DEFAULT_WORKFLOW,
            "-f",
            "stage=prod",
            "-f",
            f"version={VERSION}",
        ]

    def test_the_run_url_is_resolved_by_a_second_read_only_query(self) -> None:
        runner = FakeRunner(responses={RUN_LIST_ARGV: _run_list()})
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert runner.argvs[1] == list(RUN_LIST_ARGV)
        assert result.ok is True
        assert result.run_url == RUN_URL
        assert RUN_URL in result.output

    def test_the_run_reference_comes_back_structured(self) -> None:
        """The same query yields a ``RunRef``, so following needs no URL parsing."""
        runner = FakeRunner(responses={RUN_LIST_ARGV: _run_list()})
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.run == RunRef(workflow=DEFAULT_WORKFLOW, run_id="42", url=RUN_URL)

    @pytest.mark.parametrize(
        "listed",
        [
            CommandResult(ok=False, output="gh: could not list runs\n"),
            _run_list(url=None),
            _gh_payload("not json at all"),
        ],
    )
    def test_an_unresolved_url_is_still_a_success(self, listed: CommandResult) -> None:
        # The run is already moving; failing the step would describe a started
        # deploy as not-started.
        runner = FakeRunner(responses={RUN_LIST_ARGV: listed})
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.ok is True
        assert result.run_url is None
        assert "its URL could not be resolved" in result.output
        # Still a reference: `gh run watch` can follow the workflow's newest run,
        # which is all that is left to look at.
        assert result.run == RunRef(workflow=DEFAULT_WORKFLOW)

    def test_a_failed_dispatch_is_returned_verbatim_with_no_url(self) -> None:
        runner = FakeRunner(default=CommandResult(ok=False, output="gh: workflow not found\n"))
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.ok is False
        assert result.output == "gh: workflow not found\n"
        assert result.run_url is None
        assert result.run is None, "nothing was dispatched, so there is no run to follow"
        assert len(runner.argvs) == 1

    def test_the_step_dispatches_the_same_inputs(self) -> None:
        runner = FakeRunner(responses={RUN_LIST_ARGV: _run_list()})
        result = GitHubCli(runner=runner).run_step(
            _step(
                Op.GITHUB_RUN_WORKFLOW,
                "github",
                workflow=DEFAULT_WORKFLOW,
                stage="prod",
                version=VERSION,
            )
        )
        assert runner.argvs[0] == [
            "gh",
            "workflow",
            "run",
            DEFAULT_WORKFLOW,
            "-f",
            "stage=prod",
            "-f",
            f"version={VERSION}",
        ]
        assert result.run_url == RUN_URL


VIEW_ARGV: Final = (
    "gh",
    "run",
    "view",
    "42",
    "--json",
    "status,conclusion,url",
)


class TestGitHubRunStatus:
    """``watch`` / ``view``: how a dispatched run's verdict is read back.

    Reached only by ``presentation.follow_run()`` — no ``Op`` routes to either, so
    a plan can never contain a blocking wait. Every call here goes through the
    injected runner, so nothing blocks and no real ``gh`` is invoked.
    """

    def test_watch_selects_the_known_run(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).watch(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert runner.argvs == [["gh", "run", "watch", "42"]]

    def test_watch_without_an_id_follows_the_newest_run(self) -> None:
        """The only thing left to follow when the dispatch's URL was unresolvable."""
        runner = FakeRunner()
        GitHubCli(runner=runner).watch(RunRef(workflow=DEFAULT_WORKFLOW))
        assert runner.argvs == [["gh", "run", "watch"]]

    def test_view_reports_githubs_own_status_and_conclusion(self) -> None:
        runner = FakeRunner(
            responses={
                VIEW_ARGV: _gh_payload(
                    json.dumps({"status": "completed", "conclusion": "failure", "url": RUN_URL})
                )
            }
        )
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert status.ok is True
        assert (status.status, status.conclusion) == ("completed", "failure")
        assert status.run.url == RUN_URL

    @pytest.mark.parametrize(
        "viewed",
        [
            CommandResult(ok=False, output="gh: no run found\n"),
            _gh_payload("not json at all"),
        ],
    )
    def test_an_unreadable_status_is_not_a_passing_status(self, viewed: CommandResult) -> None:
        runner = FakeRunner(responses={VIEW_ARGV: viewed})
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert status.ok is False
        assert status.conclusion is None
        assert status.output == viewed.output


class TestGitHubEnvironment:
    """The one-time bootstrap's environment wiring, through ``gh api``."""

    def test_set_environment_argv(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment(name="prod")
        assert runner.argvs == [
            ["gh", "api", "--method", "PUT", "repos/{owner}/{repo}/environments/prod"]
        ]
        assert runner.stdins == [None]

    def test_reviewers_travel_as_a_json_body_on_stdin(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment(name="prod", reviewers=["User:1234", "Team:56"])
        assert runner.argvs[0][-2:] == ["--input", "-"]
        assert runner.stdins == [
            json.dumps(
                {"reviewers": [{"type": "User", "id": "1234"}, {"type": "Team", "id": "56"}]}
            )
        ]

    def test_the_environment_step_uses_the_planned_environment(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).run_step(
            _step(Op.GITHUB_ENVIRONMENT_SET, "github", environment="dev")
        )
        assert runner.argvs == [
            ["gh", "api", "--method", "PUT", "repos/{owner}/{repo}/environments/dev"]
        ]


class TestGitHubBranchPolicy:
    """Requirement 6.5: which refs may deploy prod, as the two calls GitHub needs.

    The mode goes on the Environment and each admitted pattern is its own
    resource, so the assertions are about **both** halves and their order: the
    per-pattern endpoint rejects an entry until ``custom_branch_policies`` is on.
    """

    def test_the_mode_precedes_one_entry_per_pattern(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment(
            name="prod", allowed_refs=["tag:v*", "branch:main"]
        )
        assert runner.argvs == [
            ["gh", "api", "--method", "PUT", ENVIRONMENT_PATH, "--input", "-"],
            ["gh", "api", "--method", "POST", POLICY_PATH, "--input", "-"],
            ["gh", "api", "--method", "POST", POLICY_PATH, "--input", "-"],
        ]
        assert runner.stdins == [
            json.dumps(
                {
                    "deployment_branch_policy": {
                        "protected_branches": False,
                        "custom_branch_policies": True,
                    }
                }
            ),
            json.dumps({"name": "v*", "type": "tag"}),
            json.dumps({"name": "main", "type": "branch"}),
        ]

    def test_the_policy_travels_with_the_reviewers_in_one_body(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment(
            name="prod", reviewers=["User:1234"], allowed_refs=["branch:main"]
        )
        assert runner.stdins[0] == json.dumps(
            {
                "reviewers": [{"type": "User", "id": "1234"}],
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            }
        )

    def test_an_absent_allowed_refs_sends_no_policy_at_all(self) -> None:
        # An absent list leaves an existing policy untouched, as an absent
        # ``reviewers`` leaves the reviewers untouched — so there is no second
        # call and the environment body carries no policy key either.
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment(name="prod", reviewers=["User:1234"])
        assert runner.argvs == [["gh", "api", "--method", "PUT", ENVIRONMENT_PATH, "--input", "-"]]
        assert "deployment_branch_policy" not in str(runner.stdins[0])

    def test_the_step_carries_the_planned_patterns(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).run_step(
            _step(
                Op.GITHUB_ENVIRONMENT_SET,
                "github",
                environment="prod",
                allowed_refs=["tag:v*"],
            )
        )
        assert runner.argvs[1] == ["gh", "api", "--method", "POST", POLICY_PATH, "--input", "-"]
        assert runner.stdins[1] == json.dumps({"name": "v*", "type": "tag"})

    def test_a_failed_environment_update_adds_no_pattern(self) -> None:
        # The mode is what makes the per-pattern endpoint answer at all, so a
        # failed PUT must stop the step rather than leave it POSTing into a 404.
        refused = CommandResult(ok=False, output="gh: HTTP 403")
        runner = FakeRunner(default=refused)
        result = GitHubCli(runner=runner).set_environment(
            name="prod", allowed_refs=["branch:main"]
        )
        assert result == refused
        assert len(runner.calls) == 1


class TestGitHubSecret:
    """Requirement 4.4: the value is on stdin, in no argv, and in no message."""

    def test_the_value_is_on_stdin_and_in_no_argument(self) -> None:
        runner = FakeRunner()
        GitHubCli(runner=runner).set_environment_secret(
            environment="prod", name=DEPLOY_ROLE_SECRET, value=SECRET
        )
        assert runner.argvs == [["gh", "secret", "set", DEPLOY_ROLE_SECRET, "--env", "prod"]]
        assert runner.stdins == [SECRET]
        assert SECRET not in runner.flat_argv
        assert "--body" not in runner.flat_argv

    def test_the_step_reads_the_value_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{SECRET_ENV_PREFIX}{DEPLOY_ROLE_SECRET}", SECRET)
        runner = FakeRunner()
        GitHubCli(runner=runner).run_step(
            _step(
                Op.GITHUB_SECRET_SET,
                "github",
                environment="prod",
                name=DEPLOY_ROLE_SECRET,
            )
        )
        assert runner.stdins == [SECRET]
        assert SECRET not in runner.flat_argv

    @pytest.mark.parametrize("present", [False, True])
    def test_a_missing_value_fails_naming_the_variable_without_invoking_gh(
        self, monkeypatch: pytest.MonkeyPatch, present: bool
    ) -> None:
        variable = f"{SECRET_ENV_PREFIX}{DEPLOY_ROLE_SECRET}"
        if present:
            monkeypatch.setenv(variable, "")
        else:
            monkeypatch.delenv(variable, raising=False)
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            GitHubCli(runner=runner).run_step(
                _step(
                    Op.GITHUB_SECRET_SET,
                    "github",
                    environment="prod",
                    name=DEPLOY_ROLE_SECRET,
                )
            )
        assert excinfo.value.field == variable
        assert variable in str(excinfo.value)
        assert excinfo.value.value is None
        assert runner.calls == []

    def test_an_empty_value_is_refused_before_gh_is_invoked(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError):
            GitHubCli(runner=runner).set_environment_secret(
                environment="prod", name=DEPLOY_ROLE_SECRET, value=""
            )
        assert runner.calls == []

    def test_a_non_github_op_is_refused_by_name(self) -> None:
        runner = FakeRunner()
        with pytest.raises(UsageError) as excinfo:
            GitHubCli(runner=runner).run_step(_step(Op.SSM_PUT, "config", path=SSM_DOMAIN))
        assert excinfo.value.value == "ssm.put"
        assert "not a GitHub operation" in excinfo.value.problem
        assert runner.calls == []


# =============================================================================
# E. ``SsmSamconfigStore`` — SSM through ``moto``, samconfig through ``tmp_path``
# =============================================================================


@pytest.fixture
def ssm() -> Iterator[Any]:
    """A ``moto``-backed SSM client; no real AWS call is ever made."""
    with moto.mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


@pytest.fixture
def samconfig(tmp_path: Path) -> Path:
    """A throwaway copy of the repository's ``samconfig.toml``.

    Every edit test works on this copy, so the tracked file the repository
    actually deploys from is never touched.
    """
    copy = tmp_path / "samconfig.toml"
    copy.write_text(SAMCONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return copy


def _store(
    *,
    client: Any = None,
    runner: FakeRunner | None = None,
    samconfig_path: Path | None = None,
) -> SsmSamconfigStore:
    """A store with every collaborator injected — no lazy real boto3 client."""
    return SsmSamconfigStore(
        client=cast("SsmClient", client if client is not None else ForbiddenSsmClient()),
        runner=runner if runner is not None else FakeRunner(),
        samconfig_path=samconfig_path,
    )


def _put(ssm: Any, path: str, value: str, *, secure: bool = False) -> None:
    ssm.put_parameter(
        Name=path,
        Value=value,
        Type=SECURE_STRING if secure else "String",
        Overwrite=True,
    )


def _stored(ssm: Any, path: str) -> str:
    parameter: dict[str, Any] = ssm.get_parameter(Name=path, WithDecryption=True)["Parameter"]
    return str(parameter["Value"])


def _stored_type(ssm: Any, path: str) -> str:
    parameter: dict[str, Any] = ssm.get_parameter(Name=path, WithDecryption=True)["Parameter"]
    return str(parameter["Type"])


def _description(ssm: Any, path: str) -> str:
    described: dict[str, Any] = ssm.describe_parameters(
        ParameterFilters=[{"Key": "Name", "Values": [path]}]
    )
    return str(described["Parameters"][0].get("Description", ""))


class TestReadMerged:
    """Requirements 3.1 / 3.2: a masked merged read that mutates nothing."""

    def test_secure_and_secret_named_values_are_masked(self, ssm: Any, samconfig: Path) -> None:
        _put(ssm, SSM_DSN, DSN_VALUE, secure=True)
        _put(ssm, SSM_TOKEN, TOKEN_VALUE)
        _put(ssm, SSM_DOMAIN, "api.example.test")

        view = _store(client=ssm, samconfig_path=samconfig).read_merged("dev")

        assert isinstance(view.ssm[SSM_DSN], SecretStr)
        assert isinstance(view.ssm[SSM_TOKEN], SecretStr)
        assert view.ssm[SSM_DOMAIN] == "api.example.test"
        assert sorted(view.masked) == sorted([SSM_DSN, SSM_TOKEN])

    def test_a_serialized_view_leaks_neither_plaintext(self, ssm: Any, samconfig: Path) -> None:
        # The mask is in the model, so --json cannot print the value either.
        _put(ssm, SSM_DSN, DSN_VALUE, secure=True)
        _put(ssm, SSM_TOKEN, TOKEN_VALUE)

        dumped = _store(client=ssm, samconfig_path=samconfig).read_merged("dev").model_dump_json()

        assert DSN_VALUE not in dumped
        assert TOKEN_VALUE not in dumped
        assert "**********" in dumped

    def test_the_samconfig_side_is_read_and_overrides_expanded(
        self, ssm: Any, samconfig: Path
    ) -> None:
        view = _store(client=ssm, samconfig_path=samconfig).read_merged("dev")
        assert view.samconfig["stack_name"] == "bdo-market-dev"
        assert view.samconfig["BdoRegions"] == "tw"

    def test_a_read_mutates_neither_location(self, ssm: Any, samconfig: Path) -> None:
        _put(ssm, SSM_DOMAIN, "api.example.test")
        before = samconfig.read_text(encoding="utf-8")

        _store(client=ssm, samconfig_path=samconfig).read_merged("dev")

        assert samconfig.read_text(encoding="utf-8") == before
        assert _stored(ssm, SSM_DOMAIN) == "api.example.test"
        assert _description(ssm, SSM_DOMAIN) == ""

    def test_an_undefined_stage_is_refused_before_aws(self, samconfig: Path) -> None:
        with pytest.raises(UsageError) as excinfo:
            _store(samconfig_path=samconfig).read_merged("staging")
        assert excinfo.value.field == "stage"


class TestPutSsm:
    """Requirements 3.4 / 3.5 / 9.1: an audited write, or none at all."""

    def test_the_diff_reports_the_real_before_and_after(self, ssm: Any) -> None:
        _put(ssm, SSM_DOMAIN, "old.example.test")

        diff = _store(client=ssm).put_ssm(SSM_DOMAIN, "new.example.test")

        assert diff == ConfigDiff(
            source="ssm", key=SSM_DOMAIN, before="old.example.test", after="new.example.test"
        )
        assert _stored(ssm, SSM_DOMAIN) == "new.example.test"

    def test_a_first_write_has_no_before(self, ssm: Any) -> None:
        diff = _store(client=ssm).put_ssm(SSM_DOMAIN, "api.example.test")
        assert diff.before is None
        assert diff.after == "api.example.test"

    def test_the_audit_record_is_written_as_the_description(self, ssm: Any) -> None:
        _store(client=ssm).put_ssm(SSM_DOMAIN, "api.example.test")
        description = _description(ssm, SSM_DOMAIN)
        assert description.startswith("set by ")
        assert "via bdo-deploy" in description

    def test_an_existing_secure_string_is_not_downgraded(self, ssm: Any) -> None:
        _put(ssm, SSM_DSN, DSN_VALUE, secure=True)

        diff = _store(client=ssm).put_ssm(SSM_DSN, "postgres://rotated")

        assert _stored_type(ssm, SSM_DSN) == SECURE_STRING
        assert _stored(ssm, SSM_DSN) == "postgres://rotated"
        assert diff.before == MASK
        assert diff.after == MASK

    @pytest.mark.parametrize(
        "path",
        [
            "/bdo/dev/db/dsn",
            "/bdo-market-insights/staging/db/dsn",
            "/bdo-market-insights/dev/dsn",
            "dev/db/dsn",
        ],
    )
    def test_a_rejected_path_is_refused_before_any_aws_call(self, ssm: Any, path: str) -> None:
        # ForbiddenSsmClient makes the "before AWS" claim structural: any call at
        # all fails the test. The moto side then shows nothing was created.
        with pytest.raises(UsageError) as excinfo:
            _store().put_ssm(path, "value")
        assert excinfo.value.field == "ssm_path"
        with pytest.raises(ClientError):
            ssm.get_parameter(Name=path)

    def test_a_rejected_path_leaves_an_existing_value_untouched(self, ssm: Any) -> None:
        _put(ssm, SSM_DOMAIN, "api.example.test")
        with pytest.raises(UsageError):
            _store(client=ssm).put_ssm("/bdo/dev/domain/api-domain-name", "hijacked")
        assert _stored(ssm, SSM_DOMAIN) == "api.example.test"

    def test_a_failed_write_leaves_the_prior_value_intact(self, ssm: Any) -> None:
        _put(ssm, SSM_DOMAIN, "old.example.test")

        with pytest.raises(UsageError) as excinfo:
            _store(client=PutFailsSsmClient(ssm)).put_ssm(SSM_DOMAIN, "new.example.test")

        assert "the prior value at that path is unchanged" in excinfo.value.problem
        assert "Traceback" not in str(excinfo.value)
        assert _stored(ssm, SSM_DOMAIN) == "old.example.test"

    def test_the_step_carries_the_value_as_a_secret(self, ssm: Any) -> None:
        result = _store(client=ssm).run_step(
            _step(
                Op.SSM_PUT,
                "config",
                path=SSM_DOMAIN,
                value=SecretStr("api.example.test"),
                overwrite=True,
            )
        )
        assert result.ok is True
        assert result.changes == [
            ConfigDiff(source="ssm", key=SSM_DOMAIN, before=None, after="api.example.test")
        ]
        assert _stored(ssm, SSM_DOMAIN) == "api.example.test"

    def test_a_non_config_op_is_refused_by_name(self) -> None:
        with pytest.raises(UsageError) as excinfo:
            _store().run_step(_step(Op.SAM_BUILD, "sam"))
        assert excinfo.value.value == "sam.build"
        assert "not a config operation" in excinfo.value.problem


class WorktreeRunner(FakeRunner):
    """A ``FakeRunner`` that honours ``git checkout -- samconfig.toml``.

    The executor delegates its rollback to git, so a fake that ignored the
    checkout could not show whether the file is really restored. This one keeps
    the pristine text and writes it back on that one invocation — nothing else
    about git is simulated.
    """

    def __init__(
        self,
        path: Path,
        *,
        responses: dict[tuple[str, ...], CommandResult] | None = None,
        prefixes: dict[tuple[str, ...], CommandResult] | None = None,
    ) -> None:
        super().__init__(responses=responses, prefixes=prefixes)
        self._path = path
        self._pristine = path.read_text(encoding="utf-8")

    def __call__(self, argv: Sequence[str], *, stdin: str | None = None) -> CommandResult:
        result = super().__call__(argv, stdin=stdin)
        if list(argv) == ["git", "checkout", "--", "samconfig.toml"]:
            self._path.write_text(self._pristine, encoding="utf-8")
        return result


PR_BRANCH: Final = "config/dev-BdoRegions"
PR_TITLE: Final = "config(dev): set BdoRegions"
PR_URL: Final = "https://github.com/RyanYCT/bdo-market-insights/pull/7"

ON_FEATURE: Final = CommandResult(ok=True, output="feat/deploy\n")
PUSH_PR_ARGV: Final = ("git", "push", "--set-upstream", "origin", PR_BRANCH)


GH_PR_POST: Final = ("gh", "api", "--method", "POST", PULLS_PATH)
"""The ``gh api`` invocation that opens the pull request, as far as its path."""

PR_RESPONSE: Final = json.dumps({"html_url": PR_URL, "number": 7})
"""What GitHub answers a successful POST with — the structured response the URL is
read out of, in place of the human-facing output that used to be scraped."""


def _regions_change(after: str = "na,eu") -> ConfigDiff:
    """The one deploy-time toggle a config PR exists for (ADR-0036, Req. 9.3)."""
    return ConfigDiff(source="samconfig", key="BdoRegions", before="tw", after=after)


def _pr_runner(
    samconfig: Path,
    *,
    pr: CommandResult | None = None,
    **overrides: CommandResult,
) -> WorktreeRunner:
    """A recorded git/gh runner: on ``feat/deploy``, clean tree, ``gh`` answers JSON.

    ``pr`` replaces what the ``gh api`` POST answers, which is how the response
    tolerance is exercised without reaching into the runner's tables.
    """
    responses: dict[tuple[str, ...], CommandResult] = {BRANCH_ARGV: ON_FEATURE}
    for name, result in overrides.items():
        responses[{"status": STATUS_ARGV, "push": PUSH_PR_ARGV}[name]] = result
    return WorktreeRunner(
        samconfig,
        responses=responses,
        prefixes={GH_PR_POST: pr or CommandResult(ok=True, output=PR_RESPONSE)},
    )


class TestOpenConfigPr:
    """Requirements 3.3 / 3.6 / 9.3: a reviewed change, and a clean failure."""

    def test_the_edit_preserves_every_comment(self, samconfig: Path) -> None:
        before = samconfig.read_text(encoding="utf-8")
        runner = _pr_runner(samconfig)

        _store(runner=runner, samconfig_path=samconfig).open_config_pr("dev", [_regions_change()])

        after = samconfig.read_text(encoding="utf-8")
        assert after.count("#") == before.count("#")
        assert "Requires SAM CLI >= 1.160.0" in after
        assert "do not collapse them back to a" in after

    def test_bdo_regions_is_edited_inside_parameter_overrides(self, samconfig: Path) -> None:
        runner = _pr_runner(samconfig)

        _store(runner=runner, samconfig_path=samconfig).open_config_pr("dev", [_regions_change()])

        after = samconfig.read_text(encoding="utf-8")
        assert 'parameter_overrides = "Stage=dev BdoRegions=na,eu UseRdsProxy=false"' in after
        # Not added as a sibling entry, and the other stage is untouched.
        assert "\nBdoRegions" not in after
        assert 'parameter_overrides = "Stage=prod BdoRegions=tw UseRdsProxy=false"' in after

    def test_the_recorded_git_and_gh_sequence(self, samconfig: Path) -> None:
        runner = _pr_runner(samconfig)

        pr = _store(runner=runner, samconfig_path=samconfig).open_config_pr(
            "dev", [_regions_change()]
        )

        assert runner.argvs[:6] == [
            list(STATUS_ARGV),
            list(BRANCH_ARGV),
            ["git", "checkout", "-b", PR_BRANCH],
            ["git", "add", "samconfig.toml"],
            ["git", "commit", "-m", PR_TITLE],
            list(PUSH_PR_ARGV),
        ]
        assert runner.argvs[6] == [
            "gh",
            "api",
            "--method",
            "POST",
            PULLS_PATH,
            "-f",
            f"title={PR_TITLE}",
            "-f",
            f"head={PR_BRANCH}",
            "-f",
            "base=main",
            "-f",
            f"body={PR_BODY}",
        ]
        assert runner.argvs[-1] == ["git", "checkout", "feat/deploy"]
        assert pr.branch == PR_BRANCH
        assert pr.base == "main"
        assert pr.title == PR_TITLE
        assert pr.url == PR_URL

    def test_a_dirty_tree_is_refused_before_anything_is_touched(self, samconfig: Path) -> None:
        before = samconfig.read_text(encoding="utf-8")
        runner = _pr_runner(samconfig, status=CommandResult(ok=True, output=" M src/app.py\n"))

        with pytest.raises(UsageError) as excinfo:
            _store(runner=runner, samconfig_path=samconfig).open_config_pr(
                "dev", [_regions_change()]
            )

        assert excinfo.value.field == "worktree"
        assert samconfig.read_text(encoding="utf-8") == before
        assert runner.argvs == [list(STATUS_ARGV)]

    def test_a_failed_push_restores_the_file_and_the_checkout(self, samconfig: Path) -> None:
        before = samconfig.read_text(encoding="utf-8")
        runner = _pr_runner(
            samconfig, push=CommandResult(ok=False, output="! [rejected] no upstream\n")
        )

        with pytest.raises(UsageError) as excinfo:
            _store(runner=runner, samconfig_path=samconfig).open_config_pr(
                "dev", [_regions_change()]
            )

        assert "could not be pushed" in excinfo.value.problem
        assert "! [rejected] no upstream" in excinfo.value.problem
        assert "Traceback" not in str(excinfo.value)
        assert samconfig.read_text(encoding="utf-8") == before
        assert runner.argvs[-3:] == [
            ["git", "checkout", "--", "samconfig.toml"],
            ["git", "checkout", "feat/deploy"],
            ["git", "branch", "--delete", "--force", PR_BRANCH],
        ]
        assert GH_PR_POST not in [tuple(argv[:5]) for argv in runner.argvs]

    def test_an_ssm_change_is_not_proposed_as_a_pull_request(self, samconfig: Path) -> None:
        runner = _pr_runner(samconfig)
        with pytest.raises(UsageError) as excinfo:
            _store(runner=runner, samconfig_path=samconfig).open_config_pr(
                "dev", [ConfigDiff(source="ssm", key=SSM_DOMAIN, before=None, after="x")]
            )
        assert excinfo.value.field == "changes"
        assert runner.calls == []

    def test_the_step_opens_the_pull_request_the_plan_previewed(self, samconfig: Path) -> None:
        runner = _pr_runner(samconfig)

        result = _store(runner=runner, samconfig_path=samconfig).run_step(
            _step(
                Op.SAMCONFIG_PR,
                "config",
                stage="dev",
                key="BdoRegions",
                value="na,eu",
                branch="config/dev-regions",
                base="main",
                title="config(dev): set BdoRegions=na,eu",
            )
        )

        assert result.ok is True
        assert result.changes == [
            ConfigDiff(source="samconfig", key="BdoRegions", before="tw", after="na,eu")
        ]
        assert PR_URL in result.output
        assert ["git", "checkout", "-b", "config/dev-regions"] in runner.argvs


# =============================================================================
# F. the two parsed I/O boundaries (task 10.7)
# =============================================================================


class ScriptedSsmClient:
    """An ``SsmClient`` answering with literal, hand-written response payloads.

    ``moto`` cannot produce a malformed response — that is the point of it — so
    the shapes a real API, a proxy or a future API version could return are
    scripted here instead: a parameter missing a field, a ``Parameters`` entry
    that is not an object, a response that is not a response at all. Writes are
    recorded rather than performed, so the read path can be starved of a payload
    without also losing the ability to check the write still happened.
    """

    def __init__(
        self,
        *,
        pages: Sequence[Any] = (),
        parameter: Any = None,
    ) -> None:
        self._pages = list(pages)
        self._parameter = parameter
        self.paths: list[str] = []
        self.tokens: list[str | None] = []
        self.puts: list[dict[str, Any]] = []

    def get_parameters_by_path(
        self,
        *,
        Path: str,
        Recursive: bool,
        WithDecryption: bool,
        NextToken: str = "",
    ) -> Any:
        self.paths.append(Path)
        self.tokens.append(NextToken or None)
        index = len(self.paths) - 1
        if index >= len(self._pages):
            raise AssertionError(f"GetParametersByPath was called {index + 1} times")
        return self._pages[index]

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> Any:
        if self._parameter is None:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
                "GetParameter",
            )
        return self._parameter

    def put_parameter(
        self,
        *,
        Name: str,
        Value: str,
        Type: str,
        Overwrite: bool,
        Description: str = "",
    ) -> dict[str, Any]:
        self.puts.append({"Name": Name, "Value": Value, "Type": Type})
        return {}


class TestGhPayloadTolerance:
    """Requirement 10.2: a malformed or partial ``gh --json`` payload invents nothing.

    The boundary is validated by a Pydantic model, and a ``ValidationError`` must
    never escape it: an unreadable run has to arrive as a reported failure, not as
    a traceback, and not as a run that quietly passed.
    """

    def test_a_partial_view_payload_reports_only_what_it_carried(self) -> None:
        runner = FakeRunner(responses={VIEW_ARGV: _gh_payload(json.dumps({"status": "queued"}))})
        status = GitHubCli(runner=runner).view(
            RunRef(workflow=DEFAULT_WORKFLOW, run_id="42", url=RUN_URL)
        )
        assert status.ok is True
        assert status.status == "queued"
        assert status.conclusion is None, "an absent conclusion is not a conclusion"
        assert status.run.url == RUN_URL, "the known URL survives a payload that omits it"

    @pytest.mark.parametrize(
        ("label", "output"),
        [
            ("an array where an object belongs", json.dumps([{"status": "completed"}])),
            ("a bare JSON string", json.dumps("completed")),
            ("a truncated object", '{"status": "comple'),
            # Noise *on stdout itself* is still unreadable: the boundary reads
            # that one stream, so anything gh printed there has to be the
            # payload. A warning on stderr is a different case — it no longer
            # reaches the parser at all (see the stderr-warning test below).
            (
                "a warning line printed into stdout",
                'warning: gh is out of date\n{"status": "completed"}',
            ),
            (
                "trailing noise printed into stdout",
                '{"status": "completed"}\nwarning: gh is out of date',
            ),
        ],
    )
    def test_an_unvalidatable_view_payload_is_a_failure_not_a_pass(
        self, label: str, output: str
    ) -> None:
        runner = FakeRunner(responses={VIEW_ARGV: _gh_payload(output)})
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert status.ok is False, label
        assert status.status is None
        assert status.conclusion is None
        assert status.output == output, "gh's own output, verbatim"

    def test_a_stderr_warning_does_not_make_a_good_payload_unreadable(self) -> None:
        """Requirements 7.6, 10.6: the run's real conclusion, warning or not.

        ``gh`` writes its own advisories to stderr. While the boundary validated
        the stdout+stderr join, such a line made a well-formed payload
        unparseable, so ``view`` reported no status at all — and a *passing* run
        came back as a failure through ``follow_run``.
        """
        runner = FakeRunner(
            responses={
                VIEW_ARGV: _gh_payload(
                    json.dumps({"status": "completed", "conclusion": "success", "url": RUN_URL}),
                    stderr="warning: gh version 2.40.0 is out of date\n",
                )
            }
        )
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert status.ok is True
        assert (status.status, status.conclusion) == ("completed", "success")
        assert status.run.url == RUN_URL
        assert "warning: gh version" in status.output, (
            "the warning still reaches the operator in gh's own output"
        )

    def test_a_stderr_warning_does_not_hide_the_dispatched_run(self) -> None:
        """The run-URL query is the same boundary, so it reads the same stream."""
        runner = FakeRunner(
            responses={
                RUN_LIST_ARGV: _gh_payload(
                    json.dumps([{"url": RUN_URL, "databaseId": 42}]),
                    stderr="warning: gh version 2.40.0 is out of date\n",
                )
            }
        )
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.run == RunRef(workflow=DEFAULT_WORKFLOW, run_id="42", url=RUN_URL)
        assert result.run_url == RUN_URL

    def test_a_malformed_payload_beside_a_warning_is_still_a_failure(self) -> None:
        """Narrowing the parse did not loosen the tolerance: nothing is invented."""
        runner = FakeRunner(
            responses={
                VIEW_ARGV: _gh_payload('{"status": "comple', stderr="warning: out of date\n")
            }
        )
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert status.ok is False
        assert (status.status, status.conclusion) == (None, None)
        assert status.output == 'warning: out of date\n{"status": "comple', (
            "both streams, verbatim, so the operator sees everything gh said"
        )

    def test_a_non_string_status_is_no_status(self) -> None:
        """A field of the wrong type reads as absent, not as a coerced status."""
        runner = FakeRunner(
            responses={VIEW_ARGV: _gh_payload(json.dumps({"status": 5, "url": 7}))}
        )
        status = GitHubCli(runner=runner).view(RunRef(workflow=DEFAULT_WORKFLOW, run_id="42"))
        assert (status.status, status.conclusion) == (None, None)
        assert status.run.url is None

    @pytest.mark.parametrize(
        ("label", "output"),
        [
            ("a non-array payload", json.dumps({"url": RUN_URL, "databaseId": 42})),
            ("an empty array", json.dumps([])),
            ("an entry missing both fields", json.dumps([{}])),
            ("an entry that is not an object", json.dumps(["nope"])),
        ],
    )
    def test_an_unlocatable_run_is_still_a_successful_dispatch(
        self, label: str, output: str
    ) -> None:
        # The run is already moving, so the dispatch succeeded; only its URL is
        # missing, and the reference still names the workflow to follow.
        runner = FakeRunner(responses={RUN_LIST_ARGV: _gh_payload(output)})
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.ok is True, label
        assert result.run_url is None
        assert result.run == RunRef(workflow=DEFAULT_WORKFLOW)
        assert "its URL could not be resolved" in result.output

    @pytest.mark.parametrize(
        ("database_id", "expected"),
        [(42, "42"), ("42", "42"), (True, None), (4.5, None), (None, None)],
    )
    def test_a_bool_is_never_read_as_a_run_id(
        self, database_id: object, expected: str | None
    ) -> None:
        """``gh`` sends the id as a number, carried as a ``str`` — but never a bool.

        Pydantic's lax mode coerces ``True`` into an ``int | str`` field as ``1``,
        so a boolean would otherwise become the run id ``"1"``: a real run number
        invented out of a flag. Only a genuine number or string is an id.
        """
        runner = FakeRunner(
            responses={
                RUN_LIST_ARGV: _gh_payload(
                    json.dumps([{"url": RUN_URL, "databaseId": database_id}])
                )
            }
        )
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.run == RunRef(workflow=DEFAULT_WORKFLOW, run_id=expected, url=RUN_URL)


class TestSsmPayloadTolerance:
    """Requirement 10.2: a malformed or partial SSM response invents no value.

    Same contract as the ``gh`` boundary: the response is validated by a Pydantic
    model, and a ``ValidationError`` is caught there rather than reaching the
    operator as a traceback. A read is never worth failing a config view or a
    legitimate write over.
    """

    def test_a_parameter_missing_its_value_reads_as_empty_not_absent(
        self, samconfig: Path
    ) -> None:
        # The parameter exists, so the view lists it; dropping the row would
        # silently narrow the merged view instead of reporting what is there.
        client = ScriptedSsmClient(
            pages=[{"Parameters": [{"Name": SSM_DOMAIN, "Type": "String"}]}]
        )
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert view.ssm[SSM_DOMAIN] == ""

    def test_a_parameter_missing_its_type_is_read_as_a_string(self, samconfig: Path) -> None:
        client = ScriptedSsmClient(
            pages=[{"Parameters": [{"Name": SSM_DOMAIN, "Value": "api.example.test"}]}]
        )
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert view.ssm[SSM_DOMAIN] == "api.example.test"
        assert view.masked == [], "a plain name and no stated type is not a masked value"

    def test_a_secret_shaped_name_is_masked_even_with_no_stated_type(
        self, samconfig: Path
    ) -> None:
        """What the ``String`` default cannot do, the name predicate still does."""
        client = ScriptedSsmClient(
            pages=[{"Parameters": [{"Name": SSM_TOKEN, "Value": TOKEN_VALUE}]}]
        )
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert isinstance(view.ssm[SSM_TOKEN], SecretStr)
        assert TOKEN_VALUE not in view.model_dump_json()

    @pytest.mark.parametrize(
        ("label", "page"),
        [
            ("a nameless parameter", {"Parameters": [{"Value": "orphan"}]}),
            ("a non-string name", {"Parameters": [{"Name": 7, "Value": "orphan"}]}),
            ("a parameter that is not an object", {"Parameters": ["nope"]}),
            ("no Parameters key at all", {}),
            ("a Parameters that is not a list", {"Parameters": "nope"}),
            ("a response that is not a response", "nope"),
        ],
    )
    def test_an_unreadable_page_yields_no_invented_row(
        self, label: str, page: Any, samconfig: Path
    ) -> None:
        client = ScriptedSsmClient(pages=[page])
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert view.ssm == {}, label
        assert view.samconfig["stack_name"] == "bdo-market-dev", "the file side is unaffected"

    def test_an_empty_next_token_ends_the_walk(self, samconfig: Path) -> None:
        """An empty token is the last page; sending it back would walk it forever."""
        client = ScriptedSsmClient(
            pages=[
                {
                    "Parameters": [{"Name": SSM_DOMAIN, "Value": "api.example.test"}],
                    "NextToken": "",
                }
            ]
        )
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert view.ssm == {SSM_DOMAIN: "api.example.test"}
        assert client.tokens == [None]

    def test_a_populated_next_token_is_followed(self, samconfig: Path) -> None:
        client = ScriptedSsmClient(
            pages=[
                {
                    "Parameters": [{"Name": SSM_DOMAIN, "Value": "api.example.test"}],
                    "NextToken": "p2",
                },
                {"Parameters": [{"Name": SSM_TOKEN, "Value": TOKEN_VALUE}]},
            ]
        )
        view = _store(client=client, samconfig_path=samconfig).read_merged("dev")
        assert set(view.ssm) == {SSM_DOMAIN, SSM_TOKEN}
        assert client.tokens == [None, "p2"]

    @pytest.mark.parametrize(
        ("label", "response"),
        [
            ("no Parameter key", {}),
            ("a Parameter that is not an object", {"Parameter": "nope"}),
            ("a non-string value", {"Parameter": {"Name": SSM_DOMAIN, "Value": 7}}),
            ("a response that is not a response", "nope"),
        ],
    )
    def test_an_unreadable_prior_parameter_is_no_prior_value(
        self, label: str, response: Any
    ) -> None:
        # The write is what the operator asked for; an unreadable prior read
        # reports no "before" rather than blocking it or guessing one.
        client = ScriptedSsmClient(parameter=response)
        diff = _store(client=client).put_ssm(SSM_DOMAIN, "api.example.test")
        assert diff.before is None, label
        assert diff.after == "api.example.test"
        assert client.puts == [{"Name": SSM_DOMAIN, "Value": "api.example.test", "Type": "String"}]

    def test_an_unstated_prior_type_does_not_downgrade_the_write(self) -> None:
        """A prior parameter whose type is unreadable is written as a ``String``.

        The same value an absent parameter yields, which is what the default
        means: there is no ``SecureString`` here to preserve.
        """
        client = ScriptedSsmClient(parameter={"Parameter": {"Name": SSM_DOMAIN, "Type": 7}})
        _store(client=client).put_ssm(SSM_DOMAIN, "api.example.test")
        assert client.puts[0]["Type"] == "String"

    def test_a_stated_secure_string_still_survives_a_partial_response(self) -> None:
        client = ScriptedSsmClient(parameter={"Parameter": {"Type": SECURE_STRING}})
        diff = _store(client=client).put_ssm(SSM_DSN, DSN_VALUE)
        assert client.puts[0]["Type"] == SECURE_STRING
        assert diff.before is None, "no readable prior value, so no before"
        assert diff.after == MASK
        assert DSN_VALUE not in diff.model_dump_json()


class TestPullRequestResponseTolerance:
    """Requirement 10.2 / 3.3: an unreadable PR response is still an opened PR.

    The third validated boundary, and the one with the least room to fail: the
    pull request has *already been created* by the time GitHub's response is read,
    so a payload that cannot be validated must report an opened PR with no URL —
    never a failure, and never a traceback. Reporting it as a failure would
    describe a pull request that exists as one that was not opened, and would send
    the caller down the restore path for a change that is already published.
    """

    @pytest.mark.parametrize(
        ("label", "output"),
        [
            ("an array where an object belongs", json.dumps([{"html_url": PR_URL}])),
            ("a bare JSON string", json.dumps(PR_URL)),
            ("a truncated object", '{"html_url": "https://githu'),
            (
                "a warning line beside the JSON",
                f'warning: gh is out of date\n{{"html_url": "{PR_URL}"}}',
            ),
            ("no html_url at all", json.dumps({"number": 7})),
            ("a non-string html_url", json.dumps({"html_url": 7})),
        ],
    )
    def test_an_unreadable_response_is_an_opened_pr_with_no_url(
        self, label: str, output: str, samconfig: Path
    ) -> None:
        runner = _pr_runner(samconfig, pr=CommandResult(ok=True, output=output))

        pr = _store(runner=runner, samconfig_path=samconfig).open_config_pr(
            "dev", [_regions_change()]
        )

        assert pr.url is None, label
        assert (pr.branch, pr.base, pr.title) == (PR_BRANCH, "main", PR_TITLE)
        # The attempt completed: the operator is back on their own branch and the
        # scratch branch was not torn down, because nothing failed.
        assert runner.argvs[-1] == ["git", "checkout", "feat/deploy"]
        assert ["git", "branch", "--delete", "--force", PR_BRANCH] not in runner.argvs

    def test_a_failed_post_restores_the_file_and_the_checkout(self, samconfig: Path) -> None:
        """A POST that failed opened nothing, so the attempt is undone and named."""
        before = samconfig.read_text(encoding="utf-8")
        runner = _pr_runner(
            samconfig, pr=CommandResult(ok=False, output="gh: Validation Failed (HTTP 422)\n")
        )

        with pytest.raises(UsageError) as excinfo:
            _store(runner=runner, samconfig_path=samconfig).open_config_pr(
                "dev", [_regions_change()]
            )

        assert "could not be opened" in excinfo.value.problem
        assert "HTTP 422" in excinfo.value.problem, "gh's own output, not a traceback"
        assert "Traceback" not in str(excinfo.value)
        assert samconfig.read_text(encoding="utf-8") == before
        assert runner.argvs[-3:] == [
            ["git", "checkout", "--", "samconfig.toml"],
            ["git", "checkout", "feat/deploy"],
            ["git", "branch", "--delete", "--force", PR_BRANCH],
        ]
