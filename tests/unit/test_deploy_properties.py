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

import contextlib
import json
import pathlib
import subprocess  # nosec B404 - patched, never invoked: see the assembly purity test
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, NamedTuple, cast

import boto3
import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from bdo_deploy.cli import main
from bdo_deploy.core.assembly import ControlPlane, build_control_plane, build_dispatcher
from bdo_deploy.core.dispatch import (
    ACTION_ARG,
    CONFIG_SET,
    CONFIG_SHOW,
    DEPLOY_WORKFLOW,
    DISPATCH_ARG,
    KEY_ARG,
    MASK,
    SYNC_ARG,
    VALUE_ARG,
    Dispatcher,
)
from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors.config import (
    SAMCONFIG_FILE,
    SECRET_NAME_SUBSTRINGS,
    ConfigStore,
    is_secret_name,
)
from bdo_deploy.core.executors.git import GitExecutor
from bdo_deploy.core.executors.github import GH, VERSION_PARAM, GitHubCli, GitHubExecutor
from bdo_deploy.core.executors.sam import SamExecutor
from bdo_deploy.core.exit_codes import ExitCode
from bdo_deploy.core.models import REVIEWERS_ARG, Capability, Command, Op, Plan, PlanStep, Target
from bdo_deploy.core.validation import PROD_STAGE, SSM_ROOT_SEGMENT, samconfig_stages
from tests.unit.test_deploy_equivalence import RecordingDispatcher
from tests.unit.test_deploy_executors import FakeRunner
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

_param_names = st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,11}", fullmatch=True).filter(
    lambda name: not is_secret_name(name)
)
"""A ``samconfig.toml`` parameter name — deploy-time config, changed by a PR.

Secret-shaped names are filtered *out* rather than left to chance: on this branch
they are refused, not planned (Requirement 3.8), so leaving them in would make
every property that quantifies over a config key intermittently sample the
refusal path and assert the planned-PR contract against nothing. The refusal has
its own generator (``_secret_param_names``) and its own property below."""

_secret_param_names = st.builds(
    lambda prefix, substring, suffix: f"{prefix}{substring.capitalize()}{suffix}",
    st.from_regex(r"[A-Za-z]{0,6}", fullmatch=True),
    st.sampled_from(SECRET_NAME_SUBSTRINGS),
    st.from_regex(r"[A-Za-z0-9]{0,6}", fullmatch=True),
)
"""A non-SSM key name the refusal must catch: some ``SECRET_NAME_SUBSTRINGS``
member embedded in an otherwise ordinary parameter name, with its case flipped so
the generated names exercise the case-insensitivity too. Deliberately includes the
false positives the design accepts (``IconKeyPrefix``): they are refused by the
same rule, and that is the trade, not a defect."""

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

_distinctive_values = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9,._=-]{7,15}", fullmatch=True)
"""``_config_values`` with a floor on its length, for the one property that asserts
a value is **absent** from a human-readable message. A one- or two-character value
occurs in ordinary English prose, so a substring search would report the message's
own wording as a leak; at eight characters and up, a match is the value itself."""

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


# -- the constructible command space ------------------------------------------
#
# Property 2 quantifies over "all commands the wizard can construct", so this
# strategy covers the whole ``Command`` space rather than the intents a front-end
# happens to express: every capability, both targets, every stage defined in
# samconfig.toml, with or without a version, and arbitrary ``args`` — including an
# arg no capability reads, because "arbitrary" is the point.

_command_args = st.fixed_dictionaries(
    {},
    optional={
        ACTION_ARG: st.sampled_from([CONFIG_SHOW, CONFIG_SET]),
        KEY_ARG: _config_keys,
        VALUE_ARG: _config_values,
        SYNC_ARG: st.booleans(),
        DISPATCH_ARG: st.booleans(),
        REVIEWERS_ARG: st.lists(_reviewer_ids, max_size=2, unique=True),
        "unknown": st.one_of(st.booleans(), _param_names),
    },
)

_commands: st.SearchStrategy[dict[str, object]] = st.builds(
    dict,
    capability=st.sampled_from(Capability),
    target=st.sampled_from(Target),
    stage=_stages,
    version=st.one_of(st.none(), _versions),
    args=_command_args,
    dry_run=st.booleans(),
    assume_yes=st.booleans(),
)
"""The *fields* of a command, not a ``Command``: whether they make one at all is
the first half of the property. A strategy that could only yield valid commands
would quietly exclude exactly the combinations the invariant is about."""

_LOCAL_SAM_MUTATIONS: frozenset[Op] = frozenset({Op.SAM_DEPLOY, Op.SAM_SYNC})
"""The ops that change a stack by running the SAM CLI **on this machine**.

``SAM_BUILD`` is local but changes no stack, and ``SAM_PIPELINE_BOOTSTRAP``
provisions the deploy plumbing rather than deploying the application — neither is
a deploy. The set is re-derived from the plans themselves below (every step that
selects a ``config_env`` must be in it), so a future op that deploys locally
cannot slip past this constant by not being listed in it."""

