"""Tests for bdo_common.config."""

from __future__ import annotations

import pytest

import bdo_common.config
from bdo_common.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Clear lru_cache before each test so env changes take effect."""
    bdo_common.config.get_settings.cache_clear()


class TestGetSettings:
    """Test get_settings reads environment correctly."""

    def test_defaults_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default values are used when env vars are absent."""
        # Ensure relevant vars are unset
        for var in (
            "BDO_REGION",
            "DB_HOST",
            "DB_PORT",
            "DB_NAME",
            "DB_USER",
            "DYNAMODB_TABLE",
            "STAGE",
            "USE_IAM_AUTH",
        ):
            monkeypatch.delenv(var, raising=False)

        s = get_settings()
        assert s.region == "tw"
        assert s.db_host == "localhost"
        assert s.db_port == 5432
        assert s.db_name == "bdo"
        assert s.db_user == "lambda_rds_user"
        assert s.dynamodb_table == "bdo-v3-items"
        assert s.stage == "dev"
        assert s.use_iam_auth is False

    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom env values are read correctly."""
        monkeypatch.setenv("BDO_REGION", "na")
        monkeypatch.setenv("DB_HOST", "myhost.rds.amazonaws.com")
        monkeypatch.setenv("DB_PORT", "5433")
        monkeypatch.setenv("DB_NAME", "mydb")
        monkeypatch.setenv("DB_USER", "myuser")
        monkeypatch.setenv("DYNAMODB_TABLE", "my-table")
        monkeypatch.setenv("STAGE", "prod")
        monkeypatch.setenv("USE_IAM_AUTH", "true")

        s = get_settings()
        assert s.region == "na"
        assert s.db_host == "myhost.rds.amazonaws.com"
        assert s.db_port == 5433
        assert s.db_name == "mydb"
        assert s.db_user == "myuser"
        assert s.dynamodb_table == "my-table"
        assert s.stage == "prod"
        assert s.use_iam_auth is True

    @pytest.mark.parametrize("value", ["true", "1", "yes", "True", "YES", "Yes"])
    def test_use_iam_auth_truthy(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("USE_IAM_AUTH", value)
        # Clear again because parametrize calls share the autouse fixture timing
        bdo_common.config.get_settings.cache_clear()
        s = get_settings()
        assert s.use_iam_auth is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "random", ""])
    def test_use_iam_auth_falsy(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("USE_IAM_AUTH", value)
        bdo_common.config.get_settings.cache_clear()
        s = get_settings()
        assert s.use_iam_auth is False

    def test_cached_returns_same_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling get_settings twice returns the same cached instance."""
        monkeypatch.delenv("BDO_REGION", raising=False)
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
