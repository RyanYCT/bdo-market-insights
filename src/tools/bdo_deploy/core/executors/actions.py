"""Adapter over the GitHub CLI: triggers ``deploy.yml`` and surfaces its status.

Skeleton only: ``run_workflow`` / ``watch`` / ``view`` land in task 3.2. This is
the shared-env and prod deploy path — CI executes the deploy, the control plane
only triggers it.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.executors import StepExecutor


class ActionsDispatcher(StepExecutor, Protocol):
    """Protocol for the GitHub Actions adapter (methods defined in task 3.2)."""