_CI_TRIGGERS: frozenset[Op] = frozenset({Op.GITHUB_RUN_WORKFLOW, Op.GIT_TAG, Op.GIT_PUSH})
"""The sanctioned triggers. Issuing one locally does not deploy anything locally:
``git tag`` / ``git push`` run on this machine, and the deploy they trigger runs in
the environment-protected CI job (Requirement 7.5)."""


def _planned(fields: dict[str, object]) -> Plan | None:
    """Return the ``Plan`` these command fields produce, or ``None`` if there is none.

    Two distinct refusals, both of which mean "no plan exists for this intent":
    the ``Command`` cannot be constructed (a LOCAL ``prod`` deploy, a ``prod``
    bootstrap naming no reviewer), or it can be but no plan shape expresses it
    (``sync`` with ``target=ci``). Either way the invariant is satisfied by there
    being nothing to inspect, which is why both collapse to ``None`` here.

    Reaches no executor: the ``Dispatcher`` is built with none injected, and
    ``plan()`` never touches one.
    """
    try:
        cmd = Command.model_validate(fields)
        return Dispatcher().plan(cmd)
    except UsageError:
        return None


# -- Property 2: no first-party prod deploy ----------------------------------


class TestNoPlanDeploysProdLocally:
    """**Property 2** — production is unreachable from this machine.

    **Validates: Requirements 6.1, 6.2, 7.5**

    Asserted on the *structured* intent a step carries — its ``op`` and ``params``
    — and deliberately not by searching ``PlanStep.command`` for the string
    ``sam deploy --config-env prod``. A string search is what an executor never
    does, so it could pass vacuously the moment the rendering changed wording,
    while the plan still told the SAM executor to deploy prod. What the step
    *means* is what has to be safe.
    """

    @settings(max_examples=500)
    @given(fields=_commands)
    def test_no_constructible_command_plans_a_local_prod_deploy(
        self, fields: dict[str, object]
    ) -> None:
        plan = _planned(fields)
        if plan is None:
            return
        for step in plan.steps:
            if step.op not in _LOCAL_SAM_MUTATIONS and "config_env" not in step.params:
                continue
            assert step.op in _LOCAL_SAM_MUTATIONS, (
                f"{step.op} selects a samconfig environment but is not accounted for as a "
                "local SAM mutation; this test's vocabulary has drifted from the planner's"
            )
            assert step.params.get("config_env") != PROD_STAGE, (
                f"{step.op} would deploy {PROD_STAGE} from this machine"
            )
            assert plan.target is Target.LOCAL, (
                f"{step.op} runs the local SAM CLI, so no plan may claim to target CI with it"
            )

    @settings(max_examples=500)
    @given(fields=_commands)
    def test_an_expressible_prod_deploy_only_triggers_the_protected_ci_job(
        self, fields: dict[str, object]
    ) -> None:
        """The positive half: prod *is* reachable, by exactly one kind of step.

        Without this, the property above would be satisfied by a control plane
        that simply refused every prod command — true, and useless. A ``deploy``
        may only dispatch ``deploy.yml``; a ``release`` may dispatch it or push the
        tag that triggers it, and either way the plan records ``target = CI``
        because the deploy runs in Actions however it was triggered.

        ``bootstrap`` is excluded: it provisions a stage's deploy plumbing rather
        than deploying it, and is out-of-band by design (Requirement 4.2).
        """
        plan = _planned(fields)
        if plan is None or fields["stage"] != PROD_STAGE:
            return
        if fields["capability"] not in {Capability.DEPLOY, Capability.RELEASE}:
            return
        ops = {step.op for step in plan.steps}
        assert ops <= _CI_TRIGGERS, f"a {PROD_STAGE} plan may only trigger CI, not {ops}"
        assert plan.target is Target.CI

    def test_the_space_generated_is_not_one_of_refusals(self) -> None:
        """A strategy nothing could be planned from would pass both properties.

        Enumerates the spine of the space deterministically — every capability
        against both targets and every stage — and pins that it yields plans, that
        a local SAM deploy of a non-prod stage is among them (so the guarded branch
        is genuinely reachable), and that a prod plan is among them (so the
        positive half is not asserted about an empty set).
        """
        plans = [
            plan
            for capability in Capability
            for target in Target
            for stage in _STAGES
            if (
                plan := _planned(
                    {
                        "capability": capability,
                        "target": target,
                        "stage": stage,
                        "version": "v1.2.3",
                        "args": {REVIEWERS_ARG: ["User:1234"]},
                    }
                )
            )
            is not None
        ]
        local_deploys = [
            step for plan in plans for step in plan.steps if step.op in _LOCAL_SAM_MUTATIONS
        ]
        assert len(plans) >= len(Capability), "the enumerated spine must produce plans"
        assert local_deploys, "a local SAM deploy must be reachable, or nothing is guarded"
        assert {step.params["config_env"] for step in local_deploys} == {"dev"}
        assert any(plan.steps and plan.target is Target.CI for plan in plans)

    def test_a_locally_issued_release_trigger_is_not_a_local_prod_deploy(self) -> None:
        """Requirement 7.5: pushing the tag is local; the deploy it starts is not.

        Spelled out as an example because it is the one case the property's
        wording has to exclude explicitly, and a reader should be able to see
        *which* steps are allowed to run on this machine for a prod release.
        """
        plan = _planned(
            {
                "capability": Capability.RELEASE,
                "target": Target.LOCAL,
                "stage": PROD_STAGE,
                "version": "v1.2.3",
                "args": {},
            }
        )
        assert plan is not None
        assert [step.op for step in plan.steps] == [Op.GIT_TAG, Op.GIT_PUSH]
        assert plan.target is Target.CI, (
            "the deploy runs in CI even though the tag was pushed here"
        )
        assert all("config_env" not in step.params for step in plan.steps)


