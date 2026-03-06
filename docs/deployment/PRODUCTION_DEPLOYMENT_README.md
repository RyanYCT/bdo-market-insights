# Production Deployment Scripts

## Overview

This directory contains scripts for deploying the BDO Market Insights system to production with blue-green deployment strategy, monitoring, validation, and rollback capabilities.

## Scripts

### deploy-production.sh / deploy-production.bat

Main production deployment script with blue-green deployment strategy.

**Features**:
- Pre-deployment validation
- Staging health check
- Blue-green deployment with version tracking
- Automatic rollback file generation
- X-Ray tracing enablement
- CloudWatch alarms configuration
- EventBridge Scheduler setup

**Usage**:
```bash
# Linux/Mac
bash scripts/deploy-production.sh

# Windows
scripts\deploy-production.bat
```

**Prerequisites**:
- AWS CLI configured with production credentials
- `config/deployment-config.sh` configured for production
- Staging environment validated
- Database credentials ready

**Output**:
- Deployment summary
- API endpoint URL
- Rollback file location
- Monitoring instructions

### validate-production.sh

Validates production deployment configuration and health.

**Checks**:
- Lambda functions exist and are configured correctly
- X-Ray tracing is enabled
- Lambda Layer is attached
- 'live' alias is configured
- Step Functions state machine is active
- API Gateway is deployed and accessible
- EventBridge schedules are enabled
- CloudWatch alarms are created
- Secrets Manager secrets exist

**Usage**:
```bash
bash scripts/validate-production.sh
```

**Exit Codes**:
- 0: All validations passed
- 1: One or more validations failed

### monitor-production.sh

Monitors production health and metrics.

**Metrics Monitored**:
- Lambda function invocations and errors
- Lambda error rates
- API Gateway request count and 5xx errors
- API Gateway latency (p95)
- CloudWatch alarm states
- Recent error logs

**Usage**:
```bash
# Monitor last 60 minutes (default)
bash scripts/monitor-production.sh

# Monitor last 15 minutes
bash scripts/monitor-production.sh 15

# Monitor last 24 hours
bash scripts/monitor-production.sh 1440
```

**Output**:
- Lambda health summary
- API Gateway health summary
- CloudWatch alarm status
- Recent error log count
- Overall health assessment

### rollback-production.sh

Rolls back Lambda functions to previous versions.

**Features**:
- Reads rollback file from deployment
- Updates all Lambda function aliases to previous versions
- Validates rollback success
- Provides rollback summary

**Usage**:
```bash
bash scripts/rollback-production.sh /tmp/bdo-production-rollback-<timestamp>.txt
```

**Prerequisites**:
- Rollback file from deployment
- AWS CLI configured with production credentials

**Output**:
- Rollback progress for each function
- Success/failure summary
- Next steps

## Deployment Workflow

### 1. Pre-Deployment

```bash
# Validate staging
bash scripts/validate-staging.sh

# Review configuration
cat config/deployment-config.sh

# Ensure team is notified
```

### 2. Deploy to Production

```bash
# Run deployment
bash scripts/deploy-production.sh

# Save rollback file location
ROLLBACK_FILE=/tmp/bdo-production-rollback-<timestamp>.txt
```

### 3. Validate Deployment

```bash
# Run validation
bash scripts/validate-production.sh

# Check initial health
bash scripts/monitor-production.sh 15
```

### 4. Test Production

```bash
# Test API endpoint
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"

# Test ETL pipeline (optional)
aws lambda invoke \
  --function-name retrieveIdList \
  --region us-east-1 \
  response.json
```

### 5. Monitor for 24 Hours

```bash
# Run every hour
bash scripts/monitor-production.sh 60

# Check CloudWatch dashboard
# Monitor alarms
# Review X-Ray traces
```

### 6. Rollback (if needed)

```bash
# If issues detected
bash scripts/rollback-production.sh $ROLLBACK_FILE

# Validate rollback
bash scripts/validate-production.sh
bash scripts/monitor-production.sh 15
```

## Monitoring Checklist

During the 24-hour monitoring period, check:

### Lambda Functions
- [ ] Error rate < 5%
- [ ] No throttling
- [ ] Duration within expected range
- [ ] Concurrent executions normal
- [ ] Memory usage acceptable

### API Gateway
- [ ] 5xx error rate < 1%
- [ ] 4xx error rate acceptable
- [ ] p95 latency < 2 seconds
- [ ] Request count as expected
- [ ] Cache hit rate (if applicable)

