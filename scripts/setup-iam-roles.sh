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

# Create managed CloudWatch Metrics policy
echo ""
echo "=========================================="
echo "Creating Managed CloudWatch Metrics Policy"
echo "=========================================="

MANAGED_POLICY_NAME="BDOMarketInsights-CloudWatchMetrics"
MANAGED_POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${MANAGED_POLICY_NAME}"

# CloudWatch metrics policy document
METRICS_POLICY_DOCUMENT='{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "cloudwatch:PutMetricData",
        "Resource": "*",
        "Condition": {
            "StringEquals": {
                "cloudwatch:namespace": "BDOMarketInsights/ETL"
            }
        }
    }]
}'

# Check if managed policy already exists
if aws iam get-policy --policy-arn "$MANAGED_POLICY_ARN" > /dev/null 2>&1; then
    echo "Managed policy already exists: $MANAGED_POLICY_NAME"
    echo "Policy ARN: $MANAGED_POLICY_ARN"
else
    echo "Creating managed policy: $MANAGED_POLICY_NAME"
    
    # Create temporary policy file
    TEMP_POLICY_FILE=".temp-cloudwatch-policy-$$.json"
    echo "$METRICS_POLICY_DOCUMENT" > "$TEMP_POLICY_FILE"
    
    # Create the managed policy
    aws iam create-policy \
        --policy-name "$MANAGED_POLICY_NAME" \
        --policy-document "file://$TEMP_POLICY_FILE" \
        --description "CloudWatch metrics permissions for BDO Market Insights ETL pipeline"
    
    rm -f "$TEMP_POLICY_FILE"
    
    if [ $? -eq 0 ]; then
        echo "✓ Managed policy created successfully"
    else
        echo "✗ Failed to create managed policy"
        exit 1
    fi
fi

# Attach managed policy to Lambda execution roles
echo ""
echo "=========================================="
echo "Attaching Policy to Lambda Execution Roles"
echo "=========================================="

# List of Lambda functions that need CloudWatch metrics permissions
LAMBDA_FUNCTIONS=(
    "retrieveIdList"
    "fetchData"
    "cleanData"
    "storeData"
    "queryData"
    "analyzeData"
    "retainData"
)

echo ""
echo "Attaching managed policy to Lambda function roles..."

SUCCESS_COUNT=0
FAILED_COUNT=0
SKIPPED_COUNT=0

for FUNCTION_NAME in "${LAMBDA_FUNCTIONS[@]}"; do
    echo ""
    echo "Processing: $FUNCTION_NAME"
    
    # Get the function's role ARN
    ROLE_ARN=$(aws lambda get-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --query 'Role' \
        --output text 2>/dev/null || echo "")
    
    if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" == "None" ]; then
        echo "  Function not found or has no role. Skipping."
        echo "  (This is normal if the function hasn't been deployed yet)"
        ((SKIPPED_COUNT++))
        continue
    fi
    
    # Extract role name from ARN
    ROLE_NAME_LAMBDA=$(echo "$ROLE_ARN" | awk -F'/' '{print $NF}')
    echo "  Role: $ROLE_NAME_LAMBDA"
    
    # Remove inline policy if it exists (migration from inline to managed)
    if aws iam get-role-policy --role-name "$ROLE_NAME_LAMBDA" --policy-name "CloudWatchMetricsPolicy" > /dev/null 2>&1; then
        echo "  Removing old inline policy..."
        aws iam delete-role-policy --role-name "$ROLE_NAME_LAMBDA" --policy-name "CloudWatchMetricsPolicy"
    fi
    
    # Attach managed policy
    ERROR_OUTPUT=$(aws iam attach-role-policy \
        --role-name "$ROLE_NAME_LAMBDA" \
        --policy-arn "$MANAGED_POLICY_ARN" 2>&1)
    
    if [ $? -eq 0 ]; then
        echo "  ✓ Managed policy attached"
        ((SUCCESS_COUNT++))
    else
        # Check if already attached
        if echo "$ERROR_OUTPUT" | grep -q "already attached"; then
            echo "  ✓ Managed policy already attached"
            ((SUCCESS_COUNT++))
        else
            echo "  ✗ Failed to attach policy"
            echo "  Error: $ERROR_OUTPUT"
            ((FAILED_COUNT++))
        fi
    fi
done

echo ""
echo "=========================================="
echo "IAM Setup Complete!"
echo "=========================================="
echo ""
echo "Created/Updated roles:"
echo "  - $ROLE_NAME (Step Functions)"
echo "  - $SCHEDULER_ROLE_NAME (EventBridge Scheduler)"
echo ""
echo "Created/Updated managed policy:"
echo "  - $MANAGED_POLICY_NAME"
echo "  - ARN: $MANAGED_POLICY_ARN"
echo ""
echo "Lambda CloudWatch Metrics Policy Attachments:"
echo "  - Successfully attached: $SUCCESS_COUNT"
echo "  - Failed: $FAILED_COUNT"
echo "  - Skipped (not deployed): $SKIPPED_COUNT"
echo ""

if [ $SKIPPED_COUNT -gt 0 ]; then
    echo "Note: Some Lambda functions were skipped because they have not been deployed yet."
    echo "Run this script again after deploying Lambda functions to attach the policy."
    echo ""
fi

if [ $FAILED_COUNT -gt 0 ]; then
    echo "Warning: Some Lambda functions failed to update."
    echo "You may need to run this script with appropriate IAM permissions."
    echo ""
fi

echo "You can now run the deployment script."

exit 0
