"""
Property-based tests for configuration management.

Feature: bdo-market-insights-rewrite
Tests Property 21 from the design document.
"""

import pytest
import os
from unittest.mock import patch
from hypothesis import given, strategies as st, settings, assume
from pydantic import ValidationError

# Import configuration classes from lambda layer
import sys
from common.config import (
    Config,
    DatabaseConfig,
    ExternalAPIConfig,
    RetryConfig,
    BatchProcessingConfig,
    DataRetentionConfig,
    APIGatewayConfig,
    QueryConfig,
    CategoryConfig,
    LoggingConfig,
    MonitoringConfig,
    Environment,
    get_config,
    ConfigurationError,
    InvalidConfigurationError,
)


# Property 21: Configuration validation and defaults
# **Validates: Requirements 14.3, 14.4**


@given(
    pool_size=st.integers(min_value=-100, max_value=0)
)
@settings(max_examples=100)
def test_invalid_database_pool_size_rejected(pool_size):
    """
    Property 21: For any configuration parameter, the system should validate
    it against defined constraints and reject invalid values with clear errors.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # pool_size must be between 1 and 20
    with pytest.raises(ValidationError) as exc_info:
        DatabaseConfig(pool_size=pool_size)
    
    # Verify error message mentions the constraint
    error_str = str(exc_info.value)
    assert "pool_size" in error_str.lower()


@given(
    rate_limit=st.integers(min_value=-1000, max_value=0)
)
@settings(max_examples=100)
def test_invalid_rate_limit_rejected(rate_limit):
    """
    Property 21: Invalid rate limit values should be rejected.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    with pytest.raises(ValidationError) as exc_info:
        ExternalAPIConfig(rate_limit_requests=rate_limit)
    
    error_str = str(exc_info.value)
    assert "rate_limit_requests" in error_str.lower()


@given(
    max_attempts=st.integers(min_value=-10, max_value=0)
)
@settings(max_examples=100)
def test_invalid_retry_attempts_rejected(max_attempts):
    """
    Property 21: Invalid retry attempt values should be rejected.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    with pytest.raises(ValidationError) as exc_info:
        RetryConfig(max_attempts=max_attempts)
    
    error_str = str(exc_info.value)
    assert "max_attempts" in error_str.lower()


@given(
    batch_size=st.integers(min_value=1, max_value=100),
    max_batch_size=st.integers(min_value=1, max_value=100)
)
@settings(max_examples=100)
def test_batch_size_constraint_validation(batch_size, max_batch_size):
    """
    Property 21: max_batch_size must be >= batch_size, otherwise reject.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    if max_batch_size < batch_size:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            BatchProcessingConfig(
                batch_size=batch_size,
                max_batch_size=max_batch_size
            )
        error_str = str(exc_info.value)
        assert "max_batch_size" in error_str.lower()
    else:
        # Should be accepted
        config = BatchProcessingConfig(
            batch_size=batch_size,
            max_batch_size=max_batch_size
        )
        assert config.batch_size == batch_size
        assert config.max_batch_size == max_batch_size


@given(
    detailed_days=st.integers(min_value=1, max_value=365),
    summary_days=st.integers(min_value=90, max_value=3650)
)
@settings(max_examples=100)
def test_retention_days_constraint_validation(detailed_days, summary_days):
    """
    Property 21: summary_retention_days must be >= detailed_retention_days.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # Ensure summary_days is at least 90 (minimum constraint)
    # and check if it's less than detailed_days
    if summary_days < detailed_days:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            DataRetentionConfig(
                detailed_retention_days=detailed_days,
                summary_retention_days=summary_days
            )
        error_str = str(exc_info.value)
        assert "summary_retention_days" in error_str.lower()
    else:
        # Should be accepted
        config = DataRetentionConfig(
            detailed_retention_days=detailed_days,
            summary_retention_days=summary_days
        )
        assert config.detailed_retention_days == detailed_days
        assert config.summary_retention_days == summary_days


@given(
    default_limit=st.integers(min_value=1, max_value=1000),
    max_limit=st.integers(min_value=1, max_value=10000)
)
@settings(max_examples=100)
def test_query_limit_constraint_validation(default_limit, max_limit):
    """
    Property 21: max_limit must be >= default_limit.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    if max_limit < default_limit:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            QueryConfig(
                default_limit=default_limit,
                max_limit=max_limit
            )
        error_str = str(exc_info.value)
        assert "max_limit" in error_str.lower()
    else:
        # Should be accepted
        config = QueryConfig(
            default_limit=default_limit,
            max_limit=max_limit
        )
        assert config.default_limit == default_limit
        assert config.max_limit == max_limit


