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


# --------------------------------------------------------------------------- #
# accessory_cron_v1 — Markov chain (Option B), worked numbers from domain-model
# --------------------------------------------------------------------------- #
# Real Deboreka Ring market snapshot (sid 0..5); cron stone flat 3,000,000.
DEBO: dict[int, float] = {
    0: 453_000_000,
    1: 1_170_000_000,
    2: 3_540_000_000,
    3: 9_450_000_000,
    4: 28_700_000_000,
    5: 186_000_000_000,
}


@pytest.fixture
def cron_model(rates: dict[str, Any]) -> pricing.AccessoryCronV1:
    return pricing.build_accessory_cron_v1(rates)


def test_accessory_cron_v1_is_registered() -> None:
    assert "accessory_cron_v1" in pricing.available_models()


@pytest.mark.parametrize(
    ("transition", "expected_attempts"),
    [
        ("0->1", 1.428571),  # 1 / 0.70
        ("1->2", 2.571429),
        ("2->3", 4.042857),
        ("3->4", 7.106667),
        ("4->5", 765.690667),  # PROVISIONAL: rides on p(4->5)=0.005
    ],
)
def test_cron_first_passage_attempts(
    cron_model: pricing.AccessoryCronV1, transition: str, expected_attempts: float
) -> None:
    # F_k = (1 + drop*(1-p_k)*F_{k-1}) / p_k, evaluated at each default stack.
    stack = cron_model.default_stack(transition)
    cost = cron_model.attempt_cost(transition, DEBO, stack)
    assert cost.expected_attempts == pytest.approx(expected_attempts, rel=1e-5)


def test_cron_attempt_cost_is_fuel_plus_cron(cron_model: pricing.AccessoryCronV1) -> None:
    # cost(0->1) = F_0 * (base_price[0] + cron_stone_price) = 1.428571 * 456M.
    cost = cron_model.attempt_cost("0->1", DEBO, cron_model.default_stack("0->1"))
    assert cost.cron_stone_price == pytest.approx(3_000_000)
    assert cost.expected_cost == pytest.approx(651_428_571.43)


def test_cron_cumulative_is_additive() -> None:
    # cumulative(->2) = base[0] + cost(0->1) + cost(1->2)
    #                 = 453M + 651,428,571.43 + 1,172,571,428.57 = 2,277,000,000.
    result = pricing.enhancement_analysis(DEBO, model_id="accessory_cron_v1")
    tiers = result["transitions"]
    assert len(tiers) == 5
    assert tiers[1]["cumulative_cost"] == pytest.approx(2_277_000_000)


def test_cron_verdicts_enhance_low_buy_pen() -> None:
    result = pricing.enhancement_analysis(DEBO, model_id="accessory_cron_v1", intent="personal")
    by_tier = {(t["sid_from"], t["sid_to"]): t for t in result["transitions"]}
    # Low tiers are cheaper to build than to buy.
    assert by_tier[(0, 1)]["verdict"] == "enhance"
    assert by_tier[(3, 4)]["verdict"] == "enhance"
    # PEN: ~349B expected cost > 186B market -> buy.
    pen = by_tier[(4, 5)]
    assert pen["expected_cost"] > DEBO[5]
    assert pen["verdict"] == "buy"


def test_accessory_v1_reports_zero_cron_cost() -> None:
    # Regression: the no-cron model leaves cron_stone_price at 0.
    result = pricing.enhancement_analysis({0: CLEAN, 1: PRI}, stack=18)
    assert result["transitions"][0]["cron_stone_price"] == pytest.approx(0.0)
