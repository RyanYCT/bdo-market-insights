# BDO Market Insights - API Gateway Infrastructure

This directory contains the infrastructure as code for the BDO Market Insights API Gateway configuration.

## Overview

The API Gateway is configured as a RESTful API with the following features:

- **GET /query** endpoint for querying market data
- API key authentication with rate limiting
- CORS support for approved origins
- Integration with Step Functions for query orchestration
- CloudWatch logging and monitoring
- X-Ray tracing enabled

## Architecture

```
Client Request (GET /query?item_id=X&start_date=Y)
    ↓
API Gateway (API Key Auth + Rate Limiting)
    ↓
Step Functions State Machine (Sync Execution)
    ↓
queryData Lambda → analyzeData Lambda
    ↓
API Gateway Response (with Cache-Control headers)
```

## Deployment

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. Step Functions state machine ARN for the query pipeline
3. Approved CORS origins list

### Deploy using AWS CLI

```bash
# Deploy to dev environment
aws cloudformation deploy \
  --template-file api-gateway-template.yaml \
  --stack-name bdo-api-gateway-dev \
  --parameter-overrides \
    Environment=dev \
    AllowedOrigins="https://dev.example.com" \
    StepFunctionsArn="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-dev" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to staging environment
aws cloudformation deploy \
  --template-file api-gateway-template.yaml \
  --stack-name bdo-api-gateway-staging \
  --parameter-overrides \
    Environment=staging \
    AllowedOrigins="https://staging.example.com" \
    StepFunctionsArn="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-staging" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to production environment
aws cloudformation deploy \
  --template-file api-gateway-template.yaml \
  --stack-name bdo-api-gateway-prod \
  --parameter-overrides \
    Environment=prod \
    AllowedOrigins="https://example.com,https://www.example.com" \
    StepFunctionsArn="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-prod" \
  --capabilities CAPABILITY_NAMED_IAM
```

### Retrieve API Key

After deployment, retrieve the API key value:

```bash
# Get API Key ID from stack outputs
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
  --output text)

# Get API Key value
aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text
```

## API Configuration

### API Documentation

The API provides interactive documentation through Swagger UI and an OpenAPI 3.0 specification.

**Documentation Endpoints:**
- **Swagger UI**: `GET /docs` - Interactive API documentation (no API key required)
- **OpenAPI Spec**: `GET /openapi.yaml` - Machine-readable API specification (no API key required)

For detailed information on deploying and managing API documentation, see [API_DOCUMENTATION_README.md](API_DOCUMENTATION_README.md).

### Rate Limiting

The API is configured with the following rate limits per API key:

- **Rate Limit**: 100 requests per minute
- **Burst Limit**: 20 concurrent requests
- **Daily Quota**: 10,000 requests per day

These limits can be adjusted by modifying the `BDOUsagePlan` resource in the CloudFormation template.

### CORS Configuration

CORS is configured to allow:

- **Origins**: Configurable via `AllowedOrigins` parameter
- **Methods**: GET, OPTIONS
- **Headers**: Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token
- **Max Age**: 3600 seconds (1 hour)

### Request Parameters

The GET /query endpoint accepts the following query parameters:

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

Successful responses (200 OK) include:

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

Response headers include:
- `Content-Type: application/json`
- `Cache-Control: public, max-age=300` (5 minutes)
- `Access-Control-Allow-Origin: <configured-origin>`

### Error Responses

- **400 Bad Request**: Invalid query parameters
- **401 Unauthorized**: Missing or invalid API key
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server-side error

## Monitoring

### CloudWatch Logs

API access logs are written to:
```
/aws/apigateway/bdo-market-insights-{environment}
```

Log format includes:
- Request ID
- Source IP
- Request time
- HTTP method
- Route
- Status code
- Response length
- Error message (if any)
- API key identifier

### CloudWatch Alarms

The following alarms are configured:

1. **API Error Rate**: Triggers when 5XX errors exceed 5% over 10 minutes
2. **API 4XX Rate**: Triggers when 4XX errors exceed 50 over 10 minutes
3. **API Latency**: Triggers when p95 latency exceeds 2 seconds over 10 minutes

