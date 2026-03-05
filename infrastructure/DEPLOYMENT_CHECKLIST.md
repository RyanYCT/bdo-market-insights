# BDO Market Insights - Deployment Checklist

This checklist ensures all infrastructure components are deployed correctly for each environment.

## Pre-Deployment Checklist

### Prerequisites

- [ ] AWS CLI installed and configured
- [ ] Appropriate AWS credentials with CloudFormation permissions
- [ ] Lambda functions deployed
- [ ] RDS PostgreSQL instance running
- [ ] DynamoDB table created
- [ ] Secrets Manager secrets configured

### Configuration

- [ ] Environment name decided (dev, staging, prod)
- [ ] AWS region selected
- [ ] Email addresses for alarm notifications collected
- [ ] CORS allowed origins list prepared
- [ ] API key usage limits determined

## Deployment Order

Deploy infrastructure components in this order:

### 1. Lambda Functions and Layer

```bash
# Deploy Lambda Layer first
cd lambda_layer
# ... deploy layer ...

# Deploy Lambda functions
cd ../retrieveIdList
# ... deploy function ...

# Repeat for all 6 Lambda functions
```

- [ ] Lambda Layer deployed
- [ ] retrieveIdList Lambda deployed
- [ ] fetchData Lambda deployed
- [ ] cleanData Lambda deployed
- [ ] storeData Lambda deployed
- [ ] queryData Lambda deployed
- [ ] analyzeData Lambda deployed

### 2. Step Functions State Machine

```bash
cd infrastructure

# Deploy Step Functions
aws cloudformation deploy \
  --template-file step-functions-template.yaml \
  --stack-name bdo-step-functions-dev \
  --parameter-overrides \
    Environment=dev \
    QueryDataLambdaArn="arn:aws:lambda:REGION:ACCOUNT:function:queryData-dev" \
    AnalyzeDataLambdaArn="arn:aws:lambda:REGION:ACCOUNT:function:analyzeData-dev" \
  --capabilities CAPABILITY_NAMED_IAM
```

- [ ] Step Functions state machine deployed
- [ ] CloudWatch log group created
- [ ] IAM execution role created
- [ ] Step Functions alarms configured

### 3. API Gateway

```bash
# Get Step Functions ARN from previous deployment
STEP_FUNCTIONS_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-step-functions-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

# Deploy API Gateway
aws cloudformation deploy \
  --template-file api-gateway-template.yaml \
  --stack-name bdo-api-gateway-dev \
  --parameter-overrides \
    Environment=dev \
    AllowedOrigins="https://dev.example.com" \
    StepFunctionsArn="$STEP_FUNCTIONS_ARN" \
  --capabilities CAPABILITY_NAMED_IAM
```

- [ ] API Gateway REST API created
- [ ] GET /query endpoint configured
- [ ] API key created
- [ ] Usage plan configured
- [ ] CORS configured
- [ ] CloudWatch logging enabled
- [ ] X-Ray tracing enabled
- [ ] API Gateway alarms configured

### 4. CloudWatch Alarms

```bash
# Linux/Mac
./deploy-alarms.sh --environment dev --email devops@example.com

# Windows
deploy-alarms.bat --environment dev --email devops@example.com
```

- [ ] SNS topic created
- [ ] Email subscription confirmed
- [ ] Lambda error rate alarms created (6 alarms)
- [ ] Database health alarms created (3 alarms)
- [ ] External API health alarms created (4 alarms)
- [ ] ETL pipeline alarms created (2 alarms)
- [ ] Query pipeline alarms created (1 alarm)
- [ ] Lambda throttling alarms created (2 alarms)

### 5. EventBridge Scheduler (ETL Pipeline)

```bash
# Create EventBridge rule for ETL pipeline
aws events put-rule \
  --name bdo-etl-pipeline-schedule-dev \
  --schedule-expression "rate(1 hour)" \
  --state ENABLED

# Add retrieveIdList as target
aws events put-targets \
  --rule bdo-etl-pipeline-schedule-dev \
  --targets "Id"="1","Arn"="arn:aws:lambda:REGION:ACCOUNT:function:retrieveIdList-dev"
```

- [ ] EventBridge rule created
- [ ] Schedule configured (hourly for dev, adjust for prod)
- [ ] Lambda target configured
- [ ] Lambda permission granted to EventBridge

## Post-Deployment Verification

### 1. API Gateway Testing

```bash
# Get API endpoint and key
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
  --output text)

API_KEY=$(aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text)

# Test query endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"
```

- [ ] API endpoint responds with 200 OK
- [ ] Response includes expected data structure
- [ ] Cache-Control headers present
- [ ] CORS headers present
- [ ] Response time < 2 seconds

### 2. ETL Pipeline Testing

```bash
# Manually trigger ETL pipeline
aws lambda invoke \
  --function-name retrieveIdList-dev \
  --payload '{}' \
  response.json

cat response.json
```

- [ ] retrieveIdList executes successfully
- [ ] fetchData processes item IDs
- [ ] cleanData validates and transforms data
- [ ] storeData inserts records into PostgreSQL
- [ ] Correlation ID propagates through all functions
- [ ] CloudWatch logs show structured JSON

### 3. CloudWatch Monitoring