# -- the config-change space --------------------------------------------------
#
# Property 3 quantifies over "every config change", which is a ``config set``:
# ``config show`` changes nothing and ``bootstrap`` / ``deploy`` / ``release``
# change no configuration location at all (the second test below is what pins
# *that* down). The key is the existing ``_config_keys`` — which generates both an
# absolute repo-scoped SSM path and a bare ``samconfig.toml`` parameter name, the
# two inputs the routing rule discriminates on — and the value is the existing
# ``_config_values``. ``target`` is generated even though ``config`` does not vary
# by it, so a future routing that *did* read it would be covered here rather than
# silently untested.

SECRET_IN_JSON: Final = "**********"
"""What Pydantic renders a ``SecretStr`` as in ``model_dump_json()``.

Asserted against the *serialized* plan rather than searched for in it: a masked
value must be absent from the serialization, and the only way to state that
without tripping over a generated value that happens to also occur in the path or
the stage is to read the one field it would have been in."""

_SANCTIONED_CONFIG_WRITES: frozenset[Op] = frozenset({Op.SAMCONFIG_PR, Op.SSM_PUT})
"""The two locations a config change may land in, and there is no third.

``Op.CONFIG_SHOW`` is the read, so it is not here. Both members are *writes*: one
opens a pull request against a tracked file, one performs an audited SSM put."""

_config_set_commands: st.SearchStrategy[dict[str, object]] = st.builds(
    lambda stage, key, value, target: {
        "capability": Capability.CONFIG,
        "target": target,
        "stage": stage,
        "args": {ACTION_ARG: CONFIG_SET, KEY_ARG: key, VALUE_ARG: value},
    },
    stage=_stages,
    key=_config_keys,
    value=_config_values,
    target=st.sampled_from(Target),
)


# -- Property 3: config-as-data ----------------------------------------------