@given(
    log_level=st.text(min_size=1, max_size=20)
)
@settings(max_examples=100)
def test_invalid_log_level_rejected(log_level):
    """
    Property 21: Invalid log levels should be rejected.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    
    if log_level.upper() not in valid_levels:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(log_level=log_level)
        error_str = str(exc_info.value)
        assert "log_level" in error_str.lower()
    else:
        # Should be accepted and normalized to uppercase
        config = LoggingConfig(log_level=log_level)
        assert config.log_level == log_level.upper()


@given(
    default_category=st.text(min_size=1, max_size=50)
)
@settings(max_examples=100)
def test_default_category_must_be_valid(default_category):
    """
    Property 21: default_category must be in valid_categories list.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    valid_categories = ["Uncategorized", "Accessory", "Buff", "Costume"]
    
    if default_category not in valid_categories:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            CategoryConfig(default_category=default_category)
        error_str = str(exc_info.value)
        assert "default_category" in error_str.lower()
    else:
        # Should be accepted
        config = CategoryConfig(default_category=default_category)
        assert config.default_category == default_category


def test_default_values_used_for_missing_parameters():
    """
    Property 21: For any missing optional configuration parameter,
    the system should use documented default values.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # Create config with no parameters (all defaults)
    db_config = DatabaseConfig()
    
    # Verify documented defaults are used
    assert db_config.secret_name == "bdo-db-credentials"
    assert db_config.pool_size == 5
    assert db_config.pool_timeout == 30
    assert db_config.idle_timeout == 300
    assert db_config.query_timeout == 30
    assert db_config.slow_query_threshold == 1.0
    
    api_config = ExternalAPIConfig()
    assert api_config.secret_name == "bdo-api-credentials"
    assert api_config.base_url == "https://api.arsha.io"
    assert api_config.timeout == 30
    assert api_config.rate_limit_requests == 100
    assert api_config.rate_limit_window == 60
    
    retry_config = RetryConfig()
    assert retry_config.max_attempts == 3
    assert retry_config.backoff_base == 2.0
    assert retry_config.backoff_multiplier == 2.0
    assert retry_config.max_backoff == 60.0
    assert retry_config.jitter is True
    
    batch_config = BatchProcessingConfig()
    assert batch_config.batch_size == 50
    assert batch_config.max_batch_size == 100
    
    retention_config = DataRetentionConfig()
    assert retention_config.detailed_retention_days == 90
    assert retention_config.summary_retention_days == 730
    assert retention_config.archive_to_glacier is True
    
    api_gateway_config = APIGatewayConfig()
    assert api_gateway_config.rate_limit_per_key == 100
    assert api_gateway_config.burst_limit == 20
    assert api_gateway_config.quota_limit == 10000
    assert api_gateway_config.cache_ttl == 300
    
    query_config = QueryConfig()
    assert query_config.default_limit == 100
    assert query_config.max_limit == 1000
    assert query_config.default_date_range_days == 30
    
    category_config = CategoryConfig()
    assert category_config.valid_categories == ["Uncategorized", "Accessory", "Buff", "Costume"]
    assert category_config.default_category == "Uncategorized"
    
    logging_config = LoggingConfig()
    assert logging_config.log_level == "INFO"
    assert logging_config.json_format is True
    assert logging_config.include_trace_id is True
    
    monitoring_config = MonitoringConfig()
    assert monitoring_config.enable_xray is True
    assert monitoring_config.enable_custom_metrics is True
    assert monitoring_config.metrics_namespace == "BDOMarketInsights"


@given(
    pool_size=st.integers(min_value=1, max_value=20),
    pool_timeout=st.integers(min_value=5, max_value=300),
    rate_limit=st.integers(min_value=1, max_value=1000),
    max_attempts=st.integers(min_value=1, max_value=10)
)
@settings(max_examples=100)
def test_valid_configuration_accepted(pool_size, pool_timeout, rate_limit, max_attempts):
    """
    Property 21: For any configuration parameter within valid constraints,
    the system should accept it.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # All these values are within valid ranges
    db_config = DatabaseConfig(pool_size=pool_size, pool_timeout=pool_timeout)
    assert db_config.pool_size == pool_size
    assert db_config.pool_timeout == pool_timeout
    
    api_config = ExternalAPIConfig(rate_limit_requests=rate_limit)
    assert api_config.rate_limit_requests == rate_limit
    
    retry_config = RetryConfig(max_attempts=max_attempts)
    assert retry_config.max_attempts == max_attempts


