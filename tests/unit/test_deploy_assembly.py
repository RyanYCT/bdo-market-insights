"""Unit tests for the composition root, ``bdo_deploy.core.assembly``.

Two claims are asserted, both structural:

- **One wiring, two entry points.** ``build_dispatcher()`` still returns the core
  alone; ``build_control_plane()`` returns it together with the very
  ``GitHubExecutor`` that core dispatches github steps through, so a dispatch and
  the watch that follows it cannot end up talking to two different GitHubs.
- **Assembling reaches no tool.** No subprocess, no AWS client, no network — which
  is what makes it safe to build before a ``--dry-run`` is known about
  (Requirement 2.4, design Property 5).

Plus the invariant that makes run-following legal at all: **no ``Op`` routes to
``watch`` or ``view``**. Following happens outside the plan, so a preview can never
reach a tool and a plan never describes a blocking wait.
"""

from __future__ import annotations

from typing import Final

import pytest

from bdo_deploy.core.assembly import ControlPlane, build_control_plane, build_dispatcher
from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.executors.config import SsmSamconfigStore
from bdo_deploy.core.executors.github import GitHubCli
from bdo_deploy.core.models import Op

GITHUB_OPS: Final = frozenset(
    {Op.GITHUB_RUN_WORKFLOW, Op.GITHUB_ENVIRONMENT_SET, Op.GITHUB_SECRET_SET}
)
"""Every ``Op`` the GitHub adapter serves. ``watch`` / ``view`` are not among them."""


class TestBuildDispatcherIsUnchanged:
    """The pre-existing entry point still returns a ``Dispatcher`` on its own."""

    def test_it_returns_a_wired_dispatcher(self) -> None:
        assert isinstance(build_dispatcher(), Dispatcher)

    def test_an_override_is_applied(self) -> None:
        github = GitHubCli()
        dispatcher = build_dispatcher(github=github)
        assert dispatcher._github is github


class TestBuildControlPlane:
    """The front-ends' entry point: the core plus the executor they may call."""

    def test_it_returns_both_collaborators(self) -> None:
        plane = build_control_plane()
        assert isinstance(plane, ControlPlane)
        assert isinstance(plane.dispatcher, Dispatcher)

    def test_the_exposed_executor_is_the_one_the_core_dispatches_through(self) -> None:
        """One object, so following a run cannot reach a different GitHub."""
        plane = build_control_plane()
        assert plane.dispatcher._github is plane.github

    def test_an_injected_github_is_used_for_both(self) -> None:
        github = GitHubCli()
        plane = build_control_plane(github=github)
        assert plane.github is github
        assert plane.dispatcher._github is github

    def test_an_injected_dispatcher_replaces_the_assembled_core(self) -> None:
        """How a front-end applies an injected core without assembling a plane."""
        core = Dispatcher()
        plane = build_control_plane(dispatcher=core)
        assert plane.dispatcher is core
        assert plane.github is not None, "a run is still followable"


class TestAssemblingReachesNoTool:
    """Building the wiring must not need credentials, a region or a subprocess."""

    def test_no_subprocess_is_spawned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("assembling must not run a command")

        monkeypatch.setattr(subprocess, "run", forbidden)
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        build_control_plane()
        build_dispatcher()

    def test_no_aws_client_is_created(self) -> None:
        """The store builds its SSM client lazily; assembling must not trip it.

        Load-bearing: an eager boto3 client would need credentials and a region
        merely to *plan*, and planning is pure.
        """
        store = build_control_plane().dispatcher._config
        assert isinstance(store, SsmSamconfigStore)
        assert store._client is None


class TestNoOpRoutesToRunStatus:
    """``watch`` / ``view`` stay unreachable from a plan, by construction."""

    def test_the_github_adapter_serves_only_the_three_dispatch_and_admin_ops(self) -> None:
        github_ops = {op for op in Op if op.value.startswith("github.")}
        assert github_ops == GITHUB_OPS

    def test_no_op_names_a_run_status_operation(self) -> None:
        assert not [op for op in Op if op.value.endswith((".watch", ".view"))]