class TestEveryConfigChangeLandsInOneOfTwoLocations:
    """**Property 3** — a pull request or an audited SSM write, never a third place.

    **Validates: Requirements 3.3, 3.4, 3.6**

    Asserted on ``op`` and ``params``, never on the rendered ``command`` string:
    the rendering is display-only and no executor parses it, so a plan that
    *rendered* a ``gh api … /pulls`` POST while telling the ``ConfigStore`` to do
    something else would satisfy a string search and violate the property. What the step
    means is what has to be in one of the two sanctioned places.
    """

    @settings(max_examples=300)
    @given(fields=_config_set_commands)
    def test_a_config_change_is_one_step_in_one_of_the_two_sanctioned_locations(
        self, fields: dict[str, object]
    ) -> None:
        """Both halves of the property: the shape, and the rule that chooses it.

        The shape alone would be satisfied by a planner that routed *everything*
        to one location; the routing rule alone would not say that nothing else
        can be emitted. An absolute repo-scoped SSM path is operational config and
        becomes the audited put; anything else is deploy-time config held in
        ``samconfig.toml`` and becomes the reviewed pull request.
        """
        plan = _planned(fields)
        if plan is None:
            return
        args = fields["args"]
        assert isinstance(args, dict)
        key = args[KEY_ARG]
        assert isinstance(key, str)

        assert len(plan.steps) == 1, "one config change, one write"
        step = plan.steps[0]
        assert step.executor == "config", (
            f"a config change reached the {step.executor!r} executor, "
            "which is not one of the two sanctioned locations"
        )
        assert step.op in _SANCTIONED_CONFIG_WRITES, f"{step.op} is a third configuration location"
        expected = Op.SSM_PUT if key.startswith("/") else Op.SAMCONFIG_PR
        assert step.op is expected, f"{key!r} was routed to {step.op}, expected {expected}"
        if step.op is Op.SSM_PUT:
            assert step.params["path"] == key, "the audited put must target the named path"
        else:
            assert step.params["key"] == key
            assert step.params["stage"] == fields["stage"], (
                "the pull request must edit the stage's parameter table, not another's"
            )

    @settings(max_examples=300)
    @given(fields=_config_set_commands)
    def test_the_ssm_write_masks_its_value_and_the_pull_request_does_not(
        self, fields: dict[str, object]
    ) -> None:
        """The asymmetry the design justifies, pinned so neither side can flip.

        An operational value is masked in the rendering and carried as a
        ``SecretStr``, so it is *absent from the serialized plan* rather than
        merely omitted by a renderer (Requirement 3.7) — ``--dry-run`` and
        ``--json`` cannot print it. A deploy-time value is not masked: it is bound
        for a public pull request against a tracked file, so hiding it would only
        make the plan a worse preview of the diff it opens.

        A property that asserted only "there are two locations" would let a
        regression mask the wrong one — a redacted PR title, or an SSM value
        printed into ``--json`` — without failing anything.

        The deploy-time half is stated for a **non-secret-shaped** key, which is
        the only kind that reaches this branch at all: a secret-shaped one is
        refused (Requirement 3.8), asserted by
        ``test_a_secret_shaped_deploy_time_key_is_refused_rather_than_rendered``.
        The two together are what closes the leak this property used to pin open —
        it previously asserted ``value in step.command`` for *any* generated key,
        secret-shaped included.
        """
        plan = _planned(fields)
        if plan is None:
            return
        args = fields["args"]
        assert isinstance(args, dict)
        value = args[VALUE_ARG]
        assert isinstance(value, str)

        step = plan.steps[0]
        serialized = json.loads(plan.model_dump_json())["steps"][0]
        if step.op is Op.SSM_PUT:
            carried = step.params["value"]
            assert isinstance(carried, SecretStr), (
                "an operational value must be carried as a SecretStr, or the "
                "serialized plan prints it"
            )
            assert carried.get_secret_value() == value, "the executor must still get the value"
            assert serialized["params"]["value"] == SECRET_IN_JSON
            assert MASK in step.command, "the rendered put must show the mask, not the value"
        else:
            assert step.params["value"] == value
            assert serialized["params"]["value"] == value, (
                "a deploy-time value belongs in the plan: it is the diff being proposed"
            )
            assert value in step.command
            assert not is_secret_name(str(step.params["key"])), (
                "a secret-shaped key must never reach the pull-request branch"
            )

    @settings(max_examples=300)
    @given(stage=_stages, key=_secret_param_names, value=_distinctive_values)
    def test_a_secret_shaped_deploy_time_key_is_refused_rather_than_rendered(
        self, stage: str, key: str, value: str
    ) -> None:
        """Requirement 3.8: no plan, exit ``2``, and the value nowhere at all.

        The deploy-time branch cannot mask: ``key=value`` is the pull request's
        title and, once merged, a line in a tracked file, so masking the preview
        would hide the leak rather than close it. Refusal is therefore the
        contract, and it is asserted as *three* things, because dropping any one of
        them would still let the value out: the error is a usage error (exit ``2``,
        so nothing reached an executor), no ``Plan`` exists to render, and the
        value does not appear in the error the operator sees either.

        The value space is ``_distinctive_values`` rather than ``_config_values``
        for the absence assertion to mean anything: a one-character value like
        ``"I"`` occurs inside the hint's own English prose, and shrinking finds it
        immediately — a true substring match that is not a leak. Eight characters
        and up, a match is the value and nothing else.

        The message is also asserted to name the key and to point at an SSM path —
        the substring predicate has false positives by design, and an operator who
        hits one has to be able to tell instantly what happened and where the value
        does belong.
        """
        fields: dict[str, object] = {
            "capability": Capability.CONFIG,
            "stage": stage,
            "args": {ACTION_ARG: CONFIG_SET, KEY_ARG: key, VALUE_ARG: value},
        }
        with pytest.raises(UsageError) as raised:
            Dispatcher().plan(Command.model_validate(fields))

        error = raised.value
        assert error.exit_code == ExitCode.USAGE_ERROR
        assert _planned(fields) is None, "a refused write must leave no plan to render"
        assert value not in str(error), "the refusal must not quote the value it refused"
        assert key in str(error), "the operator has to be told which key was refused"
        assert f"/{SSM_ROOT_SEGMENT}/{stage}/" in str(error), (
            "a refusal that does not say where the value belongs is a dead end"
        )
        assert SAMCONFIG_FILE in str(error), (
            "the false-positive case needs the tracked file named to be recognisable"
        )

    @settings(max_examples=300)
    @given(fields=_commands)
    def test_no_other_capability_writes_configuration_anywhere(
        self, fields: dict[str, object]
    ) -> None:
        """The containment half, over the whole command space rather than ``config``.

        Two directions, because each admits a different regression: a sanctioned
        write reached through some other executor would put config-as-data outside
        the ``ConfigStore``, and a ``config`` step carrying some *other* op would
        be the third location arriving from the inside. Only the read is allowed
        to join the two writes.
        """
        plan = _planned(fields)
        if plan is None:
            return
        for step in plan.steps:
            if step.op in _SANCTIONED_CONFIG_WRITES:
                assert step.executor == "config", (
                    f"{step.op} was routed to the {step.executor!r} executor"
                )
            if step.executor == "config":
                assert step.op in _SANCTIONED_CONFIG_WRITES | {Op.CONFIG_SHOW}, (
                    f"{step.op} is a config operation outside the two sanctioned writes"
                )

    def test_both_sanctioned_locations_are_actually_reachable(self) -> None:
        """A planner that refused every config change would pass the above.

        Pins one key of each kind onto the branch it must take, so neither half of
        the routing rule can become unreachable without this failing — including
        ``BdoRegions``, the single active-region toggle that Requirement 3.6 names
        as belonging in a pull request (ADR-0036).
        """
        operational = _planned(
            {
                "capability": Capability.CONFIG,
                "stage": "dev",
                "args": {
                    ACTION_ARG: CONFIG_SET,
                    KEY_ARG: f"/{SSM_ROOT_SEGMENT}/dev/domain/api-domain-name",
                    VALUE_ARG: "api.example.com",
                },
            }
        )
        deploy_time = _planned(
            {
                "capability": Capability.CONFIG,
                "stage": "dev",
                "args": {ACTION_ARG: CONFIG_SET, KEY_ARG: "BdoRegions", VALUE_ARG: "NA"},
            }
        )
        assert operational is not None and deploy_time is not None
        assert [step.op for step in operational.steps] == [Op.SSM_PUT]
        assert [step.op for step in deploy_time.steps] == [Op.SAMCONFIG_PR]

    def test_a_path_outside_the_repo_scope_is_refused_rather_than_rerouted(self) -> None:
        """The escape hatch the routing rule must not open (Requirement 9.1).

        ``_config_keys`` generates only *valid* repo-scoped paths, so the property
        above never sees this case — and the tempting way to satisfy "two
        locations" for a bad path would be to fall through to the pull-request
        branch, quietly turning a rejected SSM name into a ``samconfig.toml``
        parameter called ``/bdo/dev/db/dsn``. It is a refusal instead.
        """
        assert (
            _planned(
                {
                    "capability": Capability.CONFIG,
                    "stage": "dev",
                    "args": {
                        ACTION_ARG: CONFIG_SET,
                        KEY_ARG: "/bdo/dev/db/dsn",
                        VALUE_ARG: "postgres://x",
                    },
                }
            )
            is None
        )


