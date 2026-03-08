# BDO Market Insights - Data Retention Schedule Infrastructure

This directory contains the infrastructure as code for scheduling the BDO Market Insights data retention Lambda function.

## Overview

The data retention infrastructure automates the lifecycle management of market data through:

- **Monthly EventBridge Scheduler**: Triggers the retainData Lambda on the 1st of each month at 2 AM UTC
- **S3 Glacier Archive Bucket**: Stores archived summary data with automatic Glacier transition
- **IAM Roles and Permissions**: Grants Lambda access to S3 Glacier and Secrets Manager
- **CloudWatch Alarms**: Monitors retention process health and failures

## Architecture

```
EventBridge Scheduler (Monthly)
    ↓
retainData Lambda Function
    ↓
┌─────────────────────────────────────────┐
│ 1. Aggregate old detailed records       │
│    (older than 90 days)                 │
│    → Create daily summaries             │
│    → Delete aggregated records          │
│                                         │
│ 2. Archive old summaries                │
│    (older than 2 years)                 │
│    → Export to JSON                     │
│    → Upload to S3 Glacier               │
│    → Delete archived summaries          │
└─────────────────────────────────────────┘
    ↓
PostgreSQL (RDS) + S3 Glacier
```

## Components

### EventBridge Scheduler

- **Schedule**: `cron(0 2 1 * ? *)` - Runs at 2 AM UTC on the 1st of every month
- **Timezone**: UTC
- **Retry Policy**: 2 retry attempts with 1-hour maximum event age
- **Input**: Passes environment and retention configuration to Lambda

### S3 Archive Bucket

- **Naming**: `bdo-market-insights-archive-{environment}`
- **Encryption**: AES256 server-side encryption
- **Versioning**: Enabled for data protection
- **Lifecycle Policy**:
  - Immediate transition to Glacier for `archives/` prefix
  - Delete old versions after 90 days
- **Access**: Private (all public access blocked)

### IAM Roles

**RetainDataLambdaRole**:
- S3 Glacier read/write access to archive bucket
- Secrets Manager access for database credentials
- CloudWatch Logs and Metrics access
- X-Ray tracing access
- VPC access for database connectivity

**EventBridgeSchedulerRole**:
- Lambda invoke permission for retainData function

### CloudWatch Alarms

1. **RetentionLambdaErrorAlarm**: Triggers when Lambda function fails
2. **RetentionLambdaDurationAlarm**: Triggers when execution exceeds 10 minutes
3. **RetentionFailureMetricAlarm**: Triggers on custom retention failure metric

## Deployment

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. retainData Lambda function deployed
3. Database credentials stored in Secrets Manager
4. VPC configuration for Lambda (if database is in VPC)

### Deploy using Shell Script (Linux/Mac)

```bash
# Make script executable
chmod +x deploy-retention-schedule.sh

# Deploy to dev environment
./deploy-retention-schedule.sh dev arn:aws:lambda:us-east-1:123456789012:function:retainData

# Deploy to staging with custom parameters
./deploy-retention-schedule.sh staging \
  arn:aws:lambda:us-east-1:123456789012:function:retainData \
  retainData \
  bdo-market-insights-archive \
  'cron(0 3 1 * ? *)' \
  90 \
  730

# Deploy to production
./deploy-retention-schedule.sh prod arn:aws:lambda:us-east-1:123456789012:function:retainData
```

### Deploy using AWS CLI

```bash
# Deploy to dev environment
aws cloudformation deploy \
  --template-file retention-schedule-template.yaml \
  --stack-name bdo-retention-schedule-dev \
  --parameter-overrides \
    Environment=dev \
    RetainDataFunctionName=retainData \
    RetainDataFunctionArn=arn:aws:lambda:us-east-1:123456789012:function:retainData \
    ArchiveBucketName=bdo-market-insights-archive \
    ScheduleExpression='cron(0 2 1 * ? *)' \
    RetentionDetailedDays=90 \
    RetentionSummaryDays=730 \
  --capabilities CAPABILITY_NAMED_IAM
```

## Configuration

### Schedule Expression

The schedule uses EventBridge cron expressions:

