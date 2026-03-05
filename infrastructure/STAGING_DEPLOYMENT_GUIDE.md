# Staging Deployment Quick Start Guide

This guide provides step-by-step instructions for deploying the BDO Market Insights system to the staging environment.

## Quick Start

### Option 1: Automated Deployment (Recommended)

```bash
# Make script executable
chmod +x scripts/deploy-staging.sh

# Run deployment
./scripts/deploy-staging.sh
```

The script will guide you through:
1. Prerequisites check
2. Secrets Manager configuration
3. Lambda Layer deployment
4. Lambda Functions deployment
5. Step Functions deployment
6. API Gateway deployment
7. X-Ray enablement
8. CloudWatch alarms
9. EventBridge Scheduler configuration

### Option 2: Manual Deployment

Follow the steps below for manual deployment.

## Prerequisites

### 1. Install Required Tools

```bash
# AWS CLI v2
# macOS
brew install awscli

# Linux
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Windows
# Download from https://awscli.amazonaws.com/AWSCLIV2.msi

# Python 3.11+
python3 --version  # Should be 3.11 or higher
```

### 2. Configure AWS Credentials

```bash
# Configure AWS CLI
aws configure

# Or set environment variables
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
```

### 3. Verify Credentials

```bash
# Test AWS access
aws sts get-caller-identity

# Should output your account details
```

## Step-by-Step Deployment

### Step 1: Create AWS Secrets Manager Secrets

#### PostgreSQL Credentials

```bash
# Create secret for PostgreSQL
aws secretsmanager create-secret \
  --name bdo-db-credentials-staging \
  --description "PostgreSQL credentials for BDO Market Insights staging" \
  --secret-string '{
    "username": "your_db_user",
    "password": "your_db_password",
    "host": "your-rds-endpoint.rds.amazonaws.com",
    "port": 5432,
    "database": "bdo_market_insights"
  }' \
  --region us-east-1
```

#### External API Credentials

```bash
# Create secret for External API
aws secretsmanager create-secret \
  --name bdo-external-api-staging \
  --description "External API credentials for BDO Market Insights staging" \
  --secret-string '{
    "api_url": "https://api.example.com/market",
    "api_token": "your_api_token"
  }' \
  --region us-east-1
```

### Step 2: Deploy Lambda Layer

```bash
# Navigate to project root
cd /path/to/bdo-market-insights

# Deploy layer
./scripts/deploy-layer.sh

# Verify layer version
cat lambda_layer/layer-version.txt
```

### Step 3: Deploy Lambda Functions

```bash
# Deploy all functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    echo "Deploying $func..."
    ./scripts/deploy-function.sh $func
done

# Or deploy individually
./scripts/deploy-function.sh retrieveIdList
./scripts/deploy-function.sh fetchData
# ... etc
```

### Step 4: Deploy Step Functions State Machine

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
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

# Get state machine ARN
STEP_FUNCTIONS_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-step-functions-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

echo "State Machine ARN: $STEP_FUNCTIONS_ARN"
```

### Step 5: Deploy API Gateway

```bash
# Deploy using CloudFormation
aws cloudformation deploy \
  --template-file infrastructure/api-gateway-template.yaml \
  --stack-name bdo-api-gateway-staging \
  --parameter-overrides \
    Environment=staging \
    AllowedOrigins="https://staging.example.com" \
    StepFunctionsArn=$STEP_FUNCTIONS_ARN \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

# Get API endpoint
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

echo "API Endpoint: $API_ENDPOINT"

# Get API key
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
  --output text)

API_KEY_VALUE=$(aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text)

echo "API Key: $API_KEY_VALUE"
echo "IMPORTANT: Save this API key securely!"
```

### Step 6: Enable X-Ray Tracing

```bash
# Enable X-Ray for all Lambda functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    aws lambda update-function-configuration \
      --function-name $func \
      --tracing-config Mode=Active \
      --region us-east-1
    echo "X-Ray enabled for $func"
done
```

### Step 7: Deploy CloudWatch Alarms

```bash
# Deploy alarms
cd infrastructure
./deploy-alarms.sh
cd ..
```

### Step 8: Configure EventBridge Scheduler

#### ETL Pipeline Schedule (Daily at 2 AM UTC)

```bash
# Get Lambda ARN
RETRIEVE_ID_LIST_ARN=$(aws lambda get-function \
  --function-name retrieveIdList \
  --query 'Configuration.FunctionArn' \
  --output text)

