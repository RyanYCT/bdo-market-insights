"""Unit tests for scripts/validate_regions.py (the CI region-enum guard).

Covers the pure ``find_problems`` core (duplicates, out-of-enum, empty) plus a
smoke check that the region(s) actually configured in samconfig.toml pass
against the real canonical marketQuery enum.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
# validate_regions imports samconfig_regions by name, so scripts/ must be
# importable before the module is loaded.
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "validate_regions_script", _SCRIPTS / "validate_regions.py"
)
assert _spec and _spec.loader
validate_regions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_regions)


def test_valid_list_has_no_problems() -> None:
    assert validate_regions.find_problems(["tw", "na"], {"tw", "na", "eu"}) == []


def test_duplicate_region_is_flagged() -> None:
    problems = validate_regions.find_problems(["tw", "tw"], {"tw"})
    assert any("duplicate" in p for p in problems)


def test_unknown_region_is_flagged() -> None:
    problems = validate_regions.find_problems(["tw", "atlantis"], {"tw", "na"})
    assert any("atlantis" in p and "canonical" in p for p in problems)


def test_logical_id_collision_is_flagged() -> None:
    # Two distinct regions that strip to the same alphanumeric logical-id
    # fragment (console_eu / console.eu -> consoleeu) collide at deploy.
    canonical = {"console_eu", "console.eu"}
    problems = validate_regions.find_problems(["console_eu", "console.eu"], canonical)
    assert any("logical id" in p for p in problems)


def test_distinct_enum_regions_do_not_collide() -> None:
    # The real enum's console_* members reduce to distinct fragments.
    problems = validate_regions.find_problems(
        ["console_eu", "console_na", "console_asia"],
        {"console_eu", "console_na", "console_asia"},
    )
    assert problems == []


def test_empty_list_is_flagged() -> None:
    assert any("no regions" in p for p in validate_regions.find_problems([], {"tw"}))


def test_configured_regions_pass_against_real_enum() -> None:
    """The regions in samconfig.toml must be valid members of the real enum."""
    canonical = validate_regions.canonical_regions()
    for stage in ("dev", "prod"):
        regions = validate_regions.regions_for_stage(stage)
        assert validate_regions.find_problems(regions, canonical) == []