```
cron(minute hour day-of-month month day-of-week year)
```

**Examples**:
- `cron(0 2 1 * ? *)` - 2 AM UTC on 1st of every month (default)
- `cron(0 3 15 * ? *)` - 3 AM UTC on 15th of every month
- `cron(0 0 * * ? *)` - Midnight UTC every day (for testing)
- `rate(30 days)` - Every 30 days

### Retention Periods

**RetentionDetailedDays** (default: 90):
- Days to keep detailed MarketData records
- After this period, records are aggregated into daily summaries
- Minimum: 1 day, Maximum: 365 days

**RetentionSummaryDays** (default: 730 = 2 years):
- Days to keep summary data
- After this period, summaries are archived to S3 Glacier
- Minimum: 1 day, Maximum: 3650 days (10 years)

### Lambda Environment Variables

After deployment, update the Lambda function with these environment variables:

```bash
# Get bucket name from stack outputs
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name bdo-retention-schedule-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ArchiveBucketName`].OutputValue' \
  --output text)

# Update Lambda environment variables
aws lambda update-function-configuration \
  --function-name retainData \
  --environment Variables="{
    ARCHIVE_BUCKET_NAME=${BUCKET_NAME},
    RETENTION_DETAILED_DAYS=90,
    RETENTION_SUMMARY_DAYS=730,
    DB_SECRET_NAME=bdo-db-credentials,
    DB_POOL_SIZE=5
  }"
```

## Testing

### Manual Invocation

Test the retention Lambda manually before relying on the schedule:

```bash
# Invoke Lambda with test event
aws lambda invoke \
  --function-name retainData \
  --payload '{
    "source": "manual-test",
    "environment": "dev",
    "retention_config": {
      "retention_detailed_days": 90,
      "retention_summary_days": 730
    }
  }' \
  response.json

# View response
cat response.json
```

### Verify Schedule

```bash
# Get schedule details
aws scheduler get-schedule \
  --name bdo-retention-schedule-dev

# List all schedules
aws scheduler list-schedules \
  --name-prefix bdo-retention
```

### Check S3 Archives

```bash
# List archived files
aws s3 ls s3://bdo-market-insights-archive-dev/archives/ --recursive

# Get archive metadata
aws s3api head-object \
  --bucket bdo-market-insights-archive-dev \
  --key archives/market_data_summaries_20240115_020000.json
```

### Monitor Execution

```bash
# View Lambda logs
aws logs tail /aws/lambda/retainData --follow

# View CloudWatch metrics
aws cloudwatch get-metric-statistics \
  --namespace BDOMarketInsights/Retention \
  --metric-name RetentionRecordsAggregated \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-31T23:59:59Z \
  --period 86400 \
  --statistics Sum

# Check alarm status
aws cloudwatch describe-alarms \
  --alarm-names bdo-retainData-error-dev
```

## Monitoring

### CloudWatch Metrics

The retention Lambda emits custom metrics to `BDOMarketInsights/Retention`:

- **RetentionRecordsAggregated**: Number of detailed records aggregated
- **RetentionRecordsDeleted**: Number of detailed records deleted
- **RetentionSummariesArchived**: Number of summaries archived to S3
- **RetentionFailure**: Count of retention process failures

### CloudWatch Logs

Logs are written to `/aws/lambda/retainData` with structured JSON format:

```json
{
  "timestamp": "2024-01-15T02:00:00Z",
  "level": "INFO",
  "function_name": "retainData",
  "correlation_id": "uuid-v4",
  "message": "Data retention process completed successfully",
  "detailed_records_aggregated": 15000,
  "detailed_records_deleted": 15000,
  "summaries_archived": 500,
  "summaries_deleted": 500
}
```

### Alarms

Configure SNS notifications for alarms:

```bash
# Subscribe email to alarm topic
aws sns subscribe \
  --topic-arn arn:aws:sns:us-east-1:123456789012:bdo-alarm-notifications-dev \
  --protocol email \
  --notification-endpoint your-email@example.com
