# Production Deployment Guide

## Overview

This guide provides step-by-step instructions for deploying the BDO Market Insights system to production using a blue-green deployment strategy with gradual traffic shifting and automatic rollback capabilities.

## Prerequisites

Before deploying to production, ensure:

1. **Staging Validation Complete**
   - All staging tests have passed
   - Staging has been running successfully for at least 48 hours
   - No critical errors in staging CloudWatch logs
   - Performance metrics meet requirements

2. **Configuration Ready**
   - `config/deployment-config.sh` is configured for production
   - AWS credentials are configured with appropriate permissions
   - Database credentials are ready for Secrets Manager
   - External API credentials are available

3. **Backup and Rollback Plan**
   - Database backup is current
   - Previous Lambda versions are available
   - Rollback procedure is understood

4. **Team Notification**
   - Deployment window is scheduled
   - Team members are available for monitoring
   - Stakeholders are notified

## Deployment Strategy

The production deployment uses a **blue-green deployment** strategy:

1. **Blue Environment**: Current production (old version)
2. **Green Environment**: New deployment (new version)
3. **Traffic Shifting**: Gradual shift from blue to green
4. **Monitoring**: Continuous health checks during shift
5. **Rollback**: Automatic or manual rollback if issues detected

## Deployment Steps

### Step 1: Pre-Deployment Validation

Run the staging validation script to ensure staging is healthy:

```bash
bash scripts/validate-staging.sh
```

Check for:
- All Lambda functions are deployed
- API Gateway is responding correctly
- CloudWatch logs show no critical errors
- Performance metrics are within acceptable ranges

### Step 2: Review Configuration

Verify production configuration:

```bash
cat config/deployment-config.sh
```

Ensure:
- `ENVIRONMENT` is set to "production"
- `AWS_REGION` is correct
- `AWS_ACCOUNT_ID` is correct
- `ALLOWED_CORS_ORIGINS` includes production domains
- `ALARM_EMAIL` is set for notifications

### Step 3: Deploy to Production

Run the production deployment script:

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

**Important**: The script will prompt for confirmation before proceeding. Review all information carefully.

### Step 4: Validate Production Deployment

After deployment completes, run validation:

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

### Step 5: Initial Health Check

Run the monitoring script immediately after deployment:

```bash
bash scripts/monitor-production.sh 15
```

This checks the last 15 minutes for:
- Lambda function errors
- API Gateway 5xx errors
- CloudWatch alarm states
- Recent error logs

**Expected Results**:
- Error rate < 5%
- No API Gateway 5xx errors
- All alarms in OK state
- No critical error logs

### Step 6: Test Production API

Test the production API endpoint with a valid API key:

```bash
# Get API endpoint from deployment output
API_ENDPOINT="<your-api-endpoint>"
API_KEY="<your-api-key>"

# Test query endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}" \
  -v
```

**Expected Response**:
- HTTP 200 OK
- Valid JSON response with market data and analysis
- Response time < 2 seconds
- Cache-Control headers present

### Step 7: Test ETL Pipeline (Optional)

Manually trigger the ETL pipeline to verify it works:

```bash
# Invoke retrieveIdList Lambda
aws lambda invoke \
  --function-name retrieveIdList \
  --region us-east-1 \
  --log-type Tail \
  response.json

# Check response
cat response.json
```

Monitor CloudWatch logs for the complete ETL pipeline execution.

### Step 8: 24-Hour Monitoring Period

Monitor production continuously for 24 hours:

```bash
# Run every hour
bash scripts/monitor-production.sh 60
```

**Monitoring Checklist**:

- [ ] Lambda error rates remain < 5%
- [ ] API Gateway 5xx errors remain minimal
- [ ] Query API p95 latency < 2 seconds
- [ ] No CloudWatch alarms triggered
- [ ] ETL pipeline runs successfully on schedule
- [ ] No data loss or corruption
- [ ] X-Ray traces show healthy execution
- [ ] Database connection pool is not exhausted

**Key Metrics to Watch**:

1. **Lambda Functions**
   - Invocation count
   - Error count and rate
   - Duration (p50, p95, p99)
   - Throttles
   - Concurrent executions

2. **API Gateway**
   - Request count
   - 4xx and 5xx error rates
   - Latency (p50, p95, p99)
   - Cache hit rate

3. **Database**
   - Connection pool utilization
   - Query execution time
   - Slow query count

4. **External API**
   - Request count
   - Error rate
   - Response time
   - Rate limit hits

### Step 9: Verify Scheduled Execution

After 24 hours, verify the ETL pipeline ran on schedule:

```bash
# Check EventBridge Scheduler execution history
aws scheduler list-schedule-executions \
  --schedule-name bdo-etl-pipeline-production \
  --region us-east-1

# Check retrieveIdList Lambda logs
aws logs tail /aws/lambda/retrieveIdList \
  --since 24h \
  --region us-east-1
```

Verify:
- ETL pipeline executed at scheduled time (2 AM UTC)
- All Lambda functions completed successfully
- Data was collected and stored correctly
- No errors in CloudWatch logs

