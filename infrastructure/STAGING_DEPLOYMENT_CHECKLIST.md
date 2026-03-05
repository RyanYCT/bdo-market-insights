# Staging Deployment Checklist

This checklist ensures all components are properly deployed and configured in the staging environment.

## Pre-Deployment Checklist

### Prerequisites
- [ ] AWS CLI v2 installed and configured
- [ ] Python 3.11+ installed
- [ ] AWS credentials configured with appropriate permissions
- [ ] Environment variables set:
  - `AWS_REGION`
  - `AWS_ACCOUNT_ID`
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`

### Code Quality
- [ ] All unit tests passing (`pytest tests/unit/`)
- [ ] All property-based tests passing (`pytest tests/property/`)
- [ ] All integration tests passing (`pytest tests/integration/`)
- [ ] Code coverage ≥ 80%
- [ ] Linting passed (`flake8`, `black`, `mypy`)
- [ ] Security scan passed (`bandit`)

### Documentation
- [ ] README.md updated
- [ ] API documentation updated
- [ ] CHANGELOG.md updated
- [ ] Deployment notes documented

## Deployment Steps

### 1. AWS Secrets Manager Configuration
- [ ] PostgreSQL credentials created/updated
  - Secret name: `bdo-db-credentials-staging`
  - Contains: username, password, host, port, database
- [ ] External API credentials created/updated
  - Secret name: `bdo-external-api-staging`
  - Contains: api_url, api_token
- [ ] Secrets accessible from Lambda execution role

### 2. Lambda Layer Deployment
- [ ] Lambda Layer built successfully
- [ ] Dependencies included:
  - `pydantic`
  - `psycopg2-binary`
  - `boto3`
  - `python-json-logger`
  - Common utilities (router, logging, database, etc.)
- [ ] Layer published to AWS Lambda
- [ ] Layer version number saved to `lambda_layer/layer-version.txt`
- [ ] Layer ARN recorded

### 3. Lambda Functions Deployment
- [ ] `retrieveIdList` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
  - Execution role has DynamoDB read permissions
- [ ] `fetchData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
  - Execution role has Secrets Manager read permissions
  - Timeout set appropriately (e.g., 5 minutes)
- [ ] `cleanData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
- [ ] `storeData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
  - Execution role has RDS access permissions
  - Execution role has Secrets Manager read permissions
  - VPC configuration (if RDS is in VPC)
- [ ] `queryData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
  - Execution role has RDS access permissions
  - Execution role has Secrets Manager read permissions
  - VPC configuration (if RDS is in VPC)
- [ ] `analyzeData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
- [ ] `retainData` deployed
  - Function code updated
  - Layer attached
  - Environment variables configured
  - Execution role has RDS access permissions
  - Execution role has S3 Glacier access permissions
  - Execution role has Secrets Manager read permissions
  - VPC configuration (if RDS is in VPC)

### 4. Step Functions State Machine
- [ ] CloudFormation stack deployed: `bdo-step-functions-staging`
- [ ] State machine created: `bdo-query-pipeline-staging`
- [ ] State machine type: EXPRESS
- [ ] Lambda function ARNs configured correctly:
  - queryData ARN
  - analyzeData ARN
- [ ] Execution role has Lambda invoke permissions
- [ ] CloudWatch Logs configured
- [ ] X-Ray tracing enabled
- [ ] State machine ARN recorded

### 5. API Gateway Configuration
- [ ] CloudFormation stack deployed: `bdo-api-gateway-staging`
- [ ] REST API created: `bdo-market-insights-api-staging`
- [ ] Resources configured:
  - `/query` (GET method)
  - `/docs` (GET method)
  - `/openapi.yaml` (GET method)
- [ ] API key created and enabled
- [ ] Usage plan configured:
  - Rate limit: 100 requests/minute
  - Burst limit: 20 concurrent requests
  - Daily quota: 10,000 requests
- [ ] API key associated with usage plan
- [ ] CORS configured for allowed origins
- [ ] Step Functions integration configured
- [ ] Request validation enabled
- [ ] Response models defined
- [ ] API deployed to `staging` stage
- [ ] CloudWatch access logs enabled
- [ ] X-Ray tracing enabled
- [ ] API endpoint URL recorded
- [ ] API key value recorded (securely)

