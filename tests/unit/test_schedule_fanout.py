"""IaC tests for the per-region schedule fan-out (multi-region-readiness).

The trigger layer generates one hourly ETL rule and one daily + one weekly
insights rule per region in ``BdoRegions`` via ``Fn::ForEach``
(``AWS::LanguageExtensions``), per ADR-0036. These tests assert two properties
directly on the templates:

* **P1 (readiness / default-off):** the default ``BdoRegions=[tw]`` expands to
  exactly the baseline set that exists today -- one hourly ETL rule, one daily
  and one weekly insights rule, all ``region=tw`` -- and no others.
* **P2 (fan-out):** an N-region list expands to exactly N hourly ETL rules, N
  daily and N weekly insights rules, each carrying its own list entry as the
  input ``region``.

The expansion is driven through cfn-lint's ``AWS::LanguageExtensions`` transform
(the same one CloudFormation/``sam validate`` run), overriding the
``BdoRegions`` parameter default so the loop resolves to a known list. The
templates are also linted post-expansion to catch malformed generated resources.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from cfnlint.api import lint_file  # type: ignore[import-untyped]
from cfnlint.decode import cfn_yaml  # type: ignore[import-untyped]
from cfnlint.template import Template  # type: ignore[import-untyped]

# cfn-lint's Fn::ForEach expansion lives in a private module; it is the engine
# CloudFormation/sam use for AWS::LanguageExtensions, so driving it here mirrors
# deploy-time behaviour. Pinned via the cfn-lint dev dependency.
from cfnlint.template.transforms._language_extensions import (  # type: ignore[import-untyped]
    _Transform,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ETL = _REPO_ROOT / "infra" / "etl.yaml"
_INSIGHTS = _REPO_ROOT / "infra" / "insights.yaml"

#: Mirrors the canonical Region enum in ``market_query/app.py``.
#: ``test_enum_regions_stay_in_sync`` fails if this local copy drifts.
_ENUM_REGIONS = [
    "na",
    "eu",
    "sea",
    "mena",
    "kr",
    "ru",
    "jp",
    "th",
    "tw",
    "sa",
    "console_eu",
    "console_na",
    "console_asia",
]


def _expand(template_path: Path, regions: list[str]) -> dict[str, Any]:
    """Expand ``Fn::ForEach`` in a template for a given ``BdoRegions`` list.

    Overrides the ``BdoRegions`` parameter default with the comma-joined
    ``regions`` and runs the language-extensions transform, returning the
    resolved template dict.
    """
    template_dict = cfn_yaml.load(str(template_path))
    template_dict["Parameters"]["BdoRegions"]["Default"] = ",".join(regions)
    cfn = Template(str(template_path), copy.deepcopy(template_dict), regions=["us-east-1"])
    _, transformed = _Transform().transform(cfn)
    return cast("dict[str, Any]", transformed)


def _event_rules(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the ``AWS::Events::Rule`` resources keyed by logical id."""
    return {
        name: body
        for name, body in template.get("Resources", {}).items()
        if body.get("Type") == "AWS::Events::Rule"
    }


def _rule_input(rule: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON ``Input`` of a rule's single target."""
    return cast("dict[str, Any]", json.loads(rule["Properties"]["Targets"][0]["Input"]))


# ──────────────────────────────────────────────────────────────────────────────
# Lint coverage of the expanded templates
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("template", [_ETL, _INSIGHTS], ids=["etl", "insights"])
def test_foreach_templates_lint_clean(template: Path) -> None:
    """cfn-lint (which expands Fn::ForEach) reports no findings on the template."""
    matches = lint_file(template)
    assert matches == [], [f"{m.rule.id}: {m.message}" for m in matches]


# ──────────────────────────────────────────────────────────────────────────────
# P1 -- default [tw] reproduces exactly today's schedules
# ──────────────────────────────────────────────────────────────────────────────


def test_etl_default_tw_is_baseline() -> None:
    rules = _event_rules(_expand(_ETL, ["tw"]))
    assert len(rules) == 1
    (rule,) = rules.values()
    assert rule["Properties"]["ScheduleExpression"] == "cron(7 * * * ? *)"
    assert _rule_input(rule) == {"region": "tw"}


def test_insights_default_tw_is_baseline() -> None:
    rules = _event_rules(_expand(_INSIGHTS, ["tw"]))
    daily = {n: r for n, r in rules.items() if n.startswith("InsightsDaily")}
    weekly = {n: r for n, r in rules.items() if n.startswith("InsightsWeekly")}
    # Exactly one daily + one weekly and nothing else.
    assert len(daily) == 1
    assert len(weekly) == 1
    assert len(rules) == 2

    (daily_rule,) = daily.values()
    assert daily_rule["Properties"]["ScheduleExpression"] == "cron(0 1 * * ? *)"
    assert _rule_input(daily_rule) == {"region": "tw", "period": "daily"}

    (weekly_rule,) = weekly.values()
    assert weekly_rule["Properties"]["ScheduleExpression"] == "cron(15 1 ? * MON *)"
    assert _rule_input(weekly_rule) == {"region": "tw", "period": "weekly"}


# ──────────────────────────────────────────────────────────────────────────────
# P2 -- an N-region list fans out to N of each rule (property-based)
# ──────────────────────────────────────────────────────────────────────────────


def test_enum_regions_stay_in_sync() -> None:
    """The local enum copy must match market_query's canonical Region literal."""
    import importlib.util
    import sys
    import typing

    path = _REPO_ROOT / "src" / "functions" / "market_query" / "app.py"
    spec = importlib.util.spec_from_file_location("fn_market_query_enum", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert set(typing.get_args(module.Region)) == set(_ENUM_REGIONS)


try:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    _region_lists = st.lists(st.sampled_from(_ENUM_REGIONS), min_size=1, unique=True)

    @settings(max_examples=50, deadline=None)
    @given(regions=_region_lists)
    def test_etl_fans_out_one_hourly_rule_per_region(regions: list[str]) -> None:
        rules = _event_rules(_expand(_ETL, regions))
        assert len(rules) == len(regions)
        assert {_rule_input(r)["region"] for r in rules.values()} == set(regions)
        for rule in rules.values():
            assert rule["Properties"]["ScheduleExpression"] == "cron(7 * * * ? *)"

    @settings(max_examples=50, deadline=None)
    @given(regions=_region_lists)
    def test_insights_fans_out_daily_and_weekly_per_region(regions: list[str]) -> None:
        rules = _event_rules(_expand(_INSIGHTS, regions))
        daily = [r for n, r in rules.items() if n.startswith("InsightsDaily")]
        weekly = [r for n, r in rules.items() if n.startswith("InsightsWeekly")]
        assert len(daily) == len(regions)
        assert len(weekly) == len(regions)
        assert {_rule_input(r)["region"] for r in daily} == set(regions)
        assert {_rule_input(r)["region"] for r in weekly} == set(regions)
        assert all(_rule_input(r)["period"] == "daily" for r in daily)
        assert all(_rule_input(r)["period"] == "weekly" for r in weekly)

except ImportError:  # pragma: no cover - hypothesis is a dev dependency
    pass