# Create schedule
aws scheduler create-schedule \
  --name bdo-etl-pipeline-staging \
  --schedule-expression "cron(0 2 * * ? *)" \
  --flexible-time-window Mode=OFF \
  --target "{
    \"Arn\": \"$RETRIEVE_ID_LIST_ARN\",
    \"RoleArn\": \"arn:aws:iam::$AWS_ACCOUNT_ID:role/EventBridgeSchedulerRole\"
  }" \
  --description "Daily ETL pipeline for BDO Market Insights staging" \
  --region us-east-1
```

#### Data Retention Schedule (Monthly on 1st at 3 AM UTC)

```bash
# Get Lambda ARN
RETAIN_DATA_ARN=$(aws lambda get-function \
  --function-name retainData \
  --query 'Configuration.FunctionArn' \
  --output text)

# Create schedule
aws scheduler create-schedule \
  --name bdo-data-retention-staging \
  --schedule-expression "cron(0 3 1 * ? *)" \
  --flexible-time-window Mode=OFF \
  --target "{
    \"Arn\": \"$RETAIN_DATA_ARN\",
    \"RoleArn\": \"arn:aws:iam::$AWS_ACCOUNT_ID:role/EventBridgeSchedulerRole\"
  }" \
  --description "Monthly data retention for BDO Market Insights staging" \
  --region us-east-1
```

## Verification

### 1. Verify Lambda Functions

```bash
# List all functions
aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `retrieveIdList`) || starts_with(FunctionName, `fetchData`) || starts_with(FunctionName, `cleanData`) || starts_with(FunctionName, `storeData`) || starts_with(FunctionName, `queryData`) || starts_with(FunctionName, `analyzeData`) || starts_with(FunctionName, `retainData`)].FunctionName'

# Check function status
aws lambda get-function --function-name retrieveIdList --query 'Configuration.State'
```

### 2. Verify Step Functions

```bash
# Describe state machine
aws stepfunctions describe-state-machine \
  --state-machine-arn $STEP_FUNCTIONS_ARN

# Test execution (optional)
aws stepfunctions start-sync-execution \
  --state-machine-arn $STEP_FUNCTIONS_ARN \
  --input '{
    "queryStringParameters": {
      "item_id": "1001",
      "start_date": "2024-01-01T00:00:00Z",
      "end_date": "2024-01-31T23:59:59Z",
      "limit": "100"
    },
    "requestContext": {
      "requestId": "test-request-id"
    }
  }'
```

### 3. Verify API Gateway

```bash
# Test query endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z&limit=100" \
  -H "x-api-key: ${API_KEY_VALUE}" \
  -H "Content-Type: application/json"

# Test without API key (should return 401)
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z"

# Test API documentation
curl -X GET "${API_ENDPOINT}/docs"

# Test OpenAPI spec
curl -X GET "${API_ENDPOINT}/openapi.yaml"
```

### 4. Verify X-Ray Tracing

```bash
# Check X-Ray service map
# Visit: https://console.aws.amazon.com/xray/home?region=us-east-1#/service-map

# Or use CLI
aws xray get-service-graph \
  --start-time $(date -u -d '1 hour ago' +%s) \
  --end-time $(date -u +%s) \
  --region us-east-1
```

### 5. Verify CloudWatch Alarms

```bash
# List alarms
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --region us-east-1

# Check alarm state
aws cloudwatch describe-alarms \
  --alarm-names "bdo-api-error-rate-staging" \
  --query 'MetricAlarms[0].StateValue' \
  --output text
```

### 6. Verify EventBridge Schedules

```bash
# List schedules
aws scheduler list-schedules \
  --name-prefix "bdo-" \
  --region us-east-1

# Get schedule details
aws scheduler get-schedule \
  --name bdo-etl-pipeline-staging \
  --region us-east-1
```

## Testing

### Manual ETL Pipeline Test

```bash
# Invoke retrieveIdList manually
aws lambda invoke \
  --function-name retrieveIdList \
  --payload '{}' \
  --region us-east-1 \
  response.json

# Check response
cat response.json

# Check CloudWatch logs
aws logs tail /aws/lambda/retrieveIdList --follow
```

### API Query Test

```bash
# Test with valid parameters
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z&limit=100" \
  -H "x-api-key: ${API_KEY_VALUE}" \
  -v

# Test with invalid parameters (should return 400)
curl -X GET "${API_ENDPOINT}/query?start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY_VALUE}"

# Test rate limiting (send many requests)
for i in {1..150}; do
  curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
    -H "x-api-key: ${API_KEY_VALUE}" &
