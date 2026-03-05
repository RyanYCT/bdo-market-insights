# BDO Market Insights - Step Functions State Machine

This directory contains the Step Functions state machine definition for the BDO Market Insights Query Pipeline.

## Overview

The Step Functions state machine orchestrates the query pipeline workflow, handling GET requests from API Gateway and coordinating the queryData and analyzeData Lambda functions.

## State Machine Flow

```
API Gateway GET Request
    ↓
TransformInput (Extract query parameters)
    ↓
ValidateQueryParams (Prepare parameters for Lambda)
    ↓
QueryData Lambda (Retrieve market data from PostgreSQL)
    ↓
CheckQueryDataSuccess (Verify 200 status)
    ↓
MergeForAnalysis (Combine results with original parameters)
    ↓
AnalyzeData Lambda (Compute statistics and metrics)
    ↓
CheckAnalyzeDataSuccess (Verify 200 status)
    ↓
FormatResponse (Add cache-control headers)
    ↓
Return to API Gateway
```

## State Descriptions

### TransformInput
- **Type**: Pass
- **Purpose**: Extract query parameters from API Gateway GET request
- **Input**: API Gateway event with `queryStringParameters` and `requestContext`
- **Output**: Structured object with `queryParams` and `correlationId`

### ValidateQueryParams
- **Type**: Pass
- **Purpose**: Prepare parameters for queryData Lambda invocation
- **Transformations**:
  - Converts `limit` from string to number
  - Extracts individual parameters (item_id, name, category, start_date, end_date)
  - Preserves correlation ID
- **Output**: `validatedParams` object ready for Lambda

### QueryData
- **Type**: Task (Lambda Invoke)
- **Purpose**: Retrieve market data from PostgreSQL database
- **Retry Policy**:
  - Max attempts: 3
  - Backoff rate: 2 (exponential)
  - Interval: 2 seconds
  - Retriable errors: Lambda service exceptions, throttling
- **Error Handling**: Catches all errors and transitions to HandleError state
- **Output**: `queryResult` with `statusCode` and `body`

### CheckQueryDataSuccess
- **Type**: Choice
- **Purpose**: Verify queryData Lambda returned 200 status
- **Choices**:
  - If statusCode == 200: Continue to MergeForAnalysis
  - Otherwise: Transition to HandleQueryDataError

### HandleQueryDataError
- **Type**: Pass
- **Purpose**: Format error information for non-200 queryData responses
- **Output**: Error object with code, message, statusCode, and details

### MergeForAnalysis
- **Type**: Pass
- **Purpose**: Combine queryData results with original query parameters
- **Transformations**:
  - Extracts `market_data` from queryData response
  - Preserves original `query_params` (item_id, name, category, dates, limit)
  - Maintains `correlation_id` for tracing
- **Output**: Combined object for analyzeData Lambda

### AnalyzeData
- **Type**: Task (Lambda Invoke)
- **Purpose**: Compute statistics, profitability score, and price trends
- **Retry Policy**: Same as QueryData
- **Error Handling**: Catches all errors and transitions to HandleError state
- **Output**: `analysisResult` with `statusCode` and `body`

### CheckAnalyzeDataSuccess
- **Type**: Choice
- **Purpose**: Verify analyzeData Lambda returned 200 status
- **Choices**:
  - If statusCode == 200: Continue to FormatResponse
  - Otherwise: Transition to HandleAnalyzeDataError

### HandleAnalyzeDataError
- **Type**: Pass
- **Purpose**: Format error information for non-200 analyzeData responses
- **Output**: Error object with code, message, statusCode, and details

### FormatResponse
- **Type**: Pass
- **Purpose**: Format successful response with appropriate headers
- **Headers**:
  - `Content-Type: application/json`
  - `Cache-Control: public, max-age=300` (5 minutes)
  - `X-Correlation-Id`: Correlation ID for tracing
- **Output**: Final response object with statusCode, headers, and body

### HandleError
- **Type**: Pass
- **Purpose**: Format error response for any failures in the pipeline
- **Output**: Error response with statusCode 500, headers, and error details

