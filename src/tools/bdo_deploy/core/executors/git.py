"""Adapter over ``git``: the release path that fires the tag-triggered pipeline.

Skeleton only: ``release_preconditions`` and ``tag_and_push`` land in task 3.3.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.executors import StepExecutor


class GitExecutor(StepExecutor, Protocol):
    """Protocol for the ``git`` adapter (methods defined in task 3.3)."""
