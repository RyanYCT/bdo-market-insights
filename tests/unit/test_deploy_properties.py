"""Generative tests for the deploy control plane's stated invariants (Phase 8).

One property per stated invariant, each driving the **real** code with generated
input. These are the generative halves of claims the example-based suites already
pin down at concrete points; they exist because the invariants are universally
quantified ("for any intent", "for all commands the wizard can construct"), and a
list of examples cannot say that.

Per AGENTS.md these are property tests *for invariants*, not for completeness:
each one defends a statement from the design's "Correctness Properties" section
and cites the requirement(s) it validates.

No ``sam`` / ``gh`` / ``git`` / AWS call is made and no subprocess is spawned:
planning is pure, execution is the recording fake's, and nothing is ever
confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from hypothesis import given, settings
from hypothesis import strategies as st

from bdo_deploy.cli import main
from bdo_deploy.core.dispatch import CONFIG_SET, CONFIG_SHOW
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import Capability, Target
from bdo_deploy.core.validation import SSM_ROOT_SEGMENT, samconfig_stages
from tests.unit.test_deploy_equivalence import RecordingDispatcher
from tests.unit.test_deploy_tui import Step, check, choose, drive, set_input, set_select

# -- the intent space ---------------------------------------------------------
#
# One generated value describes an intent; the *same* value derives the argv a
# caller types and the widget steps a human performs. Deriving both from one
# description is what makes the comparison meaningful: two independently
# generated expressions could differ for reasons that have nothing to do with the
# front-ends, and the test would then prove nothing.

_STAGES: tuple[str, ...] = tuple(sorted(samconfig_stages()))

_stages = st.sampled_from(_STAGES)

_versions = st.tuples(
    st.integers(min_value=0, max_value=99),
    st.integers(min_value=0, max_value=99),
    st.integers(min_value=0, max_value=99),
).map(lambda parts: "v{}.{}.{}".format(*parts))

_param_names = st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,11}", fullmatch=True)
"""A ``samconfig.toml`` parameter name — deploy-time config, changed by a PR."""

_path_segments = st.from_regex(r"[a-z][a-z0-9-]{0,9}", fullmatch=True)

_ssm_paths = st.builds(
    lambda stage, category, name: f"/{SSM_ROOT_SEGMENT}/{stage}/{category}/{name}",
    _stages,
    _path_segments,
    _path_segments,
)
"""A repo-scoped SSM path — operational config, written by an audited put."""

_config_keys = st.one_of(_ssm_paths, _param_names)

_config_values = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9,._=-]{0,15}", fullmatch=True)
"""Values the two front-ends carry *verbatim*, so the shape is constrained only
where the front-ends genuinely differ: it must not begin with ``-`` (argv would
read it as an option rather than as this intent) and it must not contain
whitespace, which the TUI's key field strips and argv does not. Everything inside
those bounds — commas, dots, ``=``, digits — is generated."""

_reviewer_ids = st.from_regex(r"(User|Team):[0-9]{1,6}", fullmatch=True)


@dataclass(frozen=True)
class GeneratedIntent:
    """One generated operator intent, expressible through either front-end.

    Every field is generated for every capability; each capability's two
    expressions then read the same subset, so a field a capability has no widget
    and no flag for is simply unused by both derivations rather than special-cased
    in one.
    """

    capability: Capability
    stage: str
    target: Target
    version: str
    sync: bool
    dispatch: bool
    action: str
    key: str
    value: str
    reviewers: tuple[str, ...] = field(default_factory=tuple)

    def argv(self) -> list[str]:
        """What a caller (or an agent) types to submit this intent."""
        stage = ["--stage", self.stage]
        match self.capability:
            case Capability.CONFIG:
                if self.action == CONFIG_SHOW:
                    return ["config", CONFIG_SHOW, *stage]
                return ["config", CONFIG_SET, self.key, self.value, *stage]
            case Capability.BOOTSTRAP:
                reviewers = [arg for name in self.reviewers for arg in ("--reviewer", name)]
                return ["bootstrap", *stage, *reviewers]
            case Capability.DEPLOY:
                return [
                    "deploy",
                    *stage,
                    "--target",
                    self.target.value,
                    *(["--sync"] if self.sync else []),
                ]
            case Capability.RELEASE:
                return [
                    "release",
                    self.version,
                    *stage,
                    *(["--dispatch"] if self.dispatch else []),
                ]

    def steps(self) -> list[Step]:
        """What a human clicks and types to submit the same intent.

        Only the *true* toggles are clicked: an unticked ``Checkbox`` is already
        false, so ``check()`` is reused as-is rather than reimplemented as a
        set-to-boolean helper.
        """
        fields: list[Step] = [set_input("stage", self.stage)]
        match self.capability:
            case Capability.CONFIG:
                fields.append(set_select("action", self.action))
                if self.action == CONFIG_SET:
                    fields += [set_input("key", self.key), set_input("value", self.value)]
            case Capability.BOOTSTRAP:
                fields.append(set_input("reviewers", ", ".join(self.reviewers)))
            case Capability.DEPLOY:
                fields.append(set_select("target", self.target))
                if self.sync:
                    fields.append(check("sync"))
            case Capability.RELEASE:
                fields.append(set_input("version", self.version))
                if self.dispatch:
                    fields.append(check("dispatch"))
        return [*choose(self.capability), *fields, "#review"]


_intents = st.builds(
    GeneratedIntent,
    capability=st.sampled_from(Capability),
    stage=_stages,
    target=st.sampled_from(Target),
    version=_versions,
    sync=st.booleans(),
    dispatch=st.booleans(),
    action=st.sampled_from([CONFIG_SHOW, CONFIG_SET]),
    key=_config_keys,
    value=_config_values,
    reviewers=st.lists(_reviewer_ids, max_size=3, unique=True).map(tuple),
)


class Submitted(NamedTuple):
    """What one front-end did with an intent: its exit code, and the plans it caused."""

    exit_code: int
    plans: list[str]
    """Every ``Plan`` the front-end caused, serialized — empty for a refusal."""


def _through_cli(intent: GeneratedIntent) -> Submitted:
    """Submit ``intent`` to the real CLI, recording the plans it caused."""
    recorder = RecordingDispatcher()
    code = main(intent.argv(), dispatcher=recorder)
    return Submitted(code, [plan.model_dump_json() for plan in recorder.plans])


def _through_tui(intent: GeneratedIntent) -> Submitted:
    """Drive the real TUI with ``intent``, recording the plans it caused."""
    recorder = RecordingDispatcher()
    driven = drive(recorder, *intent.steps())
    plans = [plan.model_dump_json() for plan in recorder.plans]
    return Submitted(int(driven.app.exit_code), plans)


# -- Property 1: front-end equivalence ---------------------------------------


class TestFrontEndEquivalenceHoldsForAnyIntent:
    """**Property 1** — one intent, two front-ends, one serialized ``Plan``.

    **Validates: Requirements 1.5**

    ``test_deploy_equivalence`` asserts this at twelve hand-written intents; this
    asserts it over the generated intent space — every capability, every stage in
    ``samconfig.toml``, both deploy targets, both toggles, and generated config
    keys, values, versions and reviewer lists.

    The example count is bounded deliberately: each example drives the Textual
    pilot once, which costs roughly a second, so this is the honest trade between
    coverage and a suite that stays usable. ``deadline=None`` for the same reason —
    a per-example deadline would flag the pilot's own startup as a failure.
    """

    @settings(max_examples=30, deadline=None)
    @given(intent=_intents)
    def test_either_front_end_plans_the_same_thing_or_refuses_the_same_way(
        self, intent: GeneratedIntent
    ) -> None:
        """Equivalence covers the refusals too, so no generated case is skipped.

        Some generated intents legitimately have no plan: a LOCAL ``prod`` deploy
        is unconstructable, ``--sync`` with ``target=ci`` is a usage error, and a
        ``prod`` bootstrap naming no reviewer is refused. Those are asserted as
        *parity of refusal* rather than skipped — a front-end that dropped an
        unusable toggle instead of failing would run a different operation than the
        one asked for, and only one of the two would do so.

        Both facets are asserted in one test body on purpose: each example costs
        one Textual pilot run, and splitting them would double the suite's cost to
        assert two halves of a single claim.
        """
        cli = _through_cli(intent)
        tui = _through_tui(intent)

        assert cli.plans == tui.plans, "the two front-ends planned differently"
        assert cli.exit_code == tui.exit_code
        if cli.plans:
            assert len(cli.plans) == 1, "one intent, one plan"
        else:
            assert cli.exit_code == ExitCode.USAGE_ERROR, (
                "an intent no plan can express is a usage error in both modes"
            )

    def test_the_generated_intents_are_expressible_at_all(self) -> None:
        """A strategy every example of which was refused would pass vacuously.

        Pins the derivations themselves: a mis-spelled selector or a bad argv
        would make every intent a refusal, and the property above would then
        compare nothing against nothing for ever.
        """
        accepted = GeneratedIntent(
            capability=Capability.DEPLOY,
            stage="dev",
            target=Target.LOCAL,
            version="v1.2.3",
            sync=False,
            dispatch=False,
            action=CONFIG_SHOW,
            key="BdoRegions",
            value="NA",
        )
        cli = _through_cli(accepted)
        assert len(cli.plans) == 1
        assert "sam deploy --config-env dev" in cli.plans[0]
        assert cli.plans == _through_tui(accepted).plans

    def test_a_generated_intent_reaches_every_capability(self) -> None:
        """Each capability's two derivations must actually be exercisable.

        Without this, a capability whose argv or whose widget flow was wrong would
        only ever contribute refusal-parity examples — true, but not the property
        anyone wanted proved.
        """
        planned: set[Capability] = set()
        for capability in Capability:
            intent = GeneratedIntent(
                capability=capability,
                stage="dev",
                target=Target.CI,
                version="v1.2.3",
                sync=False,
                dispatch=True,
                action=CONFIG_SHOW,
                key="BdoRegions",
                value="NA",
                reviewers=("User:1234",),
            )
            if _through_cli(intent).plans:
                planned.add(capability)
        assert planned == set(Capability)
