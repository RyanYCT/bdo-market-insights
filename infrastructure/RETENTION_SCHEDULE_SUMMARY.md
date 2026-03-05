# Data Retention Schedule - Implementation Summary

## Overview

This document summarizes the implementation of Task 23: Schedule retention Lambda, which configures EventBridge Scheduler for monthly execution and sets up IAM permissions for S3 Glacier access.

## Files Created

### 1. CloudFormation Template
**File**: `infrastructure/retention-schedule-template.yaml`

Defines the complete infrastructure for data retention:
- **S3 Archive Bucket**: Encrypted bucket with Glacier lifecycle policy
- **IAM Roles**: 
  - RetainDataLambdaRole: Grants Lambda access to S3, Secrets Manager, CloudWatch, and VPC
  - EventBridgeSchedulerRole: Grants EventBridge permission to invoke Lambda
- **EventBridge Scheduler**: Monthly cron schedule (1st of month at 2 AM UTC)
- **Lambda Permission**: Allows EventBridge to invoke retainData function
- **CloudWatch Alarms**: Monitors retention process health

### 2. Deployment Scripts
**Files**: 
- `infrastructure/deploy-retention-schedule.sh` (Linux/Mac)
- `infrastructure/deploy-retention-schedule.bat` (Windows)

Automated deployment scripts that:
- Accept environment and Lambda ARN as parameters
- Deploy CloudFormation stack with configurable parameters
- Display stack outputs and next steps
- Support custom retention periods and schedule expressions

### 3. Documentation
**File**: `infrastructure/RETENTION_SCHEDULE_README.md`

Comprehensive documentation covering:
- Architecture overview and data flow
- Component descriptions (EventBridge, S3, IAM, Alarms)
- Deployment instructions for all platforms
- Configuration options (schedule, retention periods)
- Testing procedures
- Monitoring and troubleshooting
- Cost optimization
- Security considerations
- Best practices

### 4. Updated Documentation
**Files Updated**:
- `infrastructure/README.md`: Added reference to retention schedule
- `infrastructure/DEPLOYMENT_CHECKLIST.md`: Added retention deployment steps and verification

## Key Features

### EventBridge Scheduler Configuration
- **Schedule**: `cron(0 2 1 * ? *)` - Runs monthly on the 1st at 2 AM UTC
- **Timezone**: UTC
- **Retry Policy**: 2 attempts with 1-hour max event age
- **Input**: Passes environment and retention config to Lambda

### S3 Glacier Integration
- **Bucket Naming**: `bdo-market-insights-archive-{environment}`
- **Encryption**: AES256 server-side encryption
- **Versioning**: Enabled for data protection
- **Lifecycle**: Immediate Glacier transition for archives
- **Access**: Private with all public access blocked

### IAM Permissions
The RetainDataLambdaRole includes:
- S3 read/write access to archive bucket
- Secrets Manager access for database credentials
- CloudWatch Logs and Metrics permissions
- X-Ray tracing permissions
- VPC access for database connectivity

### CloudWatch Alarms
Three alarms monitor the retention process:
1. **RetentionLambdaErrorAlarm**: Lambda function failures
2. **RetentionLambdaDurationAlarm**: Execution time > 10 minutes
3. **RetentionFailureMetricAlarm**: Custom retention failure metric

## Deployment Example

```bash
# Get Lambda ARN
RETAIN_DATA_ARN=$(aws lambda get-function \
  --function-name retainData-dev \
  --query 'Configuration.FunctionArn' \
  --output text)

# Deploy to dev environment
cd infrastructure
./deploy-retention-schedule.sh dev "$RETAIN_DATA_ARN"

# Deploy to production with custom schedule (15th of month at 3 AM)
./deploy-retention-schedule.sh prod \
  "$RETAIN_DATA_ARN" \
  retainData \
  bdo-market-insights-archive \
  'cron(0 3 15 * ? *)' \
  90 \
  730
```

## Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Environment | dev | Environment name (dev/staging/prod) |
| RetainDataFunctionArn | (required) | ARN of retainData Lambda |
| RetainDataFunctionName | retainData | Name of Lambda function |
| ArchiveBucketName | bdo-market-insights-archive | S3 bucket base name |
| ScheduleExpression | cron(0 2 1 * ? *) | EventBridge cron expression |
| RetentionDetailedDays | 90 | Days to keep detailed records |
| RetentionSummaryDays | 730 | Days to keep summaries (2 years) |

## Testing

### Manual Invocation
```bash
aws lambda invoke \
  --function-name retainData-dev \
  --payload '{
    "source": "manual-test",
    "environment": "dev",
    "retention_config": {
      "retention_detailed_days": 90,
      "retention_summary_days": 730
    }
  }' \
  response.json
```

### Verify Schedule
```bash
aws scheduler get-schedule \
  --name bdo-retention-schedule-dev
```

### Check Archives
```bash
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name bdo-retention-schedule-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ArchiveBucketName`].OutputValue' \
  --output text)

aws s3 ls "s3://${BUCKET_NAME}/archives/" --recursive
```

## Monitoring

### CloudWatch Metrics
The retention Lambda emits metrics to `BDOMarketInsights/Retention`:
- RetentionRecordsAggregated
- RetentionRecordsDeleted
- RetentionSummariesArchived
- RetentionFailure

### CloudWatch Logs
Structured JSON logs in `/aws/lambda/retainData`:
```json
{
  "timestamp": "2024-01-15T02:00:00Z",
  "level": "INFO",
  "message": "Data retention process completed successfully",
  "detailed_records_aggregated": 15000,
  "summaries_archived": 500
}
```

## Requirements Satisfied

This implementation satisfies **Requirement 10.5** from the requirements document:

> "THE System SHALL execute retention policies through a scheduled Lambda function triggered monthly"

The EventBridge Scheduler is configured to trigger the retainData Lambda function monthly, with:
- Configurable schedule expression
- Automatic retry on failure
- IAM permissions for S3 Glacier access
- CloudWatch monitoring and alarms
- Comprehensive logging

## Next Steps

After deployment:

1. **Update Lambda Environment Variables**:
   ```bash
   aws lambda update-function-configuration \
     --function-name retainData-dev \
     --environment Variables="{
       ARCHIVE_BUCKET_NAME=${BUCKET_NAME},
       RETENTION_DETAILED_DAYS=90,
       RETENTION_SUMMARY_DAYS=730
     }"
   ```

2. **Test Manual Execution**: Invoke Lambda to verify functionality

3. **Monitor First Scheduled Run**: Watch logs and metrics on the 1st of next month

4. **Configure Alarm Notifications**: Subscribe email to SNS topic for alerts

5. **Review Archived Data**: Verify S3 archives are created correctly

## Security Considerations

- IAM roles follow least privilege principle
- S3 bucket encrypted at rest with AES256
- Database credentials stored in Secrets Manager
- Lambda runs in VPC for database access
- S3 versioning protects against accidental deletion
- All public access to S3 blocked

## Cost Estimate

Monthly costs per environment:
- **EventBridge Scheduler**: $0.00 (1 invocation/month is free tier)
- **Lambda Execution**: ~$0.01 (1 execution/month, ~5 min duration)
- **S3 Glacier Storage**: ~$0.004/GB/month
- **CloudWatch Alarms**: $0.30 (3 alarms × $0.10)
- **Total**: ~$0.31 + storage costs

## Rollback

To remove the retention schedule:
```bash
aws cloudformation delete-stack \
  --stack-name bdo-retention-schedule-dev
```

This deletes the schedule, IAM roles, and alarms. The S3 bucket and archived data are retained.

## Related Documentation

- [Retention Schedule README](RETENTION_SCHEDULE_README.md) - Detailed documentation
- [CloudWatch Alarms README](CLOUDWATCH_ALARMS_README.md) - Monitoring setup
- [Deployment Checklist](DEPLOYMENT_CHECKLIST.md) - Complete deployment guide
- [Requirements Document](../.kiro/specs/bdo-market-insights-rewrite/requirements.md) - Requirement 10.5

