"""BDO accessory enhancement economics.

Pure domain math for the ``accessory_v1`` model (Model A1): no AWS
imports, no I/O beyond reading the bundled ``rates.json``. The normative
specification is ``.kiro/specs/v3/domain-model.md``; the worked numbers
in that document are asserted by ``tests/unit/test_pricing.py``.

Models are pluggable via a small registry (ADR-0012): register a factory
under a ``model_id`` and add a matching entry to ``rates.json``; callers
go through :func:`enhancement_analysis` and never name a concrete model.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

Intent = Literal["personal", "resale"]

#: Bundled reference data, shipped inside the Lambda layer next to this module.
_RATES_PATH = Path(__file__).with_name("rates.json")


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TransitionCurve:
    """Probability-curve parameters for one ``sid -> sid+1`` transition.

    Piecewise-linear in failstack ``stack`` (see :func:`success_probability`).
    ``soft_cap_*`` and ``growth2`` are ``None`` for transitions without a
    soft cap (e.g. ``4->5``). ``hard_cap_stack`` is reference data only and
    is not used by the v1 probability formula.
    """

    base: float
    growth1: float
    soft_cap_stack: int | None
    soft_cap_rate: float | None
    growth2: float | None
    hard_cap_stack: int | None
    max_rate: float
    default_stack: int

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TransitionCurve:
        """Build a curve from one ``rates.json`` transition entry."""

        def opt_int(v: Any) -> int | None:
            return None if v is None else int(v)

        def opt_float(v: Any) -> float | None:
            return None if v is None else float(v)

        return cls(
            base=float(d["base"]),
            growth1=float(d["growth1"]),
            soft_cap_stack=opt_int(d["soft_cap_stack"]),
            soft_cap_rate=opt_float(d["soft_cap_rate"]),
            growth2=opt_float(d["growth2"]),
            hard_cap_stack=opt_int(d["hard_cap_stack"]),
            max_rate=float(d["max_rate"]),
            default_stack=int(d["default_stack"]),
        )


@dataclass(frozen=True)
class TaxConfig:
    """Marketplace tax modifiers. Defaults match ``domain-model.md``."""

    value_pack: bool = True
    ring: bool = False
    family_fame: int = 7000


@dataclass(frozen=True)
class AttemptCost:
    """Per-transition cost breakdown produced by a model."""

    p: float
    expected_attempts: float
    input_item_price: float
    clean_stone_price: float
    expected_cost: float


def load_rates(path: Path = _RATES_PATH) -> dict[str, Any]:
    """Load and parse the bundled (or a supplied) ``rates.json``."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# --------------------------------------------------------------------------- #
# Core math (model-agnostic helpers)
# --------------------------------------------------------------------------- #
def success_probability(curve: TransitionCurve, stack: int) -> float:
    """Per-attempt success probability at failstack ``stack``.

    Piecewise-linear, hard-capped at ``max_rate`` (0.90):

        p = base + growth1 * stack                       if stack < soft_cap_stack
          = soft_cap_rate + growth2 * (stack - soft_cap_stack)   otherwise

    ``soft_cap_rate`` is authoritative *at and above* the breakpoint, so a
    small discontinuity versus ``base + growth1 * soft_cap_stack`` is
    accepted (domain-model.md). Transitions without a soft cap
    (``soft_cap_stack is None``) are linear everywhere.
    """
    soft_stack = curve.soft_cap_stack
    if soft_stack is None or stack < soft_stack:
        p = curve.base + curve.growth1 * stack
    else:
        rate, g2 = curve.soft_cap_rate, curve.growth2
        if rate is None or g2 is None:
            # Malformed curve (soft_cap_stack set without rate/growth2);
            # fall back to the linear branch rather than crash.
            p = curve.base + curve.growth1 * stack
        else:
            p = rate + g2 * (stack - soft_stack)
    return min(p, curve.max_rate)