# -- the real workflow artefact -----------------------------------------------
#
# Property 4 is the one property here that spans two artefacts: the control
# plane's Python and a YAML file GitHub owns the semantics of. Neither half can
# state it alone, so both are read — the workflow from disk, the sent inputs from
# the real ``GitHubCli`` — and nothing about either is restated as a literal list
# of names, which is the only way the test can fail when they drift apart.

_REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
_WORKFLOW_DIR: Final = _REPO_ROOT / ".github" / "workflows"

_DISPATCH_FLAG: Final = "-f"
"""How ``gh workflow run`` is given one typed ``workflow_dispatch`` input."""


def workflow_document(workflow: str) -> dict[object, object]:
    """``workflow`` parsed from the real file in ``.github/workflows``.

    The one reader of a workflow file in the suite, shared with
    ``test_deploy_workflow.py`` so there is a single place that knows where the
    workflows live and how they parse.
    """
    document = yaml.safe_load((_WORKFLOW_DIR / workflow).read_text())
    assert isinstance(document, dict), f"{workflow} is not a YAML mapping"
    return document


def _workflow_triggers(workflow: str) -> dict[str, object]:
    """The ``on:`` block of ``workflow``, read from the real file on disk.

    YAML 1.1 reads the bare key ``on`` as the **boolean** ``True``, which is why
    the lookup tries both: the file is correct GitHub Actions YAML and it is
    ``yaml.safe_load`` that is idiosyncratic here, so the reader accommodates it
    rather than the workflow being quoted to suit the test.
    """
    document = workflow_document(workflow)
    triggers = document.get("on", document.get(True))
    assert isinstance(triggers, dict), f"{workflow} declares no on: block"
    return triggers


def _declared_dispatch_inputs(workflow: str) -> dict[str, dict[str, object]]:
    """The typed ``workflow_dispatch`` inputs ``workflow`` declares, by name."""
    dispatch = _workflow_triggers(workflow)["workflow_dispatch"]
    assert isinstance(dispatch, dict), f"{workflow} takes no workflow_dispatch inputs"
    inputs = dispatch["inputs"]
    assert isinstance(inputs, dict)
    return inputs


def _dispatched_inputs(step: PlanStep) -> dict[str, str]:
    """The inputs the control plane really sends for ``step``, from the real code path.

    Executes the planned step through the actual ``GitHubCli`` against a recording
    runner, then reads the ``-f key=value`` flags back out of the argument list it
    built. Derived rather than declared: a test that listed the names would still
    pass after someone renamed one in ``_input_flags``, which is precisely the
    drift this property exists to catch. No ``gh`` runs — the runner records.
    """
    runner = FakeRunner()
    GitHubCli(runner=runner).run_step(step)
    argv = runner.argvs[0]
    assert argv[:3] == [GH, "workflow", "run"], (
        f"a dispatch must invoke gh workflow run, not {argv}"
    )
    sent: dict[str, str] = {}
    for flag, pair in zip(argv, argv[1:], strict=False):
        if flag == _DISPATCH_FLAG:
            key, _, value = pair.partition("=")
            sent[key] = value
    return sent


def _dispatch_steps(fields: dict[str, object]) -> list[PlanStep]:
    """Every ``workflow_dispatch`` step the command fields plan, if any."""
    plan = _planned(fields)
    if plan is None:
        return []
    return [step for step in plan.steps if step.op is Op.GITHUB_RUN_WORKFLOW]


# -- Property 4: dispatch fidelity -------------------------------------------


