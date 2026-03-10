# cleanData - Internal Function Documentation

> **Note**: This is an internal ETL function triggered by Step Functions. It is not exposed via the public API Gateway. This documentation is for developers working on the ETL pipeline.

## Overview
The `cleanData` Lambda function transforms and validates raw market data from the External BDO Market API. It receives raw data from `fetchData`, transforms field names to match the internal schema, validates all records against Pydantic models, and filters out invalid records with comprehensive logging.

## Function Invocation

### Basic Usage

**Input Event:**
```json
{
  "raw_data": [
    {
      "id": 1001,
      "sid": 0,
      "currentStock": 50,
      "totalTrades": 120,
      "lastSoldPrice": 15000,
      "lastSoldTime": 1710072000
    },
    {
      "id": 1002,
      "sid": 0,
      "currentStock": 30,
      "totalTrades": 85,
      "lastSoldPrice": 20000,
      "lastSoldTime": 1710072000
    }
  ],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Output:**
```json
{
  "cleaned_data": [
    {
      "item_id": 1001,
      "sid": 0,
      "current_stock": 50,
      "total_trades": 120,
      "last_sold_price": 15000,
      "last_sold_time": "2026-03-10T12:00:00+00:00"
    },
    {
      "item_id": 1002,
      "sid": 0,
      "current_stock": 30,
      "total_trades": 85,
      "last_sold_price": 20000,
      "last_sold_time": "2026-03-10T12:00:00+00:00"
    }
  ],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "metadata": {
    "total_records": 2,
    "valid_records": 2,
    "invalid_records": 0,
    "filtered_items": []
  }
}
```

### With Invalid Records

**Input Event:**
```json
{
  "raw_data": [
    {
      "id": 1001,
      "sid": 0,
      "currentStock": 50,
      "totalTrades": 120,
      "lastSoldPrice": 15000,
      "lastSoldTime": 1710072000
    },
    {
      "id": 1002,
      "sid": 0,
      "currentStock": -10,
      "totalTrades": 85,
      "lastSoldPrice": 20000,
      "lastSoldTime": 1710072000
    },
    {
      "id": null,
      "sid": 0,
      "currentStock": 25,
      "totalTrades": 50,
      "lastSoldPrice": 18000,
      "lastSoldTime": 1710072000
    }
  ],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Output:**
```json
{
  "cleaned_data": [
    {
      "item_id": 1001,
      "sid": 0,
      "current_stock": 50,
      "total_trades": 120,
      "last_sold_price": 15000,
      "last_sold_time": "2026-03-10T12:00:00+00:00"
    }
  ],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "metadata": {
    "total_records": 3,
    "valid_records": 1,
    "invalid_records": 2,
    "filtered_items": [1002, null]
  }
}
```

## Features

### Field Transformation
Automatically transforms API field names to internal schema format:
- `id` → `item_id`
- `currentStock` → `current_stock`
- `totalTrades` → `total_trades`
- `lastSoldPrice` → `last_sold_price`
- `lastSoldTime` → `last_sold_time` (with datetime conversion)

### Datetime Handling
Flexible datetime parsing for `last_sold_time`:
- **Unix timestamps** (integers): Converted to UTC datetime
- **ISO format strings**: Parsed to datetime objects
- **Invalid formats**: Set to None and caught by validation

**Examples:**
```python
# Unix timestamp
1710072000 → "2026-03-10T12:00:00+00:00"

# ISO format
"2026-03-10T12:00:00Z" → "2026-03-10T12:00:00+00:00"

# Invalid
"invalid-date" → None (validation fails, record filtered)
```

### Schema Validation
All records validated against `MarketDataRecord` Pydantic model:
- `item_id`: Required, positive integer
- `sid`: Integer, defaults to 0
- `current_stock`: Required, non-negative integer
- `total_trades`: Required, non-negative integer
- `last_sold_price`: Required, non-negative integer
- `last_sold_time`: Required, valid datetime

### Invalid Record Filtering
Records that fail validation are automatically filtered out:
- Invalid records logged with detailed error information
- Valid records continue processing
- Metadata tracks filtered items for observability

**Common Validation Failures:**
- Negative values for stock, trades, or price
- Missing required fields
- Invalid datetime formats
- Null or incorrect data types

### Error Handling

#### Partial Success
Function succeeds even if some records are invalid:

**Scenario:** 100 records, 5 invalid
```json
{
  "metadata": {
    "total_records": 100,
    "valid_records": 95,
    "invalid_records": 5,
    "filtered_items": [1002, 1015, 1023, 1045, 1089]
  }
}
```

#### Complete Failure
Function fails only if input validation fails:

**Invalid Input:**
```json
{
  "raw_data": "not-an-array",
  "correlation_id": "test-123"
}
```

**Result:** HTTP 400 with validation error details

### Observability

#### CloudWatch Metrics
Automatically emitted metrics:
- `ETLFunctionLatency`: Total function execution time
- `ETLSuccess`: Successful completion with valid/invalid counts
- `ETLFailure`: Function failures with error type

#### Structured Logging
Comprehensive logs include:
- Input validation results
- Per-record validation failures with error details
- Summary statistics (total, valid, invalid)
- Filtered item IDs for tracking

**Example Log Entry:**
```json
{
  "level": "WARNING",
  "message": "Record validation failed, filtering out",
  "record_index": 5,
  "item_id": 1002,
  "validation_errors": [
    {
      "loc": ["current_stock"],
      "msg": "ensure this value is greater than or equal to 0",
      "type": "value_error.number.not_ge"
    }
  ]
}
```

## Configuration

### Environment Variables

No environment variables required. The function uses:
- Input from `fetchData` Lambda
- Shared schemas from `common.schemas`
- Metrics namespace: `BDOMarketInsights/ETL`

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
      "Next": "CleanData"
    },
    "CleanData": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:cleanData",
      "Parameters": {
        "raw_data.$": "$.raw_data",
        "correlation_id.$": "$.correlation_id"
      },
      "Next": "StoreData"
    }
  }
}
```

## Use Cases

1. **Data normalization**: Transform external API format to internal schema
2. **Quality assurance**: Filter out malformed or invalid records
3. **Type safety**: Ensure all downstream functions receive valid data
4. **Datetime standardization**: Convert various datetime formats to ISO 8601
5. **Defensive processing**: Continue pipeline even with partial data quality issues

## Validation Rules

### MarketDataRecord Schema

| Field | Type | Constraints | Example |
|-------|------|-------------|---------|
| `item_id` | int | Required, > 0 | 1001 |
| `sid` | int | Optional, default 0 | 0 |
| `current_stock` | int | Required, ≥ 0 | 50 |
| `total_trades` | int | Required, ≥ 0 | 120 |
| `last_sold_price` | int | Required, ≥ 0 | 15000 |
| `last_sold_time` | datetime | Required, valid datetime | "2026-03-10T12:00:00+00:00" |

### Common Validation Errors

**Negative Stock:**
```json
{
  "loc": ["current_stock"],
  "msg": "ensure this value is greater than or equal to 0",
  "type": "value_error.number.not_ge"
}
```

**Missing Required Field:**
```json
{
  "loc": ["item_id"],
  "msg": "field required",
  "type": "value_error.missing"
}
```

**Invalid Datetime:**
```json
{
  "loc": ["last_sold_time"],
  "msg": "invalid datetime format",
  "type": "value_error.datetime"
}
```

## Testing

Run unit tests:
```bash
pytest tests/unit/test_clean_data.py -v
```

Run property-based tests:
```bash
pytest tests/property/test_clean_data_properties.py -v
```

## Common Scenarios

### Scenario 1: All Valid Records
```json
{
  "raw_data": [
    {"id": 1001, "sid": 0, "currentStock": 50, "totalTrades": 120, "lastSoldPrice": 15000, "lastSoldTime": 1710072000},
    {"id": 1002, "sid": 0, "currentStock": 30, "totalTrades": 85, "lastSoldPrice": 20000, "lastSoldTime": 1710072000}
  ],
  "correlation_id": "test-123"
}
```
Result: All records cleaned and validated, 100% success rate

### Scenario 2: Mixed Valid/Invalid
```json
{
  "raw_data": [
    {"id": 1001, "sid": 0, "currentStock": 50, "totalTrades": 120, "lastSoldPrice": 15000, "lastSoldTime": 1710072000},
    {"id": 1002, "sid": 0, "currentStock": -10, "totalTrades": 85, "lastSoldPrice": 20000, "lastSoldTime": 1710072000}
  ],
  "correlation_id": "test-456"
}
```
Result: 1 valid record, 1 filtered out, metadata shows filtered item ID

### Scenario 3: API Format Changes
If API changes field names or formats:
1. Records fail validation
2. All records filtered out
3. Metadata shows 0 valid records
4. Logs show specific validation errors
5. Alerts triggered for investigation

### Scenario 4: Datetime Format Variations
```json
{
  "raw_data": [
    {"id": 1001, "lastSoldTime": 1710072000},
    {"id": 1002, "lastSoldTime": "2026-03-10T12:00:00Z"},
    {"id": 1003, "lastSoldTime": "2026-03-10T12:00:00+00:00"}
  ]
}
```
Result: All three datetime formats successfully parsed and normalized

## Performance Considerations

### Processing Speed
- Typical throughput: ~1,000 records/second
- Validation overhead: ~1ms per record
- Memory efficient: Processes records sequentially

### Lambda Configuration
Recommended settings:
- Memory: 256 MB (sufficient for most workloads)
- Timeout: 1 minute (handles up to 50,000 records)
- Reserved concurrency: Not required (stateless)

### Large Datasets
For datasets > 10,000 records:
- Consider batch processing in `fetchData`
- Monitor Lambda execution time
- Adjust timeout if needed

## Monitoring & Alerts

### Key Metrics to Monitor
1. **Invalid record rate**: Alert if > 5%
2. **Function errors**: Alert on any failures
3. **Execution time**: Alert if > 30 seconds
4. **Filtered items**: Track trends over time

### CloudWatch Alarms
Recommended alarms:
- High invalid record rate (> 5%)
- Function errors (> 0)
- Execution time anomalies

### Troubleshooting

**High invalid record rate:**
- Check API format changes
- Review validation error logs
- Verify schema compatibility

**Function timeouts:**
- Check dataset size
- Increase Lambda timeout
- Consider batch size reduction in `fetchData`

**All records filtered:**
- API format likely changed
- Check raw_data structure
- Update transformation logic if needed
