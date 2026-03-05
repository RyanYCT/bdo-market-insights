# BDO Market Insights - CloudWatch Alarms

This document describes the CloudWatch alarms configured for monitoring the BDO Market Insights system.

## Overview

The CloudWatch alarms provide comprehensive monitoring across all system components:

- **Lambda Error Rates**: Monitors all 6 Lambda functions for error rates exceeding 5%
- **Database Connection Pool**: Monitors connection pool utilization and failures
- **External API Health**: Monitors External API failures, rate limits, and latency
- **ETL Pipeline Health**: Monitors ETL pipeline execution success and duration
- **Query Pipeline Performance**: Monitors query latency and throughput
- **Lambda Throttling**: Monitors Lambda function throttling and concurrent execution limits

All alarms send notifications to an SNS topic that can be configured with email, SMS, or other notification endpoints.

## Architecture

```
CloudWatch Metrics (from Lambda, Custom Metrics)
    ↓
CloudWatch Alarms (threshold-based monitoring)
    ↓
SNS Topic (alarm notifications)
    ↓
Email / SMS / Lambda / Other Endpoints
```

## Deployment

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. Lambda functions deployed with correct names
3. Email address for alarm notifications (optional)

### Deploy using AWS CLI

```bash
# Deploy to dev environment with email notifications
aws cloudformation deploy \
  --template-file cloudwatch-alarms-template.yaml \
  --stack-name bdo-cloudwatch-alarms-dev \
  --parameter-overrides \
    Environment=dev \
    AlarmEmailAddress="devops@example.com" \
    RetrieveIdListFunctionName="retrieveIdList-dev" \
    FetchDataFunctionName="fetchData-dev" \
    CleanDataFunctionName="cleanData-dev" \
    StoreDataFunctionName="storeData-dev" \
    QueryDataFunctionName="queryData-dev" \
    AnalyzeDataFunctionName="analyzeData-dev" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to staging environment
aws cloudformation deploy \
  --template-file cloudwatch-alarms-template.yaml \
  --stack-name bdo-cloudwatch-alarms-staging \
  --parameter-overrides \
    Environment=staging \
    AlarmEmailAddress="devops@example.com" \
    RetrieveIdListFunctionName="retrieveIdList-staging" \
    FetchDataFunctionName="fetchData-staging" \
    CleanDataFunctionName="cleanData-staging" \
    StoreDataFunctionName="storeData-staging" \
    QueryDataFunctionName="queryData-staging" \
    AnalyzeDataFunctionName="analyzeData-staging" \
  --capabilities CAPABILITY_NAMED_IAM

# Deploy to production environment
aws cloudformation deploy \
  --template-file cloudwatch-alarms-template.yaml \
  --stack-name bdo-cloudwatch-alarms-prod \
  --parameter-overrides \
    Environment=prod \
    AlarmEmailAddress="alerts@example.com" \
    RetrieveIdListFunctionName="retrieveIdList-prod" \
    FetchDataFunctionName="fetchData-prod" \
    CleanDataFunctionName="cleanData-prod" \
    StoreDataFunctionName="storeData-prod" \
    QueryDataFunctionName="queryData-prod" \
    AnalyzeDataFunctionName="analyzeData-prod" \
  --capabilities CAPABILITY_NAMED_IAM
```

### Confirm SNS Subscription

After deployment, if you provided an email address, you'll receive a subscription confirmation email. Click the confirmation link to start receiving alarm notifications.

## Alarm Details

### Lambda Error Rate Alarms

**Purpose**: Monitor Lambda function errors to detect code issues, configuration problems, or service failures.

**Alarms**:
- `bdo-retrieveIdList-error-rate-{env}`: retrieveIdList Lambda errors > 5 in 10 minutes
- `bdo-fetchData-error-rate-{env}`: fetchData Lambda errors > 5 in 10 minutes
- `bdo-cleanData-error-rate-{env}`: cleanData Lambda errors > 5 in 10 minutes
- `bdo-storeData-error-rate-{env}`: storeData Lambda errors > 5 in 10 minutes
- `bdo-queryData-error-rate-{env}`: queryData Lambda errors > 5 in 10 minutes
- `bdo-analyzeData-error-rate-{env}`: analyzeData Lambda errors > 5 in 10 minutes

