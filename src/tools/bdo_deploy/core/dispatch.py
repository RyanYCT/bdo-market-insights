"""The dispatcher: the one place a ``Command`` becomes a ``Plan``.

Skeleton only. ``plan()`` (pure, side-effect-free, single-executor routing) is
implemented in task 2.1 and ``execute()`` (confirmation gate and dry-run
purity) in task 2.2.
"""

from __future__ import annotations


class Dispatcher:
    """Resolves a ``Command`` into a ``Plan`` and routes it to one executor."""
