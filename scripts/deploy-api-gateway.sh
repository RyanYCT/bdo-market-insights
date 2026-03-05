#!/bin/bash
# Deploy API Gateway
# This script deploys the API Gateway using CloudFormation

set -e

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"
STACK_NAME="bdo-market-insights-api"
STATE_MACHINE_NAME="bdo-market-insights-query"

echo "=========================================="
echo "Deploying API Gateway"
echo "=========================================="

# Check if template exists
if [ ! -f infrastructure/api-gateway-template.yaml ]; then
    echo "Error: API Gateway template not found"
    exit 1
fi

# Get state machine ARN
STATE_MACHINE_ARN="arn:aws:states:$REGION:$ACCOUNT_ID:stateMachine:$STATE_MACHINE_NAME"

echo "Deploying CloudFormation stack..."
aws cloudformation deploy \
    --template-file infrastructure/api-gateway-template.yaml \
    --stack-name "$STACK_NAME" \
    --parameter-overrides \
        StateMachineArn="$STATE_MACHINE_ARN" \
    --capabilities CAPABILITY_IAM \
    --region "$REGION" \
    --no-fail-on-empty-changeset

# Get API ID from stack outputs
echo "Getting API Gateway ID..."
API_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiId`].OutputValue' \
    --output text)

if [ -z "$API_ID" ]; then
    echo "Error: Could not retrieve API ID from stack outputs"
    exit 1
fi

# Create deployment
echo "Creating API deployment..."
DEPLOYMENT_ID=$(aws apigateway create-deployment \
    --rest-api-id "$API_ID" \
    --stage-name prod \
    --description "Deployment from script - $(date +%Y-%m-%d\ %H:%M:%S)" \
    --region "$REGION" \
    --query 'id' \
    --output text)

# Get API endpoint
API_ENDPOINT="https://$API_ID.execute-api.$REGION.amazonaws.com/prod"

echo "=========================================="
echo "API Gateway deployed successfully!"
echo "Stack Name: $STACK_NAME"
echo "API ID: $API_ID"
echo "Deployment ID: $DEPLOYMENT_ID"
echo "Endpoint: $API_ENDPOINT"
echo "=========================================="
echo ""
echo "Test the API:"
echo "curl -X GET \"$API_ENDPOINT/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z\" \\"
echo "  -H \"x-api-key: YOUR_API_KEY\""
echo ""

exit 0