**Threshold**: 5 errors in 10 minutes (2 consecutive periods)

**Actions**:
- Check CloudWatch Logs for error details
- Review X-Ray traces for failed requests
- Verify Lambda function configuration and permissions
- Check for recent code deployments

### Database Connection Pool Alarms

**Purpose**: Monitor database connection health to prevent connection exhaustion and detect database issues.

**Alarms**:
- `bdo-database-connection-pool-exhaustion-{env}`: Pool utilization > 90%
- `bdo-database-connection-failures-{env}`: Connection failures > 5 in 10 minutes
- `bdo-slow-queries-{env}`: Slow queries (>1s) > 10 in 10 minutes

**Thresholds**:
- Pool utilization: 90% (2 consecutive periods)
- Connection failures: 5 in 10 minutes
- Slow queries: 10 in 10 minutes (2 consecutive periods)

**Actions**:
- Check RDS instance CPU and memory utilization
- Review database connection pool configuration
- Analyze slow query logs for optimization opportunities
- Consider increasing RDS instance size or connection limits
- Review Lambda concurrency settings

### External API Failure Alarms

**Purpose**: Monitor External API health to detect service degradation or outages.

**Alarms**:
- `bdo-external-api-failure-rate-{env}`: API failures > 10 in 10 minutes
- `bdo-external-api-rate-limit-{env}`: Rate limit hits > 20 in 10 minutes
- `bdo-external-api-latency-{env}`: p95 latency > 5 seconds
- `bdo-circuit-breaker-open-{env}`: Circuit breaker opened

**Thresholds**:
- Failure rate: 10 failures in 10 minutes (2 consecutive periods)
- Rate limit hits: 20 in 10 minutes (2 consecutive periods)
- Latency: p95 > 5000ms (2 consecutive periods)
- Circuit breaker: Opened (immediate)

**Actions**:
- Check External API status page
- Review rate limiting configuration
- Verify API credentials are valid
- Consider implementing request batching or throttling
- Check circuit breaker logs for failure patterns

### ETL Pipeline Health Alarms

**Purpose**: Monitor ETL pipeline execution to ensure data collection runs successfully.

**Alarms**:
- `bdo-etl-pipeline-failure-{env}`: Pipeline execution failed
- `bdo-etl-pipeline-duration-{env}`: Pipeline duration > 10 minutes

**Thresholds**:
- Failures: 1 or more failures
- Duration: > 600,000ms (10 minutes)

**Actions**:
- Check Step Functions execution history
- Review Lambda function logs for all ETL functions
- Verify DynamoDB and External API availability
- Check for data volume spikes
- Review EventBridge Scheduler configuration

### Query Pipeline Performance Alarms

**Purpose**: Monitor query API performance to ensure acceptable response times.

**Alarms**:
- `bdo-query-pipeline-latency-{env}`: p95 latency > 2 seconds

**Threshold**: p95 > 2000ms (2 consecutive periods)

**Actions**:
- Review database query performance
- Check for missing indexes
- Analyze X-Ray traces for bottlenecks
- Consider implementing caching
- Review Lambda memory allocation

### Lambda Throttling Alarms

**Purpose**: Monitor Lambda throttling to detect concurrency limit issues.

**Alarms**:
- `bdo-lambda-throttling-{env}`: Throttles > 10 in 10 minutes
- `bdo-lambda-concurrent-execution-{env}`: Concurrent executions > 800

**Thresholds**:
- Throttles: 10 in 10 minutes (2 consecutive periods)
- Concurrent executions: 800 (approaching 1000 account limit)

**Actions**:
- Review Lambda reserved concurrency settings
- Consider increasing account concurrency limits
- Implement request queuing or throttling
- Analyze traffic patterns for optimization

## Custom Metrics

The system emits custom CloudWatch metrics in the `BDO/MarketInsights` namespace:

