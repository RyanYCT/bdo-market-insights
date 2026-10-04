"""The few values the core and its executors both need, and one home for them.

``core.dispatch`` imports every executor module (it type-hints against their
Protocols), so an executor cannot import ``core.dispatch`` back. Four values are
needed on both sides of that edge — the CD workflow a deploy is dispatched
against, the branch a release is cut from, the remote a release tag is pushed to,
and the placeholder a withheld value is shown as — and each of them used to be
declared twice, once per side, with a comment on the executor copy admitting the
clone and naming the cycle as the reason.

This module is that reason removed. It is deliberately a **leaf**: it imports
nothing from ``bdo_deploy``, so every module in the package can import it and no
import order it participates in can form a cycle. It holds values only — no
types, no functions, no behaviour — because anything with behaviour would
eventually want a collaborator and this module is only safe while it wants
nothing.

It is **not** a dumping ground for constants generally. A value belongs here only
when the core *and* an executor both need it; a value one module owns stays with
that module, which is why ``MASK_EFFECT`` (``core.dispatch`` alone renders
``Plan.effects``), ``SECRET_NAME_SUBSTRINGS`` (the masking criterion, which
travels with the predicate that applies it) and the per-executable names
(``GIT``, ``GH``) are not here.

The modules that used to declare a copy re-export it under the name they already
published, so ``from bdo_deploy.core.executors.git import GIT_REMOTE`` and the
rest keep working: what moved is where the value is *declared*, not where it can
be imported from.
"""

from __future__ import annotations

from typing import Final

DEPLOY_WORKFLOW: Final = "deploy.yml"
"""The dedicated CD workflow every CI deploy is dispatched against (ADR-0038).

``core.dispatch`` renders and plans it; ``executors.github`` uses it as the
default workflow of a direct ``run_workflow`` call, where it is re-exported as
``DEFAULT_WORKFLOW``. A planned step carries ``workflow`` in ``params``, so the
planner's value is what a dispatched step actually sends — the two names are the
same value either way (Requirement 8.3).
"""

RELEASE_BASE_BRANCH: Final = "main"
"""The only branch a release may be cut from (Requirement 7.1).

Also the base a config pull request targets, and — through
``dispatch.PROD_ALLOWED_REFS`` — one of the two refs a ``prod`` deploy may run
from, so the deployment policy cannot admit a branch the release path does not
use (Requirement 6.5).
"""

GIT_REMOTE: Final = "origin"
"""The remote a release tag is published to (Requirement 7.4)."""

MASK: Final = "***"
"""What a value that must not be rendered is shown as instead.

One string for one convention, in both of the places an operator meets it: a
masked value *rendered* into ``PlanStep.command`` by the planner (Requirement
3.7) and a masked value *reported* in a ``ConfigDiff`` by the config executor
(Requirement 3.2). Those are two moments of the same operation — the plan
previews ``--value ***`` and the resulting diff reports ``after="***"`` — so a
change to one that left the other alone would make the preview and the report of
a single write disagree about how a withheld value looks.

Distinct from ``dispatch.MASK_EFFECT``, which is prose (``"(masked)"``) describing
an effect in ``Plan.effects`` rather than a stand-in for a value, and which only
``core.dispatch`` produces.
"""

__all__ = ["DEPLOY_WORKFLOW", "GIT_REMOTE", "MASK", "RELEASE_BASE_BRANCH"]