done
wait
```

## Monitoring

### CloudWatch Logs

```bash
# View Lambda logs
aws logs tail /aws/lambda/retrieveIdList --follow
aws logs tail /aws/lambda/queryData --follow

# View API Gateway logs
aws logs tail /aws/apigateway/bdo-market-insights-staging --follow

# View Step Functions logs
aws logs tail /aws/stepfunctions/bdo-query-pipeline-staging --follow
```

### CloudWatch Metrics

```bash
# View Lambda metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=retrieveIdList \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum \
  --region us-east-1

# View API Gateway metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway \
  --metric-name Count \
  --dimensions Name=ApiName,Value=bdo-market-insights-api-staging \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum \
  --region us-east-1
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
  --end-time $(date -u +%s) \
  --region us-east-1
```

## Troubleshooting

### Lambda Function Errors

```bash
# Check function configuration
aws lambda get-function-configuration --function-name retrieveIdList

# Check recent errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/retrieveIdList \
  --filter-pattern "ERROR" \
  --start-time $(date -u -d '1 hour ago' +%s)000
```

### API Gateway Errors

```bash
# Check API Gateway logs
aws logs filter-log-events \
  --log-group-name /aws/apigateway/bdo-market-insights-staging \
  --filter-pattern "5XX" \
  --start-time $(date -u -d '1 hour ago' +%s)000
```

### Step Functions Errors

```bash
# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn $STEP_FUNCTIONS_ARN \
  --status-filter FAILED \
  --max-results 10

# Get execution details
aws stepfunctions describe-execution \
  --execution-arn <execution-arn>
```

### Database Connection Issues

```bash
# Check Lambda VPC configuration
aws lambda get-function-configuration \
  --function-name storeData \
  --query 'VpcConfig'

# Check security groups
aws ec2 describe-security-groups \
  --group-ids <security-group-id>
```

## Rollback

If issues are encountered:

```bash
# Rollback all Lambda functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    ./scripts/rollback-function.sh $func
done

# Rollback CloudFormation stacks
aws cloudformation update-stack \
  --stack-name bdo-step-functions-staging \
  --use-previous-template \
  --capabilities CAPABILITY_NAMED_IAM

aws cloudformation update-stack \
  --stack-name bdo-api-gateway-staging \
  --use-previous-template \
  --capabilities CAPABILITY_NAMED_IAM
```

## Clean Up (Optional)

To remove the staging environment:

```bash
# Delete EventBridge schedules
aws scheduler delete-schedule --name bdo-etl-pipeline-staging
aws scheduler delete-schedule --name bdo-data-retention-staging

# Delete CloudFormation stacks
aws cloudformation delete-stack --stack-name bdo-api-gateway-staging
aws cloudformation delete-stack --stack-name bdo-step-functions-staging

# Delete Lambda functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    aws lambda delete-function --function-name $func
done

# Delete Lambda Layer
aws lambda delete-layer-version \
  --layer-name bdo-common-layer \
  --version-number <version>

# Delete Secrets
aws secretsmanager delete-secret \
  --secret-id bdo-db-credentials-staging \
  --force-delete-without-recovery

aws secretsmanager delete-secret \
  --secret-id bdo-external-api-staging \
  --force-delete-without-recovery
```

## Next Steps

After successful deployment:

1. **Run Task 27**: Execute staging validation tests
2. **Monitor for 24 hours**: Watch CloudWatch metrics and alarms
3. **Document any issues**: Update troubleshooting guide
4. **Prepare for production**: Review production deployment checklist

## Support

For deployment issues:
- Check [STAGING_DEPLOYMENT_CHECKLIST.md](STAGING_DEPLOYMENT_CHECKLIST.md)
- Review [DEPLOYMENT.md](../DEPLOYMENT.md)
- Check CloudWatch logs
- Review X-Ray traces
- Contact DevOps team

## References

- [Deployment Checklist](STAGING_DEPLOYMENT_CHECKLIST.md)
- [Deployment Guide](../DEPLOYMENT.md)
- [API Documentation](API_DOCUMENTATION_README.md)
- [Step Functions Documentation](STEP_FUNCTIONS_README.md)
- [CloudWatch Alarms Documentation](CLOUDWATCH_ALARMS_README.md)
- [Design Document](../.kiro/specs/bdo-market-insights-rewrite/design.md)
- [Requirements Document](../.kiro/specs/bdo-market-insights-rewrite/requirements.md)