### Database Metrics
- `DatabaseConnectionPoolUtilization`: Percentage of pool connections in use
- `DatabaseConnectionFailures`: Count of failed connection attempts
- `SlowQueries`: Count of queries exceeding 1 second

### External API Metrics
- `ExternalAPIFailures`: Count of failed API calls
- `ExternalAPIRateLimitHits`: Count of rate limit responses
- `ExternalAPILatency`: Response time in milliseconds
- `CircuitBreakerOpen`: Binary indicator (1 = open, 0 = closed)

### Pipeline Metrics
- `ETLPipelineFailures`: Count of ETL pipeline failures
- `ETLPipelineDuration`: Pipeline execution time in milliseconds
- `QueryPipelineLatency`: Query response time in milliseconds

All custom metrics include an `Environment` dimension for filtering by environment.

## Viewing Alarms

### AWS Console

1. Navigate to CloudWatch → Alarms
2. Filter by alarm name prefix: `bdo-`
3. View alarm state (OK, ALARM, INSUFFICIENT_DATA)
4. Click alarm name for details and history

### AWS CLI

```bash
# List all alarms for an environment
aws cloudwatch describe-alarms \
  --alarm-name-prefix "bdo-" \
  --query 'MetricAlarms[?contains(AlarmName, `dev`)].[AlarmName,StateValue]' \
  --output table

# Get alarm history
aws cloudwatch describe-alarm-history \
  --alarm-name "bdo-fetchData-error-rate-dev" \
  --max-records 10

# Get alarm details
aws cloudwatch describe-alarms \
  --alarm-names "bdo-external-api-failure-rate-dev"
```

## Testing Alarms

### Test Lambda Error Alarm

```bash
# Invoke Lambda with invalid input to trigger error
aws lambda invoke \
  --function-name fetchData-dev \
  --payload '{"invalid": "input"}' \
  /dev/null

# Repeat 6 times to exceed threshold
for i in {1..6}; do
  aws lambda invoke \
    --function-name fetchData-dev \
    --payload '{"invalid": "input"}' \
    /dev/null
  sleep 1
done

# Wait 5-10 minutes for alarm to trigger
```

### Test Custom Metric Alarm

```bash
# Publish test metric
aws cloudwatch put-metric-data \
  --namespace BDO/MarketInsights \
  --metric-name DatabaseConnectionPoolUtilization \
  --value 95 \
  --dimensions Environment=dev

# Wait 5-10 minutes for alarm to trigger
```

## Managing Notifications

### Add Email Subscriber

```bash
# Get SNS topic ARN
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name bdo-cloudwatch-alarms-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicArn`].OutputValue' \
  --output text)

# Subscribe email
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint "newuser@example.com"

# Confirm subscription via email
```

### Add SMS Subscriber

```bash
# Subscribe SMS
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol sms \
  --notification-endpoint "+1234567890"
```

### Add Lambda Subscriber (for custom actions)

```bash
# Subscribe Lambda function
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol lambda \
  --notification-endpoint "arn:aws:lambda:us-east-1:123456789012:function:alarm-handler"

# Grant SNS permission to invoke Lambda
aws lambda add-permission \
  --function-name alarm-handler \
  --statement-id sns-invoke \
  --action lambda:InvokeFunction \
  --principal sns.amazonaws.com \
  --source-arn $TOPIC_ARN
```

### Remove Subscriber

```bash
# List subscriptions
aws sns list-subscriptions-by-topic \
  --topic-arn $TOPIC_ARN

# Unsubscribe
aws sns unsubscribe \
  --subscription-arn "arn:aws:sns:us-east-1:123456789012:bdo-alarm-notifications-dev:..."
```

## Alarm Tuning

### Adjusting Thresholds

Edit the CloudFormation template and update the `Threshold` property for the alarm:

```yaml
ExternalAPIFailureRateAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    # ... other properties ...
    Threshold: 20  # Changed from 10 to 20
```

Redeploy the stack:

```bash
aws cloudformation deploy \
  --template-file cloudwatch-alarms-template.yaml \
  --stack-name bdo-cloudwatch-alarms-dev \
  --parameter-overrides Environment=dev \
  --capabilities CAPABILITY_NAMED_IAM
