"""
Example usage of the configuration management module.

This file demonstrates how to use the Config class to load and access
configuration values in Lambda functions.
"""

from common.config import get_config, Config, Environment

# Example 1: Get global configuration instance
def example_basic_usage():
    """Basic usage - get configuration with defaults."""
    config = get_config()
    
    print(f"Environment: {config.get_environment_name()}")
    print(f"Database pool size: {config.database.pool_size}")
    print(f"API rate limit: {config.external_api.rate_limit_requests}")
    print(f"Retry max attempts: {config.retry.max_attempts}")


# Example 2: Check environment
def example_environment_check():
    """Check which environment we're running in."""
    config = get_config()
    
    if config.is_production():
        print("Running in PRODUCTION - using strict settings")
        # Use production-specific logic
    elif config.is_development():
        print("Running in DEVELOPMENT - using relaxed settings")
        # Use development-specific logic
    else:
        print(f"Running in {config.get_environment_name()}")


# Example 3: Access nested configuration
def example_nested_config():
    """Access nested configuration values."""
    config = get_config()
    
    # Database configuration
    db_config = config.database
    print(f"DB Secret: {db_config.secret_name}")
    print(f"DB Pool Size: {db_config.pool_size}")
    print(f"DB Pool Timeout: {db_config.pool_timeout}s")
    
    # External API configuration
    api_config = config.external_api
    print(f"API Base URL: {api_config.base_url}")
    print(f"API Timeout: {api_config.timeout}s")
    print(f"API Rate Limit: {api_config.rate_limit_requests} req/min")
    
    # Retry configuration
    retry_config = config.retry
    print(f"Max Retry Attempts: {retry_config.max_attempts}")
    print(f"Backoff Base: {retry_config.backoff_base}s")
    print(f"Use Jitter: {retry_config.jitter}")


# Example 4: Lambda function using configuration
def lambda_handler(event, context):
    """Example Lambda function using configuration."""
    # Get configuration
    config = get_config()
    
    # Use configuration values
    pool_size = config.database.pool_size
    timeout = config.external_api.timeout
    max_attempts = config.retry.max_attempts
    
    print(f"Initializing with pool_size={pool_size}, timeout={timeout}s")
    
    # Your Lambda logic here
    return {
        'statusCode': 200,
        'body': 'Success'
    }


# Example 5: Environment variable configuration
"""
To override configuration via environment variables, set them with the BDO_ prefix:

# Top-level configuration
export BDO_ENVIRONMENT=production
export BDO_AWS_REGION=us-west-2

# Database configuration (use double underscore for nesting)
export BDO_DATABASE__POOL_SIZE=10
export BDO_DATABASE__POOL_TIMEOUT=60
export BDO_DATABASE__IDLE_TIMEOUT=600

# External API configuration
export BDO_EXTERNAL_API__RATE_LIMIT_REQUESTS=200
export BDO_EXTERNAL_API__TIMEOUT=45

# Retry configuration
export BDO_RETRY__MAX_ATTEMPTS=5
export BDO_RETRY__BACKOFF_BASE=3.0

# Batch processing configuration
export BDO_BATCH_PROCESSING__BATCH_SIZE=75
export BDO_BATCH_PROCESSING__MAX_BATCH_SIZE=150

# Data retention configuration
export BDO_DATA_RETENTION__DETAILED_RETENTION_DAYS=120
export BDO_DATA_RETENTION__SUMMARY_RETENTION_DAYS=1095
export BDO_DATA_RETENTION__ARCHIVE_TO_GLACIER=true

# API Gateway configuration
export BDO_API_GATEWAY__RATE_LIMIT_PER_KEY=150
export BDO_API_GATEWAY__BURST_LIMIT=30
export BDO_API_GATEWAY__CORS_ALLOWED_ORIGINS=https://example.com,https://app.example.com

# Query configuration
export BDO_QUERY__DEFAULT_LIMIT=200
export BDO_QUERY__MAX_LIMIT=2000

# Logging configuration
export BDO_LOGGING__LOG_LEVEL=DEBUG
export BDO_LOGGING__JSON_FORMAT=true

# Monitoring configuration
export BDO_MONITORING__ENABLE_XRAY=true
export BDO_MONITORING__ENABLE_CUSTOM_METRICS=true
"""


# Example 6: Reload configuration
def example_reload_config():
    """Reload configuration from environment (useful for testing)."""
    # Get initial config
    config1 = get_config()
    print(f"Initial pool size: {config1.database.pool_size}")
    
    # Change environment variable (in real code, this would be set externally)
    import os
    os.environ['BDO_DATABASE__POOL_SIZE'] = '15'
    
    # Reload configuration
    config2 = get_config(reload=True)
    print(f"Reloaded pool size: {config2.database.pool_size}")


# Example 7: Convert to dictionary
def example_to_dict():
    """Convert configuration to dictionary for logging or debugging."""
    config = get_config()
    config_dict = config.to_dict()
    
    # Now you can log or inspect the entire configuration
    import json
    print(json.dumps(config_dict, indent=2, default=str))


# Example 8: Create custom configuration (for testing)
def example_custom_config():
    """Create custom configuration instance (useful for testing)."""
    from common.config import DatabaseConfig, RetryConfig
    
    # Create custom config with specific values
    custom_config = Config(
        environment=Environment.DEVELOPMENT,
        database=DatabaseConfig(pool_size=3, pool_timeout=15),
        retry=RetryConfig(max_attempts=2, backoff_base=1.0)
    )
    
    print(f"Custom pool size: {custom_config.database.pool_size}")
    print(f"Custom max attempts: {custom_config.retry.max_attempts}")


if __name__ == "__main__":
    print("=== Example 1: Basic Usage ===")
    example_basic_usage()
    
    print("\n=== Example 2: Environment Check ===")
    example_environment_check()
    
    print("\n=== Example 3: Nested Configuration ===")
    example_nested_config()
    
    print("\n=== Example 7: Convert to Dictionary ===")
    example_to_dict()
