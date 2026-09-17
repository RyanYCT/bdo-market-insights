"""The composition root: the one place the concrete adapters are named.

``core.dispatch`` type-hints against the four executor Protocols and injects
them, which is what makes ``execute()`` testable — but it means *something* has
to name the concrete adapters. That something is this module, and only this
module: ``SamCli``, ``GitHubCli``, ``Git`` and ``SsmSamconfigStore`` appear
together here and nowhere else, so the CLI front-end and the TUI front-end
(Phase 5) both call ``build_dispatcher()`` instead of each assembling their own
wiring and drifting from one another. Requirement 1.4 asks that both front-ends
route through *the same* ``Dispatcher``; a single assembly function is how that
stops being an aspiration.

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

So ``build_dispatcher()`` is safe to call unconditionally at front-end start-up,
before it is known whether the command is a dry run.

Every parameter is optional and defaults to the real adapter, which is what keeps
the wiring overridable: a test substitutes one adapter — a store with a ``moto``
SSM client and a recorded command runner, say — and inherits the real wiring for
the rest.
"""

from __future__ import annotations

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


__all__ = ["build_dispatcher"]