def expected_enhance_cost(input_item_price: float, clean_price: float, p: float) -> float:
    """Expected silver to land one ``sid -> sid+1`` upgrade (Model A1).

    Each attempt consumes one clean (``sid:0``) copy and risks the
    in-progress item; failure destroys it. Expected attempts is ``1 / p``,
    so cost is ``(input_item_price + clean_price) / p``.
    """
    if p <= 0:
        raise ValueError("success probability must be > 0 to compute expected cost")
    return (input_item_price + clean_price) / p


def family_fame_bonus(fame: int, table: Mapping[str, float]) -> float:
    """Marketplace bonus for ``fame``: the highest tier threshold it meets."""
    return max((v for k, v in table.items() if fame >= int(k)), default=0.0)


def net_rate(tax: TaxConfig, constants: Mapping[str, Any]) -> float:
    """Fraction of a sale retained after tax, given marketplace modifiers.

    net_rate = (1 - base_tax) * (1 + value_pack + ring + family_fame)
    """
    base = 1.0 - float(constants["base_tax"])
    bonus = 0.0
    if tax.value_pack:
        bonus += float(constants["value_pack_bonus"])
    if tax.ring:
        bonus += float(constants["rich_merchant_ring_bonus"])
    bonus += family_fame_bonus(tax.family_fame, constants["family_fame_bonus"])
    return base * (1.0 + bonus)


# --------------------------------------------------------------------------- #
# Model protocol + registry (ADR-0012)
# --------------------------------------------------------------------------- #
class EnhancementModel(Protocol):
    """Behaviour every pricing model exposes to :func:`enhancement_analysis`."""

    model_id: str

    def transitions(self) -> list[str]: ...

    def default_stack(self, transition: str) -> int: ...

    def success_probability(self, transition: str, stack: int) -> float: ...

    def attempt_cost(
        self, transition: str, prices: Mapping[int, float], stack: int
    ) -> AttemptCost: ...


ModelFactory = Callable[[Mapping[str, Any]], EnhancementModel]

_FACTORIES: dict[str, ModelFactory] = {}


def register_model(model_id: str, factory: ModelFactory) -> None:
    """Register ``factory`` (built from a ``rates`` mapping) under ``model_id``."""
    _FACTORIES[model_id] = factory


def get_model(model_id: str, rates: Mapping[str, Any]) -> EnhancementModel:
    """Instantiate the model registered as ``model_id`` from ``rates`` data."""
    try:
        factory = _FACTORIES[model_id]
    except KeyError:
        raise KeyError(f"unknown enhancement model: {model_id!r}") from None
    return factory(rates)


def available_models() -> list[str]:
    """Currently registered model ids."""
    return list(_FACTORIES)


def _parse_transition(transition: str) -> tuple[int, int]:
    sid_from, sid_to = transition.split("->")
    return int(sid_from), int(sid_to)


# --------------------------------------------------------------------------- #
# accessory_v1 (Model A1)
# --------------------------------------------------------------------------- #
class AccessoryV1:
    """Market-input, probability-weighted accessory model (no cron)."""

    model_id = "accessory_v1"

    def __init__(self, curves: Mapping[str, TransitionCurve]) -> None:
        self._curves = dict(curves)

    def transitions(self) -> list[str]:
        return list(self._curves)

    def default_stack(self, transition: str) -> int:
        return self._curves[transition].default_stack

    def success_probability(self, transition: str, stack: int) -> float:
        return success_probability(self._curves[transition], stack)

    def attempt_cost(
        self, transition: str, prices: Mapping[int, float], stack: int
    ) -> AttemptCost:
        sid_from, _ = _parse_transition(transition)
        input_item_price = float(prices[sid_from])
        clean_price = float(prices[0])
        p = self.success_probability(transition, stack)
        cost = expected_enhance_cost(input_item_price, clean_price, p)
        return AttemptCost(
            p=p,
            expected_attempts=1.0 / p,
            input_item_price=input_item_price,
            clean_stone_price=clean_price,
            expected_cost=cost,
        )


