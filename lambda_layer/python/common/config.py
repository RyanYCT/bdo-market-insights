"""
Configuration management for BDO Market Insights system.

Provides centralized configuration loading from environment variables with
validation, defaults, and environment-specific overrides. All configurable
parameters are documented with descriptions and valid ranges.
"""

import os
from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator


class Environment(str, Enum):
    """Valid deployment environments."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class DatabaseConfig(BaseModel):
    """Database connection configuration."""
    
    secret_name: str = Field(
        default="bdo-db-credentials",
        description="AWS Secrets Manager secret name for database credentials"
    )
    pool_size: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of connections in the pool (1-20)"
    )
    pool_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Timeout in seconds for acquiring a connection from pool (5-300)"
    )
    idle_timeout: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Timeout in seconds before closing idle connections (60-3600)"
    )
    query_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout in seconds for query execution (1-300)"
    )
    slow_query_threshold: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Threshold in seconds for logging slow queries (0.1-60.0)"
    )


class ExternalAPIConfig(BaseModel):
    """External API configuration."""
    
    secret_name: str = Field(
        default="bdo-api-credentials",
        description="AWS Secrets Manager secret name for API credentials"
    )
    base_url: str = Field(
        default="https://api.arsha.io",
        description="Base URL for the BDO market data API"
    )
    timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Request timeout in seconds (5-120)"
    )
    rate_limit_requests: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum requests per minute (1-1000)"
    )
    rate_limit_window: int = Field(
        default=60,
        ge=1,
        le=300,
        description="Rate limit window in seconds (1-300)"
    )
    circuit_breaker_threshold: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of failures before opening circuit breaker (1-20)"
    )
    circuit_breaker_timeout: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Seconds to wait before attempting to close circuit breaker (10-600)"
    )


class RetryConfig(BaseModel):
    """Retry and backoff configuration."""
    
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of retry attempts (1-10)"
    )
    backoff_base: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Base delay in seconds for exponential backoff (1.0-10.0)"
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="Multiplier for exponential backoff (1.0-5.0)"
    )
    max_backoff: float = Field(
        default=60.0,
        ge=1.0,
        le=300.0,
        description="Maximum backoff delay in seconds (1.0-300.0)"
    )
    jitter: bool = Field(
        default=True,
        description="Whether to add random jitter to backoff delays"
    )


class BatchProcessingConfig(BaseModel):
    """Batch processing configuration for ETL pipeline."""
    
    batch_size: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Number of items to process per batch (1-100)"
    )
    max_batch_size: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum batch size before splitting (1-500)"
    )
    
    @field_validator('max_batch_size')
    @classmethod
    def max_batch_must_be_greater_than_batch(cls, v, info):
        """Ensure max_batch_size is greater than or equal to batch_size."""
        if info.data.get('batch_size') and v < info.data['batch_size']:
            raise ValueError('max_batch_size must be >= batch_size')
        return v


class DataRetentionConfig(BaseModel):
    """Data retention policy configuration."""
    
    detailed_retention_days: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Days to retain detailed market data records (1-365)"
    )
    summary_retention_days: int = Field(
        default=730,
        ge=90,
        le=3650,
        description="Days to retain aggregated summary data (90-3650, ~10 years)"
    )
    archive_to_glacier: bool = Field(
        default=True,
        description="Whether to archive old summaries to S3 Glacier"
    )
    glacier_bucket: str = Field(
        default="bdo-market-data-archive",
        description="S3 bucket name for Glacier archival"
    )
    
    @field_validator('summary_retention_days')
    @classmethod
    def summary_must_be_longer_than_detailed(cls, v, info):
        """Ensure summary retention is longer than detailed retention."""
        if info.data.get('detailed_retention_days') and v < info.data['detailed_retention_days']:
            raise ValueError('summary_retention_days must be >= detailed_retention_days')
        return v


class APIGatewayConfig(BaseModel):
    """API Gateway configuration."""
    
    rate_limit_per_key: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Requests per minute per API key (1-10000)"
    )
    burst_limit: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="Maximum concurrent requests per API key (1-1000)"
    )
    quota_limit: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="Daily request quota per API key (100-1000000)"
    )
    cors_allowed_origins: List[str] = Field(
        default=["*"],
        description="List of allowed CORS origins (use ['*'] for all)"
    )
    cache_ttl: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Cache TTL in seconds for GET responses (0-3600, 0=no cache)"
    )


class QueryConfig(BaseModel):
    """Query pipeline configuration."""
    
    default_limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Default number of results to return (1-1000)"
    )
    max_limit: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum allowed limit parameter (1-10000)"
    )
    default_date_range_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Default date range in days if not specified (1-365)"
    )
    
    @field_validator('max_limit')
    @classmethod
    def max_limit_must_be_greater_than_default(cls, v, info):
        """Ensure max_limit is greater than or equal to default_limit."""
        if info.data.get('default_limit') and v < info.data['default_limit']:
            raise ValueError('max_limit must be >= default_limit')
        return v


class CategoryConfig(BaseModel):
    """Item category configuration."""
    
    valid_categories: List[str] = Field(
        default=["Uncategorized", "Accessory", "Buff", "Costume"],
        description="List of valid item categories"
    )
    default_category: str = Field(
        default="Uncategorized",
        description="Default category for items without a category"
    )
    
    @field_validator('default_category')
    @classmethod
    def default_must_be_valid(cls, v, info):
        """Ensure default_category is in valid_categories."""
        if info.data.get('valid_categories') and v not in info.data['valid_categories']:
            raise ValueError(f'default_category must be one of {info.data["valid_categories"]}')
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""
    
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )
    json_format: bool = Field(
        default=True,
        description="Whether to output logs in JSON format"
    )
    include_trace_id: bool = Field(
        default=True,
        description="Whether to include X-Ray trace ID in logs"
    )
    
    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v):
        """Ensure log_level is valid."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f'log_level must be one of {valid_levels}')
        return v.upper()


