# X-Ray Tracing Configuration

This document describes how to enable and configure AWS X-Ray tracing for the BDO Market Insights Lambda functions.

## Overview

X-Ray tracing provides distributed tracing capabilities that allow you to:
- Track requests across multiple Lambda functions
- Visualize service dependencies and call patterns
- Identify performance bottlenecks
- Debug errors with detailed trace information
- Correlate logs with traces using trace IDs

## Lambda Configuration

### Enable X-Ray Tracing

For each Lambda function, enable X-Ray tracing in the AWS Console or via Infrastructure as Code:

**AWS Console:**
1. Navigate to Lambda function configuration
2. Go to "Configuration" → "Monitoring and operations tools"
3. Under "AWS X-Ray", click "Edit"
4. Enable "Active tracing"
5. Save changes

**CloudFormation/SAM:**
```yaml
Resources:
  RetrieveIdListFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: retrieveIdList
      Runtime: python3.14
      Handler: lambda_function.lambda_handler
      TracingConfig:
        Mode: Active  # Enable X-Ray tracing
      # ... other properties
```

**Terraform:**
```hcl
resource "aws_lambda_function" "retrieve_id_list" {
  function_name = "retrieveIdList"
  runtime       = "python3.14"
  handler       = "lambda_function.lambda_handler"
  
  tracing_config {
    mode = "Active"  # Enable X-Ray tracing
  }
  
  # ... other properties
}
```

**AWS CLI:**
```bash
aws lambda update-function-configuration \
  --function-name retrieveIdList \
  --tracing-config Mode=Active
```

### IAM Permissions

Ensure the Lambda execution role has X-Ray write permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

Or use the AWS managed policy:
```yaml
ManagedPolicyArns:
  - arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess
```

## Environment Variables

The X-Ray integration can be controlled via environment variables:

- `AWS_XRAY_TRACING_ENABLED`: Set to `false` to disable X-Ray (default: enabled in Lambda)
- `AWS_XRAY_CONTEXT_MISSING`: Set to `LOG_ERROR` to log missing context instead of throwing errors
- `AWS_XRAY_DAEMON_ADDRESS`: X-Ray daemon address (default: auto-detected in Lambda)

## Code Integration

### Automatic Instrumentation

The Lambda Layer automatically instruments common libraries when X-Ray is enabled:

- **boto3/botocore**: AWS SDK calls (DynamoDB, Secrets Manager, etc.)
- **requests/urllib3**: HTTP client calls
- **psycopg2**: PostgreSQL database queries

This happens automatically via `initialize_xray()` called when the common module is imported.

### Manual Instrumentation

Use the provided utilities for custom tracing:

```python
from common import create_subsegment, add_annotation, add_metadata

# Create subsegment for custom operation
with create_subsegment('custom_operation'):
    result = perform_operation()

# Add annotations (indexed, filterable)
add_annotation('user_id', 'user-123')
add_annotation('operation_type', 'batch_process')

# Add metadata (not indexed, any JSON data)
add_metadata('request_details', {
    'batch_size': 50,
    'item_count': 150
}, namespace='custom')
```

### Function Decorator

Trace entire functions:

```python
from common import trace_function

@trace_function(name='process_batch')
def process_batch(items):
    # Function execution will be traced
    return [process_item(item) for item in items]
```

## Structured Logging Integration