def build_accessory_v1(rates: Mapping[str, Any]) -> AccessoryV1:
    """Factory: build the accessory_v1 model from a ``rates`` mapping."""
    section = rates["models"]["accessory_v1"]["transitions"]
    curves = {name: TransitionCurve.from_dict(params) for name, params in section.items()}
    return AccessoryV1(curves)


register_model("accessory_v1", build_accessory_v1)


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def enhancement_analysis(
    prices: Mapping[int, float],
    *,
    model_id: str = "accessory_v1",
    rates: Mapping[str, Any] | None = None,
    stack: int | None = None,
    intent: Intent = "personal",
    tax: TaxConfig | None = None,
) -> dict[str, Any]:
    """Per-tier enhancement economics for one item.

    ``prices`` maps ``sid -> base_price`` (canonical market price). For each
    ``sid -> sid+1`` transition the model supports, returns expected cost,
    target market price, revenue (net of tax for resale), profit, ROI and a
    verdict. Transitions missing any required price (input, clean ``sid:0``,
    or target) are omitted.

    ``stack`` defaults to each transition's ``default_stack`` (v1 is
    base-rate only; failstack tuning is out of scope). ``intent``:

    - ``"personal"``  -> ``net_rate`` is 1.0, revenue is the target price;
      verdict ``"enhance"`` if expected cost < target price else ``"buy"``.
    - ``"resale"``    -> revenue is target price * net_rate; verdict
      ``"profit"`` if expected profit > 0 else ``"loss"``.

    Also includes ``cumulative_cost`` (expected silver to build ``clean ->
    sid:N`` by enhancing each tier yourself) and ``best_tier`` (highest ROI).
    """
    if intent not in ("personal", "resale"):
        raise ValueError(f"intent must be 'personal' or 'resale', got {intent!r}")

    data = rates if rates is not None else load_rates()
    constants = data["constants"]
    model = get_model(model_id, data)
    tax_cfg = tax if tax is not None else TaxConfig()
    sale_rate = 1.0 if intent == "personal" else net_rate(tax_cfg, constants)

    transitions_out: list[dict[str, Any]] = []
    cumulative_cost: float | None = None
    chain_intact = True

    for transition in model.transitions():
        sid_from, sid_to = _parse_transition(transition)
        if 0 not in prices or sid_from not in prices or sid_to not in prices:
            # Missing price -> omit this transition and break the self-build
            # chain (we can no longer cost clean -> sid:N for higher tiers).
            chain_intact = False
            continue

        use_stack = stack if stack is not None else model.default_stack(transition)
        cost = model.attempt_cost(transition, prices, use_stack)
        target = float(prices[sid_to])
        revenue = target if intent == "personal" else target * sale_rate
        expected_profit = revenue - cost.expected_cost
        roi = expected_profit / cost.expected_cost if cost.expected_cost else 0.0

        if intent == "personal":
            verdict = "enhance" if cost.expected_cost < target else "buy"
        else:
            verdict = "profit" if expected_profit > 0 else "loss"

        if chain_intact:
            if cumulative_cost is None:
                cumulative_cost = cost.expected_cost
            else:
                # Build the input item yourself instead of buying it: the
                # in-progress item is now worth the prior cumulative cost.
                cumulative_cost = (cumulative_cost + float(prices[0])) / cost.p

        transitions_out.append(
            {
                "sid_from": sid_from,
                "sid_to": sid_to,
                "stack": use_stack,
                "p": cost.p,
                "expected_attempts": cost.expected_attempts,
                "clean_stone_price": cost.clean_stone_price,
                "input_item_price": cost.input_item_price,
                "expected_cost": cost.expected_cost,
                "target_market_price": target,
                "net_rate": sale_rate,
                "revenue": revenue,
                "expected_profit": expected_profit,
                "roi": roi,
                "verdict": verdict,
                "cumulative_cost": cumulative_cost if chain_intact else None,
            }
        )

    best_tier = max(transitions_out, key=lambda t: t["roi"], default=None)

    return {
        "model_id": model_id,
        "intent": intent,
        "stack": stack,
        "net_rate": sale_rate,
        "transitions": transitions_out,
        "best_tier": best_tier,
    }
