"""Unit tests for scripts/samconfig_regions.py and the composed parameter set.

Three things are pinned here:

* the reader itself -- both spellings SAM accepts for ``parameter_overrides``, a
  missing stage, and how a caller's overrides are applied;
* that every parameter ``template.yaml`` declares is accounted for, so a
  newly-declared one cannot silently fall back to its template default on a
  ``sam deploy --config-env <stage>`` (Req 2.5);
* that every SSM-resolved parameter carries a repo-scoped SSM **key path** in
  each stage's set, never a literal value (ADR-0024, Req 2.2 / 9.2) -- so an
  inlined hostname or hosted-zone id fails the suite rather than reaching a
  commit.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tomllib
from typing import Any, Final

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
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

STAGES: Final = ("dev", "prod")
"""The deploy environments samconfig.toml defines."""

DERIVED_PARAMETERS: Final = frozenset({"ApiVersion", "MigrationsFingerprint"})
"""The only two parameters deliberately absent from samconfig.toml (Req 2.5).

Both are derived at deploy time and have no static value to record: ``ApiVersion``
is the release tag (ADR-0037) and ``MigrationsFingerprint`` a content hash of
``migrations/versions`` (ADR-0025). The two full-state callers supply exactly
these and read the rest from the file.
"""

TEMPLATE_DEFAULT_PARAMETERS: Final = frozenset(
    {
        "BedrockModelId",
        "BedrockFoundationModelId",
        "CatalogSyncSchedule",
        "LogRetentionInDays",
    }
)
"""Parameters deliberately left at their ``template.yaml`` default.

The third and last category, and the reason the coverage assertion below is a
three-way classification rather than two-way: no caller has ever passed these,
so putting them in samconfig.toml would commit a second copy of a value that
already has exactly one home. ``CatalogSyncSchedule`` could not go there anyway
-- its cron value contains spaces, which the space-delimited
``--parameter-overrides`` spelling cannot carry.

Listing them EXPLICITLY is what keeps the guard's teeth: a newly-declared
parameter matches none of the three sets and fails the suite until somebody
classifies it on purpose, which is the failure task 11.9 asks for.
"""

SSM_PATH_PREFIX: Final = "/bdo-market-insights/"
SSM_PARAMETER_TYPE: Final = "AWS::SSM::Parameter::Value<String>"


def _write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "samconfig.toml"
    path.write_text(body, encoding="utf-8")
    return path


class _CloudFormationLoader(yaml.SafeLoader):
    """A loader that tolerates CloudFormation's short-form intrinsic tags.

    ``template.yaml`` is full of ``!Ref`` / ``!Sub`` / ``!GetAtt``, which
    ``SafeLoader`` refuses outright. The ``Parameters`` block this test reads
    contains none of them, so resolving every unknown tag to its raw node is
    enough to get the document parsed -- no intrinsic is interpreted.
    """


def _any_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> str:
    return f"!{tag_suffix} {node.value!r}"


_CloudFormationLoader.add_multi_constructor("!", _any_tag)  # type: ignore[no-untyped-call]


def template_parameters() -> dict[str, dict[str, Any]]:
    """``template.yaml``'s ``Parameters`` block, as declared."""
    document = yaml.load(  # noqa: S506 - _CloudFormationLoader subclasses SafeLoader
        (_ROOT / "template.yaml").read_text(encoding="utf-8"), Loader=_CloudFormationLoader
    )
    parameters = document["Parameters"]
    assert isinstance(parameters, dict) and parameters
    return parameters


def committed_parameters(stage: str) -> dict[str, str]:
    """``stage``'s committed set, read straight from the tracked samconfig.toml."""
    parameters: dict[str, str] = samconfig_regions.parameters_for_stage(stage)
    return parameters


