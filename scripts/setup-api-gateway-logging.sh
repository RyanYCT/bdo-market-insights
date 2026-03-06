#!/bin/bash
# Setup API Gateway CloudWatch Logs Role
# This is a one-time setup per AWS account
# Run this with an IAM user/role that has IAM admin permissions

set -e

REGION="${AWS_REGION:-us-east-1}"

echo "=========================================="
echo "Setting up API Gateway CloudWatch Logs Role"
echo "Region: $REGION"
echo "=========================================="

# Check if the role already exists
ROLE_NAME="APIGatewayCloudWatchLogsRole"

if aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
    echo "Role $ROLE_NAME already exists"
    ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
else
    echo "Creating IAM role: $ROLE_NAME"
    
    # Create the role
    ROLE_ARN=$(aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "apigateway.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --description "Allows API Gateway to write logs to CloudWatch" \
        --query 'Role.Arn' \
        --output text)
    
    echo "Role created: $ROLE_ARN"
    
    # Attach the managed policy for CloudWatch Logs
    echo "Attaching CloudWatch Logs policy..."
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
    
    # Wait a bit for IAM to propagate
    echo "Waiting for IAM role to propagate..."
    sleep 10
fi

# Set the CloudWatch Logs role ARN in API Gateway account settings
echo "Configuring API Gateway account settings..."
aws apigateway update-account \
    --patch-operations op=replace,path=/cloudwatchRoleArn,value="$ROLE_ARN" \
    --region "$REGION" > /dev/null

echo ""
echo "=========================================="
echo "API Gateway CloudWatch Logs Setup Complete!"
echo "=========================================="
echo ""
echo "Role ARN: $ROLE_ARN"
echo ""
echo "You can now deploy API Gateway with logging enabled."

exit 0
