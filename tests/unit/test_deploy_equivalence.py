"""Front-end equivalence: one intent, two front-ends, one ``Plan``.

Requirement 1.5 / design Property 1: when the same intent is submitted through
CLI_Mode and through TUI_Mode, the ``Dispatcher`` produces byte-for-byte
identical serialized ``Plan`` objects. This is the **example-based** half of that
claim — every capability, both deploy targets, and both toggles, spelled out as
the concrete invocations an operator or an agent would actually issue.
(Generative coverage of the same property is task 8.1; the structural half — that
neither front-end *contains* any routing, validation or rendering to disagree
with — is ``test_deploy_cli.TestOneSharedCore``.)

**How the plans are captured, and why it matters.** Both front-ends are driven
for real: ``cli.main([...])`` parses the actual argv, and the Textual pilot
clicks the actual widgets. The ``Plan`` compared is the one each front-end
*caused*, recorded at the dispatcher seam by ``RecordingDispatcher`` — the real
``Dispatcher.plan()``, wrapped only to keep what it returned. Building a
``Command`` in the test and planning it twice would prove something weaker and
already-known (that the core is deterministic); recording at the seam is what
makes the *front-ends* the subject, so a TUI that mis-spelled an ``args`` key or
defaulted a field differently fails here.

**The legitimate difference between the two, and how it is handled.**
``Command.dry_run`` and ``assume_yes`` are CLI-only: the TUI has no dry-run mode
(reviewing the plan *is* the preview) and confirms by keypress rather than by
flag. No field is normalised away to accommodate that, because none has to —
``Plan`` carries neither flag, so the two front-ends' plans are directly
comparable as serialized. ``test_the_plan_does_not_depend_on_the_cli_only_flags``
pins that down rather than leaving it as an assumption.

No ``sam`` / ``gh`` / ``git`` / AWS call is made and no subprocess is spawned:
planning is pure, and execution is the fake's. Nothing here is confirmed, so no
front-end gets past the confirmation gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from bdo_deploy.cli import main
from bdo_deploy.core.dispatch import Dispatcher
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import Capability, Command, Plan, Target
from tests.unit.test_deploy_cli import SSM_KEY, FakeDispatcher
from tests.unit.test_deploy_tui import Step, check, choose, drive, set_input, set_select


class RecordingDispatcher(FakeDispatcher):
    """The real planner, with every ``Plan`` it produced kept for comparison.

    Subclasses the CLI suite's fake so that *executing* still reaches nothing —
    an unconfirmed mutating plan raises ``ConfirmationRequired`` exactly as the
    core does, and no executor is injected — while ``plan()`` delegates to the
    **real** ``Dispatcher.plan()``. That combination is what this test needs: a
    genuine plan to compare, produced with no tool anywhere near it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.plans: list[Plan] = []

    def plan(self, cmd: Command) -> Plan:
        plan = Dispatcher.plan(self, cmd)
        self.planned.append(cmd)
        self.plans.append(plan)
        return plan


@dataclass(frozen=True)
class Intent:
    """One operator intent, expressed once per front-end.

    ``argv`` is what a caller types; ``steps`` is what a human clicks and types to
    ask for the same thing. Holding the two side by side is the whole point: they
    are the *only* two ways this intent can be submitted, and they must meet.
    """

    name: str
    argv: tuple[str, ...]
    steps: tuple[Step, ...]
    capability: Capability


def intent(
    name: str,
    capability: Capability,
    argv: list[str],
    steps: list[Step],
) -> Intent:
    """Pair an argv with the TUI flow that expresses the same intent."""
    return Intent(name=name, argv=tuple(argv), steps=tuple(steps), capability=capability)


REVIEWERS: tuple[str, ...] = ("User:1234", "Team:56")
VERSION = "v1.4.0"