### Database
- [ ] Connection pool not exhausted
- [ ] Query execution time normal
- [ ] No slow queries
- [ ] No connection errors

### External API
- [ ] Request success rate > 95%
- [ ] Response time acceptable
- [ ] No rate limiting issues
- [ ] Circuit breaker not triggered

### CloudWatch
- [ ] No alarms triggered
- [ ] Logs show no critical errors
- [ ] X-Ray traces are healthy
- [ ] Custom metrics being emitted

### Scheduled Jobs
- [ ] ETL pipeline runs on schedule
- [ ] ETL pipeline completes successfully
- [ ] Data is collected correctly
- [ ] No data loss

## Rollback Criteria

Consider rollback if:

- Lambda error rate > 10% for > 1 hour
- API Gateway 5xx error rate > 5%
- API p95 latency > 5 seconds consistently
- Critical CloudWatch alarms triggered
- Data loss or corruption detected
- ETL pipeline fails multiple times
- Database connection pool exhausted
- External API integration broken

## Success Criteria

Deployment is successful when:

- All validations pass
- Error rate < 5% for 24 hours
- API p95 latency < 2 seconds
- No CloudWatch alarms triggered
- ETL pipeline runs successfully on schedule
- No data loss or corruption
- X-Ray traces show healthy execution
- Team confirms system is stable

## Troubleshooting

### Deployment Fails

1. Check AWS credentials
2. Verify configuration file
3. Check staging health
4. Review error messages
5. Check AWS service quotas

### Validation Fails

1. Identify failed checks
2. Review CloudFormation stacks
3. Check Lambda function configuration
4. Verify IAM permissions
5. Check Secrets Manager

### High Error Rate

1. Check CloudWatch logs
2. Review X-Ray traces
3. Verify database connectivity
4. Check External API availability
5. Consider rollback

### API Latency Issues

1. Check database query performance
2. Review X-Ray traces
3. Check connection pool utilization
4. Verify network connectivity
5. Consider increasing Lambda resources

## Configuration

### Environment Variables

Set in `config/deployment-config.sh`:

```bash
export ENVIRONMENT="production"
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
export ALLOWED_CORS_ORIGINS="https://example.com"
export ALARM_EMAIL="devops@example.com"
export ETL_SCHEDULE_EXPRESSION="cron(0 2 * * ? *)"
export RETENTION_SCHEDULE_EXPRESSION="cron(0 3 1 * ? *)"
```

### AWS Permissions Required

The deployment user/role needs:

- Lambda: Full access
- CloudFormation: Full access
- API Gateway: Full access
- Step Functions: Full access
- EventBridge Scheduler: Full access
- CloudWatch: Full access
- X-Ray: Write access
- Secrets Manager: Read access
- IAM: Limited (for role creation)

## Best Practices

1. **Always validate staging first**
   - Run staging validation before production deployment
   - Ensure staging has been stable for 48+ hours

2. **Deploy during low-traffic periods**
   - Schedule deployments during maintenance windows
   - Notify users of potential disruption

3. **Monitor continuously**
   - Run monitoring script every hour for 24 hours
   - Set up alerts for critical metrics

4. **Keep rollback ready**
   - Save rollback file location
   - Test rollback procedure in staging
   - Keep previous versions available

5. **Document everything**
   - Record deployment date and time
   - Note any issues encountered
   - Update runbooks as needed

6. **Communicate with team**
   - Notify team before deployment
   - Share deployment status
   - Coordinate monitoring responsibilities

## Support

For issues or questions:

- Check [Production Deployment Guide](../PRODUCTION_DEPLOYMENT_GUIDE.md)
- Review [Troubleshooting Guide](../guides/TROUBLESHOOTING.md)
- Contact DevOps team: devops@example.com
- Use PagerDuty for urgent issues

## Additional Resources

- [Staging Validation Guide](../STAGING_VALIDATION_GUIDE.md)
- [CloudWatch Alarms Documentation](../../infrastructure/CLOUDWATCH_ALARMS_README.md)
- [API Documentation](../../infrastructure/API_DOCUMENTATION_README.md)
- [Step Functions Documentation](../../infrastructure/STEP_FUNCTIONS_README.md)
- [Rollback Procedures](../guides/ROLLBACK_PROCEDURES.md)