@given(
    environment=st.sampled_from(["development", "staging", "production"])
)
@settings(max_examples=100)
def test_environment_specific_configuration(environment):
    """
    Property 21: System should support environment-specific configuration.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # Create config with specific environment
    config = Config(environment=environment)
    
    assert config.environment.value == environment
    assert config.get_environment_name() == environment
    
    # Verify environment checks work correctly
    if environment == "production":
        assert config.is_production() is True
        assert config.is_development() is False
    elif environment == "development":
        assert config.is_development() is True
        assert config.is_production() is False
    else:
        assert config.is_production() is False
        assert config.is_development() is False


@given(
    pool_size=st.integers(min_value=1, max_value=20),
    rate_limit=st.integers(min_value=1, max_value=1000),
    max_attempts=st.integers(min_value=1, max_value=10)
)
@settings(max_examples=100)
def test_configuration_from_environment_variables(pool_size, rate_limit, max_attempts):
    """
    Property 21: System should load configuration from environment variables
    with validation.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    env_vars = {
        "BDO_ENVIRONMENT": "staging",
        "BDO_AWS_REGION": "us-west-2",
        "BDO_DATABASE__POOL_SIZE": str(pool_size),
        "BDO_EXTERNAL_API__RATE_LIMIT_REQUESTS": str(rate_limit),
        "BDO_RETRY__MAX_ATTEMPTS": str(max_attempts),
    }
    
    with patch.dict(os.environ, env_vars, clear=False):
        config = Config.from_env()
        
        # Verify environment variables were loaded correctly
        assert config.environment == Environment.STAGING
        assert config.aws_region == "us-west-2"
        assert config.database.pool_size == pool_size
        assert config.external_api.rate_limit_requests == rate_limit
        assert config.retry.max_attempts == max_attempts


@given(
    enable_xray=st.booleans(),
    enable_metrics=st.booleans(),
    json_format=st.booleans()
)
@settings(max_examples=100)
def test_boolean_configuration_from_env(enable_xray, enable_metrics, json_format):
    """
    Property 21: Boolean configuration values should be parsed correctly
    from environment variables.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    env_vars = {
        "BDO_MONITORING__ENABLE_XRAY": "true" if enable_xray else "false",
        "BDO_MONITORING__ENABLE_CUSTOM_METRICS": "1" if enable_metrics else "0",
        "BDO_LOGGING__JSON_FORMAT": "yes" if json_format else "no",
    }
    
    with patch.dict(os.environ, env_vars, clear=False):
        config = Config.from_env()
        
        assert config.monitoring.enable_xray == enable_xray
        assert config.monitoring.enable_custom_metrics == enable_metrics
        assert config.logging.json_format == json_format


@given(
    origins=st.lists(
        st.text(
            min_size=1,
            max_size=50,
            alphabet=st.characters(blacklist_characters='\x00,')  # Exclude null and comma
        ).filter(lambda x: x.strip()),  # Ensure non-empty after stripping
        min_size=1,
        max_size=5
    )
)
@settings(max_examples=100)
def test_list_configuration_from_env(origins):
    """
    Property 21: List configuration values should be parsed correctly
    from comma-separated environment variables.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    origins_str = ",".join(origins)
    env_vars = {
        "BDO_API_GATEWAY__CORS_ALLOWED_ORIGINS": origins_str,
    }
    
    with patch.dict(os.environ, env_vars, clear=False):
        config = Config.from_env()
        
        # Verify list was parsed correctly (with whitespace stripped)
        expected_origins = [o.strip() for o in origins]
        assert config.api_gateway.cors_allowed_origins == expected_origins


