#!/bin/bash
# Setup IAM Roles for BDO Market Insights
# This script creates the necessary IAM roles that CloudFormation needs
# Run this with an IAM user/role that has IAM admin permissions

set -e

ENVIRONMENT="${ENVIRONMENT:-staging}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

echo "=========================================="
echo "Setting up IAM Roles for BDO Market Insights"
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo "=========================================="

# Get Lambda ARNs
echo "Retrieving Lambda function ARNs..."
QUERY_DATA_ARN=$(aws lambda get-function --function-name queryData --region "$REGION" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")
ANALYZE_DATA_ARN=$(aws lambda get-function --function-name analyzeData --region "$REGION" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")

if [ -z "$QUERY_DATA_ARN" ] || [ -z "$ANALYZE_DATA_ARN" ]; then
    echo "Warning: Lambda functions not found. Creating role with placeholder ARNs."
    QUERY_DATA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:queryData"
    ANALYZE_DATA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:analyzeData"
fi

# Create Step Functions Execution Role
ROLE_NAME="bdo-stepfunctions-execution-role-${ENVIRONMENT}"
echo ""
echo "Creating IAM role: $ROLE_NAME"

# Check if role exists
if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    echo "Role already exists. Updating policies..."
else
    # Create the role
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "states.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --description "Execution role for BDO Market Insights Step Functions ${ENVIRONMENT}" \
        --region "$REGION"
    
    echo "Role created successfully"
fi

# Attach managed policies
echo "Attaching managed policies..."
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"

aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"

# Create inline policy for Lambda invocation
echo "Creating inline policy for Lambda invocation..."
aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "InvokeLambdaFunctions" \
    --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
            \"Effect\": \"Allow\",
            \"Action\": [\"lambda:InvokeFunction\"],
            \"Resource\": [
                \"${QUERY_DATA_ARN}\",
                \"${ANALYZE_DATA_ARN}\"
            ]
        }]
    }"

echo "Step Functions execution role configured successfully"

# Create EventBridge Scheduler Role
SCHEDULER_ROLE_NAME="EventBridgeSchedulerRole"
echo ""
echo "Creating IAM role: $SCHEDULER_ROLE_NAME"

if aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" > /dev/null 2>&1; then
    echo "Role already exists. Updating policies..."
else
    aws iam create-role \
        --role-name "$SCHEDULER_ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "scheduler.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --description "Execution role for EventBridge Scheduler" \
        --region "$REGION"
    
    echo "Role created successfully"
fi

# Create inline policy for Lambda invocation
echo "Creating inline policy for Lambda invocation..."
aws iam put-role-policy \
    --role-name "$SCHEDULER_ROLE_NAME" \
    --policy-name "InvokeLambdaFunctions" \
    --policy-document "{
        \"Version\": \"2012-10-17\",
        \"Statement\": [{
            \"Effect\": \"Allow\",
            \"Action\": [\"lambda:InvokeFunction\"],
            \"Resource\": [
                \"arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:retrieveIdList\",
                \"arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:retainData\"
            ]
        }]
    }"

echo "EventBridge Scheduler role configured successfully"

echo ""
echo "=========================================="
echo "IAM Roles Setup Complete!"
echo "=========================================="
echo ""
echo "Created/Updated roles:"
echo "  - $ROLE_NAME"
echo "  - $SCHEDULER_ROLE_NAME"
echo ""
echo "You can now run the deployment script."

exit 0
