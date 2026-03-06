# Staging Validation Guide

This guide provides instructions for validating the BDO Market Insights staging environment after deployment.

## Overview

The staging validation process tests all critical components of the system to ensure they are functioning correctly before promoting to production. The validation covers:

1. ETL Pipeline execution
2. Query API functionality
3. API authentication and authorization
4. Rate limiting
5. CloudWatch logs (JSON format and correlation IDs)
6. X-Ray distributed tracing
7. Custom CloudWatch metrics
8. Error handling scenarios

## Prerequisites

Before running the validation script, ensure you have:

- AWS CLI v2 installed and configured
- `curl` command-line tool installed
- `jq` JSON processor installed (for parsing JSON responses)
- Valid AWS credentials with permissions to:
  - Invoke Lambda functions
  - Query CloudWatch Logs
  - Access X-Ray traces
  - List CloudWatch metrics
  - Describe CloudFormation stacks
  - Get API Gateway configuration

### Installing Prerequisites

**AWS CLI v2:**
- Download from: https://aws.amazon.com/cli/
- Verify installation: `aws --version`

**curl:**
- Linux/macOS: Usually pre-installed
- Windows: Download from https://curl.se/windows/ or use Git Bash

**jq:**
- Linux: `sudo apt-get install jq` or `sudo yum install jq`
- macOS: `brew install jq`
- Windows: Download from https://stedolan.github.io/jq/download/

## Running the Validation Script

### Linux/macOS

```bash
# Make the script executable (if not already)
chmod +x scripts/validate-staging.sh

# Run the validation
bash scripts/validate-staging.sh
```

### Windows

```cmd
# Run the validation
scripts\validate-staging.bat
```

## Validation Tests

### Test 1: ETL Pipeline Execution

**What it tests:**
- Manual invocation of the `retrieveIdList` Lambda function
- Validates that the function returns a valid response with item IDs

**Expected result:**
- Lambda function executes successfully
- Response contains `item_ids` array
- No errors in execution

**Troubleshooting:**
- Check Lambda function logs in CloudWatch
- Verify DynamoDB table exists and contains data
- Ensure Lambda has proper IAM permissions

### Test 2: Query API with Valid Parameters

**What it tests:**
- API Gateway endpoint accessibility
- Query with `item_id` parameter
- Response format and structure

**Expected result:**
- HTTP 200 status code
- Response contains `statistics` object
- Data is properly formatted

**Troubleshooting:**
- Verify API Gateway is deployed
- Check Step Functions execution logs
- Ensure queryData and analyzeData Lambdas are working

### Test 3: Query API with Various Parameters

**What it tests:**
- Query with `name` parameter
- Query with `category` parameter
- Different parameter combinations

**Expected result:**
- All parameter types are handled correctly
- Appropriate responses for each query type

**Troubleshooting:**
- Check Pydantic schema validation
- Review Lambda function logs for validation errors

### Test 4: API Key Authentication

**What it tests:**
- Request without API key (should be rejected)
- Request with invalid API key (should be rejected)
- Request with valid API key (should succeed)

**Expected result:**
- Requests without API key return 401 or 403
- Requests with invalid API key return 401 or 403
- Requests with valid API key return 200

**Troubleshooting:**
- Verify API Gateway API key configuration
- Check usage plan association
- Ensure API key is active

### Test 5: Rate Limiting

**What it tests:**
- Multiple rapid requests to trigger rate limiting
- Rate limit enforcement per API key

**Expected result:**
- After exceeding rate limit, requests return 429 (Too Many Requests)
- Rate limiting is enforced consistently

**Note:** If rate limits are set high, this test may not trigger a 429 response with only 15 requests. This is expected behavior.

**Troubleshooting:**
- Review API Gateway usage plan settings
- Adjust rate limits if needed
- Check throttling settings

### Test 6: CloudWatch Logs Validation

