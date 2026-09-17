"""Adapter over the GitHub CLI: everything the control plane does through ``gh``.

Its remit is three groups: **workflow dispatch** (trigger the ``deploy.yml`` run
that performs a shared-env or prod deploy), **run status** (surface that run back
to the operator), and **repository / environment administration** (create or
update a GitHub Environment and set an Environment secret — used only by the
one-time ``bootstrap`` capability).

Named ``GitHubExecutor`` rather than ``ActionsDispatcher`` because its remit is
broader than Actions, and because "Dispatcher" collided with the core
``Dispatcher`` that routes *to* it.

Skeleton only: the method bodies land in task 3.2.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.executors.base import StepExecutor
from bdo_deploy.core.models import CommandResult


class GitHubExecutor(StepExecutor, Protocol):
    """Protocol for the GitHub CLI adapter (bodies defined in task 3.2).

    Workflow dispatch (``run_workflow``) and run status (``watch`` / ``view``)
    arrive with their ``RunRef`` / ``RunStatus`` types in task 3.2; the
    administration pair below is declared now because ``bootstrap`` already plans
    ``github.environment_set`` and ``github.secret_set`` steps against it.
    """

    def set_environment(
        self,
        *,
        name: str,
        reviewers: list[str] | None = None,
    ) -> CommandResult:
        """Create or update a GitHub Environment, e.g. ``prod`` with reviewers.

        ``gh api repos/{owner}/{repo}/environments/{name}``. Required reviewers
        are what makes the prod gate a *platform* control rather than application
        code (Requirement 6.3).
        """
        raise NotImplementedError

    def set_environment_secret(
        self,
        *,
        environment: str,
        name: str,
        value: str,
    ) -> CommandResult:
        """Set an Environment secret, e.g. ``AWS_DEPLOY_ROLE_ARN``.

        Only ``name`` is ever rendered in a ``Plan``; the role ARN is
        account-identifying, so ``value`` is supplied at execution time and never
        appears in a plan, in ``--json`` output, or in any tracked file
        (Requirement 4.4). A planned value, if one is ever carried, travels in
        ``params`` as a ``SecretStr`` and is recovered with
        ``.get_secret_value()``.
        """
        raise NotImplementedError


__all__: list[str] = ["GitHubExecutor"]
