"""Unit tests for scripts/samconfig_regions.py.

Confirms BdoRegions is parsed from both accepted forms of the samconfig
``parameter_overrides`` (space-delimited string and TOML array), and that a
missing stage or missing BdoRegions fails loudly.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "samconfig_regions_script", _SCRIPTS / "samconfig_regions.py"
)
assert _spec and _spec.loader
samconfig_regions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(samconfig_regions)

_STRING_FORM = """
[prod.deploy.parameters]
parameter_overrides = "Stage=prod BdoRegions=tw,na UseRdsProxy=false"
"""

_LIST_FORM = """
[prod.deploy.parameters]
parameter_overrides = ["Stage=prod", "BdoRegions=tw,na", "UseRdsProxy=false"]
"""


def _write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "samconfig.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_parses_string_form(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path, _STRING_FORM)
    assert samconfig_regions.regions_for_stage("prod", cfg) == ["tw", "na"]


def test_parses_toml_array_form(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path, _LIST_FORM)
    assert samconfig_regions.regions_for_stage("prod", cfg) == ["tw", "na"]


def test_missing_stage_exits(tmp_path: pathlib.Path) -> None:
    cfg = _write(tmp_path, _STRING_FORM)
    with pytest.raises(SystemExit):
        samconfig_regions.regions_for_stage("dev", cfg)


def test_missing_bdoregions_exits(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path,
        '[prod.deploy.parameters]\nparameter_overrides = "Stage=prod UseRdsProxy=false"\n',
    )
    with pytest.raises(SystemExit):
        samconfig_regions.regions_for_stage("prod", cfg)
