# Configuration Management

This document describes all configurable parameters for the BDO Market Insights system.

## Overview

The configuration system provides centralized management of all system parameters with:
- **Environment variable loading** with validation
- **Type-safe configuration** using Pydantic models
- **Default values** for all optional parameters
- **Constraint validation** to ensure valid configurations
- **Environment-specific overrides** (development, staging, production)

## Usage

```python
from common.config import get_config

# Get global configuration instance
config = get_config()

# Access configuration values
pool_size = config.database.pool_size
rate_limit = config.external_api.rate_limit_requests
```

## Environment Variables

All configuration can be overridden via environment variables using the `BDO_` prefix and double underscores (`__`) for nesting.

### Format

```bash
BDO_<SECTION>__<PARAMETER>=<value>
```

### Examples

```bash
# Top-level
export BDO_ENVIRONMENT=production
export BDO_AWS_REGION=us-west-2

# Database
export BDO_DATABASE__POOL_SIZE=10

# External API
export BDO_EXTERNAL_API__RATE_LIMIT_REQUESTS=200
```

## Configuration Sections

### Top-Level Configuration

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `environment` | string | `development` | `development`, `staging`, `production` | Deployment environment |
| `aws_region` | string | `us-east-1` | Any AWS region | AWS region for all services |

**Environment Variable:**
```bash
BDO_ENVIRONMENT=production
BDO_AWS_REGION=us-west-2
```

---

### Database Configuration

Configuration for PostgreSQL database connections and connection pooling.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `secret_name` | string | `bdo-db-credentials` | - | AWS Secrets Manager secret name for database credentials |
| `pool_size` | int | `5` | 1-20 | Maximum number of connections in the pool |
| `pool_timeout` | int | `30` | 5-300 | Timeout in seconds for acquiring a connection from pool |
| `idle_timeout` | int | `300` | 60-3600 | Timeout in seconds before closing idle connections |
| `query_timeout` | int | `30` | 1-300 | Timeout in seconds for query execution |
| `slow_query_threshold` | float | `1.0` | 0.1-60.0 | Threshold in seconds for logging slow queries |

**Environment Variables:**
```bash
BDO_DATABASE__SECRET_NAME=my-db-credentials
BDO_DATABASE__POOL_SIZE=10
BDO_DATABASE__POOL_TIMEOUT=60
BDO_DATABASE__IDLE_TIMEOUT=600
BDO_DATABASE__QUERY_TIMEOUT=45
BDO_DATABASE__SLOW_QUERY_THRESHOLD=2.0
```

---

### External API Configuration

Configuration for the BDO market data API integration.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `secret_name` | string | `bdo-api-credentials` | - | AWS Secrets Manager secret name for API credentials |
| `base_url` | string | `https://api.arsha.io` | - | Base URL for the BDO market data API |
| `timeout` | int | `30` | 5-120 | Request timeout in seconds |
| `rate_limit_requests` | int | `100` | 1-1000 | Maximum requests per minute |
| `rate_limit_window` | int | `60` | 1-300 | Rate limit window in seconds |
| `circuit_breaker_threshold` | int | `5` | 1-20 | Number of failures before opening circuit breaker |
| `circuit_breaker_timeout` | int | `60` | 10-600 | Seconds to wait before attempting to close circuit breaker |

**Environment Variables:**
```bash
BDO_EXTERNAL_API__SECRET_NAME=my-api-credentials
BDO_EXTERNAL_API__BASE_URL=https://api.example.com
BDO_EXTERNAL_API__TIMEOUT=45
BDO_EXTERNAL_API__RATE_LIMIT_REQUESTS=200
BDO_EXTERNAL_API__RATE_LIMIT_WINDOW=60
BDO_EXTERNAL_API__CIRCUIT_BREAKER_THRESHOLD=10
BDO_EXTERNAL_API__CIRCUIT_BREAKER_TIMEOUT=120
```

---

### Retry Configuration

Configuration for retry logic and exponential backoff.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `max_attempts` | int | `3` | 1-10 | Maximum number of retry attempts |
| `backoff_base` | float | `2.0` | 1.0-10.0 | Base delay in seconds for exponential backoff |
| `backoff_multiplier` | float | `2.0` | 1.0-5.0 | Multiplier for exponential backoff |
| `max_backoff` | float | `60.0` | 1.0-300.0 | Maximum backoff delay in seconds |
| `jitter` | bool | `true` | - | Whether to add random jitter to backoff delays |