### 6. X-Ray Tracing
- [ ] X-Ray enabled for all Lambda functions
- [ ] X-Ray enabled for API Gateway
- [ ] X-Ray enabled for Step Functions
- [ ] X-Ray daemon permissions configured
- [ ] Service map visible in X-Ray console

### 7. CloudWatch Alarms
- [ ] Lambda error rate alarms created (> 5%)
- [ ] API Gateway error rate alarms created (> 5%)
- [ ] API Gateway 4XX rate alarms created (> 50)
- [ ] API Gateway latency alarms created (p95 > 2s)
- [ ] Step Functions execution failed alarms created
- [ ] Step Functions throttled alarms created
- [ ] Step Functions duration alarms created (> 25s)
- [ ] Database connection pool alarms created (if applicable)
- [ ] SNS topic configured for alarm notifications (optional)

### 8. EventBridge Scheduler
- [ ] ETL pipeline schedule created: `bdo-etl-pipeline-staging`
  - Schedule: Daily at 2 AM UTC (`cron(0 2 * * ? *)`)
  - Target: retrieveIdList Lambda function
  - Execution role configured
- [ ] Data retention schedule created: `bdo-data-retention-staging`
  - Schedule: Monthly on 1st at 3 AM UTC (`cron(0 3 1 * ? *)`)
  - Target: retainData Lambda function
  - Execution role configured

### 9. S3 Bucket for API Documentation
- [ ] S3 bucket created: `bdo-api-docs-staging-{account-id}`
- [ ] Bucket policy configured for public read access
- [ ] Website hosting enabled
- [ ] CORS configured
- [ ] OpenAPI specification uploaded
- [ ] Swagger UI files uploaded

## Post-Deployment Validation

### Lambda Functions
- [ ] All functions show "Active" status in AWS Console
- [ ] Test invocations successful for each function
- [ ] CloudWatch log groups created for each function
- [ ] No errors in CloudWatch logs
- [ ] Layer version matches expected version

### API Gateway
- [ ] API endpoint accessible
- [ ] GET /query endpoint responds correctly
- [ ] API key authentication working (401 without key)
- [ ] Rate limiting working (429 after threshold)
- [ ] CORS headers present in responses
- [ ] Cache-Control headers present in responses
- [ ] API documentation accessible at /docs
- [ ] OpenAPI spec accessible at /openapi.yaml

### Step Functions
- [ ] State machine shows "Active" status
- [ ] Test execution successful
- [ ] Input transformation working correctly
- [ ] Output merging working correctly
- [ ] Error handling working correctly
- [ ] CloudWatch logs showing execution details
- [ ] X-Ray traces showing complete flow

### X-Ray Tracing
- [ ] Service map shows all components
- [ ] Traces visible for API requests
- [ ] Traces show complete request flow
- [ ] Trace IDs present in CloudWatch logs
- [ ] No missing segments in traces

### CloudWatch Alarms
- [ ] All alarms in "OK" state
- [ ] Alarm actions configured (if applicable)
- [ ] Test alarm triggers (optional)

### EventBridge Scheduler
- [ ] ETL schedule shows "Enabled" status
- [ ] Retention schedule shows "Enabled" status
- [ ] Next execution times correct
- [ ] Manual trigger test successful (optional)

## Functional Testing

### ETL Pipeline Test
- [ ] Manually trigger retrieveIdList
- [ ] Verify item IDs retrieved from DynamoDB
- [ ] Verify fetchData called with correct input
- [ ] Verify data fetched from External API
- [ ] Verify cleanData transforms data correctly
- [ ] Verify storeData inserts into PostgreSQL
- [ ] Verify correlation ID propagated through pipeline
- [ ] Check CloudWatch logs for all stages
- [ ] Verify X-Ray trace shows complete flow

### Query API Test
- [ ] Test with valid item_id
  ```bash
  curl -X GET "https://{api-id}.execute-api.{region}.amazonaws.com/staging/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
    -H "x-api-key: {api-key}"
  ```
- [ ] Verify response contains:
  - item_id
  - item_name
  - date_range
  - statistics (avg, min, max prices, stock, trades)
  - profitability_score
  - price_trend
  - correlation_id
- [ ] Test with invalid parameters (should return 400)
- [ ] Test without API key (should return 401)
- [ ] Test rate limiting (should return 429 after threshold)
- [ ] Test CORS preflight (OPTIONS request)
- [ ] Verify cache-control headers present
- [ ] Check CloudWatch logs for query execution
- [ ] Verify X-Ray trace shows complete flow