class TestTheWorkflowAcceptsEverythingTheControlPlaneSends:
    """**Property 4** — the Actions UI and the wizard dispatch the identical run.

    **Validates: Requirements 8.3**

    Quantified over the whole generated command space rather than over the two
    capabilities that happen to dispatch today, so a third path to
    ``workflow_dispatch`` is covered the moment it exists.
    """

    @settings(max_examples=300)
    @given(fields=_commands)
    def test_every_sent_input_is_one_the_workflow_declares(
        self, fields: dict[str, object]
    ) -> None:
        """The superset claim, with both sides read from the real artefacts.

        Renaming ``stage`` in ``deploy.yml`` fails this because the sent name is no
        longer declared; renaming it in ``_input_flags`` fails it for the same
        reason from the other side. Neither name appears in this test.
        """
        for step in _dispatch_steps(fields):
            workflow = step.params["workflow"]
            assert isinstance(workflow, str)
            declared = set(_declared_dispatch_inputs(workflow))
            sent = _dispatched_inputs(step)
            assert set(sent) <= declared, (
                f"the control plane sends {sorted(set(sent) - declared)}, which "
                f"{workflow} does not declare as a workflow_dispatch input"
            )
            assert sent, "a dispatch that sent no input would satisfy any superset"
            assert sent["stage"] == step.params["stage"], (
                "the dispatched stage must be the planned one"
            )
            if VERSION_PARAM in step.params:
                assert sent[VERSION_PARAM] == step.params[VERSION_PARAM]
            else:
                assert VERSION_PARAM not in sent

    @settings(max_examples=300)
    @given(fields=_commands)
    def test_the_workflow_the_control_plane_targets_is_the_file_that_exists(
        self, fields: dict[str, object]
    ) -> None:
        """A dispatch at a workflow that is not there would trigger nothing at all.

        The superset property is satisfiable by a plan targeting a file nobody
        ever added — ``_declared_dispatch_inputs`` would simply fail to open it, so
        this states the requirement directly: whatever ``DEPLOY_WORKFLOW`` names,
        that file is on disk and offers ``workflow_dispatch``.
        """
        for step in _dispatch_steps(fields):
            assert step.params["workflow"] == DEPLOY_WORKFLOW
            assert (_WORKFLOW_DIR / DEPLOY_WORKFLOW).is_file()
            assert "workflow_dispatch" in _workflow_triggers(DEPLOY_WORKFLOW)

    @settings(max_examples=300)
    @given(fields=_commands)
    def test_no_input_the_workflow_insists_on_is_left_unsent(
        self, fields: dict[str, object]
    ) -> None:
        """The other direction of the superset: every *required* input is sent.

        The claim is stated against ``required`` alone, not against
        ``required`` **and** no ``default``. The narrower form is what the
        workflow's own dispatch mechanics insist on — an input with a default is
        one ``gh`` can fill in for itself — but it is vacuous against
        ``deploy.yml`` today (``stage`` is required *and* defaulted), and an
        assertion that no example can reach guards nothing. Quantifying over every
        ``required`` input makes the same test non-vacuous: ``stage`` is required,
        so this genuinely asserts that the control plane sends it, and it fails
        both if ``stage`` stops being dispatched and if a second required input is
        declared and left unsent — whether or not it carries a default.

        Sending a defaulted required input is also the stronger behaviour to hold
        the control plane to, which is why the property is worth stating this way
        round: relying on the default would mean the wizard and the Actions UI
        agree only as long as the default does not change, whereas an explicitly
        sent value is the same run either way (Requirement 8.3).

        The ``required``-with-no-default case is kept as the reason this matters at
        all, and it is *implied* by the assertion below rather than tested
        separately — it is a subset of the inputs quantified over. What it
        contributes is the failure mode: such an input would make every wizard
        dispatch fail at ``gh`` while the superset property still held, so the run
        the Actions UI starts and the run the wizard starts would no longer be the
        same reachable run.
        """
        for step in _dispatch_steps(fields):
            workflow = step.params["workflow"]
            assert isinstance(workflow, str)
            sent = _dispatched_inputs(step)
            required = {
                name
                for name, declaration in _declared_dispatch_inputs(workflow).items()
                if declaration.get("required")
            }
            assert required, (
                f"{workflow} marks no workflow_dispatch input required, so this "
                "assertion has nothing to check — see the non-vacuity test below"
            )
            for name in sorted(required):
                undefaulted = "default" not in _declared_dispatch_inputs(workflow)[name]
                assert name in sent, (
                    f"{workflow} marks {name!r} required"
                    f"{' with no default' if undefaulted else ''}, and the control "
                    "plane does not send it"
                )

    def test_a_dispatch_is_reachable_and_carries_both_inputs(self) -> None:
        """A generated space with no dispatch in it would pass all three vacuously.

        Pins the two capabilities that reach ``workflow_dispatch`` today onto a
        concrete dispatch, and pins that the optional input is genuinely
        exercised — otherwise the ``version`` branch of the property above would
        never run and a rename there would go unnoticed.
        """
        ci_deploy = _dispatch_steps(
            {"capability": Capability.DEPLOY, "target": Target.CI, "stage": PROD_STAGE}
        )
        release = _dispatch_steps(
            {
                "capability": Capability.RELEASE,
                "target": Target.CI,
                "stage": PROD_STAGE,
                "version": "v1.2.3",
                "args": {DISPATCH_ARG: True},
            }
        )
        assert len(ci_deploy) == 1 and len(release) == 1
        assert _dispatched_inputs(ci_deploy[0]) == {"stage": PROD_STAGE}
        assert _dispatched_inputs(release[0]) == {"stage": PROD_STAGE, VERSION_PARAM: "v1.2.3"}
        declared = _declared_dispatch_inputs(DEPLOY_WORKFLOW)
        assert set(declared) == {"stage", VERSION_PARAM}
        # And pins the other direction's non-vacuity: the required-input property
        # above quantifies over the inputs deploy.yml marks required, so it asserts
        # nothing unless at least one of them is required. ``stage`` is, and is
        # sent — which is the example that makes that property bite today.
        assert declared["stage"].get("required") is True
        assert declared[VERSION_PARAM].get("required") is False