def test_global_config_instance_caching():
    """
    Property 21: Global configuration instance should be cached and reused.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # Clear any existing instance
    import common.config as config_module
    config_module._config_instance = None
    
    # First call should create instance
    config1 = get_config()
    
    # Second call should return same instance
    config2 = get_config()
    
    assert config1 is config2
    
    # Reload should create new instance
    config3 = get_config(reload=True)
    
    assert config3 is not config1


@given(
    error_rate=st.floats(min_value=-1.0, max_value=2.0)
)
@settings(max_examples=100)
def test_alarm_threshold_validation(error_rate):
    """
    Property 21: Alarm thresholds should be validated to be between 0.0 and 1.0.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    if error_rate < 0.0 or error_rate > 1.0:
        # Should be rejected
        with pytest.raises(ValidationError) as exc_info:
            MonitoringConfig(alarm_error_rate_threshold=error_rate)
        error_str = str(exc_info.value)
        assert "alarm_error_rate_threshold" in error_str.lower()
    else:
        # Should be accepted
        config = MonitoringConfig(alarm_error_rate_threshold=error_rate)
        assert config.alarm_error_rate_threshold == error_rate


@given(
    backoff_base=st.floats(min_value=0.1, max_value=15.0),
    backoff_multiplier=st.floats(min_value=0.5, max_value=10.0),
    max_backoff=st.floats(min_value=0.5, max_value=500.0)
)
@settings(max_examples=100)
def test_retry_backoff_configuration_validation(backoff_base, backoff_multiplier, max_backoff):
    """
    Property 21: Retry backoff configuration should validate numeric ranges.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    # Check if values are within valid ranges
    base_valid = 1.0 <= backoff_base <= 10.0
    multiplier_valid = 1.0 <= backoff_multiplier <= 5.0
    max_valid = 1.0 <= max_backoff <= 300.0
    
    if base_valid and multiplier_valid and max_valid:
        # Should be accepted
        config = RetryConfig(
            backoff_base=backoff_base,
            backoff_multiplier=backoff_multiplier,
            max_backoff=max_backoff
        )
        assert config.backoff_base == backoff_base
        assert config.backoff_multiplier == backoff_multiplier
        assert config.max_backoff == max_backoff
    else:
        # Should be rejected
        with pytest.raises(ValidationError):
            RetryConfig(
                backoff_base=backoff_base,
                backoff_multiplier=backoff_multiplier,
                max_backoff=max_backoff
            )


def test_config_to_dict_conversion():
    """
    Property 21: Configuration should be convertible to dictionary format.
    
    Feature: bdo-market-insights-rewrite, Property 21: Configuration validation and defaults
    """
    config = Config()
    config_dict = config.to_dict()
    
    # Verify it's a dictionary
    assert isinstance(config_dict, dict)
    
    # Verify it contains expected top-level keys
    assert "environment" in config_dict
    assert "aws_region" in config_dict
    assert "database" in config_dict
    assert "external_api" in config_dict
    assert "retry" in config_dict
    
    # Verify nested structure
    assert isinstance(config_dict["database"], dict)
    assert "pool_size" in config_dict["database"]
