"""Executors: adapters over the sanctioned tools (sam, gh, git, config stores).

``StepExecutor`` is the one seam ``Dispatcher.execute()`` dispatches through. A
``Plan`` is an ordered list of exact command lines, so the dispatcher needs a
single uniform call per step rather than knowledge of each adapter's method set;
the adapters keep their own domain methods (``build``, ``run_workflow``,
``put_ssm``, …) and map a step onto them. Every executor Protocol extends this
one, so the dispatcher's executor table is typed and no step can reach a tool
through any other route.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.models import CommandResult, PlanStep


class StepExecutor(Protocol):
    """Runs one ``PlanStep`` and reports what the sanctioned tool said."""

    def run_step(self, step: PlanStep) -> CommandResult:
        """Run ``step.command`` through this adapter's tool.

        Implementations land with the executors (tasks 3.1-3.4); until then a
        call fails loudly rather than silently reporting success.
        """
        raise NotImplementedError


__all__: list[str] = ["StepExecutor"]