class MonitoringConfig(BaseModel):
    """Monitoring and observability configuration."""
    
    enable_xray: bool = Field(
        default=True,
        description="Whether to enable X-Ray tracing"
    )
    enable_custom_metrics: bool = Field(
        default=True,
        description="Whether to emit custom CloudWatch metrics"
    )
    metrics_namespace: str = Field(
        default="BDOMarketInsights",
        description="CloudWatch metrics namespace"
    )
    alarm_error_rate_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Error rate threshold for alarms (0.0-1.0, e.g., 0.05 = 5%)"
    )


class Config(BaseModel):
    """
    Central configuration for BDO Market Insights system.
    
    Loads configuration from environment variables with validation and defaults.
    Supports environment-specific configuration overrides.
    
    Example:
        >>> config = Config.from_env()
        >>> print(config.database.pool_size)
        5
        >>> print(config.external_api.rate_limit_requests)
        100
    """
    
    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Deployment environment (development, staging, production)"
    )
    aws_region: str = Field(
        default="us-east-1",
        description="AWS region for all services"
    )
    
    # Component configurations
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    external_api: ExternalAPIConfig = Field(default_factory=ExternalAPIConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    batch_processing: BatchProcessingConfig = Field(default_factory=BatchProcessingConfig)
    data_retention: DataRetentionConfig = Field(default_factory=DataRetentionConfig)
    api_gateway: APIGatewayConfig = Field(default_factory=APIGatewayConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    category: CategoryConfig = Field(default_factory=CategoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    
    @classmethod
    def from_env(cls, env_prefix: str = "BDO_") -> "Config":
        """
        Load configuration from environment variables.
        
        Environment variables should be prefixed with env_prefix and use
        double underscores to separate nested configuration levels.
        
        Examples:
            BDO_ENVIRONMENT=production
            BDO_DATABASE__POOL_SIZE=10
            BDO_EXTERNAL_API__RATE_LIMIT_REQUESTS=200
            BDO_RETRY__MAX_ATTEMPTS=5
        
        Args:
            env_prefix: Prefix for environment variables (default: "BDO_")
            
        Returns:
            Config: Validated configuration instance
            
        Raises:
            ValidationError: If configuration values are invalid
        """
        config_dict: Dict[str, Any] = {}
        
        # Load top-level configuration
        environment = os.getenv(f"{env_prefix}ENVIRONMENT", "development")
        config_dict["environment"] = environment
        
        aws_region = os.getenv(f"{env_prefix}AWS_REGION", "us-east-1")
        config_dict["aws_region"] = aws_region
        
        # Load nested configurations
        config_dict["database"] = cls._load_nested_config(
            env_prefix, "DATABASE", DatabaseConfig
        )
        config_dict["external_api"] = cls._load_nested_config(
            env_prefix, "EXTERNAL_API", ExternalAPIConfig
        )
        config_dict["retry"] = cls._load_nested_config(
            env_prefix, "RETRY", RetryConfig
        )
        config_dict["batch_processing"] = cls._load_nested_config(
            env_prefix, "BATCH_PROCESSING", BatchProcessingConfig
        )
        config_dict["data_retention"] = cls._load_nested_config(
            env_prefix, "DATA_RETENTION", DataRetentionConfig
        )
        config_dict["api_gateway"] = cls._load_nested_config(
            env_prefix, "API_GATEWAY", APIGatewayConfig
        )
        config_dict["query"] = cls._load_nested_config(
            env_prefix, "QUERY", QueryConfig
        )
        config_dict["category"] = cls._load_nested_config(
            env_prefix, "CATEGORY", CategoryConfig
        )
        config_dict["logging"] = cls._load_nested_config(
            env_prefix, "LOGGING", LoggingConfig
        )
        config_dict["monitoring"] = cls._load_nested_config(
            env_prefix, "MONITORING", MonitoringConfig
        )
        
        return cls(**config_dict)
    
    @staticmethod
    def _load_nested_config(
        env_prefix: str,
        section: str,
        config_class: type
    ) -> Dict[str, Any]:
        """
        Load nested configuration from environment variables.
        
        Args:
            env_prefix: Prefix for environment variables
            section: Configuration section name (e.g., "DATABASE")
            config_class: Pydantic model class for this section
            
        Returns:
            dict: Configuration values for this section
        """
        config_dict = {}
        section_prefix = f"{env_prefix}{section}__"
        
        # Get all fields from the config class
        for field_name, field_info in config_class.model_fields.items():
            env_var_name = f"{section_prefix}{field_name.upper()}"
            env_value = os.getenv(env_var_name)
            
            if env_value is not None:
                # Convert string to appropriate type
                field_type = field_info.annotation
                
                if field_type == bool:
                    config_dict[field_name] = env_value.lower() in ('true', '1', 'yes', 'on')
                elif field_type == int:
                    config_dict[field_name] = int(env_value)
                elif field_type == float:
                    config_dict[field_name] = float(env_value)
                elif field_type == List[str]:
                    # Parse comma-separated list
                    config_dict[field_name] = [s.strip() for s in env_value.split(',')]
                else:
                    config_dict[field_name] = env_value
        
        return config_dict
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.
        
        Returns:
            dict: Configuration as nested dictionary
        """
        return self.model_dump()
    
    def get_environment_name(self) -> str:
        """
        Get the current environment name.
        
        Returns:
            str: Environment name (development, staging, production)
        """
        return self.environment.value
    
    def is_production(self) -> bool:
        """
        Check if running in production environment.
        
        Returns:
            bool: True if production, False otherwise
        """
        return self.environment == Environment.PRODUCTION
    
    def is_development(self) -> bool:
        """
        Check if running in development environment.
        
        Returns:
            bool: True if development, False otherwise
        """
        return self.environment == Environment.DEVELOPMENT


# Custom exceptions for configuration errors

class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass


class InvalidConfigurationError(ConfigurationError):
    """Raised when configuration values are invalid."""
    pass


class MissingConfigurationError(ConfigurationError):
    """Raised when required configuration is missing."""
    pass


# Global configuration instance (lazy-loaded)
_config_instance: Optional[Config] = None


def get_config(reload: bool = False) -> Config:
    """
    Get the global configuration instance.
    
    Loads configuration from environment variables on first call.
    Subsequent calls return the cached instance unless reload=True.
    
    Args:
        reload: If True, reload configuration from environment
        
    Returns:
        Config: Global configuration instance
        
    Example:
        >>> config = get_config()
        >>> print(config.database.pool_size)
        5
    """
    global _config_instance
    
    if _config_instance is None or reload:
        _config_instance = Config.from_env()
    
    return _config_instance
