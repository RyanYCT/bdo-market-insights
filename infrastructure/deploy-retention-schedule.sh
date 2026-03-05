#!/bin/bash

# Deploy EventBridge Scheduler and IAM permissions for BDO Market Insights Data Retention Lambda
# Usage: ./deploy-retention-schedule.sh <environment> <function-arn>
# Example: ./deploy-retention-schedule.sh dev arn:aws:lambda:us-east-1:123456789012:function:retainData

set -e

# Check arguments
if [ $# -lt 2 ]; then
    echo "Usage: $0 <environment> <function-arn>"
    echo "Example: $0 dev arn:aws:lambda:us-east-1:123456789012:function:retainData"
    exit 1
fi

ENVIRONMENT=$1
FUNCTION_ARN=$2
STACK_NAME="bdo-retention-schedule-${ENVIRONMENT}"

# Optional parameters with defaults
FUNCTION_NAME=${3:-retainData}
ARCHIVE_BUCKET=${4:-bdo-market-insights-archive}
SCHEDULE_EXPRESSION=${5:-'cron(0 2 1 * ? *)'}  # 2 AM UTC on 1st of month
RETENTION_DETAILED_DAYS=${6:-90}
RETENTION_SUMMARY_DAYS=${7:-730}

echo "=========================================="
echo "Deploying BDO Retention Schedule"
echo "=========================================="
echo "Environment: ${ENVIRONMENT}"
echo "Stack Name: ${STACK_NAME}"
echo "Function ARN: ${FUNCTION_ARN}"
echo "Function Name: ${FUNCTION_NAME}"
echo "Archive Bucket: ${ARCHIVE_BUCKET}-${ENVIRONMENT}"
echo "Schedule: ${SCHEDULE_EXPRESSION}"
echo "Retention Detailed Days: ${RETENTION_DETAILED_DAYS}"
echo "Retention Summary Days: ${RETENTION_SUMMARY_DAYS}"
echo "=========================================="

# Deploy CloudFormation stack
aws cloudformation deploy \
  --template-file retention-schedule-template.yaml \
  --stack-name "${STACK_NAME}" \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    RetainDataFunctionName="${FUNCTION_NAME}" \
    RetainDataFunctionArn="${FUNCTION_ARN}" \
    ArchiveBucketName="${ARCHIVE_BUCKET}" \
    ScheduleExpression="${SCHEDULE_EXPRESSION}" \
    RetentionDetailedDays="${RETENTION_DETAILED_DAYS}" \
    RetentionSummaryDays="${RETENTION_SUMMARY_DAYS}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags \
    Environment="${ENVIRONMENT}" \
    Application=BDO-Market-Insights \
    ManagedBy=CloudFormation

# Check deployment status
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "Deployment successful!"
    echo "=========================================="
    
    # Get outputs
    echo ""
    echo "Stack Outputs:"
    aws cloudformation describe-stacks \
      --stack-name "${STACK_NAME}" \
      --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
      --output table
    
    echo ""
    echo "Archive Bucket:"
    BUCKET_NAME=$(aws cloudformation describe-stacks \
      --stack-name "${STACK_NAME}" \
      --query 'Stacks[0].Outputs[?OutputKey==`ArchiveBucketName`].OutputValue' \
      --output text)
    echo "  ${BUCKET_NAME}"
    
    echo ""
    echo "EventBridge Schedule:"
    SCHEDULE_NAME=$(aws cloudformation describe-stacks \
      --stack-name "${STACK_NAME}" \
      --query 'Stacks[0].Outputs[?OutputKey==`RetentionScheduleName`].OutputValue' \
      --output text)
    echo "  ${SCHEDULE_NAME}"
    
    echo ""
    echo "Next Steps:"
    echo "1. Verify the Lambda function has the correct IAM role attached"
    echo "2. Update Lambda environment variables:"
    echo "   - ARCHIVE_BUCKET_NAME=${BUCKET_NAME}"
    echo "   - RETENTION_DETAILED_DAYS=${RETENTION_DETAILED_DAYS}"
    echo "   - RETENTION_SUMMARY_DAYS=${RETENTION_SUMMARY_DAYS}"
    echo "3. Test the schedule by invoking the Lambda manually"
    echo "4. Monitor CloudWatch alarms for retention failures"
    
else
    echo ""
    echo "=========================================="
    echo "Deployment failed!"
    echo "=========================================="
    exit 1
fi