## Rollback Procedure

If issues are detected during the monitoring period, perform a rollback:

### Automatic Rollback

The deployment script saves rollback information to `/tmp/bdo-production-rollback-<timestamp>.txt`.

To rollback:

```bash
# Find the rollback file
ls -lt /tmp/bdo-production-rollback-*.txt | head -1

# Run rollback script
bash scripts/rollback-production.sh /tmp/bdo-production-rollback-<timestamp>.txt
```

### Manual Rollback

If the rollback script is not available:

1. **Identify Previous Versions**:
   ```bash
   aws lambda list-versions-by-function \
     --function-name <function-name> \
     --region us-east-1
   ```

2. **Update Alias to Previous Version**:
   ```bash
   aws lambda update-alias \
     --function-name <function-name> \
     --name live \
     --function-version <previous-version> \
     --region us-east-1
   ```

3. **Repeat for All Functions**

4. **Verify Rollback**:
   ```bash
   bash scripts/validate-production.sh
   bash scripts/monitor-production.sh 15
   ```

## Post-Deployment Tasks

After successful 24-hour monitoring:

1. **Document Deployment**
   - Record deployment date and time
   - Note any issues encountered
   - Update deployment log

2. **Clean Up Old Versions**
   - Keep last 3 versions of each Lambda function
   - Delete older versions to save storage

3. **Update Documentation**
   - Update API documentation if endpoints changed
   - Update runbooks if procedures changed

4. **Team Communication**
   - Notify team of successful deployment
   - Share any lessons learned
   - Update on-call procedures if needed

5. **Archive Rollback Information**
   - Move rollback file to permanent storage
   - Document rollback procedure used (if any)

## Troubleshooting

### High Error Rate

If Lambda error rate exceeds 5%:

1. Check CloudWatch logs for error details
2. Check X-Ray traces for bottlenecks
3. Verify database connectivity
4. Check External API availability
5. Consider rollback if errors persist

### API Latency Issues

If API p95 latency exceeds 2 seconds:

1. Check database query performance
2. Review X-Ray traces for slow operations
3. Check database connection pool utilization
4. Verify network connectivity
5. Consider increasing Lambda memory/timeout

### ETL Pipeline Failures

If ETL pipeline fails:

1. Check retrieveIdList Lambda logs
2. Verify DynamoDB access
3. Check External API availability
4. Verify database connectivity
5. Check for rate limiting issues

### CloudWatch Alarms Triggered

If alarms are triggered:

1. Identify which alarm triggered
2. Check associated metrics
3. Review CloudWatch logs for errors
4. Investigate root cause
5. Take corrective action or rollback

## Success Criteria

Deployment is considered successful when:

- [ ] All Lambda functions deployed without errors
- [ ] API Gateway responding correctly
- [ ] Error rate < 5% for 24 hours
- [ ] API p95 latency < 2 seconds
- [ ] No CloudWatch alarms triggered
- [ ] ETL pipeline runs successfully on schedule
- [ ] No data loss or corruption detected
- [ ] X-Ray traces show healthy execution
- [ ] Team confirms system is stable

## Rollback Criteria

Consider rollback if:

- Lambda error rate > 10% for more than 1 hour
- API Gateway 5xx error rate > 5%
- API p95 latency > 5 seconds consistently
- Critical CloudWatch alarms triggered
- Data loss or corruption detected
- ETL pipeline fails multiple times
- Database connection pool exhausted
- External API integration broken

## Support Contacts

- **DevOps Team**: devops@example.com
- **On-Call Engineer**: Use PagerDuty
- **Database Admin**: dba@example.com
- **AWS Support**: Use AWS Support Console

## Additional Resources

- [Staging Validation Guide](STAGING_VALIDATION_GUIDE.md)
- [CloudWatch Alarms Documentation](../infrastructure/CLOUDWATCH_ALARMS_README.md)
- [API Documentation](../infrastructure/API_DOCUMENTATION_README.md)
- [Step Functions Documentation](../infrastructure/STEP_FUNCTIONS_README.md)

## Appendix: Deployment Checklist

### Pre-Deployment
- [ ] Staging validation passed
- [ ] Configuration reviewed
- [ ] Team notified
- [ ] Backup verified
- [ ] Rollback plan ready

### Deployment
- [ ] Run deploy-production.sh
- [ ] Review deployment output
- [ ] Save rollback information
- [ ] Run validate-production.sh
- [ ] Run initial health check

### Post-Deployment
- [ ] Test API endpoint
- [ ] Test ETL pipeline (optional)
- [ ] Monitor for 24 hours
- [ ] Verify scheduled execution
- [ ] Document deployment
- [ ] Clean up old versions
- [ ] Notify team of success

### Rollback (if needed)
- [ ] Identify issues
- [ ] Run rollback script
- [ ] Verify rollback
- [ ] Monitor after rollback
- [ ] Document issues
- [ ] Plan remediation