**What it tests:**
- Logs are in JSON format
- Correlation IDs are present in logs
- Structured logging is working

**Expected result:**
- Log entries are valid JSON
- Each log entry contains a `correlation_id` field
- Standard fields are present (timestamp, level, function_name, etc.)

**Troubleshooting:**
- Check Lambda Layer deployment
- Verify StructuredLogger is being used
- Review log group permissions

### Test 7: X-Ray Traces Validation

**What it tests:**
- X-Ray tracing is enabled on Lambda functions
- Traces are being generated
- Traces are complete without errors

**Expected result:**
- Recent traces are found in X-Ray
- Traces show complete execution path
- No error traces (or minimal errors)

**Troubleshooting:**
- Verify X-Ray is enabled on all Lambda functions
- Check IAM permissions for X-Ray
- Review X-Ray SDK integration in code

### Test 8: Custom Metrics Validation

**What it tests:**
- Custom CloudWatch metrics are being emitted
- Metrics are in the correct namespace (BDO/MarketInsights)

**Expected result:**
- Multiple custom metrics are found
- Metrics include ETL pipeline metrics, query latency, etc.

**Troubleshooting:**
- Check metric emission code in Lambda functions
- Verify CloudWatch permissions
- Review metric namespace configuration

### Test 9: Error Handling Validation

**What it tests:**
- Invalid query parameters return 400
- Missing required parameters return 400
- Invalid date ranges return 400
- Error responses include proper error details

**Expected result:**
- All invalid requests return 400 status code
- Error responses contain error details
- Correlation IDs are included in error responses

**Troubleshooting:**
- Review Pydantic schema validation
- Check error handling in Lambda functions
- Verify error response formatting

## Manual Validation Steps

In addition to the automated tests, perform these manual checks:

### 1. CloudWatch Logs Deep Dive

```bash
# View recent logs for a Lambda function
aws logs tail /aws/lambda/retrieveIdList --follow --region us-east-1

# Search for correlation IDs
aws logs filter-log-events \
  --log-group-name /aws/lambda/retrieveIdList \
  --filter-pattern "correlation_id" \
  --region us-east-1
```

**What to look for:**
- All logs are in JSON format
- Correlation IDs are consistent across related log entries
- No unexpected errors or warnings
- Log levels are appropriate (INFO, ERROR, etc.)

### 2. X-Ray Service Map

1. Open AWS X-Ray Console
2. Navigate to Service Map
3. Select time range (last 5 minutes)

**What to look for:**
- Complete service map showing all components
- API Gateway → Step Functions → Lambda connections
- No red nodes indicating errors
- Reasonable latency values

### 3. CloudWatch Metrics Dashboard

```bash
# List all custom metrics
aws cloudwatch list-metrics \
  --namespace "BDO/MarketInsights" \
  --region us-east-1

# Get metric statistics
aws cloudwatch get-metric-statistics \
  --namespace "BDO/MarketInsights" \
  --metric-name "ETLPipelineSuccess" \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-31T23:59:59Z \
  --period 3600 \
  --statistics Sum \
  --region us-east-1
```

**What to look for:**
- Metrics for ETL pipeline success/failure
- Query latency metrics
- External API response time metrics
- Database connection pool utilization

### 4. API Gateway Logs

If API Gateway logging is enabled:

```bash
# View API Gateway execution logs
aws logs tail /aws/apigateway/bdo-market-insights-staging --follow --region us-east-1
```

**What to look for:**
- Request/response logging
- Integration latency
- No 5xx errors
- Proper CORS headers

### 5. Step Functions Execution History

1. Open AWS Step Functions Console
2. Select the Query Pipeline state machine
3. View recent executions

**What to look for:**
- Successful executions (green)
- Proper data flow between states
- No failed states
- Reasonable execution duration

### 6. Database Connection Testing

