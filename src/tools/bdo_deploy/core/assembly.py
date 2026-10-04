"""The composition root: the one place the concrete adapters are named.

``core.dispatch`` type-hints against the four executor Protocols and injects
them, which is what makes ``execute()`` testable — but it means *something* has
to name the concrete adapters. That something is this module, and only this
module: ``SamCli``, ``GitHubCli``, ``Git`` and ``SsmSamconfigStore`` appear
together here and nowhere else, so the CLI front-end and the TUI front-end both
ask this module for their wiring instead of each assembling their own and
drifting from one another. Requirement 1.4 asks that both front-ends route
through *the same* ``Dispatcher``; a single assembly function is how that stops
being an aspiration.

Two entry points, one wiring. ``build_control_plane()`` is the **only** one a
front-end calls: it returns the core together with the ``GitHubExecutor`` a
front-end needs to follow a dispatched run (``ControlPlane``, below).
``build_dispatcher()`` returns the core alone and is where the wiring itself
lives — ``build_control_plane()`` delegates to it, so every dispatcher either
front-end runs on is built by it — but nothing outside this module calls it in
production. It stays public as the seam for a caller that wants the core without
a ``ControlPlane`` around it, which today means the tests: ``build_dispatcher()``
is what they assert the four defaults through, and inlining it into
``build_control_plane()`` would move that wiring somewhere it cannot be exercised
on its own without buying anything.

It is deliberately a **function, not a container**. There is no registry, no
plugin lookup and no dependency-injection framework: there are exactly four
executors, they are known at write time, and the closed ``Op`` vocabulary means a
fifth cannot appear without ``core.models`` changing too. Speculative generality
is a documented anti-pattern in this repository (``AGENTS.md``), and a registry
here would buy nothing that a keyword argument does not.

**Assembling reaches no tool.** Constructing the four adapters starts no
subprocess, creates no AWS client and touches no network:

- ``SamCli`` / ``GitHubCli`` / ``Git`` only store the injected ``CommandRunner``;
  nothing is run until a step executes.
- ``SsmSamconfigStore`` creates its SSM client **lazily**, on first use — see its
  ``client`` property. That is load-bearing and is preserved here by passing no
  client: building a boto3 client eagerly would need credentials and a region
  merely to *plan*, and planning is pure (Requirement 2.4, design Property 5). A
  ``--dry-run`` therefore stops at ``plan()`` having reached nothing at all.

So either entry point is safe to call unconditionally at front-end start-up,
before it is known whether the command is a dry run.

Every parameter is optional and defaults to the real adapter, which is what keeps
the wiring overridable: a test substitutes one adapter — a store with a ``moto``
SSM client and a recorded command runner, say — and inherits the real wiring for
the rest.
"""

from __future__ import annotations

from typing import NamedTuple

from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.executors.config import ConfigStore, SsmSamconfigStore
from bdo_deploy.core.executors.git import Git, GitExecutor
from bdo_deploy.core.executors.github import GitHubCli, GitHubExecutor
from bdo_deploy.core.executors.sam import SamCli, SamExecutor


def build_dispatcher(
    *,
    sam: SamExecutor | None = None,
    github: GitHubExecutor | None = None,
    git: GitExecutor | None = None,
    config: ConfigStore | None = None,
) -> Dispatcher:
    """Return a ``Dispatcher`` wired to the four concrete executor adapters.

    **This is the wiring, not a front-end entry point.** The front-ends call
    ``build_control_plane()``, which delegates here — so this function builds
    every dispatcher that runs in production, while being called directly only by
    tests and by a programmatic caller that wants the core alone. Naming the four
    defaults in one function keeps them assertable in isolation.

    Each argument overrides one adapter and defaults to the real one, so a caller
    that needs a stand-in for a single tool does not have to restate the wiring
    for the other three. Passing all four is how a test gets a fully fake
    control plane while still going through the same assembly the front-ends use.

    Creates no AWS client, runs no subprocess and performs no network I/O — see
    the module docstring — so this is safe to call before a ``--dry-run`` is
    known about.
    """
    return Dispatcher(
        sam=sam if sam is not None else SamCli(),
        github=github if github is not None else GitHubCli(),
        git=git if git is not None else Git(),
        config=config if config is not None else SsmSamconfigStore(),
    )


class ControlPlane(NamedTuple):
    """What a front-end needs: the shared core, plus the one executor it may call.

    ``github`` is exposed **for ``presentation.follow_run()`` only**, and it is
    never routed to by a plan: no ``Op`` maps onto ``watch`` or ``view``, and that
    is what keeps a preview from reaching a tool (design Property 5) and a plan
    from describing a blocking wait (Property 1). Following a dispatched run
    happens *after* ``execute()`` has returned, so it cannot be a planned step —
    which leaves handing the executor to the front-end as the only way to do it at
    all.

    A ``NamedTuple`` rather than a class with behaviour: it holds two already-built
    collaborators and decides nothing, so anything more would invite the front-end
    to ask it for logic that belongs in the core.
    """

    dispatcher: Dispatcher
    github: GitHubExecutor


def build_control_plane(
    *,
    dispatcher: Dispatcher | None = None,
    sam: SamExecutor | None = None,
    github: GitHubExecutor | None = None,
    git: GitExecutor | None = None,
    config: ConfigStore | None = None,
) -> ControlPlane:
    """Return the ``Dispatcher`` **and** the ``GitHubExecutor`` behind it.

    Sits beside ``build_dispatcher()``, which keeps working unchanged, and takes
    the same executor overrides. The one ``GitHubCli`` it builds is the one the
    dispatcher routes github steps to *and* the one the front-end follows a run
    with, so a test that fakes ``github=`` fakes both — a dispatch and the watch
    that follows it cannot end up talking to two different GitHubs.

    ``dispatcher`` overrides the assembled core wholesale, which is how a
    front-end applies an injected ``Dispatcher`` (a test driving the whole
    front-end against a fake core) without assembling a ``ControlPlane`` of its
    own. Combining the two here rather than in each front-end is the point: the
    front-ends then have exactly one way to obtain their wiring, whether or not
    something was injected.

    Reaches no tool, for the same reasons ``build_dispatcher()`` does not (see the
    module docstring), so a front-end can call it unconditionally at start-up
    before it knows whether the command is a ``--dry-run``.
    """
    resolved_github = github if github is not None else GitHubCli()
    return ControlPlane(
        dispatcher=(
            dispatcher
            if dispatcher is not None
            else build_dispatcher(sam=sam, github=resolved_github, git=git, config=config)
        ),
        github=resolved_github,
    )


__all__ = ["ControlPlane", "build_control_plane", "build_dispatcher"]
