"""Control-plane errors and the single outcome -> exit-code mapping.

Every error the control plane reports carries the ``ExitCode`` it terminates
with, so a front-end can render ``str(exc)`` and exit — no Python traceback is
emitted (Requirements 10.2, 10.3).

``ControlPlaneError`` deliberately does **not** subclass ``ValueError``: Pydantic
wraps ``ValueError``/``AssertionError`` raised inside a validator into its own
``ValidationError``, which would hide the offending field and the intended exit
code. Any other exception type propagates out of model construction unchanged,
so a ``UsageError`` raised by a validator reaches the caller intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError as PydanticValidationError

from bdo_deploy.core.exit_codes import ExitCode

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    # ``core.models`` imports ``UsageError`` from this module, so the ``Plan``
    # annotation stays a type-checking-only import.
    from bdo_deploy.core.models import Plan


class ControlPlaneError(Exception):
    """Base class for every error the control plane reports to an operator."""

    exit_code: ClassVar[ExitCode] = ExitCode.EXECUTOR_FAILED

    def __init__(self, summary: str) -> None:
        super().__init__(summary)
        self.summary = summary


class UsageError(ControlPlaneError):
    """A usage/validation error, raised before any executor call (exit ``2``).

    Names the offending field and value, and optionally how to proceed, as
    Requirements 5.2, 9.1 and 10.3 require.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.USAGE_ERROR

    def __init__(
        self,
        *,
        field: str,
        value: object,
        problem: str,
        hint: str | None = None,
    ) -> None:
        self.field = field
        self.value = value
        self.problem = problem
        self.hint = hint
        summary = f"{field}: {problem}"
        if hint is not None:
            summary = f"{summary} — {hint}"
        super().__init__(summary)


class ExecutorFailed(ControlPlaneError):
    """An executor (``sam`` / ``gh`` / ``git``) failed (exit ``1``).

    ``output`` carries the executor's own output verbatim (Requirement 10.2).
    """

    exit_code: ClassVar[ExitCode] = ExitCode.EXECUTOR_FAILED

    def __init__(self, summary: str, *, output: str | None = None) -> None:
        self.output = output
        super().__init__(summary)


class ExecutorUnavailable(ControlPlaneError):
    """A plan needs an executor that was never injected (exit ``1``).

    A wiring fault, not operator input: raised while resolving a plan's steps,
    before any executor is called, so nothing has been mutated. Named so the
    caller sees the missing executor instead of an ``AttributeError`` or a
    ``None`` dereference.
    """

    exit_code: ClassVar[ExitCode] = ExitCode.EXECUTOR_FAILED


class ConfirmationRequired(ControlPlaneError):
    """A mutating plan was invoked without confirmation (exit ``3``).

    Nothing has been executed; ``plan`` carries the effects the caller inspects
    before re-invoking with ``--yes`` (Requirement 10.4).
    """

    exit_code: ClassVar[ExitCode] = ExitCode.CONFIRMATION_REQUIRED

    def __init__(self, summary: str, *, plan: Plan | None = None) -> None:
        self.plan = plan
        super().__init__(summary)


def exit_code_for(outcome: BaseException | None) -> ExitCode:
    """Map a terminating outcome to exactly one ``ExitCode`` (Requirement 10.7).

    This is the only place the mapping is made, so both front-ends and the
    dispatcher agree on it.

    ``None`` means the command completed. A ``ControlPlaneError`` carries its own
    exit code. A Pydantic ``ValidationError`` — a malformed ``Command`` rejected
    by model validation — is a usage error. Anything else is an action that
    failed, which is exit ``1``: the contract admits no fifth outcome.
    """
    if outcome is None:
        return ExitCode.SUCCESS
    if isinstance(outcome, ControlPlaneError):
        return outcome.exit_code
    if isinstance(outcome, PydanticValidationError):
        return ExitCode.USAGE_ERROR
    return ExitCode.EXECUTOR_FAILED


__all__ = [
    "ConfirmationRequired",
    "ControlPlaneError",
    "ExecutorFailed",
    "ExecutorUnavailable",
    "ExitCode",
    "UsageError",
    "exit_code_for",
]
