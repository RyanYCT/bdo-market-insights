# retrieveIdList - Internal Function Documentation

> **Note**: This is an internal ETL function triggered by EventBridge Scheduler. It is not exposed via the public API Gateway. This documentation is for developers working on the ETL pipeline.

## Overview
The `retrieveIdList` Lambda function retrieves item IDs from one or more DynamoDB tables. It now supports querying multiple tables in a single invocation.

## Function Invocation

### Single Table (Original Behavior)

**Input Event:**
```json
{
  "table_name": "bdo.accessory"
}
```

**Output:**
```json
{
  "item_ids": [1001, 1002, 1003, 1004],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Multiple Tables (New Feature)

**Input Event:**
```json
{
  "table_name": "bdo.accessory,bdo.buff"
}
```

**Output:**
```json
{
  "item_ids": [1001, 1002, 1003, 2001, 2002, 2003],
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**With Spaces (Also Supported):**
```json
{
  "table_name": "bdo.accessory, bdo.buff, bdo.weapon"
}
```

## Features

### Duplicate Removal
When querying multiple tables, duplicate IDs are automatically removed while preserving order:

**Example:**
- Table 1: `[1001, 1002, 1003]`
- Table 2: `[1002, 1003, 1004]`
- Table 3: `[1003, 1004, 1005]`

**Result:** `[1001, 1002, 1003, 1004, 1005]`

### Error Handling
If any table fails to be queried, the entire operation fails with an appropriate error message and returns HTTP 500.

### Logging & Observability
The function logs detailed information about each table processed:
- Number of tables being queried
- IDs retrieved from each table
- Total items, unique items, and duplicates removed
- Per-table statistics in X-Ray metadata

## Configuration

### Environment Variable
You can set the default table name(s) via environment variable:
```bash
DYNAMODB_TABLE_NAME=bdo.accessory,bdo.buff
```

### Step Functions Integration
When used in Step Functions, pass the `table_name` in the input state:
```json
{
  "Comment": "ETL Pipeline",
  "StartAt": "RetrieveIdList",
  "States": {
    "RetrieveIdList": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:retrieveIdList",
      "Parameters": {
        "table_name": "bdo.accessory,bdo.buff"
      },
      "Next": "FetchData"
    }
  }
}
```

## Use Cases

1. **Category-based queries**: Query multiple item categories in one call
2. **Cross-table aggregation**: Combine IDs from related tables
3. **Batch processing**: Process multiple data sources efficiently
4. **Backward compatibility**: Single table queries work exactly as before

## Testing

Run unit tests:
```bash
pytest tests/unit/test_retrieve_id_list.py -v
```

See `tests/unit/test_retrieve_id_list.py` for comprehensive test examples including multiple table scenarios.