```bash
# Invoke queryData Lambda to test database connectivity
aws lambda invoke \
  --function-name queryData \
  --payload '{
    "item_id": 1001,
    "start_date": "2024-01-01T00:00:00Z",
    "end_date": "2024-01-31T23:59:59Z",
    "limit": 10,
    "correlation_id": "manual-test-123"
  }' \
  /tmp/query-response.json \
  --region us-east-1

# View the response
cat /tmp/query-response.json | jq .
```

**What to look for:**
- Successful database connection
- Query results returned
- No connection pool exhaustion errors

## Validation Checklist

Use this checklist to track validation progress:

- [ ] All automated tests pass
- [ ] CloudWatch logs are in JSON format
- [ ] Correlation IDs are present in all logs
- [ ] X-Ray traces show complete execution paths
- [ ] Custom metrics are being emitted
- [ ] API authentication works correctly
- [ ] Rate limiting is enforced
- [ ] Error handling returns proper status codes
- [ ] ETL pipeline executes successfully
- [ ] Query API returns correct data
- [ ] Step Functions state machine executes properly
- [ ] Database connections are working
- [ ] No critical errors in CloudWatch logs
- [ ] CloudWatch alarms are configured
- [ ] EventBridge schedules are set up

## Common Issues and Solutions

### Issue: API Gateway returns 403 Forbidden

**Possible causes:**
- API key not provided
- Invalid API key
- API key not associated with usage plan

**Solution:**
```bash
# Verify API key is active
aws apigateway get-api-key --api-key <API_KEY_ID> --include-value --region us-east-1

# Check usage plan association
aws apigateway get-usage-plans --region us-east-1
```

### Issue: Lambda function timeout

**Possible causes:**
- Database connection issues
- External API slow response
- Insufficient memory allocation

**Solution:**
- Increase Lambda timeout setting
- Check database connectivity
- Review CloudWatch logs for specific errors

### Issue: No X-Ray traces found

**Possible causes:**
- X-Ray not enabled on Lambda functions
- IAM permissions missing
- X-Ray SDK not properly integrated

**Solution:**
```bash
# Enable X-Ray on Lambda function
aws lambda update-function-configuration \
  --function-name <FUNCTION_NAME> \
  --tracing-config Mode=Active \
  --region us-east-1
```

### Issue: Custom metrics not appearing

**Possible causes:**
- Metrics not being emitted from code
- CloudWatch permissions missing
- Incorrect namespace

**Solution:**
- Review metric emission code
- Check IAM role has `cloudwatch:PutMetricData` permission
- Verify namespace is "BDO/MarketInsights"

## Next Steps

After successful validation:

1. **Document any issues found** and their resolutions
2. **Update configuration** if needed based on validation results
3. **Run load testing** to verify performance under load
4. **Schedule production deployment** if all tests pass
5. **Prepare rollback plan** in case of production issues

## Support

If you encounter issues during validation:

1. Check CloudWatch logs for detailed error messages
2. Review the troubleshooting sections above
3. Consult the main README.md for architecture details
4. Check AWS service health dashboard for any outages

## Validation Report Template

After completing validation, document the results:

```
# Staging Validation Report

**Date:** [Date]
**Validator:** [Name]
**Environment:** staging
**Region:** us-east-1

## Test Results

- Total Tests: [X]
- Passed: [X]
- Failed: [X]

## Failed Tests (if any)

[List any failed tests and their details]

## Manual Validation Results

- CloudWatch Logs: [PASS/FAIL]
- X-Ray Traces: [PASS/FAIL]
- Custom Metrics: [PASS/FAIL]
- API Gateway: [PASS/FAIL]
- Step Functions: [PASS/FAIL]
- Database Connectivity: [PASS/FAIL]

## Issues Found

[List any issues discovered during validation]

## Recommendations

[Any recommendations for improvements or fixes]

## Approval

- [ ] Staging environment is ready for production deployment
- [ ] Issues need to be resolved before production deployment

**Approved by:** [Name]
**Date:** [Date]
```