# -- an executor that cannot be used ------------------------------------------


class Untouchable:
    """A stand-in for an executor that fails loudly if anything touches it.

    The weak way to test dry-run purity is to record what a fake was asked to do
    and assert the record is empty; the strong way is for the fake to have no
    usable behaviour at all, so a dry run that reached it could not also *succeed*.
    Both are asserted below — the raise catches a reached executor even where the
    caller swallows exceptions, and the empty record catches a call that was
    somehow tolerated.

    Every attribute resolves to the same exploding callable, which is what lets one
    class stand in for all four executor Protocols: their method sets differ, none
    of the methods may be called, and enumerating fifteen raising stubs would only
    invite one of them to be forgotten.
    """

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., object]:
        def explode(*_args: object, **_kwargs: object) -> object:
            self.touched.append(name)
            raise AssertionError(f"a dry run reached the executor: {name}()")

        return explode


class Executors(NamedTuple):
    """The four untouchable executors, and whether any of them was touched."""

    sam: Untouchable
    github: Untouchable
    git: Untouchable
    config: Untouchable

    @property
    def touched(self) -> list[str]:
        """Every executor method called, across all four — empty for a pure run."""
        return [name for executor in self for name in executor.touched]


_mutating_intents = _intents.filter(
    lambda intent: intent.capability is not Capability.CONFIG or intent.action != CONFIG_SHOW
)
"""The intents a TUI *preview* is a preview of.

``config show`` is the one plan that requires no confirmation, so the TUI runs it
on reaching the review step rather than previewing it — a merged read, which is
none of the mutations the property enumerates. Excluding it here keeps the TUI
property about previews;
``test_the_tui_read_is_the_one_flow_that_legitimately_reaches_an_executor`` pins
the exclusion so it cannot widen."""


def _untouchable_executors() -> Executors:
    return Executors(Untouchable(), Untouchable(), Untouchable(), Untouchable())


def _untouchable_plane(executors: Executors) -> ControlPlane:
    """A ``ControlPlane`` from the **real** composition root, wired to explode.

    ``build_control_plane()`` is what the front-ends call, so the assembly under
    test is the production one; only the four adapters are substituted. Typed
    through ``cast`` because ``Untouchable`` satisfies the four Protocols by
    ``__getattr__`` rather than by declaring their methods — the cast is the
    test's statement that it knows these stand in for executors and intends never
    to call one.
    """
    return build_control_plane(
        sam=cast("SamExecutor", executors.sam),
        github=cast("GitHubExecutor", executors.github),
        git=cast("GitExecutor", executors.git),
        config=cast("ConfigStore", executors.config),
    )


# -- Property 5: dry-run purity ----------------------------------------------


