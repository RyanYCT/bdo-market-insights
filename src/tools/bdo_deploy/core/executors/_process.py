"""The one subprocess wrapper every CLI-wrapping executor uses.

``SamExecutor``, ``GitExecutor`` and ``GitHubExecutor`` all reach their tool the
same way: run a fixed argument list from the repository root and report what the
tool said. That wrapper lives here **once** so the three adapters cannot each
grow their own — duplicated logic that then drifts is a documented anti-pattern
in this repo (see ``AGENTS.md``). ``ConfigStore`` is deliberately not a client:
it talks to SSM through boto3, not through a CLI.

Guarantees this module makes, so no caller has to restate them:

- A command is a **list of arguments**, never a shell string: ``shell=True`` is
  never used, so no value a caller passes can be re-interpreted by a shell.
- The command runs from the repository root (``core.validation.REPO_ROOT``), the
  directory ``sam``/``git``/``gh`` all expect, so the caller never chooses a cwd.
- stdout and stderr are captured **together** and returned verbatim in
  ``CommandResult.output``; ``ok`` is the exit status. Nothing is reformatted and
  no traceback is produced, which is what Requirement 10.2 asks of executor
  output.
- A missing executable and a timeout are reported as the tool's absence or the
  timed-out command, not as a ``FileNotFoundError`` / ``TimeoutExpired``
  traceback.
- **Nothing here logs or echoes anything.** No argument is written to a log, and
  the only place an argument appears in returned text is the timeout/missing-tool
  message, so a secret value must never be passed as an argument — the executors
  pass secrets to ``gh`` on stdin or via the API instead.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed-argv CLI invocation only (no shell, no user-composed string)
from collections.abc import Sequence
from typing import Final, Protocol

from bdo_deploy.core.models import CommandResult
from bdo_deploy.core.validation import REPO_ROOT

DEFAULT_TIMEOUT_SECONDS: Final = 1800.0
"""Half an hour: long enough for a real ``sam deploy``, short enough that a hung
CLI fails the command instead of hanging an operator's terminal forever."""


class CommandRunner(Protocol):
    """How an executor invokes its CLI; ``run_command`` is the real one.

    A Protocol rather than a hard dependency so an executor can be tested
    against the exact argument list it would have run, without a real ``sam`` /
    ``git`` / ``gh`` on the machine (design: "the SAM / gh / git executors are
    tested against recorded command invocations, not live clouds").
    """

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        """Run ``argv`` and report the tool's own output."""
        ...


def run_command(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run ``argv`` from the repository root and report what the tool said.

    ``argv[0]`` is the executable; the rest are its arguments, passed through
    untouched. Returns ``ok=False`` with the tool's verbatim combined output on a
    non-zero exit, and a named failure (never a traceback) when the executable is
    missing or the command times out.
    """
    if not argv:
        raise ValueError("run_command needs at least an executable")
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv from a closed op vocabulary, shell=False
            list(argv),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(
            ok=False,
            output=(
                f"{argv[0]}: command not found — the deploy control plane dispatches to it "
                "and cannot substitute for it; install it and re-run"
            ),
        )
    except subprocess.TimeoutExpired as expired:
        return CommandResult(
            ok=False,
            output=(
                f"{' '.join(argv)}: timed out after {timeout:g}s\n"
                f"{_partial_output(expired.stdout, expired.stderr)}"
            ),
        )
    return CommandResult(
        ok=completed.returncode == 0,
        output=_combined(completed.stdout, completed.stderr),
    )


def _combined(stdout: str | None, stderr: str | None) -> str:
    """Join what the tool wrote, verbatim and in the order a terminal shows it."""
    return "".join(stream for stream in (stdout, stderr) if stream)


def _partial_output(stdout: bytes | str | None, stderr: bytes | str | None) -> str:
    """The partial output of a timed-out command, decoded leniently.

    ``TimeoutExpired`` types its captured streams as ``bytes | str | None``
    regardless of ``text=True``, so both shapes are handled rather than assumed.
    """
    return _combined(_as_text(stdout), _as_text(stderr))


def _as_text(stream: bytes | str | None) -> str | None:
    if stream is None:
        return None
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "CommandRunner", "run_command"]