```

## Troubleshooting

### Common Issues

1. **Lambda Timeout**
   - Increase Lambda timeout (default: 15 minutes, max: 15 minutes)
   - Consider processing data in smaller batches
   - Check database query performance

2. **S3 Access Denied**
   - Verify IAM role has S3 permissions
   - Check bucket policy doesn't block Lambda
   - Ensure bucket name matches environment

3. **Database Connection Failures**
   - Verify Lambda is in correct VPC/subnets
   - Check security group allows database access
   - Verify Secrets Manager credentials are correct

4. **Schedule Not Triggering**
   - Verify schedule is enabled
   - Check EventBridge Scheduler role has Lambda invoke permission
   - Review CloudWatch Events for failed invocations

### Viewing Logs

```bash
# View recent Lambda executions
aws lambda list-functions \
  --query 'Functions[?FunctionName==`retainData`].[FunctionName,LastModified]'

# Get Lambda configuration
aws lambda get-function-configuration \
  --function-name retainData

# View schedule execution history
aws scheduler list-schedule-executions \
  --schedule-name bdo-retention-schedule-dev \
  --max-results 10
```

### Disable Schedule

Temporarily disable the schedule without deleting it:

```bash
# Disable schedule
aws scheduler update-schedule \
  --name bdo-retention-schedule-dev \
  --state DISABLED \
  --schedule-expression 'cron(0 2 1 * ? *)' \
  --flexible-time-window Mode=OFF \
  --target '{
    "Arn": "arn:aws:lambda:us-east-1:123456789012:function:retainData",
    "RoleArn": "arn:aws:iam::123456789012:role/bdo-eventbridge-scheduler-role-dev"
  }'

# Re-enable schedule
aws scheduler update-schedule \
  --name bdo-retention-schedule-dev \
  --state ENABLED \
  --schedule-expression 'cron(0 2 1 * ? *)' \
  --flexible-time-window Mode=OFF \
  --target '{
    "Arn": "arn:aws:lambda:us-east-1:123456789012:function:retainData",
    "RoleArn": "arn:aws:iam::123456789012:role/bdo-eventbridge-scheduler-role-dev"
  }'
```

## Cost Optimization

### S3 Glacier Costs

- **Storage**: ~$0.004 per GB/month (Glacier)
- **Retrieval**: $0.01 per GB + $0.05 per 1,000 requests (standard retrieval)
- **Early Deletion**: Charged for minimum 90-day storage if deleted early

### Lambda Costs

- **Invocations**: 1 per month (minimal cost)
- **Duration**: Depends on data volume (typically < 5 minutes)
- **Memory**: Configure based on actual usage (recommend 1024 MB)

### Database Costs

- **Storage**: Reduced by aggregating old data
- **I/O**: Monthly batch operations have minimal impact

## Security Considerations

1. **IAM Roles**: Follow principle of least privilege
2. **S3 Encryption**: AES256 encryption at rest
3. **Secrets Manager**: Database credentials never in code
4. **VPC**: Lambda runs in VPC for database access
5. **Versioning**: S3 versioning protects against accidental deletion
6. **Access Logs**: Enable S3 access logging for audit trail

## Rollback

To rollback or delete the retention schedule:

```bash
# Delete CloudFormation stack
aws cloudformation delete-stack \
  --stack-name bdo-retention-schedule-dev

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name bdo-retention-schedule-dev
```

**Warning**: This will delete:
- EventBridge schedule
- IAM roles
- CloudWatch alarms
- S3 bucket (if empty)

Archived data in S3 will be retained unless explicitly deleted.

## Best Practices

1. **Test First**: Always test in dev environment before production
2. **Monitor Closely**: Watch first few executions for issues
3. **Backup Strategy**: Keep database backups before first retention run
4. **Gradual Rollout**: Start with longer retention periods, then reduce
5. **Document Changes**: Track retention policy changes in version control
6. **Review Regularly**: Periodically review archived data and retention periods
7. **Cost Monitoring**: Set up billing alerts for S3 Glacier costs

## Related Documentation

- [CloudWatch Alarms README](CLOUDWATCH_ALARMS_README.md)
- [API Gateway README](README.md)
- [Step Functions README](STEP_FUNCTIONS_README.md)
- [Deployment Checklist](DEPLOYMENT_CHECKLIST.md)

