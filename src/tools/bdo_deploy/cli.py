"""CLI front-end (non-interactive mode) for the deploy control plane.

Skeleton only: the Typer application, per-capability subcommands, and the
``--json`` / ``--yes`` / ``--dry-run`` contract are implemented in task 5.1.
This module exists now so the ``bdo-deploy`` console entry point resolves
(Requirement 2.3).
"""

from __future__ import annotations

import sys

USAGE = """\
bdo-deploy — deploy control plane (scaffolding; not yet implemented)

usage: bdo-deploy [--tui] <capability> [options]

capabilities: config | bootstrap | deploy | release
options:      --json  --yes  --dry-run

Exit codes: 0 success · 1 executor failed · 2 usage/validation · 3 confirmation required
"""


def main(argv: list[str] | None = None) -> int:
    """Process entry point for the ``bdo-deploy`` console script.

    Returns the process exit code. Currently a placeholder that renders usage;
    intent collection and dispatch arrive with the Typer front-end (task 5.1).
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in {"-h", "--help"}:
        print(USAGE, end="")
        return 0
    print("bdo-deploy: not implemented yet", file=sys.stderr)
    return 2