**Environment Variables:**
```bash
BDO_RETRY__MAX_ATTEMPTS=5
BDO_RETRY__BACKOFF_BASE=3.0
BDO_RETRY__BACKOFF_MULTIPLIER=2.5
BDO_RETRY__MAX_BACKOFF=120.0
BDO_RETRY__JITTER=true
```

---

### Batch Processing Configuration

Configuration for ETL pipeline batch processing.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `batch_size` | int | `50` | 1-100 | Number of items to process per batch |
| `max_batch_size` | int | `100` | 1-500 | Maximum batch size before splitting |

**Constraints:**
- `max_batch_size` must be >= `batch_size`

**Environment Variables:**
```bash
BDO_BATCH_PROCESSING__BATCH_SIZE=75
BDO_BATCH_PROCESSING__MAX_BATCH_SIZE=150
```

---

### Data Retention Configuration

Configuration for data retention policies and archival.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `detailed_retention_days` | int | `90` | 1-365 | Days to retain detailed market data records |
| `summary_retention_days` | int | `730` | 90-3650 | Days to retain aggregated summary data (~2 years default) |
| `archive_to_glacier` | bool | `true` | - | Whether to archive old summaries to S3 Glacier |
| `glacier_bucket` | string | `bdo-market-data-archive` | - | S3 bucket name for Glacier archival |

**Constraints:**
- `summary_retention_days` must be >= `detailed_retention_days`

**Environment Variables:**
```bash
BDO_DATA_RETENTION__DETAILED_RETENTION_DAYS=120
BDO_DATA_RETENTION__SUMMARY_RETENTION_DAYS=1095
BDO_DATA_RETENTION__ARCHIVE_TO_GLACIER=true
BDO_DATA_RETENTION__GLACIER_BUCKET=my-archive-bucket
```

---

### API Gateway Configuration

Configuration for API Gateway rate limiting and CORS.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `rate_limit_per_key` | int | `100` | 1-10000 | Requests per minute per API key |
| `burst_limit` | int | `20` | 1-1000 | Maximum concurrent requests per API key |
| `quota_limit` | int | `10000` | 100-1000000 | Daily request quota per API key |
| `cors_allowed_origins` | list[str] | `["*"]` | - | List of allowed CORS origins (use `["*"]` for all) |
| `cache_ttl` | int | `300` | 0-3600 | Cache TTL in seconds for GET responses (0=no cache) |

**Environment Variables:**
```bash
BDO_API_GATEWAY__RATE_LIMIT_PER_KEY=150
BDO_API_GATEWAY__BURST_LIMIT=30
BDO_API_GATEWAY__QUOTA_LIMIT=20000
BDO_API_GATEWAY__CORS_ALLOWED_ORIGINS=https://example.com,https://app.example.com
BDO_API_GATEWAY__CACHE_TTL=600
```

---

### Query Configuration

Configuration for query pipeline behavior.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `default_limit` | int | `100` | 1-1000 | Default number of results to return |
| `max_limit` | int | `1000` | 1-10000 | Maximum allowed limit parameter |
| `default_date_range_days` | int | `30` | 1-365 | Default date range in days if not specified |

**Constraints:**
- `max_limit` must be >= `default_limit`

**Environment Variables:**
```bash
BDO_QUERY__DEFAULT_LIMIT=200
BDO_QUERY__MAX_LIMIT=2000
BDO_QUERY__DEFAULT_DATE_RANGE_DAYS=60
```

---

### Category Configuration

Configuration for item category management.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `valid_categories` | list[str] | `["Uncategorized", "Accessory", "Buff", "Costume"]` | - | List of valid item categories |
| `default_category` | string | `Uncategorized` | Must be in `valid_categories` | Default category for items without a category |

**Constraints:**
- `default_category` must be one of `valid_categories`

**Environment Variables:**
```bash
BDO_CATEGORY__VALID_CATEGORIES=Uncategorized,Accessory,Buff,Costume,Weapon
BDO_CATEGORY__DEFAULT_CATEGORY=Uncategorized
```

---

### Logging Configuration

Configuration for structured logging.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `log_level` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Logging level |
| `json_format` | bool | `true` | - | Whether to output logs in JSON format |
| `include_trace_id` | bool | `true` | - | Whether to include X-Ray trace ID in logs |