```bash
# Check alarm status
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --query 'MetricAlarms[?contains(AlarmName, `dev`)].[AlarmName,StateValue]' \
  --output table
```

- [ ] All alarms in OK state (or INSUFFICIENT_DATA initially)
- [ ] SNS topic has confirmed subscriptions
- [ ] CloudWatch logs receiving data
- [ ] Custom metrics being emitted

### 4. X-Ray Tracing

- [ ] X-Ray traces visible in AWS Console
- [ ] Traces show complete request flow
- [ ] Service map displays all components
- [ ] Trace IDs in CloudWatch logs

### 5. Database Verification

```bash
# Connect to RDS and verify data
psql -h <rds-endpoint> -U <username> -d <database>

# Check recent data
SELECT COUNT(*) FROM market_data WHERE scrape_id IN (
  SELECT id FROM market_scrape ORDER BY scrape_time DESC LIMIT 1
);
```

- [ ] MarketScrape records created
- [ ] Item records created
- [ ] MarketData records inserted
- [ ] Indexes present and used

## Security Verification

- [ ] API requires API key for access
- [ ] Secrets stored in Secrets Manager (not environment variables)
- [ ] IAM roles follow least privilege principle
- [ ] RDS not publicly accessible
- [ ] Lambda functions in VPC (if required)
- [ ] CloudWatch logs encrypted
- [ ] API Gateway uses HTTPS only

## Performance Verification

- [ ] API response time p95 < 2 seconds
- [ ] ETL pipeline completes in < 10 minutes
- [ ] Database connection pool not exhausted
- [ ] Lambda cold starts < 3 seconds
- [ ] No Lambda throttling occurring

## Cost Verification

Estimated monthly costs per environment:

| Component | Cost |
|-----------|------|
| Lambda executions | $5-20 |
| API Gateway requests | $3-10 |
| CloudWatch Logs | $5-15 |
| CloudWatch Alarms | $1.80 |
| Step Functions | $1-5 |
| RDS (db.t3.micro) | $15-20 |
| **Total** | **$30-70/month** |

- [ ] Cost estimates reviewed
- [ ] Budget alerts configured
- [ ] Resource tagging applied

## Rollback Plan

If issues are discovered:

1. **API Gateway**: Revert to previous deployment
   ```bash
   aws cloudformation update-stack \
     --stack-name bdo-api-gateway-dev \
     --use-previous-template
   ```

2. **Lambda Functions**: Use version aliases to switch back
   ```bash
   aws lambda update-alias \
     --function-name queryData-dev \
     --name LIVE \
     --function-version <previous-version>
   ```

3. **Step Functions**: Update state machine to previous definition
   ```bash
   aws stepfunctions update-state-machine \
     --state-machine-arn <arn> \
     --definition file://previous-definition.json
   ```

4. **CloudWatch Alarms**: Delete stack and redeploy previous version
   ```bash
   aws cloudformation delete-stack \
     --stack-name bdo-cloudwatch-alarms-dev
   ```

## Documentation

- [ ] API documentation updated
- [ ] Runbooks created for each alarm
- [ ] Architecture diagrams updated
- [ ] Deployment guide reviewed
- [ ] Team trained on new infrastructure

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Developer | | | |
| DevOps Engineer | | | |
| QA Engineer | | | |
| Product Owner | | | |

## Environment-Specific Notes

### Development

- [ ] Relaxed rate limits for testing
- [ ] Verbose logging enabled
- [ ] Test data populated
- [ ] Alarm thresholds adjusted for low traffic

### Staging

- [ ] Production-like configuration
- [ ] Load testing completed
- [ ] Failover testing completed
- [ ] Security scan completed

### Production

- [ ] All tests passed in staging
- [ ] Backup and recovery tested
- [ ] Monitoring dashboards created
- [ ] On-call rotation configured
- [ ] Incident response plan documented
- [ ] Blue-green deployment strategy confirmed

## Post-Deployment Monitoring (First 48 Hours)

- [ ] Hour 1: Check all alarms, logs, and metrics
- [ ] Hour 4: Verify ETL pipeline execution
- [ ] Hour 12: Review error rates and latency
- [ ] Hour 24: Check database performance
- [ ] Hour 48: Review cost and usage patterns

## Success Criteria

Deployment is successful when:

- ✅ All infrastructure components deployed without errors
- ✅ API responds correctly to test requests
- ✅ ETL pipeline executes successfully on schedule
- ✅ All alarms in OK state
- ✅ No errors in CloudWatch logs
- ✅ X-Ray traces show complete request flows
- ✅ Database contains expected data
- ✅ Performance meets SLAs (p95 < 2s)
- ✅ Security scan passes
- ✅ Team trained and documentation complete

## Troubleshooting

Common issues and solutions:

1. **API returns 401**: Check API key is correct and associated with usage plan
2. **Lambda timeout**: Increase memory allocation or timeout setting
3. **Database connection errors**: Check security groups and RDS Proxy configuration
4. **Alarms not triggering**: Verify metrics are being emitted
5. **High costs**: Review Lambda memory settings and API Gateway caching

## Support

For issues or questions:

- Documentation: See README files in infrastructure directory
- Logs: CloudWatch Logs for each component
- Metrics: CloudWatch Metrics and X-Ray
- Alarms: SNS notifications to configured email
- Escalation: Contact DevOps team or on-call engineer