class TestRegionsForStage:
    """The original contract, which scripts/validate_regions.py depends on."""

    def test_parses_string_form(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _STRING_FORM)
        assert samconfig_regions.regions_for_stage("prod", cfg) == ["tw", "na"]

    def test_parses_toml_array_form(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _LIST_FORM)
        assert samconfig_regions.regions_for_stage("prod", cfg) == ["tw", "na"]

    def test_missing_stage_exits(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _STRING_FORM)
        with pytest.raises(SystemExit):
            samconfig_regions.regions_for_stage("dev", cfg)

    def test_missing_bdoregions_exits(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(
            tmp_path,
            '[prod.deploy.parameters]\nparameter_overrides = "Stage=prod UseRdsProxy=false"\n',
        )
        with pytest.raises(SystemExit):
            samconfig_regions.regions_for_stage("prod", cfg)


class TestParametersForStage:
    """The widened reader: the whole set, in file order, from either spelling."""

    def test_reads_the_whole_set_from_the_string_form(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _STRING_FORM)
        assert samconfig_regions.parameters_for_stage("prod", cfg) == {
            "Stage": "prod",
            "BdoRegions": "tw,na",
            "UseRdsProxy": "false",
        }

    def test_reads_the_whole_set_from_the_toml_array_form(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _LIST_FORM)
        assert samconfig_regions.parameters_for_stage("prod", cfg) == {
            "Stage": "prod",
            "BdoRegions": "tw,na",
            "UseRdsProxy": "false",
        }

    def test_both_spellings_read_identically(self, tmp_path: pathlib.Path) -> None:
        """The two forms are interchangeable to SAM, so they must be here too."""
        string_form = samconfig_regions.parameters_for_stage(
            "prod", _write(tmp_path / "a", _STRING_FORM)
        )
        list_form = samconfig_regions.parameters_for_stage(
            "prod", _write(tmp_path / "b", _LIST_FORM)
        )
        assert string_form == list_form
        assert list(string_form) == list(list_form), "file order is preserved in both"

    def test_missing_stage_exits_naming_the_table(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _STRING_FORM)
        with pytest.raises(SystemExit, match=r"\[dev\.deploy\.parameters\]"):
            samconfig_regions.parameters_for_stage("dev", cfg)

    def test_a_missing_deploy_table_exits(self, tmp_path: pathlib.Path) -> None:
        """A stage present in the file but shaped wrong is still a loud failure."""
        cfg = _write(tmp_path, "[prod.build.parameters]\ncached = true\n")
        with pytest.raises(SystemExit):
            samconfig_regions.parameters_for_stage("prod", cfg)


class TestMergedParameters:
    """How a caller's derived values and overrides are applied."""

    def test_an_absent_key_is_appended(self, tmp_path: pathlib.Path) -> None:
        cfg = _write(tmp_path, _STRING_FORM)
        merged = samconfig_regions.merged_parameters("prod", {"ApiVersion": "v1.2.3"}, cfg)
        assert list(merged) == ["Stage", "BdoRegions", "UseRdsProxy", "ApiVersion"]

    def test_a_carried_key_is_replaced_in_place_not_duplicated(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The hazard this merge exists for.

        ``make deploy STAGE=<env> AUTO_MIGRATE=false`` is step 2 of a first-time
        bring-up (ADR-0025). Appending a second ``AutoMigrate=`` token would leave
        SAM to pick between duplicates, which is undocumented; replacing in place
        is defined. A silently-ignored ``AUTO_MIGRATE=false`` would break bring-up.
        """
        cfg = _write(
            tmp_path,
            "[prod.deploy.parameters]\n"
            'parameter_overrides = "Stage=prod AutoMigrate=true UseRdsProxy=false"\n',
        )
        merged = samconfig_regions.merged_parameters("prod", {"AutoMigrate": "false"}, cfg)
        assert merged == {"Stage": "prod", "AutoMigrate": "false", "UseRdsProxy": "false"}
        rendered = samconfig_regions.render_parameters(merged)
        assert rendered.count("AutoMigrate=") == 1

    def test_a_malformed_override_token_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="Key=Value"):
            samconfig_regions.parse_parameters(["AutoMigrate"])

    def test_a_value_containing_whitespace_is_refused(self) -> None:
        """A space-delimited string cannot carry it, so say so rather than split it."""
        with pytest.raises(SystemExit, match="whitespace"):
            samconfig_regions.render_parameters({"CatalogSyncSchedule": "cron(0 8 ? * THU *)"})


class TestTemplateParameterCoverage:
    """Requirement 2.5: no parameter silently falls back to a template default."""

    @pytest.mark.parametrize("stage", STAGES)
    def test_every_template_parameter_is_accounted_for(self, stage: str) -> None:
        """Each declared parameter is committed, derived, or explicitly defaulted.

        The point of the assertion is the *third* category being an explicit list:
        a parameter added to template.yaml lands in none of the three and fails
        here, so it cannot reach a deploy taking a template default nobody chose.
        """
        committed = set(committed_parameters(stage))
        unaccounted = sorted(
            set(template_parameters())
            - committed
            - DERIVED_PARAMETERS
            - TEMPLATE_DEFAULT_PARAMETERS
        )
        assert not unaccounted, (
            f"template.yaml declares {unaccounted} which [{stage}.deploy.parameters] "
            "does not carry: add them to samconfig.toml, or -- if they are meant to "
            "keep their template default -- to TEMPLATE_DEFAULT_PARAMETERS here"
        )

    @pytest.mark.parametrize("stage", STAGES)
    def test_the_committed_set_declares_nothing_the_template_does_not(self, stage: str) -> None:
        """A typo'd or stale entry would be rejected by CloudFormation at deploy."""
        declared = set(template_parameters())
        assert not sorted(set(committed_parameters(stage)) - declared)

    @pytest.mark.parametrize("stage", STAGES)
    def test_the_derived_two_are_not_committed(self, stage: str) -> None:
        """Committing either would give a deploy-time value a stale static home."""
        assert not DERIVED_PARAMETERS & set(committed_parameters(stage))

    def test_the_explicitly_defaulted_set_is_not_stale(self) -> None:
        """Every name exempted above is still a parameter template.yaml declares."""
        assert not TEMPLATE_DEFAULT_PARAMETERS - set(template_parameters())


class TestSsmResolvedParameters:
    """ADR-0024 / Requirements 2.2, 9.2: key paths in the file, values in SSM."""

    @pytest.mark.parametrize("stage", STAGES)
    def test_every_ssm_parameter_carries_a_repo_scoped_key_path(self, stage: str) -> None:
        ssm_parameters = sorted(
            name
            for name, declaration in template_parameters().items()
            if declaration.get("Type") == SSM_PARAMETER_TYPE
        )
        assert ssm_parameters, "template.yaml declares SSM-resolved parameters"
        committed = committed_parameters(stage)
        for name in ssm_parameters:
            value = committed.get(name)
            assert value is not None, (
                f"[{stage}.deploy.parameters] must carry {name}: it is SSM-resolved, so "
                "omitting it falls back to template.yaml's dev-scoped default path"
            )
            assert value.startswith(SSM_PATH_PREFIX), (
                f"{name}={value!r} in [{stage}.deploy.parameters] is not a repo-scoped "
                f"SSM key path. CloudFormation resolves the STORED value at deploy "
                f"(ADR-0024), so this entry must be a {SSM_PATH_PREFIX}... path -- a "
                "literal hostname or hosted-zone id here commits an account-specific "
                "value to a tracked file"
            )

    @pytest.mark.parametrize("stage", STAGES)
    def test_every_ssm_key_path_is_scoped_to_its_own_stage(self, stage: str) -> None:
        """The prod table's paths say ``/prod/``, not ``/dev/``.

        The original defect: template.yaml's defaults are all ``/dev/`` paths, so a
        prod deploy that fell back to them would have resolved prod's domain and
        demo-key config out of dev's parameters.
        """
        committed = committed_parameters(stage)
        for name, declaration in template_parameters().items():
            if declaration.get("Type") != SSM_PARAMETER_TYPE:
                continue
            assert committed[name].startswith(f"{SSM_PATH_PREFIX}{stage}/"), (
                f"{name} in [{stage}.deploy.parameters] is scoped to another stage"
            )

    @pytest.mark.parametrize("stage", STAGES)
    def test_no_committed_value_looks_like_a_literal_host_or_zone(self, stage: str) -> None:
        """A blunt backstop over the whole set, not just the SSM-typed entries.

        Catches an account-specific value inlined under a name whose template Type
        was also changed -- a hostname or a Route 53 zone id anywhere in the set.
        """
        for name, value in committed_parameters(stage).items():
            assert not re.fullmatch(r"Z[A-Z0-9]{12,}", value), (
                f"{name}={value!r} looks like a Route 53 hosted-zone id; "
                f"commit a {SSM_PATH_PREFIX}... key path instead (ADR-0024)"
            )
            assert not re.fullmatch(r"(?:[a-z0-9-]+\.){2,}[a-z]{2,}", value), (
                f"{name}={value!r} looks like a hostname; "
                f"commit a {SSM_PATH_PREFIX}... key path instead (ADR-0024)"
            )


class TestCommandLine:
    """Both CLI modes, since two workflows and the Makefile invoke them."""

    def test_the_default_mode_still_prints_the_region_list(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        samconfig_regions.main(["samconfig_regions.py", "prod"])
        assert capsys.readouterr().out.strip() == ",".join(
            samconfig_regions.regions_for_stage("prod")
        )

    def test_the_parameters_mode_prints_the_merged_set(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        samconfig_regions.main(
            [
                "samconfig_regions.py",
                "--parameters",
                "dev",
                "ApiVersion=v1.2.3",
                "MigrationsFingerprint=abc123",
                "AutoMigrate=false",
            ]
        )
        emitted = samconfig_regions.parse_parameters(capsys.readouterr().out.split())
        expected = committed_parameters("dev") | {
            "AutoMigrate": "false",
            "ApiVersion": "v1.2.3",
            "MigrationsFingerprint": "abc123",
        }
        assert emitted == expected


class TestSamconfigShape:
    """Guards on the tracked file the reader and the deploy both depend on."""

    def test_both_stages_are_present_and_name_themselves(self) -> None:
        document = tomllib.loads((_ROOT / "samconfig.toml").read_text(encoding="utf-8"))
        assert set(STAGES) <= set(document)
        for stage in STAGES:
            assert committed_parameters(stage)["Stage"] == stage

    @pytest.mark.parametrize("stage", STAGES)
    def test_the_set_round_trips_through_the_rendered_string(self, stage: str) -> None:
        """What the reader emits parses back to what it read -- no value is mangled."""
        committed = committed_parameters(stage)
        rendered = samconfig_regions.render_parameters(committed)
        assert samconfig_regions.parse_parameters(rendered.split()) == committed