**Environment Variables:**
```bash
BDO_LOGGING__LOG_LEVEL=DEBUG
BDO_LOGGING__JSON_FORMAT=true
BDO_LOGGING__INCLUDE_TRACE_ID=true
```

---

### Monitoring Configuration

Configuration for monitoring and observability.

| Parameter | Type | Default | Valid Range | Description |
|-----------|------|---------|-------------|-------------|
| `enable_xray` | bool | `true` | - | Whether to enable X-Ray tracing |
| `enable_custom_metrics` | bool | `true` | - | Whether to emit custom CloudWatch metrics |
| `metrics_namespace` | string | `BDOMarketInsights` | - | CloudWatch metrics namespace |
| `alarm_error_rate_threshold` | float | `0.05` | 0.0-1.0 | Error rate threshold for alarms (e.g., 0.05 = 5%) |

**Environment Variables:**
```bash
BDO_MONITORING__ENABLE_XRAY=true
BDO_MONITORING__ENABLE_CUSTOM_METRICS=true
BDO_MONITORING__METRICS_NAMESPACE=MyCustomNamespace
BDO_MONITORING__ALARM_ERROR_RATE_THRESHOLD=0.10
```

---

## Validation

All configuration values are validated when loaded:

- **Type validation**: Values must match the expected type (int, float, bool, string, list)
- **Range validation**: Numeric values must be within specified ranges
- **Constraint validation**: Cross-field constraints are enforced (e.g., max >= default)
- **Enum validation**: String values must be from allowed sets

If validation fails, a `ValidationError` is raised with details about the invalid values.

## Environment-Specific Configuration

The system supports three environments:

1. **development**: For local development and testing
2. **staging**: For pre-production testing
3. **production**: For production deployment

Set the environment using:
```bash
export BDO_ENVIRONMENT=production
```

You can check the current environment in code:
```python
config = get_config()

if config.is_production():
    # Production-specific logic
elif config.is_development():
    # Development-specific logic
```

## Testing

For testing, you can create custom configuration instances:

```python
from common.config import Config, DatabaseConfig, Environment

# Create test configuration
test_config = Config(
    environment=Environment.DEVELOPMENT,
    database=DatabaseConfig(pool_size=2, pool_timeout=10)
)
```

Or reload configuration after changing environment variables:

```python
import os
os.environ['BDO_DATABASE__POOL_SIZE'] = '10'

# Reload configuration
config = get_config(reload=True)
```

## Best Practices

1. **Use environment variables** for environment-specific values (credentials, URLs, limits)
2. **Keep defaults sensible** for development environments
3. **Validate early** by loading configuration at Lambda initialization
4. **Log configuration** (excluding secrets) at startup for debugging
5. **Use type hints** when accessing configuration values
6. **Test with different configurations** to ensure robustness

## Example Lambda Function

```python
from common.config import get_config
from common.logging import StructuredLogger

# Load configuration once at module level (outside handler)
config = get_config()
logger = StructuredLogger(__name__, "init")

# Log configuration (excluding secrets)
logger.info(
    "Configuration loaded",
    environment=config.get_environment_name(),
    pool_size=config.database.pool_size,
    rate_limit=config.external_api.rate_limit_requests
)

def lambda_handler(event, context):
    """Lambda function using configuration."""
    # Use configuration values
    timeout = config.external_api.timeout
    max_attempts = config.retry.max_attempts
    
    # Your Lambda logic here
    return {'statusCode': 200}
```

## Troubleshooting

### Configuration not loading from environment variables

- Ensure environment variables use the correct prefix (`BDO_`)
- Use double underscores (`__`) for nesting
- Check variable names match field names exactly (case-sensitive)

### Validation errors

- Check that values are within valid ranges
- Ensure cross-field constraints are satisfied (e.g., max >= default)
- Verify enum values are from allowed sets

### Type conversion errors

- Boolean values: use `true`, `false`, `1`, `0`, `yes`, `no`, `on`, `off`
- Lists: use comma-separated values (e.g., `value1,value2,value3`)
- Numbers: ensure valid integer or float format

## See Also

- `config.py` - Configuration module implementation
- `config_example.py` - Usage examples
- `test_configuration_properties.py` - Property-based tests
