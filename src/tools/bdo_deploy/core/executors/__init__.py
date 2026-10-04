"""Executors: adapters over the sanctioned tools (sam, gh, git, config stores).

The execution seam every adapter implements, ``StepExecutor``, lives in
``base`` (design: "Execution seam: ``StepExecutor`` (``core/executors/base.py``)")
and is re-exported here so callers can import it from the package.
"""

from __future__ import annotations

from bdo_deploy.core.executors.base import StepExecutor

__all__: list[str] = ["StepExecutor"]