For comprehensive system-wide alarms including Lambda error rates, database connection pool monitoring, and External API health, see [CLOUDWATCH_ALARMS_README.md](CLOUDWATCH_ALARMS_README.md).

### X-Ray Tracing

X-Ray tracing is enabled for all API requests. Traces include:
- API Gateway request/response
- Step Functions execution
- Lambda function invocations
- Database queries

## Testing

### Test API Endpoint

```bash
# Set variables
API_ENDPOINT="https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/dev"
API_KEY="your-api-key-value"

# Test query endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z&limit=100" \
  -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json"

# Test CORS preflight
curl -X OPTIONS "${API_ENDPOINT}/query" \
  -H "Origin: https://example.com" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: x-api-key"

# Test without API key (should return 401)
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "Content-Type: application/json"

# Test rate limiting (send many requests quickly)
for i in {1..150}; do
  curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
    -H "x-api-key: ${API_KEY}" \
    -H "Content-Type: application/json" &
done
wait
```

## Managing API Keys

### Create Additional API Keys

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

### Revoke API Key

```bash
# Disable API key
aws apigateway update-api-key \
  --api-key <api-key-id> \
  --patch-operations op=replace,path=/enabled,value=false

# Delete API key
aws apigateway delete-api-key \
  --api-key <api-key-id>
```

### Update Rate Limits

```bash
# Update usage plan throttle settings
aws apigateway update-usage-plan \
  --usage-plan-id <usage-plan-id> \
  --patch-operations \
    op=replace,path=/throttle/rateLimit,value=200 \
    op=replace,path=/throttle/burstLimit,value=50

# Update quota
aws apigateway update-usage-plan \
  --usage-plan-id <usage-plan-id> \
  --patch-operations \
    op=replace,path=/quota/limit,value=20000
```

## Troubleshooting

### Common Issues

1. **401 Unauthorized**
   - Verify API key is included in `x-api-key` header
   - Check API key is enabled and associated with usage plan
   - Verify API key hasn't expired

2. **429 Too Many Requests**
   - Check current rate limit usage in CloudWatch metrics
   - Consider increasing rate limits or implementing client-side throttling
   - Verify burst limit isn't being exceeded

3. **500 Internal Server Error**
   - Check CloudWatch logs for detailed error messages
   - Verify Step Functions state machine is deployed and accessible
   - Check Lambda function logs for errors
   - Verify IAM role has correct permissions

4. **CORS Errors**
   - Verify origin is in allowed origins list
   - Check OPTIONS preflight request is successful
   - Verify response includes correct CORS headers

### Viewing Logs

```bash
# View API access logs
aws logs tail /aws/apigateway/bdo-market-insights-dev --follow

# View specific request by correlation ID
aws logs filter-log-events \
  --log-group-name /aws/apigateway/bdo-market-insights-dev \
  --filter-pattern "correlation-id-here"
```

## Security Considerations

1. **API Keys**: Store API keys securely (e.g., AWS Secrets Manager, environment variables)
2. **CORS**: Only allow trusted origins
3. **Rate Limiting**: Adjust limits based on expected traffic patterns
4. **Monitoring**: Set up alerts for unusual traffic patterns
5. **Logging**: Ensure sensitive data is not logged
6. **IAM Roles**: Follow principle of least privilege

## Rollback

To rollback to a previous version:

```bash
# List stack events to find previous deployment
aws cloudformation describe-stack-events \
  --stack-name bdo-api-gateway-dev

# Rollback to previous template
aws cloudformation update-stack \
  --stack-name bdo-api-gateway-dev \
  --use-previous-template \
  --capabilities CAPABILITY_NAMED_IAM
```

## Clean Up

To delete the API Gateway stack:

```bash
aws cloudformation delete-stack \
  --stack-name bdo-api-gateway-dev
```

**Warning**: This will delete all API keys, usage plans, and access logs associated with the stack.
