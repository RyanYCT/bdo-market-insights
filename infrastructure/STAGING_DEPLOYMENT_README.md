# Staging Deployment Documentation

This directory contains comprehensive documentation and scripts for deploying the BDO Market Insights system to the staging environment.

## Overview

The staging deployment process includes:
1. AWS Secrets Manager configuration for credentials
2. Lambda Layer deployment with shared code
3. Lambda Functions deployment (7 functions)
4. Step Functions state machine deployment
5. API Gateway configuration with API keys
6. X-Ray tracing enablement
7. CloudWatch alarms setup
8. EventBridge Scheduler configuration for ETL and retention

## Quick Start

### Automated Deployment (Recommended)

**Linux/macOS:**
```bash
chmod +x scripts/deploy-staging.sh
./scripts/deploy-staging.sh
```

**Windows:**
```cmd
scripts\deploy-staging.bat
```

The automated script will:
- Check prerequisites
- Guide you through Secrets Manager setup
- Deploy all components in the correct order
- Configure monitoring and scheduling
- Provide API endpoint and key information

### Manual Deployment

Follow the step-by-step guide in [STAGING_DEPLOYMENT_GUIDE.md](STAGING_DEPLOYMENT_GUIDE.md).

## Documentation Files

### 1. STAGING_DEPLOYMENT_GUIDE.md
**Purpose:** Comprehensive step-by-step deployment instructions

**Contents:**
- Prerequisites and setup
- Detailed deployment steps for each component
- Verification procedures
- Testing instructions
- Monitoring setup
- Troubleshooting guide
- Rollback procedures

**Use when:** You need detailed instructions for manual deployment or troubleshooting.

### 2. STAGING_DEPLOYMENT_CHECKLIST.md
**Purpose:** Complete checklist for deployment validation

**Contents:**
- Pre-deployment checklist
- Deployment steps checklist
- Post-deployment validation
- Functional testing checklist
- Security validation
- Monitoring setup verification
- Sign-off procedures

**Use when:** You need to ensure all deployment steps are completed and validated.

### 3. Deployment Scripts

#### scripts/deploy-staging.sh (Linux/macOS)
Automated deployment script that handles the entire staging deployment process.

**Features:**
- Interactive prompts for configuration
- Color-coded output for easy reading
- Error handling and validation
- Progress tracking
- Deployment summary

#### scripts/deploy-staging.bat (Windows)
Windows-compatible version of the deployment script.

**Features:**
- Same functionality as the shell script
- Windows command prompt compatible
- Handles Windows-specific path separators
- Uses Windows environment variables

## Prerequisites

### Required Tools
- AWS CLI v2
- Python 3.11+
- Git
- Bash (for Linux/macOS) or Command Prompt (for Windows)

### AWS Permissions
The deployment user/role needs:
- Lambda: Full access
- IAM: Create and manage roles
- CloudFormation: Full access
- API Gateway: Full access
- Step Functions: Full access
- CloudWatch: Create and manage alarms, log groups
- Secrets Manager: Create and manage secrets
- EventBridge Scheduler: Create and manage schedules
- S3: Access for API documentation bucket

### Environment Variables
```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
```

## Deployment Components

### 1. AWS Secrets Manager
**Secrets Created:**
- `bdo-db-credentials-staging`: PostgreSQL connection details
- `bdo-external-api-staging`: External API authentication

**Format:**
```json
{
  "username": "db_user",
  "password": "db_password",
  "host": "rds-endpoint.amazonaws.com",
  "port": 5432,
  "database": "bdo_market_insights"
}
```

### 2. Lambda Layer
**Name:** `bdo-common-layer`

**Contents:**
- Common utilities (router, logging, database, etc.)
- Pydantic schemas
- Dependencies (psycopg2, boto3, etc.)

**Version:** Stored in `lambda_layer/layer-version.txt`

### 3. Lambda Functions
**Functions Deployed:**
1. `retrieveIdList` - Fetches item IDs from DynamoDB
2. `fetchData` - Calls External API with rate limiting
3. `cleanData` - Transforms and validates data
4. `storeData` - Persists to PostgreSQL
5. `queryData` - Retrieves data from PostgreSQL
6. `analyzeData` - Computes statistics and metrics
7. `retainData` - Handles data retention and archival

**Configuration:**
- Runtime: Python 3.11
- Layer: Latest version of bdo-common-layer
- X-Ray: Enabled
- Environment variables: Configured per function
- VPC: Configured for database access (if needed)

### 4. Step Functions State Machine
**Name:** `bdo-query-pipeline-staging`

**Type:** EXPRESS (synchronous execution)

**Features:**
- Input transformation from API Gateway GET parameters
- Data flow between queryData and analyzeData
- Error handling with retry logic
- CloudWatch Logs integration
- X-Ray tracing

### 5. API Gateway
**Name:** `bdo-market-insights-api-staging`

