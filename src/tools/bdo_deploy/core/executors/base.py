"""The execution seam: the uniform contract between a planned step and its tool.

``StepExecutor`` is the one seam ``Dispatcher.execute()`` dispatches through. The
dispatcher needs a single uniform call per step rather than knowledge of each
adapter's method set; the adapters keep their own typed domain methods (``build``,
``run_workflow``, ``put_ssm``, …) and map a step onto them. Every executor
Protocol extends this one, so the dispatcher's executor table is typed and no
step can reach a tool through any other route.

This seam sits *in front of* those domain Protocols; it does not replace them.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.models import CommandResult, PlanStep


class StepExecutor(Protocol):
    """Runs one ``PlanStep`` and reports what the sanctioned tool said."""

    def run_step(self, step: PlanStep) -> CommandResult:
        """Perform ``step``'s intent and report the tool's own output.

        Implementations switch on ``step.op`` and read the typed values out of
        ``step.params``, dispatching onto their own domain method. They **never
        parse ``step.command``**, which exists only as the display rendering —
        the intent arrives structured, so each adapter reaches its tool natively
        (shelling out to ``sam``/``git``/``gh``, or calling boto3 for SSM).

        Implementations land with the executors (tasks 3.1-3.4); until then a
        call fails loudly rather than silently reporting success.
        """
        raise NotImplementedError


def str_param(step: PlanStep, name: str) -> str:
    """Read a required string out of ``step.params``, or raise ``UsageError``.

    A step reaching an executor without the value its op documents is a planning
    fault; naming the missing param beats a ``KeyError`` or an invocation built
    around ``None``.

    Lives here rather than in each adapter because every executor needs exactly
    this: four copies of the same eight lines is the duplication this repo's
    anti-pattern list warns about (``AGENTS.md``), and a copy that drifts would
    change what a missing param reports depending on which tool the step routed
    to.
    """
    value = step.params.get(name)
    if not isinstance(value, str):
        raise UsageError(
            field=f"params.{name}",
            value=value,
            problem=f"{step.op.value} needs a string {name!r} param",
        )
    return value


def optional_str_param(step: PlanStep, name: str) -> str | None:
    """Read an optional string out of ``step.params``.

    Some params are present only when the command carried one (``version`` on a
    dispatch, say), so absence is normal; a present-but-wrongly-typed value is
    still a planning fault and is reported by ``str_param``.
    """
    if name not in step.params:
        return None
    return str_param(step, name)


__all__: list[str] = ["StepExecutor", "optional_str_param", "str_param"]
