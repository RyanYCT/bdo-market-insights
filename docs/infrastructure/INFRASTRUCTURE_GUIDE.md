# Infrastructure Guide

Complete guide for deploying and managing the BDO Market Insights infrastructure components.

## Table of Contents

1. [Overview](#overview)
2. [API Gateway](#api-gateway)
3. [Step Functions](#step-functions)
4. [CloudWatch Alarms](#cloudwatch-alarms)
5. [API Documentation](#api-documentation)
6. [Data Retention](#data-retention)
7. [Monitoring](#monitoring)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The BDO Market Insights infrastructure consists of:

- **API Gateway**: RESTful API endpoints with authentication and rate limiting
- **Step Functions**: Query pipeline orchestration
- **CloudWatch Alarms**: System-wide monitoring and alerting
- **API Documentation**: OpenAPI specification and Swagger UI
- **Data Retention**: Automated data lifecycle management

### Architecture

```
Client Request
    ↓
API Gateway (API Key Auth + Rate Limiting)
    ↓
Step Functions State Machine (Sync Execution)
    ↓
queryData Lambda → analyzeData Lambda
    ↓
API Gateway Response (with Cache-Control headers)
```

---

## API Gateway

### Features

- **GET /query** endpoint for querying market data
- **GET /docs** endpoint for Swagger UI documentation
- **GET /openapi.yaml** endpoint for OpenAPI specification
- API key authentication with rate limiting
- CORS support for approved origins
- Integration with Step Functions for query orchestration
- CloudWatch logging and monitoring
- X-Ray tracing enabled

### Deployment

```bash
# Get Step Functions ARN
STEP_FUNCTIONS_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-step-functions-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

# Deploy API Gateway
aws cloudformation deploy \
  --template-file infrastructure/api-gateway-template.yaml \
  --stack-name bdo-api-gateway-staging \
  --parameter-overrides \
    Environment=staging \
    AllowedOrigins="https://staging.example.com" \
    StepFunctionsArn=$STEP_FUNCTIONS_ARN \
  --capabilities CAPABILITY_NAMED_IAM
```

### Retrieve API Key

```bash
# Get API Key ID from stack outputs
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
  --output text)

# Get API Key value
API_KEY_VALUE=$(aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text)

echo "API Key: $API_KEY_VALUE"
echo "IMPORTANT: Save this API key securely!"
```

### Rate Limiting

The API is configured with the following rate limits per API key:

- **Rate Limit**: 100 requests per minute
- **Burst Limit**: 20 concurrent requests
- **Daily Quota**: 10,000 requests per day

### Request Parameters

The GET /query endpoint accepts:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| item_id | integer | No* | Specific item ID to query |
| name | string | No* | Item name to search |
| category | string | No | Category filter (Uncategorized, Accessory, Buff, Costume) |
| start_date | ISO 8601 datetime | Yes | Start of date range |
| end_date | ISO 8601 datetime | Yes | End of date range |
| limit | integer | No | Max results (default: 100, max: 1000) |

*Either `item_id` or `name` must be provided.

### Response Format

Successful responses (200 OK):

```json
{
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
  "correlation_id": "request-id-from-api-gateway"
}
```

### Error Responses

- **400 Bad Request**: Invalid query parameters
- **401 Unauthorized**: Missing or invalid API key
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server-side error

### Testing

```bash
# Set variables
API_ENDPOINT="https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/staging"
API_KEY="your-api-key-value"

# Test query endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z&limit=100" \
  -H "x-api-key: ${API_KEY}"

# Test without API key (should return 401)
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z"

# Test rate limiting
for i in {1..150}; do
  curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
    -H "x-api-key: ${API_KEY}" &
done
wait
```

### Managing API Keys

**Create Additional API Keys:**
```bash
# Create new API key
aws apigateway create-api-key \
  --name "client-name-api-key" \
  --description "API key for client-name" \
  --enabled

# Associate with usage plan
aws apigateway create-usage-plan-key \
  --usage-plan-id <usage-plan-id> \
  --key-id <api-key-id> \
  --key-type API_KEY
```

**Revoke API Key:**
```bash
# Disable API key
aws apigateway update-api-key \
  --api-key <api-key-id> \
  --patch-operations op=replace,path=/enabled,value=false

# Delete API key
aws apigateway delete-api-key \
  --api-key <api-key-id>
```

---

## Step Functions

### State Machine Flow

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

### State Descriptions

**TransformInput**:
- Extracts query parameters from API Gateway GET request
- Outputs structured object with `queryParams` and `correlationId`

**ValidateQueryParams**:
- Prepares parameters for queryData Lambda invocation
- Converts `limit` from string to number
- Preserves correlation ID

**QueryData**:
- Retrieves market data from PostgreSQL database
- Retry policy: 3 attempts with exponential backoff
- Error handling: Catches all errors and transitions to HandleError

**CheckQueryDataSuccess**:
- Verifies queryData Lambda returned 200 status
- Routes to MergeForAnalysis or HandleQueryDataError

**MergeForAnalysis**:
- Combines queryData results with original query parameters
- Maintains correlation_id for tracing

**AnalyzeData**:
- Computes statistics, profitability score, and price trends
- Retry policy: Same as QueryData

**CheckAnalyzeDataSuccess**:
- Verifies analyzeData Lambda returned 200 status
- Routes to FormatResponse or HandleAnalyzeDataError

**FormatResponse**:
- Formats successful response with appropriate headers
- Adds Cache-Control and X-Correlation-Id headers

**HandleError**:
- Formats error response for any failures in the pipeline
- Returns statusCode 500 with error details

### Deployment

```bash
# Get Lambda ARNs
QUERY_DATA_ARN=$(aws lambda get-function --function-name queryData --query 'Configuration.FunctionArn' --output text)
ANALYZE_DATA_ARN=$(aws lambda get-function --function-name analyzeData --query 'Configuration.FunctionArn' --output text)

# Deploy using CloudFormation
aws cloudformation deploy \
  --template-file infrastructure/step-functions-template.yaml \
  --stack-name bdo-step-functions-staging \
  --parameter-overrides \
    Environment=staging \
    QueryDataLambdaArn=$QUERY_DATA_ARN \
    AnalyzeDataLambdaArn=$ANALYZE_DATA_ARN \
  --capabilities CAPABILITY_NAMED_IAM
```

### Testing

```bash
# Set variables
STATE_MACHINE_ARN="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-staging"

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
```

### Monitoring

**CloudWatch Logs**: `/aws/stepfunctions/bdo-query-pipeline-{environment}`

**Key Metrics**:
- ExecutionsStarted
- ExecutionsSucceeded
- ExecutionsFailed
- ExecutionThrottled
- ExecutionTime (p50, p95, p99)

**X-Ray Tracing**: Enabled for complete request flow visibility

---

## CloudWatch Alarms

### Overview

Comprehensive monitoring across all system components:

- Lambda Error Rates (6 alarms)
- Database Connection Pool (3 alarms)
- External API Health (4 alarms)
- ETL Pipeline Health (2 alarms)
- Query Pipeline Performance (1 alarm)
- Lambda Throttling (2 alarms)
- API Gateway (3 alarms)
- Step Functions (3 alarms)

### Deployment

```bash
cd infrastructure
./deploy-alarms.sh
cd ..
```

Or using CloudFormation:

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudwatch-alarms-template.yaml \
  --stack-name bdo-cloudwatch-alarms-staging \
  --parameter-overrides \
    Environment=staging \
    AlarmEmailAddress="devops@example.com" \
    RetrieveIdListFunctionName="retrieveIdList" \
    FetchDataFunctionName="fetchData" \
    CleanDataFunctionName="cleanData" \
    StoreDataFunctionName="storeData" \
    QueryDataFunctionName="queryData" \
    AnalyzeDataFunctionName="analyzeData" \
  --capabilities CAPABILITY_NAMED_IAM
```

### Alarm Categories

#### Lambda Error Rate Alarms

**Threshold**: 5 errors in 10 minutes (2 consecutive periods)

**Actions**:
- Check CloudWatch Logs for error details
- Review X-Ray traces for failed requests
- Verify Lambda function configuration and permissions

#### Database Connection Pool Alarms

**Thresholds**:
- Pool utilization: 90% (2 consecutive periods)
- Connection failures: 5 in 10 minutes
- Slow queries: 10 in 10 minutes (2 consecutive periods)

**Actions**:
- Check RDS instance CPU and memory utilization
- Review database connection pool configuration
- Analyze slow query logs for optimization opportunities

#### External API Failure Alarms

**Thresholds**:
- Failure rate: 10 failures in 10 minutes (2 consecutive periods)
- Rate limit hits: 20 in 10 minutes (2 consecutive periods)
- Latency: p95 > 5000ms (2 consecutive periods)
- Circuit breaker: Opened (immediate)

**Actions**:
- Check External API status page
- Review rate limiting configuration
- Verify API credentials are valid

#### API Gateway Alarms

**Thresholds**:
- 5XX error rate: > 5% over 10 minutes
- 4XX error rate: > 50 over 10 minutes
- Latency: p95 > 2 seconds over 10 minutes

**Actions**:
- Check Step Functions execution logs
- Verify Lambda functions are not timing out
- Check Lambda function errors in CloudWatch

#### Step Functions Alarms

**Thresholds**:
- Execution failed: > 5 executions fail in 5 minutes
- Execution throttled: > 10 executions throttled in 10 minutes
- Execution duration: p95 > 25 seconds

**Actions**:
- Check Lambda function logs
- Verify database connectivity
- Review X-Ray traces for bottlenecks

### Custom Metrics

The system emits custom CloudWatch metrics in the `BDO/MarketInsights` namespace:

**Database Metrics**:
- DatabaseConnectionPoolUtilization
- DatabaseConnectionFailures
- SlowQueries

**External API Metrics**:
- ExternalAPIFailures
- ExternalAPIRateLimitHits
- ExternalAPILatency
- CircuitBreakerOpen

**Pipeline Metrics**:
- ETLPipelineFailures
- ETLPipelineDuration
- QueryPipelineLatency

### Managing Notifications

**Add Email Subscriber:**
```bash
# Get SNS topic ARN
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-cloudwatch-alarms-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicArn`].OutputValue' \
  --output text)

# Subscribe email
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint "newuser@example.com"
```

### Viewing Alarms

```bash
# List all alarms for an environment
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --query 'MetricAlarms[?contains(AlarmName, `staging`)].[AlarmName,StateValue]' \
  --output table

# Get alarm history
aws cloudwatch describe-alarm-history \
  --alarm-name "bdo-fetchData-error-rate-staging" \
  --max-records 10
```

---

## API Documentation

### Overview

The API provides interactive documentation through:

- **Swagger UI**: Interactive API documentation at `/docs`
- **OpenAPI Spec**: Machine-readable API specification at `/openapi.yaml`

### Deployment

```bash
# Linux/Mac
chmod +x infrastructure/deploy-api-docs.sh
./infrastructure/deploy-api-docs.sh staging us-east-1

# Windows
infrastructure\deploy-api-docs.bat staging us-east-1
```

### Accessing Documentation

**Swagger UI**:
```
https://{api-id}.execute-api.{region}.amazonaws.com/{environment}/docs
```

**OpenAPI Specification**:
```
https://{api-id}.execute-api.{region}.amazonaws.com/{environment}/openapi.yaml
```

### Using Swagger UI

1. Open the Swagger UI URL in your browser
2. Scroll to the "API Key Configuration" section
3. Enter your API key
4. Click "Save"
5. Test endpoints by clicking "Try it out"

### Updating Documentation

1. Edit `infrastructure/openapi-spec.yaml`
2. Validate the spec:
   ```bash
   swagger-cli validate infrastructure/openapi-spec.yaml
   ```
3. Redeploy:
   ```bash
   ./infrastructure/deploy-api-docs.sh staging us-east-1
   ```

---

## Data Retention

### Overview

Automates data lifecycle management through:

- **Monthly EventBridge Scheduler**: Triggers retainData Lambda on 1st of each month at 2 AM UTC
- **S3 Glacier Archive Bucket**: Stores archived summary data
- **Automated Aggregation**: Converts old detailed records to daily summaries
- **Automated Archival**: Moves old summaries to S3 Glacier

### Architecture

```
EventBridge Scheduler (Monthly)
    ↓
retainData Lambda Function
    ↓
┌─────────────────────────────────────────┐
│ 1. Aggregate old detailed records       │
│    (older than 90 days)                 │
│    → Create daily summaries             │
│    → Delete aggregated records          │
│                                         │
│ 2. Archive old summaries                │
│    (older than 2 years)                 │
│    → Export to JSON                     │
│    → Upload to S3 Glacier               │
│    → Delete archived summaries          │
└─────────────────────────────────────────┘
    ↓
PostgreSQL (RDS) + S3 Glacier
```

### Deployment

```bash
# Linux/Mac
chmod +x infrastructure/deploy-retention-schedule.sh
./infrastructure/deploy-retention-schedule.sh staging arn:aws:lambda:us-east-1:123456789012:function:retainData

# Windows
infrastructure\deploy-retention-schedule.bat staging arn:aws:lambda:us-east-1:123456789012:function:retainData
```

### Configuration

**Schedule Expression**: `cron(0 2 1 * ? *)` - 2 AM UTC on 1st of every month

**Retention Periods**:
- **RetentionDetailedDays** (default: 90): Days to keep detailed MarketData records
- **RetentionSummaryDays** (default: 730): Days to keep summary data before archiving

### Testing

```bash
# Invoke Lambda manually
aws lambda invoke \
  --function-name retainData \
  --payload '{
    "source": "manual-test",
    "environment": "staging",
    "retention_config": {
      "retention_detailed_days": 90,
      "retention_summary_days": 730
    }
  }' \
  response.json

# View response
cat response.json

# Check S3 archives
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name bdo-retention-schedule-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`ArchiveBucketName`].OutputValue' \
  --output text)

aws s3 ls "s3://${BUCKET_NAME}/archives/" --recursive
```

### Monitoring

**CloudWatch Metrics** (namespace: `BDOMarketInsights/Retention`):
- RetentionRecordsAggregated
- RetentionRecordsDeleted
- RetentionSummariesArchived
- RetentionFailure

**CloudWatch Logs**: `/aws/lambda/retainData`

**Alarms**:
- RetentionLambdaErrorAlarm: Lambda function fails
- RetentionLambdaDurationAlarm: Execution exceeds 10 minutes
- RetentionFailureMetricAlarm: Custom retention failure metric

---

## Monitoring

### CloudWatch Logs

```bash
# Lambda logs
aws logs tail /aws/lambda/retrieveIdList --follow

# API Gateway logs
aws logs tail /aws/apigateway/bdo-market-insights-staging --follow

# Step Functions logs
aws logs tail /aws/stepfunctions/bdo-query-pipeline-staging --follow
```

### CloudWatch Metrics

```bash
# Lambda invocations
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=retrieveIdList \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum
```

### X-Ray Traces

Visit the X-Ray console:
```
https://console.aws.amazon.com/xray/home?region=us-east-1#/traces
```

Or use CLI:
```bash
aws xray get-trace-summaries \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s)
```

---

## Troubleshooting

### API Gateway Issues

**401 Unauthorized**:
- Verify API key is included in `x-api-key` header
- Check API key is enabled and associated with usage plan
- Verify API key hasn't expired

**429 Too Many Requests**:
- Check current rate limit usage in CloudWatch metrics
- Consider increasing rate limits or implementing client-side throttling
- Verify burst limit isn't being exceeded

**500 Internal Server Error**:
- Check CloudWatch logs for detailed error messages
- Verify Step Functions state machine is deployed and accessible
- Check Lambda function logs for errors
- Verify IAM role has correct permissions

### Step Functions Issues

**Execution Fails at QueryData State**:
- Check queryData Lambda logs for errors
- Verify Lambda has correct IAM permissions for database access
- Check database connectivity and credentials in Secrets Manager
- Verify input parameters are correctly formatted

**Execution Throttled**:
- Check Lambda concurrency limits
- Consider increasing Lambda reserved concurrency
- Review API Gateway rate limits
- Check Step Functions execution rate limits

### Lambda Issues

**Function Timeout**:
- Increase Lambda timeout setting
- Check database connectivity
- Review CloudWatch logs for specific errors
- Increase memory allocation if needed

**High Error Rate**:
- Check CloudWatch logs for error details
- Check X-Ray traces for bottlenecks
- Verify database connectivity
- Check External API availability
- Consider rollback if errors persist

### Database Issues

**Connection Timeout**:
- Verify Lambda functions are in the same VPC as RDS
- Check security groups allow traffic from Lambda to RDS
- Verify RDS endpoint is correct in Secrets Manager
- Check database is running and accessible

**Slow Queries**:
- Check database query performance
- Review database indexes
- Analyze slow query logs
- Consider query optimization

### X-Ray Issues

**Traces Missing**:
- Verify X-Ray is enabled for all components
- Check Lambda execution role has X-Ray write permissions
- Wait a few minutes for traces to appear
- Verify requests are actually being made

---

## Security Considerations

1. **API Keys**: Store API keys securely (AWS Secrets Manager, environment variables)
2. **CORS**: Only allow trusted origins
3. **Rate Limiting**: Adjust limits based on expected traffic patterns
4. **Monitoring**: Set up alerts for unusual traffic patterns
5. **Logging**: Ensure sensitive data is not logged
6. **IAM Roles**: Follow principle of least privilege
7. **Encryption**: All data encrypted in transit and at rest
8. **Secrets**: Use AWS Secrets Manager for all credentials

---

## Quick Reference

### Verification Commands

```bash
# Check Lambda functions
aws lambda list-functions --query 'Functions[].FunctionName'

# Check Step Functions
aws stepfunctions list-state-machines

# Check API Gateway
aws apigateway get-rest-apis

# Check CloudWatch alarms
aws cloudwatch describe-alarms --alarm-name-prefix "bdo-"

# Check EventBridge schedules
aws scheduler list-schedules --name-prefix "bdo-"
```

### Quick Links

- [AWS Console](https://console.aws.amazon.com/)
- [CloudWatch Logs](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups)
- [X-Ray Service Map](https://console.aws.amazon.com/xray/home#/service-map)
- [Lambda Functions](https://console.aws.amazon.com/lambda/home#/functions)
- [API Gateway](https://console.aws.amazon.com/apigateway/home#/apis)

---

## Support

For infrastructure issues:
1. Check this guide
2. Review CloudWatch logs
3. Check X-Ray traces
4. Contact DevOps team