**Endpoints:**
- `GET /query` - Query market data (API key required)
- `GET /docs` - Swagger UI documentation (public)
- `GET /openapi.yaml` - OpenAPI specification (public)

**Features:**
- API key authentication
- Rate limiting (100 req/min, 20 burst, 10k daily)
- CORS support
- Request validation
- CloudWatch access logs
- X-Ray tracing

### 6. CloudWatch Alarms
**Alarms Created:**
- Lambda error rates (> 5%)
- API Gateway error rates (> 5%)
- API Gateway 4XX rates (> 50)
- API Gateway latency (p95 > 2s)
- Step Functions execution failures
- Step Functions throttling
- Step Functions duration (> 25s)

### 7. EventBridge Scheduler
**Schedules Created:**
- `bdo-etl-pipeline-staging`: Daily at 2 AM UTC
- `bdo-data-retention-staging`: Monthly on 1st at 3 AM UTC

## Verification

After deployment, verify:

### 1. Lambda Functions
```bash
aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `retrieveIdList`)].FunctionName'
```

### 2. Step Functions
```bash
aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `staging`)].name'
```

### 3. API Gateway
```bash
curl -X GET "https://{api-id}.execute-api.{region}.amazonaws.com/staging/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: {api-key}"
```

### 4. X-Ray Tracing
Visit: https://console.aws.amazon.com/xray/home?region=us-east-1#/service-map

### 5. CloudWatch Alarms
```bash
aws cloudwatch describe-alarms --alarm-name-prefix "bdo-"
```

### 6. EventBridge Schedules
```bash
aws scheduler list-schedules --name-prefix "bdo-"
```

## Testing

### Manual ETL Pipeline Test
```bash
aws lambda invoke \
  --function-name retrieveIdList \
  --payload '{}' \
  response.json

cat response.json
```

### API Query Test
```bash
# Test with valid parameters
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}" \
  -v

# Test without API key (should return 401)
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z"

# Test with invalid parameters (should return 400)
curl -X GET "${API_ENDPOINT}/query?start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"
```

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
Access the CloudWatch console:
```
https://console.aws.amazon.com/cloudwatch/home?region=us-east-1
```

### X-Ray Traces
Access the X-Ray console:
```
https://console.aws.amazon.com/xray/home?region=us-east-1#/traces
```

## Troubleshooting

### Common Issues

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

### Getting Help

1. Check [STAGING_DEPLOYMENT_GUIDE.md](STAGING_DEPLOYMENT_GUIDE.md) for detailed instructions
2. Review [STAGING_DEPLOYMENT_CHECKLIST.md](STAGING_DEPLOYMENT_CHECKLIST.md) for validation steps
3. Check CloudWatch logs for error messages
4. Review X-Ray traces for request flow
5. Check AWS service status
6. Contact DevOps team

## Rollback

If issues are encountered:

```bash
# Rollback Lambda functions
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

## Next Steps

After successful staging deployment:

1. **Complete Task 27:** Run staging validation tests
2. **Monitor for 24 hours:** Watch metrics and alarms
3. **Document issues:** Update troubleshooting guide
4. **Prepare for production:** Review production deployment checklist
5. **Team handover:** Brief operations team on staging environment

## Security Considerations

### Secrets Management
- Never commit secrets to version control
- Use AWS Secrets Manager for all credentials
- Rotate secrets regularly
- Audit secret access

### API Security
- Protect API keys
- Monitor API usage
- Review rate limits regularly
- Audit API access logs

### Network Security
- Use VPC for database access
- Configure security groups restrictively
- Use RDS Proxy for connection pooling
- Enable encryption in transit and at rest

### IAM Security
- Follow least privilege principle
- Review IAM policies regularly
- Use IAM roles, not access keys
- Enable MFA for sensitive operations

## Support

For deployment support:
- Email: devops@example.com
- Slack: #bdo-market-insights
- On-call: Check PagerDuty

## References

- [Deployment Guide](STAGING_DEPLOYMENT_GUIDE.md)
- [Deployment Checklist](STAGING_DEPLOYMENT_CHECKLIST.md)
- [Main Deployment Documentation](../DEPLOYMENT.md)
- [API Documentation](API_DOCUMENTATION_README.md)
- [Step Functions Documentation](STEP_FUNCTIONS_README.md)
- [CloudWatch Alarms Documentation](CLOUDWATCH_ALARMS_README.md)
- [Design Document](../.kiro/specs/bdo-market-insights-rewrite/design.md)
- [Requirements Document](../.kiro/specs/bdo-market-insights-rewrite/requirements.md)
- [Tasks Document](../.kiro/specs/bdo-market-insights-rewrite/tasks.md)

## Change Log

### Version 1.0.0 (Initial Release)
- Created automated deployment scripts
- Added comprehensive documentation
- Implemented staging environment configuration
- Set up monitoring and alerting
- Configured EventBridge scheduling

---

**Last Updated:** 2024-03-06
**Maintained By:** DevOps Team
**Status:** Active