## Deployment

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. queryData Lambda function deployed and ARN available
3. analyzeData Lambda function deployed and ARN available

### Deploy using AWS CLI

```bash
# Deploy to dev environment
aws cloudformation deploy \
  --template-file step-functions-template.yaml \
  --stack-name bdo-stepfunctions-dev \
  --parameter-overrides \
    Environment=dev \
    QueryDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:queryData-dev" \
    AnalyzeDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:analyzeData-dev" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to staging environment
aws cloudformation deploy \
  --template-file step-functions-template.yaml \
  --stack-name bdo-stepfunctions-staging \
  --parameter-overrides \
    Environment=staging \
    QueryDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:queryData-staging" \
    AnalyzeDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:analyzeData-staging" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to production environment
aws cloudformation deploy \
  --template-file step-functions-template.yaml \
  --stack-name bdo-stepfunctions-prod \
  --parameter-overrides \
    Environment=prod \
    QueryDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:queryData-prod" \
    AnalyzeDataLambdaArn="arn:aws:lambda:us-east-1:123456789012:function:analyzeData-prod" \
  --capabilities CAPABILITY_NAMED_IAM
```

### Retrieve State Machine ARN

```bash
# Get State Machine ARN from stack outputs
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-stepfunctions-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

echo $STATE_MACHINE_ARN
```

## Testing

### Test State Machine Execution

```bash
# Set variables
STATE_MACHINE_ARN="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-dev"

# Test with valid input
aws stepfunctions start-sync-execution \
  --state-machine-arn $STATE_MACHINE_ARN \
  --input '{
    "queryStringParameters": {
      "item_id": "1001",
      "start_date": "2024-01-01T00:00:00Z",
      "end_date": "2024-01-31T23:59:59Z",
      "limit": "100"
    },
    "requestContext": {
      "requestId": "test-request-id-123"
    }
  }'

# Test with name parameter instead of item_id
aws stepfunctions start-sync-execution \
  --state-machine-arn $STATE_MACHINE_ARN \
  --input '{
    "queryStringParameters": {
      "name": "Black Stone (Weapon)",
      "start_date": "2024-01-01T00:00:00Z",
      "end_date": "2024-01-31T23:59:59Z",
      "limit": "50"
    },
    "requestContext": {
      "requestId": "test-request-id-456"
    }
  }'

# Test with category filter
aws stepfunctions start-sync-execution \
  --state-machine-arn $STATE_MACHINE_ARN \
  --input '{
    "queryStringParameters": {
      "category": "Accessory",
      "start_date": "2024-01-01T00:00:00Z",
      "end_date": "2024-01-31T23:59:59Z",
      "limit": "200"
    },
    "requestContext": {
      "requestId": "test-request-id-789"
    }
  }'
```

### View Execution History

```bash
# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn $STATE_MACHINE_ARN \
  --max-results 10

# Get execution details
EXECUTION_ARN="<execution-arn-from-list>"
aws stepfunctions describe-execution \
  --execution-arn $EXECUTION_ARN

# Get execution history (all state transitions)
aws stepfunctions get-execution-history \
  --execution-arn $EXECUTION_ARN
```

## Monitoring

### CloudWatch Logs

Step Functions execution logs are written to:
```
/aws/stepfunctions/bdo-query-pipeline-{environment}
```

Log entries include:
- Execution start/stop events
- State transitions
- Input/output for each state
- Error details

### CloudWatch Metrics

Key metrics to monitor:

1. **ExecutionsStarted**: Number of executions started
2. **ExecutionsSucceeded**: Number of successful executions
3. **ExecutionsFailed**: Number of failed executions
4. **ExecutionThrottled**: Number of throttled executions
5. **ExecutionTime**: Duration of executions (p50, p95, p99)

### CloudWatch Alarms

The following alarms are configured:

1. **Execution Failed**: Triggers when > 5 executions fail in 5 minutes
2. **Execution Throttled**: Triggers when > 10 executions throttled in 10 minutes
3. **Execution Duration**: Triggers when p95 duration > 25 seconds

### X-Ray Tracing

X-Ray tracing is enabled for the state machine. Traces include:
- State machine execution
- Lambda function invocations
- Service integrations
- Error details

