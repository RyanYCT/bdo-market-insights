"""Tests for bdo_common.pricing.

The numbers asserted here are the normative worked examples from
``.kiro/specs/v3/domain-model.md``. If a number changes, the spec is the
thing to change first.
"""

from __future__ import annotations

from typing import Any

import pytest

from bdo_common import pricing
from bdo_common.pricing import TaxConfig

# Deboreka Ring base prices used in the domain-model worked example.
CLEAN = 448_000_000  # base_price[0]
PRI = 1_020_000_000  # base_price[1]


@pytest.fixture
def rates() -> dict[str, Any]:
    return pricing.load_rates()


@pytest.fixture
def model(rates: dict[str, Any]) -> pricing.AccessoryV1:
    return pricing.build_accessory_v1(rates)


# --------------------------------------------------------------------------- #
# Probability curve — anchor points verified in domain-model.md
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("transition", "stack", "expected"),
    [
        ("0->1", 18, 0.70),  # soft-cap breakpoint
        ("1->2", 40, 0.50),
        ("2->3", 44, 0.40),  # soft_cap_rate authoritative (not 0.405)
        ("3->4", 110, 0.30),
        ("4->5", 0, 0.005),  # no soft cap -> linear at base
    ],
)
def test_probability_anchor_points(
    model: pricing.AccessoryV1, transition: str, stack: int, expected: float
) -> None:
    assert model.success_probability(transition, stack) == pytest.approx(expected)


def test_probability_linear_below_soft_cap(model: pricing.AccessoryV1) -> None:
    # 0->1 at stack 10: base 0.25 + growth1 0.025 * 10 = 0.50
    assert model.success_probability("0->1", 10) == pytest.approx(0.50)


def test_probability_reaches_and_caps_at_hard_cap(model: pricing.AccessoryV1) -> None:
    # 0->1 hard cap stack 58: 0.70 + 0.005 * (58 - 18) = 0.90
    assert model.success_probability("0->1", 58) == pytest.approx(0.90)
    # Beyond the hard cap it is clamped to max_rate (0.90).
    assert model.success_probability("0->1", 100) == pytest.approx(0.90)


# --------------------------------------------------------------------------- #
# Expected cost — Model A1
# --------------------------------------------------------------------------- #
def test_expected_enhance_cost_deboreka_0_to_1() -> None:
    # (base_price[0] + base_price[0]) / 0.70 = 1.28e9
    cost = pricing.expected_enhance_cost(CLEAN, CLEAN, 0.70)
    assert cost == pytest.approx(1_280_000_000)


def test_expected_enhance_cost_rejects_zero_probability() -> None:
    with pytest.raises(ValueError, match="probability"):
        pricing.expected_enhance_cost(CLEAN, CLEAN, 0.0)


# --------------------------------------------------------------------------- #
# Tax / net sale rate — three verified 100M cases
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("tax", "expected_net_on_100m"),
    [
        (TaxConfig(value_pack=True, ring=True, family_fame=7000), 88_725_000),
        (TaxConfig(value_pack=True, ring=False, family_fame=7000), 85_475_000),
        (TaxConfig(value_pack=False, ring=False, family_fame=7000), 65_975_000),
    ],
)
def test_net_rate_worked_cases(
    rates: dict[str, Any], tax: TaxConfig, expected_net_on_100m: float
) -> None:
    rate = pricing.net_rate(tax, rates["constants"])
    assert rate * 100_000_000 == pytest.approx(expected_net_on_100m)


def test_family_fame_bonus_picks_highest_met_threshold(rates: dict[str, Any]) -> None:
    table = rates["constants"]["family_fame_bonus"]
    assert pricing.family_fame_bonus(0, table) == pytest.approx(0.0)
    assert pricing.family_fame_bonus(5000, table) == pytest.approx(0.010)  # 4000 tier
    assert pricing.family_fame_bonus(7000, table) == pytest.approx(0.015)


# --------------------------------------------------------------------------- #
# enhancement_analysis — Deboreka worked example
# --------------------------------------------------------------------------- #
def test_analysis_personal_verdict_buy() -> None:
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI}, stack=18, intent="personal")
    tiers = result["transitions"]
    assert len(tiers) == 1
    tier = tiers[0]
    assert (tier["sid_from"], tier["sid_to"]) == (0, 1)
    assert tier["p"] == pytest.approx(0.70)
    assert tier["expected_cost"] == pytest.approx(1_280_000_000)
    assert tier["net_rate"] == pytest.approx(1.0)
    assert tier["revenue"] == pytest.approx(PRI)
    # cost (1.28e9) > target (1.02e9) -> not worth enhancing yourself
    assert tier["verdict"] == "buy"


def test_analysis_resale_verdict_loss() -> None:
    result = pricing.enhancement_analysis(
        {0: CLEAN, 1: PRI},
        stack=18,
        intent="resale",
        tax=TaxConfig(value_pack=True, ring=False, family_fame=7000),
    )
    tier = result["transitions"][0]
    assert tier["net_rate"] == pytest.approx(0.85475)
    assert tier["revenue"] == pytest.approx(871_845_000)
    assert tier["expected_profit"] < 0
    assert tier["verdict"] == "loss"


def test_analysis_uses_default_stack_when_unspecified() -> None:
    # default_stack for 0->1 is 18, so omitting stack reproduces p=0.70.
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI}, intent="personal")
    tier = result["transitions"][0]
    assert tier["stack"] == 18
    assert tier["p"] == pytest.approx(0.70)


def test_analysis_omits_transitions_with_missing_prices() -> None:
    # Missing sid:2 -> 0->1 stays, every transition needing sid:2 drops out.
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI, 3: 5_000_000_000})
    pairs = [(t["sid_from"], t["sid_to"]) for t in result["transitions"]]
    assert pairs == [(0, 1)]


def test_analysis_cumulative_cost_first_tier_equals_expected_cost() -> None:
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI}, stack=18)
    tier = result["transitions"][0]
    assert tier["cumulative_cost"] == pytest.approx(tier["expected_cost"])


def test_analysis_best_tier_is_highest_roi() -> None:
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI})
    assert result["best_tier"] is result["transitions"][0]


# --------------------------------------------------------------------------- #
# Model registry (ADR-0012)
# --------------------------------------------------------------------------- #
def test_accessory_v1_is_registered() -> None:
    assert "accessory_v1" in pricing.available_models()


def test_get_model_unknown_raises(rates: dict[str, Any]) -> None:
    with pytest.raises(KeyError, match="unknown enhancement model"):
        pricing.get_model("does_not_exist", rates)
