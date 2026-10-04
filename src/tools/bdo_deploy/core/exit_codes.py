"""The exit-code contract.

A leaf module: it imports nothing from the rest of the package, so both the
models and the error hierarchy can depend on it without an import cycle.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """The exit-code contract (Requirement 10.7).

    Every terminating outcome maps to exactly one of these; the mapping itself
    lives in one place, :func:`bdo_deploy.core.errors.exit_code_for`.
    """

    SUCCESS = 0
    """The command completed; ``Result.ok`` is true."""

    EXECUTOR_FAILED = 1
    """An executor (``sam`` / ``gh`` / ``git``) or the action itself failed."""

    USAGE_ERROR = 2
    """A usage/validation error, caught before any executor call."""

    CONFIRMATION_REQUIRED = 3
    """A mutating plan was invoked without confirmation; nothing was run."""


__all__ = ["ExitCode"]
