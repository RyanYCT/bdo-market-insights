"""Adapter over the SAM CLI for LOCAL, non-prod work.

Skeleton only: ``validate`` / ``build`` / ``deploy`` / ``sync`` land in task 3.1.
``samconfig.toml`` environments own the parameter set; this executor selects
only ``--config-env`` and never composes ``--parameter-overrides``.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.executors.base import StepExecutor


class SamExecutor(StepExecutor, Protocol):
    """Protocol for the SAM CLI adapter (methods defined in task 3.1)."""
