# CloudWatch Alarms Quick Reference

## Quick Deployment

```bash
# Linux/Mac
cd infrastructure
./deploy-alarms.sh --environment dev --email your-email@example.com

# Windows
cd infrastructure
deploy-alarms.bat --environment dev --email your-email@example.com
```

## Alarm Summary

| Alarm Category | Count | Purpose |
|----------------|-------|---------|
| Lambda Error Rates | 6 | Monitor all Lambda functions for errors > 5% |
| Database Health | 3 | Monitor connection pool, failures, and slow queries |
| External API Health | 4 | Monitor API failures, rate limits, latency, circuit breaker |
| ETL Pipeline | 2 | Monitor pipeline execution success and duration |
| Query Pipeline | 1 | Monitor query response time |
| Lambda Throttling | 2 | Monitor Lambda concurrency and throttling |

**Total Alarms**: 18 per environment

## Critical Alarms (Immediate Action Required)

| Alarm Name | Threshold | Action |
|------------|-----------|--------|
| `bdo-etl-pipeline-failure-{env}` | 1 failure | Check ETL Lambda logs immediately |
| `bdo-database-connection-pool-exhaustion-{env}` | 90% utilization | Scale RDS or reduce Lambda concurrency |
| `bdo-circuit-breaker-open-{env}` | Circuit opened | Check External API status |
| `bdo-lambda-concurrent-execution-{env}` | 800 concurrent | Approaching account limit |

## Warning Alarms (Monitor and Investigate)

| Alarm Name | Threshold | Action |
|------------|-----------|--------|
| Lambda error rates | 5 errors in 10 min | Review logs for error patterns |
| `bdo-external-api-failure-rate-{env}` | 10 failures in 10 min | Check API health |
| `bdo-query-pipeline-latency-{env}` | p95 > 2 seconds | Optimize queries or add caching |
| `bdo-slow-queries-{env}` | 10 queries > 1s | Review and optimize SQL |

## Common Responses

### Lambda Error Rate Alarm

1. Check CloudWatch Logs for the specific Lambda function
2. Look for correlation ID to trace the request
3. Check X-Ray traces for bottlenecks
4. Verify recent deployments
5. Check for configuration changes

### Database Connection Pool Exhaustion

1. Check RDS CPU and memory metrics
2. Review current connection count
3. Check Lambda concurrent executions
4. Consider increasing RDS instance size
5. Review connection pool configuration

### External API Failure

1. Check External API status page
2. Review rate limiting configuration
3. Check circuit breaker logs
4. Verify API credentials
5. Consider implementing backoff strategy

### ETL Pipeline Failure

1. Check Step Functions execution history
2. Review all ETL Lambda logs
3. Verify DynamoDB availability
4. Check External API availability
5. Review EventBridge Scheduler

## Viewing Alarm Status

```bash
# List all alarms for an environment
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --query 'MetricAlarms[?contains(AlarmName, `dev`)].[AlarmName,StateValue]' \
  --output table

# Get specific alarm details
aws cloudwatch describe-alarms \
  --alarm-names "bdo-fetchData-error-rate-dev"

# View alarm history
aws cloudwatch describe-alarm-history \
  --alarm-name "bdo-fetchData-error-rate-dev" \
  --max-records 10
```

## Testing Alarms

```bash
# Test Lambda error alarm (trigger 6 errors)
for i in {1..6}; do
  aws lambda invoke \
    --function-name fetchData-dev \
    --payload '{"invalid": "input"}' \
    /dev/null
  sleep 1
done

# Test custom metric alarm
aws cloudwatch put-metric-data \
  --namespace BDO/MarketInsights \
  --metric-name DatabaseConnectionPoolUtilization \
  --value 95 \
  --dimensions Environment=dev
```

## Disabling Alarms Temporarily

```bash
# Disable specific alarm
aws cloudwatch disable-alarm-actions \
  --alarm-names "bdo-external-api-failure-rate-dev"

# Re-enable
aws cloudwatch enable-alarm-actions \
  --alarm-names "bdo-external-api-failure-rate-dev"

# Disable all alarms for maintenance
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --query 'MetricAlarms[?contains(AlarmName, `dev`)].AlarmName' \
  --output text | xargs aws cloudwatch disable-alarm-actions --alarm-names
```

## Custom Metrics Reference

All custom metrics are in the `BDO/MarketInsights` namespace with `Environment` dimension.

| Metric Name | Unit | Description |
|-------------|------|-------------|
| `DatabaseConnectionPoolUtilization` | Percent | % of pool connections in use |
| `DatabaseConnectionFailures` | Count | Failed connection attempts |
| `SlowQueries` | Count | Queries exceeding 1 second |
| `ExternalAPIFailures` | Count | Failed API calls |
| `ExternalAPIRateLimitHits` | Count | Rate limit responses |
| `ExternalAPILatency` | Milliseconds | API response time |
| `CircuitBreakerOpen` | None | 1 = open, 0 = closed |
| `ETLPipelineFailures` | Count | Pipeline execution failures |
| `ETLPipelineDuration` | Milliseconds | Pipeline execution time |
| `QueryPipelineLatency` | Milliseconds | Query response time |

## SNS Topic Management

```bash
# Get topic ARN
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-cloudwatch-alarms-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicArn`].OutputValue' \
  --output text)

# Add email subscriber
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint "newuser@example.com"

# Add SMS subscriber
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol sms \
  --notification-endpoint "+1234567890"

# List subscribers
aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN

# Remove subscriber
aws sns unsubscribe --subscription-arn "arn:aws:sns:..."
```

## Alarm Costs

- Standard Alarms: $0.10/alarm/month
- 18 alarms per environment = $1.80/month
- 3 environments (dev, staging, prod) = $5.40/month total

## Integration Examples

### PagerDuty

```bash
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol https \
  --notification-endpoint "https://events.pagerduty.com/integration/YOUR_KEY/enqueue"
```

### Slack (via Lambda)

1. Create Lambda function that posts to Slack webhook
2. Subscribe Lambda to SNS topic
3. Grant SNS permission to invoke Lambda

### Email with Custom Formatting

1. Create Lambda function that formats alarm messages
2. Subscribe Lambda to SNS topic
3. Lambda sends formatted email via SES

## Troubleshooting

### Alarm Not Triggering

- Check metric data exists: `aws cloudwatch get-metric-statistics ...`
- Verify alarm configuration: `aws cloudwatch describe-alarms ...`
- Check alarm state reason in console

### False Positives

- Adjust threshold or evaluation periods
- Use anomaly detection for adaptive thresholds
- Review metric patterns for normal behavior

### Missing Notifications

- Verify SNS subscription is confirmed
- Check spam folder for emails
- Verify SNS topic policy allows CloudWatch to publish

## Best Practices

1. ✅ Start with conservative thresholds
2. ✅ Review and adjust based on actual patterns
3. ✅ Document runbooks for each alarm type
4. ✅ Test alarms quarterly
5. ✅ Use composite alarms to reduce noise
6. ✅ Tag alarms for easier management
7. ✅ Implement auto-remediation where possible
8. ✅ Monitor alarm state changes

## Emergency Contacts

| Environment | Contact | Method |
|-------------|---------|--------|
| Production | On-call engineer | PagerDuty |
| Staging | DevOps team | Email + Slack |
| Development | Development team | Email |

## Related Documentation

- [Full Alarms Documentation](CLOUDWATCH_ALARMS_README.md)
- [API Gateway Monitoring](README.md#monitoring)
- [Step Functions Alarms](STEP_FUNCTIONS_README.md)
- [Lambda Metrics](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics.html)
