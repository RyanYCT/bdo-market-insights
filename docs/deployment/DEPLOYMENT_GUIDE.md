# Deployment Guide

Complete guide for deploying the BDO Market Insights serverless application to staging and production environments.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Prerequisites](#prerequisites)
3. [Staging Deployment](#staging-deployment)
4. [Production Deployment](#production-deployment)
5. [Validation](#validation)
6. [Monitoring](#monitoring)
7. [Rollback Procedures](#rollback-procedures)
8. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Deploy to Staging in 5 Steps

```bash
# 1. Setup IAM permissions (one-time)
# See docs/deployment/IAM_SETUP_GUIDE.md for complete instructions
# Replace YOUR_ACCOUNT_ID in iam-policy-template.json and attach to your IAM user

# 2. Setup IAM roles
./scripts/setup-iam-roles.sh

# 3. Setup configuration (one-time)
chmod +x scripts/setup-deployment-config.sh
./scripts/setup-deployment-config.sh

# 4. Verify AWS access
source config/deployment-config.sh
aws sts get-caller-identity

# 5. Deploy to staging
chmod +x scripts/deploy-staging.sh
./scripts/deploy-staging.sh
```

### Deploy to Production

```bash
# After staging validation passes
bash scripts/deploy-production.sh
```

---

## Prerequisites

### Required Tools

- **AWS CLI v2** - [Installation guide](https://aws.amazon.com/cli/)
- **Python 3.14+** - Check with `python3 --version`
- **Git** - For version control
- **Make** (optional) - For convenience commands

### AWS Permissions

The deployment user/role needs permissions to manage AWS resources.

**See [IAM_SETUP_GUIDE.md](IAM_SETUP_GUIDE.md) for complete setup instructions.**

**Quick summary:**
1. Replace `YOUR_ACCOUNT_ID` in `iam-policy-template.json` with your AWS account ID
2. Attach the policy to your IAM user (inline or managed policy)
3. Run `./scripts/setup-iam-roles.sh` to configure IAM roles

**What the policy provides:**
- Lambda: Full access (create, update, publish functions and layers)
- IAM: Create and manage roles for Lambda functions
- CloudFormation: Full access for stack management
- API Gateway: Full access
- Step Functions: Full access
- CloudWatch: Create and manage alarms, log groups, publish metrics
- Secrets Manager: Create and manage secrets
- EventBridge Scheduler: Create and manage schedules
- DynamoDB, S3, SNS, X-Ray: Project-specific access

All permissions are scoped to BDO-specific resources following the principle of least privilege.

### Configuration Methods

#### Option A: AWS CLI Profile (Recommended)
```bash
aws configure --profile bdo-staging
# Then in config: export AWS_PROFILE="bdo-staging"
```

#### Option B: AWS SSO (For Organizations)
```bash
aws configure sso --profile bdo-staging
aws sso login --profile bdo-staging
# Then in config: export AWS_PROFILE="bdo-staging"
```

#### Option C: Environment Variables (Testing Only)
```bash
# In config: export AWS_ACCESS_KEY_ID="..."
# In config: export AWS_SECRET_ACCESS_KEY="..."
```

### Required Information

You'll need during setup:

**AWS Configuration:**
- AWS Region (e.g., `us-east-1`)
- AWS Account ID (12-digit number)
- AWS Credentials (profile or keys)

**Database Credentials (for Secrets Manager):**
- PostgreSQL host
- PostgreSQL port (default: 5432)
- Database name
- Username
- Password

**API Configuration:**
- External API URL
- API token (if required)
- CORS allowed origins

**Monitoring (Optional):**
- Alarm notification email
- Slack webhook URL

---

## Staging Deployment

### Automated Deployment (Recommended)

```bash
chmod +x scripts/deploy-staging.sh
./scripts/deploy-staging.sh
```

The script will guide you through:
1. Prerequisites check
2. Secrets Manager configuration
3. Lambda Layer deployment
4. Lambda Functions deployment (7 functions)
5. Step Functions deployment
6. API Gateway deployment
7. X-Ray enablement
8. CloudWatch alarms
9. EventBridge Scheduler configuration

### Manual Deployment Steps

#### Step 1: Create AWS Secrets Manager Secrets

**PostgreSQL Credentials:**
```bash
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

**External API Credentials:**
```bash
aws secretsmanager create-secret \
  --name bdo-external-api-staging \
  --description "External API credentials for BDO Market Insights staging" \
  --secret-string '{
    "api_url": "https://api.example.com/market",
    "api_token": "your_api_token"
  }' \
  --region us-east-1
```

#### Step 2: Deploy Lambda Layer

```bash
./scripts/deploy-layer.sh
cat lambda_layer/layer-version.txt  # Verify version
```

#### Step 3: Deploy Lambda Functions

```bash
# Deploy all functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    echo "Deploying $func..."
    ./scripts/deploy-function.sh $func
done
```

#### Step 4: Deploy Step Functions State Machine

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
```

#### Step 5: Deploy API Gateway

```bash
# Get Step Functions ARN
STEP_FUNCTIONS_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-step-functions-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

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

# Get API endpoint and key
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-staging \
  --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
  --output text)

API_KEY_VALUE=$(aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text)

echo "API Endpoint: $API_ENDPOINT"
echo "API Key: $API_KEY_VALUE"
echo "IMPORTANT: Save this API key securely!"
```

#### Step 6: Enable X-Ray Tracing

```bash
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    aws lambda update-function-configuration \
      --function-name $func \
      --tracing-config Mode=Active \
      --region us-east-1
    echo "X-Ray enabled for $func"
done
```

#### Step 7: Deploy CloudWatch Alarms

```bash
cd infrastructure
./deploy-alarms.sh
cd ..
```

#### Step 8: Configure EventBridge Scheduler

**ETL Pipeline Schedule (Daily at 2 AM UTC):**
```bash
RETRIEVE_ID_LIST_ARN=$(aws lambda get-function \
  --function-name retrieveIdList \
  --query 'Configuration.FunctionArn' \
  --output text)

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

**Data Retention Schedule (Monthly on 1st at 3 AM UTC):**
```bash
RETAIN_DATA_ARN=$(aws lambda get-function \
  --function-name retainData \
  --query 'Configuration.FunctionArn' \
  --output text)

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

### What Gets Deployed

1. ✅ AWS Secrets Manager (PostgreSQL + API credentials)
2. ✅ Lambda Layer (shared code)
3. ✅ 7 Lambda Functions (ETL + Query pipeline)
4. ✅ Step Functions (query orchestration)
5. ✅ API Gateway (REST API with API keys)
6. ✅ X-Ray Tracing (all components)
7. ✅ CloudWatch Alarms (monitoring)
8. ✅ EventBridge Scheduler (ETL + retention)

---

## Production Deployment

### Deployment Strategy

Production uses a **blue-green deployment** strategy:

1. **Blue Environment**: Current production (old version)
2. **Green Environment**: New deployment (new version)
3. **Traffic Shifting**: Gradual shift from blue to green
4. **Monitoring**: Continuous health checks during shift
5. **Rollback**: Automatic or manual rollback if issues detected

### Pre-Deployment Checklist

Before deploying to production, ensure:

- [ ] **Staging Validation Complete**
  - All staging tests have passed
  - Staging has been running successfully for at least 48 hours
  - No critical errors in staging CloudWatch logs
  - Performance metrics meet requirements

- [ ] **Configuration Ready**
  - `config/deployment-config.sh` is configured for production
  - AWS credentials are configured with appropriate permissions
  - Database credentials are ready for Secrets Manager
  - External API credentials are available

- [ ] **Backup and Rollback Plan**
  - Database backup is current
  - Previous Lambda versions are available
  - Rollback procedure is understood

- [ ] **Team Notification**
  - Deployment window is scheduled
  - Team members are available for monitoring
  - Stakeholders are notified

### Production Deployment Steps

#### Step 1: Pre-Deployment Validation

```bash
# Run staging validation
bash scripts/validate-staging.sh

# Verify production configuration
cat config/deployment-config.sh
```

#### Step 2: Deploy to Production

```bash
bash scripts/deploy-production.sh
```

The script will:
1. Verify prerequisites
2. Check staging health
3. Deploy Lambda Layer
4. Deploy Lambda functions with blue-green strategy
5. Update Step Functions state machine
6. Update API Gateway
7. Enable X-Ray tracing
8. Update CloudWatch alarms
9. Configure EventBridge Scheduler
10. Save rollback information

**Important**: The script will prompt for confirmation before proceeding.

#### Step 3: Validate Production Deployment

```bash
bash scripts/validate-production.sh
```

This checks:
- All Lambda functions are deployed correctly
- X-Ray tracing is enabled
- Lambda Layer is attached
- Step Functions state machine is active
- API Gateway is configured correctly
- EventBridge schedules are enabled
- CloudWatch alarms are created
- Secrets Manager secrets exist

#### Step 4: Initial Health Check

```bash
bash scripts/monitor-production.sh 15
```

Expected results:
- Error rate < 5%
- No API Gateway 5xx errors
- All alarms in OK state
- No critical error logs

#### Step 5: Test Production API

```bash
API_ENDPOINT="<your-api-endpoint>"
API_KEY="<your-api-key>"

curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}" \
  -v
```

Expected response:
- HTTP 200 OK
- Valid JSON response with market data and analysis
- Response time < 2 seconds
- Cache-Control headers present

#### Step 6: 24-Hour Monitoring Period

```bash
# Run every hour
bash scripts/monitor-production.sh 60
```

Monitor:
- [ ] Lambda error rates remain < 5%
- [ ] API Gateway 5xx errors remain minimal
- [ ] Query API p95 latency < 2 seconds
- [ ] No CloudWatch alarms triggered
- [ ] ETL pipeline runs successfully on schedule
- [ ] No data loss or corruption
- [ ] X-Ray traces show healthy execution
- [ ] Database connection pool is not exhausted

### Blue-Green Deployment Details

Lambda functions use aliases for blue-green deployment:

1. **New version deployed**: Code is updated and published as a new version
2. **Smoke test**: New version is tested with a test invocation
3. **Alias update**: If test passes, 'live' alias points to new version
4. **Automatic rollback**: If test fails, alias stays on previous version

**Alias Strategy:**
- **$LATEST**: Always points to the most recent code (not used in production)
- **Numbered versions**: Immutable versions (1, 2, 3, etc.)
- **live alias**: Points to the current production version

---

## Validation

### Staging Validation

Run the automated validation script:

```bash
bash scripts/validate-staging.sh
```

**Tests performed:**
1. ETL Pipeline execution
2. Query API functionality
3. API authentication and authorization
4. Rate limiting
5. CloudWatch logs (JSON format and correlation IDs)
6. X-Ray distributed tracing
7. Custom CloudWatch metrics
8. Error handling scenarios
8. Error handling scenarios

### Production Validation

Run the production validation script:

```bash
bash scripts/validate-production.sh
```

**Checks performed:**
- Lambda functions exist and configured correctly
- X-Ray tracing enabled
- Lambda Layer attached
- 'live' alias configured
- Step Functions state machine active
- API Gateway deployed and accessible
- EventBridge schedules enabled
- CloudWatch alarms created
- Secrets Manager secrets exist

### Manual Verification

#### 1. Lambda Functions
```bash
aws lambda list-functions --query 'Functions[].FunctionName'
aws lambda get-function --function-name retrieveIdList --query 'Configuration.State'
```

#### 2. Step Functions
```bash
aws stepfunctions describe-state-machine --state-machine-arn $STEP_FUNCTIONS_ARN
```

#### 3. API Gateway
```bash
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"
```

#### 4. X-Ray Tracing
Visit: https://console.aws.amazon.com/xray/home?region=us-east-1#/service-map

#### 5. CloudWatch Alarms
```bash
aws cloudwatch describe-alarms --alarm-name-prefix "bdo-"
```

#### 6. EventBridge Schedules
```bash
aws scheduler list-schedules --name-prefix "bdo-"
```

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

# API Gateway requests
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway \
  --metric-name Count \
  --dimensions Name=ApiName,Value=bdo-market-insights-api-staging \
  --start-time $(date -u -d '1 hour ago' --iso-8601=seconds) \
  --end-time $(date -u --iso-8601=seconds) \
  --period 300 \
  --statistics Sum
```

### Production Monitoring Script

```bash
# Monitor last 60 minutes (default)
bash scripts/monitor-production.sh

# Monitor last 15 minutes
bash scripts/monitor-production.sh 15

# Monitor last 24 hours
bash scripts/monitor-production.sh 1440
```

**Metrics monitored:**
- Lambda function invocations and errors
- Lambda error rates
- API Gateway request count and 5xx errors
- API Gateway latency (p95)
- CloudWatch alarm states
- Recent error logs

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

## Rollback Procedures

### Automatic Rollback

The deployment scripts automatically rollback on smoke test failure. No manual intervention needed.

### Manual Rollback - Staging

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

### Manual Rollback - Production

```bash
# Use rollback file from deployment
bash scripts/rollback-production.sh /tmp/bdo-production-rollback-<timestamp>.txt

# Or rollback individual function
./scripts/rollback-function.sh <function-name>

# Or rollback to specific version
./scripts/rollback-function.sh <function-name> <version-number>
```

### Rollback Criteria

Consider rollback if:

- Lambda error rate > 10% for more than 1 hour
- API Gateway 5xx error rate > 5%
- API p95 latency > 5 seconds consistently
- Critical CloudWatch alarms triggered
- Data loss or corruption detected
- ETL pipeline fails multiple times
- Database connection pool exhausted
- External API integration broken

---

## Troubleshooting

### Common Issues

#### 0. IAM Permission Denied Errors

**Symptom:** Scripts fail with "AccessDenied" or "not authorized to perform" errors

**Examples:**
```
User: arn:aws:iam::YOUR_ACCOUNT_ID:user/bdo-market-insights is not authorized 
to perform: iam:PutRolePolicy on resource: role retrieveIdList-role-nc31m9mk
```

**Solution:**

See [IAM_SETUP_GUIDE.md](IAM_SETUP_GUIDE.md) for complete instructions. Quick summary:

1. **Attach IAM policy to your user**:
   - Replace `YOUR_ACCOUNT_ID` in `iam-policy-template.json`
   - Attach policy to your IAM user (see IAM_SETUP_GUIDE.md for commands)
   - Run `./scripts/setup-iam-roles.sh`

2. **Use AWS Console** (Alternative):
   - See [IAM_SETUP_GUIDE.md - Manual Fix](IAM_SETUP_GUIDE.md#manual-fix-aws-console)
   - Manually attach policy via IAM console

3. **Use AWS CloudShell** (If you have console access):
   - CloudShell typically has admin permissions
   - Run fix commands from there

#### 1. Secrets Manager Access Denied

**Symptom:** Lambda functions fail with "Access Denied" when retrieving secrets

**Solution:**
- Verify Lambda execution role has `secretsmanager:GetSecretValue` permission
- Check secret name matches exactly (case-sensitive)
- Verify secret exists in the correct region

#### 2. Database Connection Timeout

**Symptom:** storeData/queryData functions timeout

**Solution:**
- Verify Lambda functions are in the same VPC as RDS
- Check security groups allow traffic from Lambda to RDS
- Verify RDS endpoint is correct in Secrets Manager
- Check database is running and accessible

#### 3. API Gateway 502 Error

**Symptom:** API returns 502 Bad Gateway

**Solution:**
- Check Step Functions execution logs
- Verify Lambda functions are not timing out
- Check Lambda function errors in CloudWatch
- Verify Step Functions has permission to invoke Lambdas

#### 4. X-Ray Traces Missing

**Symptom:** No traces visible in X-Ray console

**Solution:**
- Verify X-Ray is enabled for all components
- Check Lambda execution role has X-Ray write permissions
- Wait a few minutes for traces to appear
- Verify requests are actually being made

#### 5. EventBridge Schedule Not Triggering

**Symptom:** ETL pipeline not running on schedule

**Solution:**
- Verify schedule is enabled
- Check schedule expression is correct
- Verify EventBridge has permission to invoke Lambda
- Check Lambda function logs for invocation errors

#### 6. Lambda Function Timeout

**Symptom:** Functions timeout before completing

**Solution:**
- Increase Lambda timeout setting
- Check database connectivity
- Review CloudWatch logs for specific errors
- Increase memory allocation if needed

#### 7. High Error Rate

**Symptom:** Error rate exceeds 5%

**Solution:**
- Check CloudWatch logs for error details
- Check X-Ray traces for bottlenecks
- Verify database connectivity
- Check External API availability
- Consider rollback if errors persist

#### 8. API Latency Issues

**Symptom:** API p95 latency exceeds 2 seconds

**Solution:**
- Check database query performance
- Review X-Ray traces for slow operations
- Check database connection pool utilization
- Verify network connectivity
- Consider increasing Lambda memory/timeout

### Debug Mode

Enable verbose output:

```bash
# For deployment scripts
set -x
./scripts/deploy-function.sh retrieveIdList

# For AWS CLI
export AWS_DEBUG=1
aws lambda get-function --function-name retrieveIdList
```

### Getting Help

1. Check CloudWatch Logs
2. Review X-Ray traces
3. Check GitHub Actions logs
4. Review this documentation
5. Contact development team

---

## Security Checklist

- [ ] Configuration file created: `config/deployment-config.sh`
- [ ] File permissions set: `chmod 600 config/deployment-config.sh`
- [ ] AWS credentials configured (profile or keys)
- [ ] AWS access verified: `aws sts get-caller-identity`
- [ ] Configuration NOT committed to git (check `.gitignore`)
- [ ] API keys stored securely
- [ ] Secrets stored in AWS Secrets Manager
- [ ] IAM roles follow least privilege principle
- [ ] RDS not publicly accessible
- [ ] CloudWatch logs encrypted
- [ ] API Gateway uses HTTPS only

---

## Best Practices

### Before Deployment

1. **Run tests locally**
   ```bash
   make test-all
   make lint
   ```

2. **Review changes**
   ```bash
   git diff main..develop
   ```

3. **Update documentation**
   - Update README if needed
   - Update API documentation
   - Update CHANGELOG

### During Deployment

1. **Monitor logs**
   ```bash
   aws logs tail /aws/lambda/retrieveIdList --follow
   ```

2. **Check metrics**
   - Watch CloudWatch dashboard
   - Monitor error rates
   - Check latency

3. **Verify functionality**
   - Test API endpoints
   - Run smoke tests
   - Check data flow

### After Deployment

1. **Monitor for 24 hours**
   - Watch CloudWatch alarms
   - Check error rates
   - Monitor performance

2. **Document changes**
   - Update CHANGELOG
   - Document any issues
   - Share with team

3. **Keep rollback ready**
   - Know previous version numbers
   - Have rollback scripts ready
   - Monitor for issues

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

### Test API

```bash
API_ENDPOINT="https://xxx.execute-api.us-east-1.amazonaws.com/staging"
API_KEY="your-api-key-from-deployment"

curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"
```

### Quick Links

- [AWS Console](https://console.aws.amazon.com/)
- [CloudWatch Logs](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups)
- [X-Ray Service Map](https://console.aws.amazon.com/xray/home#/service-map)
- [Lambda Functions](https://console.aws.amazon.com/lambda/home#/functions)
- [API Gateway](https://console.aws.amazon.com/apigateway/home#/apis)

---

## Support

For deployment issues:
1. Check this guide
2. Review CloudWatch logs
3. Check GitHub Actions logs
4. Contact DevOps team

**Security Reminder:** Never commit `config/deployment-config.sh` to version control! 🔒

