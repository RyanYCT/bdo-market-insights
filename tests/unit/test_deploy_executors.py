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
- ``GitHubCli`` — the dispatch argv and run-URL resolution, and the secret write
  whose value is on stdin and in no argument.
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
    SECURE_STRING,
    SsmClient,
    SsmSamconfigStore,
)
from bdo_deploy.core.executors.git import Git
from bdo_deploy.core.executors.github import (
    DEFAULT_WORKFLOW,
    SECRET_ENV_PREFIX,
    GitHubCli,
)
from bdo_deploy.core.executors.sam import SamCli
from bdo_deploy.core.models import CommandResult, ConfigDiff, Op, PlanStep
from bdo_deploy.core.validation import SAMCONFIG_PATH

SECRET: Final = "sup3r-s3cret-value"
"""A value that must never appear in an argv or in a returned message."""

VERSION: Final = "v1.4.0"
RUN_URL: Final = "https://github.com/RyanYCT/bdo-market-insights/actions/runs/42"
DEPLOY_ROLE_SECRET: Final = "AWS_DEPLOY_ROLE_ARN"

SSM_DSN: Final = "/bdo-market-insights/dev/db/dsn"
SSM_TOKEN: Final = "/bdo-market-insights/dev/api/token"  # noqa: S105
SSM_DOMAIN: Final = "/bdo-market-insights/dev/domain/api-domain-name"

DSN_VALUE: Final = "postgres://secure-dsn-plaintext"
TOKEN_VALUE: Final = "token-plaintext-value"  # noqa: S105


# -- fakes -------------------------------------------------------------------


class FakeRunner:
    """A ``CommandRunner`` that records every invocation and scripts replies.

    Exact-argv lookup first, then a prefix match (for an invocation with a long
    trailing argument such as ``gh pr create --body …``), then a success with no
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
        Git(runner=runner).run_step(_step(Op.GIT_TAG, "git", version=VERSION, base_branch="main"))
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


def _run_list(url: str | None = RUN_URL) -> CommandResult:
    """What ``gh run list --json`` prints for the newest run of the workflow."""
    body = [] if url is None else [{"url": url, "databaseId": 42}]
    return CommandResult(ok=True, output=json.dumps(body))


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

    @pytest.mark.parametrize(
        "listed",
        [
            CommandResult(ok=False, output="gh: could not list runs\n"),
            _run_list(url=None),
            CommandResult(ok=True, output="not json at all"),
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

    def test_a_failed_dispatch_is_returned_verbatim_with_no_url(self) -> None:
        runner = FakeRunner(default=CommandResult(ok=False, output="gh: workflow not found\n"))
        result = GitHubCli(runner=runner).run_workflow(stage="dev")
        assert result.ok is False
        assert result.output == "gh: workflow not found\n"
        assert result.run_url is None
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


GH_PR_CREATE: Final = ("gh", "pr", "create")


def _regions_change(after: str = "na,eu") -> ConfigDiff:
    """The one deploy-time toggle a config PR exists for (ADR-0036, Req. 9.3)."""
    return ConfigDiff(source="samconfig", key="BdoRegions", before="tw", after=after)


def _pr_runner(samconfig: Path, **overrides: CommandResult) -> WorktreeRunner:
    """A recorded git/gh runner: on ``feat/deploy``, clean tree, ``gh`` prints a URL."""
    responses: dict[tuple[str, ...], CommandResult] = {BRANCH_ARGV: ON_FEATURE}
    for name, result in overrides.items():
        responses[{"status": STATUS_ARGV, "push": PUSH_PR_ARGV}[name]] = result
    return WorktreeRunner(
        samconfig,
        responses=responses,
        prefixes={GH_PR_CREATE: CommandResult(ok=True, output=f"{PR_URL}\n")},
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
        assert runner.argvs[6][:8] == [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            PR_BRANCH,
            "--title",
        ]
        assert runner.argvs[6][8] == PR_TITLE
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
        assert GH_PR_CREATE not in [tuple(argv[:3]) for argv in runner.argvs]

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