View traces in the AWS X-Ray console or using CLI:

```bash
# Get trace summaries
aws xray get-trace-summaries \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --filter-expression 'service(id(name: "bdo-query-pipeline-dev"))'
```

## Data Flow Examples

### Example 1: Query by Item ID

**Input (from API Gateway)**:
```json
{
  "queryStringParameters": {
    "item_id": "1001",
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "limit": "100"
  },
  "requestContext": {
    "requestId": "abc-123-def-456"
  }
}
```

**After TransformInput**:
```json
{
  "queryParams": {
    "item_id": "1001",
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "limit": "100"
  },
  "correlationId": "abc-123-def-456"
}
```

**After ValidateQueryParams**:
```json
{
  "validatedParams": {
    "item_id": "1001",
    "name": null,
    "category": null,
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "limit": 100,
    "correlation_id": "abc-123-def-456"
  }
}
```

**After QueryData** (successful):
```json
{
  "queryResult": {
    "statusCode": 200,
    "body": {
      "data": [
        {
          "item_id": 1001,
          "item_name": "Black Stone (Weapon)",
          "current_stock": 50,
          "total_trades": 1234,
          "last_sold_price": 15000,
          "last_sold_time": "2024-01-15T10:30:00Z"
        }
      ],
      "count": 100
    }
  }
}
```

**After MergeForAnalysis**:
```json
{
  "market_data": [
    {
      "item_id": 1001,
      "item_name": "Black Stone (Weapon)",
      "current_stock": 50,
      "total_trades": 1234,
      "last_sold_price": 15000,
      "last_sold_time": "2024-01-15T10:30:00Z"
    }
  ],
  "query_params": {
    "item_id": "1001",
    "name": null,
    "category": null,
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "limit": 100
  },
  "correlation_id": "abc-123-def-456"
}
```

**After AnalyzeData** (successful):
```json
{
  "analysisResult": {
    "statusCode": 200,
    "body": {
      "item_id": 1001,
      "item_name": "Black Stone (Weapon)",
      "date_range": {
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-31T23:59:59Z"
      },
      "statistics": {
        "avg_last_sold_price": 14850.50,
        "min_last_sold_price": 14000,
        "max_last_sold_price": 16000,
        "avg_current_stock": 48.5,
        "total_trades_sum": 123400
      },
      "profitability_score": 0.85,
      "price_trend": "increasing",
      "correlation_id": "abc-123-def-456"
    }
  }
}
```

**Final Response** (FormatResponse):
```json
{
  "statusCode": 200,
  "headers": {
    "Content-Type": "application/json",
    "Cache-Control": "public, max-age=300",
    "X-Correlation-Id": "abc-123-def-456"
  },
  "body": {
    "item_id": 1001,
    "item_name": "Black Stone (Weapon)",
    "date_range": {
      "start": "2024-01-01T00:00:00Z",
      "end": "2024-01-31T23:59:59Z"
    },
    "statistics": {
      "avg_last_sold_price": 14850.50,
      "min_last_sold_price": 14000,
      "max_last_sold_price": 16000,
      "avg_current_stock": 48.5,
      "total_trades_sum": 123400
    },
    "profitability_score": 0.85,
    "price_trend": "increasing",
    "correlation_id": "abc-123-def-456"
  }
}
```

### Example 2: Error Handling

**Scenario**: queryData Lambda returns 400 (validation error)

**After QueryData**:
```json
{
  "queryResult": {
    "statusCode": 400,
    "body": {
      "error": "Invalid date range: end_date must be after start_date"
    }
  }
}
```

**After CheckQueryDataSuccess** → **HandleQueryDataError**:
```json
{
  "error": {
    "code": "QUERY_DATA_ERROR",
    "message": "queryData Lambda returned non-200 status",
    "statusCode": 400,
    "details": {
      "error": "Invalid date range: end_date must be after start_date"
    },
    "correlationId": "abc-123-def-456"
  }
}
```

