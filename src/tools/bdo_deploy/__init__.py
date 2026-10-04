"""``bdo-deploy`` — the deploy control plane (dev/ops tooling).

A thin control plane over this repo's existing deployment surface: it translates
operator/agent intent into a typed ``Command``, plans it, and dispatches to
sanctioned executors (the SAM CLI, GitHub Actions via ``gh``, and ``git``). It
reimplements no deploy logic and has no code path that runs a production deploy.

This package lives under ``src/tools/`` rather than ``src/layer/python/`` so its
dev/ops-only dependencies (Typer, Textual) can never be packaged into the
``bdo-common`` Lambda layer (Requirement 9.4).
"""

from __future__ import annotations

__all__: list[str] = []