### Error Handling Test
- [ ] Test with non-existent item_id
- [ ] Test with invalid date range
- [ ] Test with missing required parameters
- [ ] Test with database connection failure (if possible)
- [ ] Verify error responses include correlation_id
- [ ] Verify errors logged to CloudWatch
- [ ] Verify alarms triggered for high error rates

### Performance Test
- [ ] Measure query API latency (should be < 2s p95)
- [ ] Test concurrent requests (within burst limit)
- [ ] Verify no Lambda cold start issues
- [ ] Verify database connection pooling working
- [ ] Check CloudWatch metrics for performance

## Security Validation

### Secrets Management
- [ ] Secrets not exposed in environment variables
- [ ] Secrets not logged to CloudWatch
- [ ] Secrets retrieved from Secrets Manager at runtime
- [ ] Secrets cached appropriately

### API Security
- [ ] API key required for all protected endpoints
- [ ] Invalid API keys rejected
- [ ] Rate limiting enforced per API key
- [ ] CORS restricted to allowed origins
- [ ] No sensitive data in API responses

### IAM Permissions
- [ ] Lambda execution roles follow least privilege
- [ ] API Gateway execution role has minimal permissions
- [ ] Step Functions execution role has minimal permissions
- [ ] No overly permissive policies

### Network Security
- [ ] Lambda functions in VPC (if RDS is in VPC)
- [ ] Security groups configured correctly
- [ ] RDS not publicly accessible
- [ ] S3 bucket policies restrictive

## Monitoring Setup

### CloudWatch Dashboards
- [ ] Dashboard created for staging environment
- [ ] Lambda metrics visible
- [ ] API Gateway metrics visible
- [ ] Step Functions metrics visible
- [ ] Custom metrics visible

### Log Aggregation
- [ ] All logs in JSON format
- [ ] Correlation IDs present in all logs
- [ ] Log retention configured (30 days)
- [ ] Log insights queries created

### Alerting
- [ ] Alarm notifications configured
- [ ] Team notified of alarm setup
- [ ] Escalation procedures documented

## Documentation

### Deployment Documentation
- [ ] Deployment steps documented
- [ ] Configuration parameters documented
- [ ] Secrets documented (not values!)
- [ ] API endpoint URLs documented
- [ ] Troubleshooting guide updated

### Runbook
- [ ] Operational procedures documented
- [ ] Rollback procedures documented
- [ ] Incident response procedures documented
- [ ] Contact information updated

## Sign-Off

### Technical Validation
- [ ] All deployment steps completed
- [ ] All validation tests passed
- [ ] No critical issues identified
- [ ] Performance meets requirements

### Stakeholder Approval
- [ ] Development team sign-off
- [ ] QA team sign-off
- [ ] DevOps team sign-off
- [ ] Product owner sign-off (if required)

### Handover
- [ ] Operations team briefed
- [ ] Documentation shared
- [ ] Access credentials provided
- [ ] Support procedures communicated

## Rollback Plan

If issues are discovered:

1. **Immediate Actions**
   - [ ] Identify affected components
   - [ ] Assess impact and severity
   - [ ] Notify stakeholders

2. **Rollback Lambda Functions**
   ```bash
   for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
       ./scripts/rollback-function.sh $func
   done
   ```

3. **Rollback Infrastructure**
   - [ ] Revert Step Functions state machine
   - [ ] Revert API Gateway configuration
   - [ ] Revert CloudWatch alarms

4. **Verification**
   - [ ] Verify rollback successful
   - [ ] Test functionality
   - [ ] Monitor for issues

5. **Post-Mortem**
   - [ ] Document issues encountered
   - [ ] Identify root causes
   - [ ] Create action items for fixes

## Notes

- Deployment date: _______________
- Deployed by: _______________
- Deployment duration: _______________
- Issues encountered: _______________
- Resolution: _______________

## References

- [Deployment Guide](../DEPLOYMENT.md)
- [API Documentation](../infrastructure/API_DOCUMENTATION_README.md)
- [Step Functions Documentation](../infrastructure/STEP_FUNCTIONS_README.md)
- [CloudWatch Alarms Documentation](../infrastructure/CLOUDWATCH_ALARMS_README.md)
- [Design Document](../.kiro/specs/bdo-market-insights-rewrite/design.md)
- [Requirements Document](../.kiro/specs/bdo-market-insights-rewrite/requirements.md)