X-Ray trace IDs are automatically included in structured logs when X-Ray is enabled:

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "function_name": "retrieveIdList",
  "correlation_id": "uuid-v4",
  "xray_trace_id": "1-5e645f3e-1234567890abcdef12345678",
  "message": "Processing started"
}
```

This allows you to:
1. Find logs for a specific trace in CloudWatch Logs Insights
2. Navigate from X-Ray trace to related logs
3. Correlate distributed traces with log entries

## Viewing Traces

### AWS Console

1. Navigate to AWS X-Ray console
2. Click "Traces" in the left sidebar
3. Use filters to find specific traces:
   - Filter by annotation: `annotation.function_name = "retrieveIdList"`
   - Filter by error: `error = true`
   - Filter by duration: `duration > 5`

### Service Map

View the service map to see:
- All services and their dependencies
- Request rates and error rates
- Average latency for each service

### CloudWatch Logs Insights

Query logs by X-Ray trace ID:

```
fields @timestamp, log_message, xray_trace_id
| filter xray_trace_id = "1-5e645f3e-1234567890abcdef12345678"
| sort @timestamp asc
```

## Performance Impact

X-Ray tracing has minimal performance impact:
- Adds < 1ms latency per traced operation
- Minimal memory overhead
- Asynchronous trace submission (non-blocking)

## Sampling

X-Ray uses sampling to control costs and data volume:

**Default Sampling Rules:**
- First request per second: Always sampled
- Additional requests: 5% sampling rate

**Custom Sampling Rules:**
Configure in X-Ray console or via API to:
- Sample 100% of errors
- Sample specific operations at higher rates
- Reduce sampling for high-volume operations

## Troubleshooting

### Traces Not Appearing

1. Verify X-Ray is enabled: `Mode: Active` in Lambda configuration
2. Check IAM permissions: Lambda role has `xray:PutTraceSegments`
3. Check environment: `AWS_XRAY_TRACING_ENABLED` not set to `false`
4. Wait 1-2 minutes for traces to appear in console

### Missing Trace IDs in Logs

1. Verify X-Ray SDK is installed: Check `lambda_layer/requirements.txt`
2. Verify Lambda Layer is attached to function
3. Check that `initialize_xray()` is called (automatic in common module)

### Subsegment Errors

If you see "cannot find the current segment/subsegment" errors:
1. Ensure X-Ray is enabled in Lambda configuration
2. Set `AWS_XRAY_CONTEXT_MISSING=LOG_ERROR` to log instead of throw
3. Wrap subsegment creation in try-except (already done in utilities)

## Cost Considerations

X-Ray pricing (as of 2024):
- First 100,000 traces per month: Free
- Additional traces: $5.00 per 1 million traces
- Trace retrieval: $0.50 per 1 million traces retrieved

For typical usage (300K requests/month with 5% sampling):
- Traces recorded: ~15,000/month
- Cost: Free tier covers all traces

## Best Practices

1. **Use Annotations for Filtering**: Add annotations for fields you'll filter by
2. **Use Metadata for Details**: Add metadata for detailed information
3. **Create Subsegments for Key Operations**: Trace database queries, API calls, etc.
4. **Correlate with Logs**: Use trace IDs to link traces and logs
5. **Monitor Service Map**: Regularly review service dependencies
6. **Set Up Alarms**: Alert on high error rates or latency
7. **Use Sampling**: Adjust sampling rates based on traffic volume

## Example: Complete Lambda Function

```python
from common import (
    LambdaRouter,
    create_subsegment,
    add_annotation,
    add_metadata,
)

router = LambdaRouter()

@router.route(function_name="myFunction")
def handler(event, context, logger):
    # Annotations automatically added by router
    add_annotation('correlation_id', logger.correlation_id)
    
    # Custom operation tracing
    with create_subsegment('database_query'):
        add_annotation('query_type', 'select')
        results = db.query("SELECT * FROM items")
        add_metadata('result_count', len(results), 'database')
    
    # Process results
    with create_subsegment('process_results'):
        processed = process(results)
    
    return {"processed": len(processed)}

def lambda_handler(event, context):
    return handler(event, context)
```

## References

- [AWS X-Ray Developer Guide](https://docs.aws.amazon.com/xray/latest/devguide/)
- [X-Ray SDK for Python](https://docs.aws.amazon.com/xray-sdk-for-python/latest/reference/)
- [Lambda X-Ray Integration](https://docs.aws.amazon.com/lambda/latest/dg/services-xray.html)