**Final Response** (HandleError):
```json
{
  "statusCode": 500,
  "headers": {
    "Content-Type": "application/json",
    "X-Correlation-Id": "abc-123-def-456"
  },
  "body": {
    "error": {
      "code": "QUERY_DATA_ERROR",
      "message": "queryData Lambda returned non-200 status",
      "correlation_id": "abc-123-def-456"
    }
  }
}
```

## Troubleshooting

### Common Issues

1. **Execution Fails at QueryData State**
   - Check queryData Lambda logs for errors
   - Verify Lambda has correct IAM permissions for database access
   - Check database connectivity and credentials in Secrets Manager
   - Verify input parameters are correctly formatted

2. **Execution Fails at AnalyzeData State**
   - Check analyzeData Lambda logs for errors
   - Verify market_data array is not empty
   - Check for data format issues from queryData

3. **Execution Throttled**
   - Check Lambda concurrency limits
   - Consider increasing Lambda reserved concurrency
   - Review API Gateway rate limits
   - Check Step Functions execution rate limits

4. **Slow Execution Times**
   - Check queryData Lambda duration (database query performance)
   - Check analyzeData Lambda duration (computation complexity)
   - Review database indexes and query optimization
   - Consider caching frequently accessed data

### Viewing Detailed Logs

```bash
# View Step Functions logs
aws logs tail /aws/stepfunctions/bdo-query-pipeline-dev --follow

# Filter by correlation ID
aws logs filter-log-events \
  --log-group-name /aws/stepfunctions/bdo-query-pipeline-dev \
  --filter-pattern "abc-123-def-456"

# View Lambda logs
aws logs tail /aws/lambda/queryData-dev --follow
aws logs tail /aws/lambda/analyzeData-dev --follow
```

## Performance Considerations

1. **State Machine Type**: EXPRESS
   - Optimized for high-volume, short-duration workflows
   - Max duration: 5 minutes
   - Synchronous execution (waits for completion)
   - Lower cost than STANDARD type

2. **Retry Configuration**:
   - Max 3 attempts per Lambda invocation
   - Exponential backoff (2s, 4s, 8s)
   - Handles transient failures gracefully

3. **Execution Duration**:
   - Target: < 2 seconds p95
   - Alarm threshold: 25 seconds p95
   - Optimize Lambda functions for fast execution

## Cost Considerations

**Step Functions Costs (EXPRESS type)**:
- $1.00 per million state transitions
- Includes all state transitions in the workflow

**Example Monthly Cost (10K requests/day)**:
- State transitions per execution: ~10 (average)
- Total transitions: 300K requests × 10 = 3M transitions
- Cost: 3M / 1M × $1.00 = $3.00/month

**Additional Costs**:
- Lambda invocations (queryData + analyzeData)
- CloudWatch Logs storage
- X-Ray traces

## Security Considerations

1. **IAM Permissions**:
   - Step Functions role has least-privilege access
   - Only invoke specified Lambda functions
   - CloudWatch Logs write access
   - X-Ray write access

2. **Data in Transit**:
   - All data encrypted in transit (TLS)
   - Correlation IDs for request tracing
   - No sensitive data in state machine definition

3. **Logging**:
   - Full execution data logged (includes input/output)
   - Ensure no sensitive data in query parameters
   - Set appropriate log retention periods

## Integration with API Gateway

The state machine is designed to be invoked synchronously from API Gateway using the `StartSyncExecution` action. The API Gateway integration template handles:

1. Transforming GET query parameters into state machine input
2. Extracting the response body from state machine output
3. Setting appropriate response headers (including Cache-Control)
4. Handling errors and returning appropriate status codes

See `api-gateway-template.yaml` for the complete integration configuration.

## Rollback

To rollback to a previous version:

```bash
# List stack events to find previous deployment
aws cloudformation describe-stack-events \
  --stack-name bdo-stepfunctions-dev

# Rollback to previous template
aws cloudformation update-stack \
  --stack-name bdo-stepfunctions-dev \
  --use-previous-template \
  --capabilities CAPABILITY_NAMED_IAM
```

## Clean Up

To delete the Step Functions stack:

```bash
aws cloudformation delete-stack \
  --stack-name bdo-stepfunctions-dev
```

**Warning**: This will delete the state machine, execution role, and CloudWatch logs.
