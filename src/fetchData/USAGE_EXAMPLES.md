# fetchData - Internal Function Documentation

> **Note**: This is an internal ETL function triggered by Step Functions. It is not exposed via the public API Gateway. This documentation is for developers working on the ETL pipeline.

## Overview
The `fetchData` Lambda function fetches market data from the External BDO Market API. It receives item IDs from `retrieveIdList`, processes them in configurable batches, and implements robust error handling with rate limiting, retry logic, and circuit breaker patterns.

## Function Invocation

### Basic Usage

**Input Event:**
```json
{
  "item_ids": [1001, 1002, 1003, 1004],
  "batch_size": 50,
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Output:**
```json
{
  "raw_data": [
    {
      "itemId": 1001,
      "price": 15000,
      "stock": 50,
      "lastUpdated": "2026-03-10T12:00:00Z"
    },
    {
      "itemId": 1002,
      "price": 20000,
      "stock": 30,
      "lastUpdated": "2026-03-10T12:00:00Z"
    }
  ],
  "scrape_endpoint": "https://api.example.com/v1/region/MarketData",
  "scrape_time": "2026-03-10T12:00:00.123456+00:00",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "metadata": {
    "total_items_requested": 4,
    "total_records_fetched": 4,
    "batches_processed": 1,
    "batches_failed": 0,
    "batch_size": 50
  }
}
```

### Custom Batch Size

**Input Event:**
```json
{
  "item_ids": [1001, 1002, 1003, 1004, 1005, 1006],
  "batch_size": 2,
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

This will process items in 3 batches of 2 items each.

### Large Dataset (Multiple Batches)

**Input Event:**
```json
{
  "item_ids": [1001, 1002, ..., 1150],
  "batch_size": 50,
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Output Metadata:**
```json
{
  "metadata": {
    "total_items_requested": 150,
    "total_records_fetched": 150,
    "batches_processed": 3,
    "batches_failed": 0,
    "batch_size": 50
  }
}
```

## Features

### Batch Processing
Items are processed in configurable batches to optimize API calls and stay within rate limits:
- Default batch size: 50 items
- Maximum batch size: 100 items (enforced)
- Configurable via `batch_size` parameter (1-100)

### Rate Limiting
Automatic rate limiting to respect External API limits:
- Default: 60 calls per minute
- Tracks calls in a sliding window
- Automatically waits when limit is reached
- Configurable via `RATE_LIMIT_PER_MINUTE` environment variable

### Retry Logic
Automatic retries for transient failures:
- Maximum 3 attempts per request
- Exponential backoff (base 2.0)
- Retries on: Network errors, rate limit errors (429), service unavailable (503)
- Respects `Retry-After` header from API

### Circuit Breaker
Fail-fast mechanism to prevent cascading failures:
- Opens after 5 consecutive failures (configurable)
- Timeout: 60 seconds (configurable)
- When open, remaining batches fail immediately
- Prevents overwhelming a failing API

### Error Handling

#### Partial Success
If some batches fail, the function returns successfully with partial data:

**Output:**
```json
{
  "raw_data": [...],
  "metadata": {
    "total_items_requested": 200,
    "total_records_fetched": 100,
    "batches_processed": 2,
    "batches_failed": 2,
    "batch_size": 50
  }
}
```

#### Circuit Breaker Open
When circuit breaker opens, processing stops immediately:

**Output:**
```json
{
  "raw_data": [...],
  "metadata": {
    "total_items_requested": 200,
    "total_records_fetched": 50,
    "batches_processed": 1,
    "batches_failed": 3,
    "batch_size": 50
  }
}
```

### Observability

#### CloudWatch Metrics
Automatically emitted metrics:
- `APICallSuccess`: Successful API calls
- `APICallFailure`: Failed API calls
- `APIResponseTime`: Response time in milliseconds
- `ETLFunctionLatency`: Total function execution time
- `RecordsFetched`: Number of records retrieved

#### X-Ray Tracing
Detailed tracing for:
- API call duration
- Batch processing
- Rate limiting waits
- Circuit breaker state changes

#### Structured Logging
Comprehensive logs include:
- Batch processing progress
- API call details
- Rate limiting events
- Circuit breaker state changes
- Error details with context

## Configuration

### Environment Variables

```bash
# API Configuration (REQUIRED)
BASE_URL=https://api.example.com/v1
REGION=us-east
API_ENDPOINT=MarketData

# Batch Configuration
BATCH_SIZE=50
MAX_BATCH_SIZE=100

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60

# Circuit Breaker
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=60
```

**Note:** `BASE_URL`, `REGION`, and `API_ENDPOINT` are required and must be set in Lambda environment variables.

### Step Functions Integration

```json
{
  "Comment": "ETL Pipeline",
  "StartAt": "RetrieveIdList",
  "States": {
    "RetrieveIdList": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:retrieveIdList",
      "Next": "FetchData"
    },
    "FetchData": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:fetchData",
      "Parameters": {
        "item_ids.$": "$.item_ids",
        "batch_size": 50,
        "correlation_id.$": "$.correlation_id"
      },
      "Next": "CleanData"
    }
  }
}
```

## Use Cases

1. **Standard ETL**: Fetch market data for all items in a category
2. **Incremental updates**: Fetch data for a subset of items
3. **Rate-limited APIs**: Automatically handle rate limits with batching
4. **Resilient fetching**: Continue processing even if some batches fail
5. **Large datasets**: Process thousands of items efficiently

## Performance Considerations

### Batch Size Selection
- Smaller batches (10-25): Better for rate-limited scenarios, more granular error handling
- Medium batches (50): Balanced approach, good default
- Larger batches (75-100): Fewer API calls, faster for large datasets

### Rate Limiting Impact
With 60 calls/minute limit:
- Batch size 50: ~3,000 items/minute
- Batch size 100: ~6,000 items/minute

### Lambda Timeout
Ensure Lambda timeout accommodates:
- Number of batches × (API response time + rate limit wait)
- Recommended: 5 minutes for typical workloads

## Testing

Run unit tests:
```bash
pytest tests/unit/test_fetch_data.py -v
```

Run property-based tests:
```bash
pytest tests/property/test_fetch_data_properties.py -v
```

## Common Scenarios

### Scenario 1: Small Dataset
```json
{
  "item_ids": [1001, 1002, 1003],
  "batch_size": 50,
  "correlation_id": "test-123"
}
```
Result: Single batch, fast execution

### Scenario 2: Large Dataset
```json
{
  "item_ids": [1001, ..., 5000],
  "batch_size": 100,
  "correlation_id": "test-456"
}
```
Result: 50 batches, rate limiting applied

### Scenario 3: API Degradation
If API starts failing:
1. Retries attempt 3 times per batch
2. After 5 consecutive failures, circuit breaker opens
3. Remaining batches fail fast
4. Function returns partial data with metadata

### Scenario 4: Rate Limit Hit
If rate limit is exceeded:
1. Function automatically waits for rate limit window to reset
2. Processing continues after wait
3. All batches complete successfully
4. Logs show wait events