INTENTS: tuple[Intent, ...] = (
    # config: the read, and both sanctioned write destinations (an SSM path is
    # operational config; a bare name is deploy-time config changed by a PR).
    intent(
        "config-show",
        Capability.CONFIG,
        ["config", "show"],
        [*choose(Capability.CONFIG), "#review"],
    ),
    intent(
        "config-set-ssm",
        Capability.CONFIG,
        ["config", "set", SSM_KEY, "s3cret-value"],
        [
            *choose(Capability.CONFIG),
            set_select("action", "set"),
            set_input("key", SSM_KEY),
            set_input("value", "s3cret-value"),
            "#review",
        ],
    ),
    intent(
        "config-set-samconfig",
        Capability.CONFIG,
        ["config", "set", "BdoRegions", "NA,EU"],
        [
            *choose(Capability.CONFIG),
            set_select("action", "set"),
            set_input("key", "BdoRegions"),
            set_input("value", "NA,EU"),
            "#review",
        ],
    ),
    # bootstrap: with and without the required reviewers a prod Environment needs.
    intent(
        "bootstrap-dev",
        Capability.BOOTSTRAP,
        ["bootstrap"],
        [*choose(Capability.BOOTSTRAP), "#review"],
    ),
    intent(
        "bootstrap-prod-with-reviewers",
        Capability.BOOTSTRAP,
        [
            "bootstrap",
            "--stage",
            "prod",
            "--reviewer",
            REVIEWERS[0],
            "--reviewer",
            REVIEWERS[1],
        ],
        [
            *choose(Capability.BOOTSTRAP),
            set_input("stage", "prod"),
            set_input("reviewers", f" {REVIEWERS[0]} , {REVIEWERS[1]} "),
            "#review",
        ],
    ),
    # deploy: both targets, and the --sync toggle on the target that can honour it.
    intent(
        "deploy-local",
        Capability.DEPLOY,
        ["deploy"],
        [*choose(Capability.DEPLOY), "#review"],
    ),
    intent(
        "deploy-local-sync",
        Capability.DEPLOY,
        ["deploy", "--sync"],
        [*choose(Capability.DEPLOY), check("sync"), "#review"],
    ),
    intent(
        "deploy-ci-dev",
        Capability.DEPLOY,
        ["deploy", "--target", "ci"],
        [*choose(Capability.DEPLOY), set_select("target", Target.CI), "#review"],
    ),
    intent(
        "deploy-ci-prod",
        Capability.DEPLOY,
        ["deploy", "--stage", "prod", "--target", "ci"],
        [
            *choose(Capability.DEPLOY),
            set_input("stage", "prod"),
            set_select("target", Target.CI),
            "#review",
        ],
    ),
    # release: the pushed tag, and the --dispatch alternative.
    intent(
        "release-tag",
        Capability.RELEASE,
        ["release", VERSION],
        [*choose(Capability.RELEASE), set_input("version", VERSION), "#review"],
    ),
    intent(
        "release-dispatch",
        Capability.RELEASE,
        ["release", VERSION, "--dispatch"],
        [
            *choose(Capability.RELEASE),
            set_input("version", VERSION),
            check("dispatch"),
            "#review",
        ],
    ),
    intent(
        "release-dispatch-prod",
        Capability.RELEASE,
        ["release", VERSION, "--stage", "prod", "--dispatch"],
        [
            *choose(Capability.RELEASE),
            set_input("stage", "prod"),
            set_input("version", VERSION),
            check("dispatch"),
            "#review",
        ],
    ),
)


def _through_cli(argv: tuple[str, ...]) -> str:
    """Submit ``argv`` to the real CLI and return the serialized ``Plan`` it caused."""
    recorder = RecordingDispatcher()
    main(list(argv), dispatcher=recorder)
    (plan,) = recorder.plans
    return plan.model_dump_json()


def _through_tui(steps: tuple[Step, ...]) -> str:
    """Drive the real TUI through ``steps`` and return the ``Plan`` it caused."""
    recorder = RecordingDispatcher()
    drive(recorder, *steps)
    (plan,) = recorder.plans
    return plan.model_dump_json()


class TestFrontEndEquivalence:
    """The same intent, either way in, is the same plan on the wire."""

    @pytest.mark.parametrize("case", INTENTS, ids=[case.name for case in INTENTS])
    def test_the_two_front_ends_produce_identical_plans(self, case: Intent) -> None:
        assert _through_cli(case.argv) == _through_tui(case.steps)

    def test_every_capability_is_covered(self) -> None:
        """An intent list that quietly stopped covering a capability proves less."""
        assert {case.capability for case in INTENTS} == set(Capability)

    def test_the_compared_plans_are_not_vacuously_equal(self) -> None:
        """Two empty recordings would compare equal; a real plan must be there.

        Guards the helpers themselves: if a front-end stopped reaching the core,
        or a TUI selector stopped resolving, every case above would pass on a
        pair of nothings.
        """
        serialized = _through_cli(("deploy",))
        assert '"steps":[' in serialized
        assert "sam deploy --config-env dev" in serialized

    def test_distinct_intents_do_not_collapse_to_one_plan(self) -> None:
        """The comparison has to be able to fail: differing intents must differ."""
        plans = {_through_cli(case.argv) for case in INTENTS}
        assert len(plans) == len(INTENTS)

    def test_the_plan_does_not_depend_on_the_cli_only_flags(self) -> None:
        """``--yes`` / ``--dry-run`` / ``--json`` are CLI-only, and ``Plan`` ignores them.

        This is why no field has to be normalised away to compare the two
        front-ends: ``Command.dry_run`` and ``assume_yes`` have no counterpart in
        the TUI, and they have no counterpart in a ``Plan`` either.
        """
        plain = _through_cli(("deploy",))
        assert plain == _through_cli(("deploy", "--yes"))
        assert plain == _through_cli(("deploy", "--dry-run"))
        assert plain == _through_cli(("deploy", "--json", "--yes"))

    def test_an_intent_no_plan_can_express_is_refused_by_both(self) -> None:
        """``--sync`` with ``target=ci`` is a usage error, identically in both modes.

        Equivalence covers the refusals too: a front-end that dropped the toggle
        instead of failing would dispatch a full CI deploy for a fast-loop
        request, and only one of the two would do so.
        """
        cli = RecordingDispatcher()
        code = main(["deploy", "--target", "ci", "--sync"], dispatcher=cli)

        tui = RecordingDispatcher()
        driven = drive(
            tui,
            *choose(Capability.DEPLOY),
            set_select("target", Target.CI),
            check("sync"),
            "#review",
        )

        assert code == ExitCode.USAGE_ERROR
        assert driven.app.exit_code == ExitCode.USAGE_ERROR
        assert cli.plans == [] and tui.plans == [], "neither mode produced a plan"