```

### Adjusting Evaluation Periods

Change the `EvaluationPeriods` property to require more consecutive breaches:

```yaml
ExternalAPIFailureRateAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    # ... other properties ...
    EvaluationPeriods: 3  # Changed from 2 to 3
```

### Disabling Alarms

Temporarily disable an alarm without deleting it:

```bash
aws cloudwatch disable-alarm-actions \
  --alarm-names "bdo-external-api-failure-rate-dev"

# Re-enable
aws cloudwatch enable-alarm-actions \
  --alarm-names "bdo-external-api-failure-rate-dev"
```

## Troubleshooting

### Alarm Not Triggering

1. **Check metric data**:
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/Lambda \
     --metric-name Errors \
     --dimensions Name=FunctionName,Value=fetchData-dev \
     --start-time 2024-01-01T00:00:00Z \
     --end-time 2024-01-01T01:00:00Z \
     --period 300 \
     --statistics Sum
   ```

2. **Verify alarm configuration**:
   ```bash
   aws cloudwatch describe-alarms \
     --alarm-names "bdo-fetchData-error-rate-dev"
   ```

3. **Check alarm state reason**:
   - View in AWS Console → CloudWatch → Alarms
   - Look for "State reason" message

### False Positive Alarms

1. **Review alarm threshold**: May be too sensitive
2. **Check evaluation periods**: May need more consecutive breaches
3. **Analyze metric patterns**: Look for normal spikes vs. actual issues
4. **Consider using anomaly detection**: CloudWatch Anomaly Detection can adapt to patterns

### Missing Alarm Notifications

1. **Verify SNS subscription**:
   ```bash
   aws sns list-subscriptions-by-topic \
     --topic-arn $TOPIC_ARN
   ```

2. **Check subscription status**: Should be "Confirmed"
3. **Check spam folder**: Email notifications may be filtered
4. **Verify SNS topic policy**: Ensure CloudWatch can publish

## Best Practices

1. **Start Conservative**: Begin with higher thresholds and adjust based on actual patterns
2. **Use Composite Alarms**: Combine multiple alarms to reduce noise
3. **Document Runbooks**: Create runbooks for each alarm type
4. **Regular Review**: Review alarm effectiveness monthly
5. **Test Regularly**: Test alarms quarterly to ensure they work
6. **Use Tags**: Tag alarms for easier management and filtering
7. **Monitor Alarm State**: Set up alarms on alarm state changes
8. **Implement Auto-Remediation**: Use Lambda functions to automatically respond to certain alarms

## Integration with Incident Management

### PagerDuty Integration

```bash
# Create PagerDuty integration endpoint
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol https \
  --notification-endpoint "https://events.pagerduty.com/integration/..."
```

### Slack Integration

```bash
# Create Slack webhook Lambda
# Subscribe Lambda to SNS topic
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol lambda \
  --notification-endpoint "arn:aws:lambda:us-east-1:123456789012:function:slack-notifier"
```

### Jira Integration

Create a Lambda function that creates Jira tickets on alarm and subscribe it to the SNS topic.

## Cost Optimization

CloudWatch alarm costs:
- **Standard Alarms**: $0.10 per alarm per month
- **High-Resolution Alarms**: $0.30 per alarm per month
- **Composite Alarms**: $0.50 per alarm per month

Current template creates approximately 20 alarms = $2.00/month per environment.

To reduce costs:
- Combine related alarms using composite alarms
- Use metric math to create derived metrics
- Remove alarms for non-critical metrics in dev/staging

## Clean Up

To delete all alarms:

```bash
aws cloudformation delete-stack \
  --stack-name bdo-cloudwatch-alarms-dev
```

**Warning**: This will delete all alarms and the SNS topic. Alarm history will be lost.

## References

- [CloudWatch Alarms Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)
- [CloudWatch Metrics Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/working_with_metrics.html)
- [SNS Documentation](https://docs.aws.amazon.com/sns/latest/dg/welcome.html)
- [Lambda Metrics](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics.html)