class TestADryRunTouchesNothing:
    """**Property 5** — a preview renders the plan and reaches no tool at all.

    **Validates: Requirements 10.5**

    Purity here is structural rather than a flag checked at run time: ``plan()``
    holds no executor reference and ``execute()`` is the only method that can
    reach one, so a dry run is inert because it stops earlier — not because
    something remembered to look at ``dry_run``. These properties are what keep
    that true as the planner grows.
    """

    @settings(max_examples=300)
    @given(fields=_commands)
    def test_planning_any_command_reaches_no_executor(self, fields: dict[str, object]) -> None:
        """The core claim, over the whole ``Command`` field space.

        ``plan()`` is called on a ``Dispatcher`` that *has* all four executors
        injected, so this is not satisfied by there being nothing to reach: the
        executors are present and are still never touched.
        """
        executors = _untouchable_executors()
        dispatcher = _untouchable_plane(executors).dispatcher
        with contextlib.suppress(UsageError):
            # A refusal is one of the outcomes quantified over, not a case to
            # skip: an intent no plan expresses must also reach no executor.
            dispatcher.plan(Command.model_validate({**fields, "dry_run": True}))
        assert executors.touched == []

    @settings(max_examples=100)
    @given(intent=_intents)
    def test_a_dry_run_through_the_cli_mutates_nothing_even_with_yes(
        self, intent: GeneratedIntent
    ) -> None:
        """The whole front-end, driven for real, with ``--dry-run`` **and** ``--yes``.

        ``--yes`` is deliberate: without it the confirmation gate would stop every
        mutating command anyway, and the test would prove Requirement 10.4 twice
        over instead of Requirement 10.5 once. Confirmed *and* dry means the only
        thing left standing between the command and a mutation is the dry-run
        contract itself.

        The exit code is asserted too, because "touched nothing" is also true of a
        crash: a dry run succeeds (exit ``0``) unless the intent is one no plan can
        express, which is a usage error (exit ``2``) — and either way nothing ran.
        """
        executors = _untouchable_executors()
        # `config show` is the one read-only subcommand and deliberately offers no
        # `--yes`, so passing it would make Click refuse the invocation and the
        # example would prove nothing. It confirms itself in the front-end, which
        # is the same starting position the flag buys for the others.
        confirmable = not (intent.capability is Capability.CONFIG and intent.action == CONFIG_SHOW)
        code = main(
            [*intent.argv(), "--dry-run", *(["--yes"] if confirmable else [])],
            plane=_untouchable_plane(executors),
        )
        assert executors.touched == [], f"a dry run reached {executors.touched}"
        assert code in {int(ExitCode.SUCCESS), int(ExitCode.USAGE_ERROR)}

    @settings(max_examples=10, deadline=None)
    @given(intent=_mutating_intents)
    def test_a_tui_preview_mutates_nothing(self, intent: GeneratedIntent) -> None:
        """The TUI's preview is its review screen: reaching it must change nothing.

        The TUI has no ``--dry-run`` flag — reviewing the plan *is* the preview —
        so the equivalent of a dry run is driving the real widgets up to the
        confirmation step and stopping, which is exactly what ``intent.steps()``
        does. The session's exit code says it stopped there: a mutating plan was
        produced and not confirmed.

        Fewer examples than the CLI property above because each one runs a Textual
        pilot; the CLI half carries the breadth.
        """
        executors = _untouchable_executors()
        driven = drive(_untouchable_plane(executors).dispatcher, *intent.steps())
        assert executors.touched == [], f"a TUI preview reached {executors.touched}"
        assert int(driven.app.exit_code) in {
            int(ExitCode.CONFIRMATION_REQUIRED),
            int(ExitCode.USAGE_ERROR),
        }

    def test_the_tui_read_is_the_one_flow_that_legitimately_reaches_an_executor(self) -> None:
        """Why ``config show`` is excluded above, stated rather than left implicit.

        ``config show`` is the only plan in the whole space that needs no
        confirmation, so the TUI runs it on reaching the review step instead of
        previewing it — and that is correct: the property forbids a *mutation*, and
        a merged read is none of the things it lists. Pinned so the exclusion stays
        narrow: exactly one executor is reached, it is the ``ConfigStore``, and the
        three that could mutate anything are not touched at all.
        """
        executors = _untouchable_executors()
        reading = GeneratedIntent(
            capability=Capability.CONFIG,
            stage="dev",
            target=Target.LOCAL,
            version="v1.2.3",
            sync=False,
            dispatch=False,
            action=CONFIG_SHOW,
            key="BdoRegions",
            value="NA",
        )
        drive(_untouchable_plane(executors).dispatcher, *reading.steps())
        assert executors.config.touched == ["run_step"], "the read, once, and nothing else"
        assert executors.sam.touched == []
        assert executors.git.touched == []
        assert executors.github.touched == []

    def test_a_dry_run_renders_the_plan_it_did_not_run(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Half the property is that the plan *is* rendered, not merely skipped.

        A front-end that refused every dry run, or printed nothing, would satisfy
        every purity assertion above. Pinned as an example because it is a claim
        about one concrete rendering rather than about all of them.
        """
        executors = _untouchable_executors()
        code = main(
            ["deploy", "--stage", "dev", "--dry-run", "--yes"],
            plane=_untouchable_plane(executors),
        )
        printed = capsys.readouterr().out
        assert code == int(ExitCode.SUCCESS)
        assert executors.touched == []
        assert "sam deploy --config-env dev" in printed, "the dry run must show the plan"
        assert "nothing executed" in printed

    def test_assembling_the_control_plane_creates_no_client_and_runs_no_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The composition root is reached before ``--dry-run`` is known about.

        Both front-ends build their wiring at start-up, so if *assembling* the real
        adapters created a boto3 client or spawned a process, a dry run would have
        performed I/O before the planner ever ran — and none of the properties
        above would notice, since they substitute the adapters away.

        Honest about its reach: patching the two factories the real adapters would
        use proves that **these** paths are not taken (no SSM client is created
        lazily-or-otherwise, no subprocess is spawned). It does not prove the
        absence of all I/O in-process — a future adapter could read a file or open
        a socket by some other route, which no in-process assertion can rule out.
        What it does do is fail if the documented laziness is undone.
        """

        def no_client(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("assembling the control plane created a boto3 client")

        def no_process(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("assembling the control plane spawned a subprocess")

        # Patched on the modules the adapters call through (``boto3.client`` and
        # ``subprocess.run``), so nothing about the production import graph has to
        # be rearranged to make the assertion possible.
        monkeypatch.setattr(boto3, "client", no_client)
        monkeypatch.setattr(subprocess, "run", no_process)

        assert build_dispatcher() is not None
        assert build_control_plane().github is not None
