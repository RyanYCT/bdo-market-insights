#!/bin/bash
# Deploy Step Functions State Machine
# This script creates or updates the Step Functions state machine using CloudFormation

# Configuration
ENVIRONMENT="${ENVIRONMENT:-staging}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

echo "=========================================="
echo "Deploying Step Functions State Machine"
echo "=========================================="
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo ""

# Get Lambda ARNs for queryData and analyzeData
echo "Retrieving Lambda function ARNs..."
QUERY_DATA_ARN=$(aws lambda get-function --function-name queryData --region "$REGION" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")
ANALYZE_DATA_ARN=$(aws lambda get-function --function-name analyzeData --region "$REGION" --query 'Configuration.FunctionArn' --output text 2>/dev/null || echo "")

if [ -z "$QUERY_DATA_ARN" ] || [ -z "$ANALYZE_DATA_ARN" ]; then
    echo "Error: Could not retrieve Lambda ARNs for queryData or analyzeData"
    echo "Please ensure these Lambda functions are deployed first."
    exit 1
fi

echo "✓ queryData ARN: $QUERY_DATA_ARN"
echo "✓ analyzeData ARN: $ANALYZE_DATA_ARN"
echo ""

# Check if CloudFormation template exists
if [ ! -f "infrastructure/step-functions-template.yaml" ]; then
    echo "Error: CloudFormation template not found: infrastructure/step-functions-template.yaml"
    exit 1
fi

# Check if stack exists and is in ROLLBACK_COMPLETE state
STACK_NAME="bdo-step-functions-${ENVIRONMENT}"
echo "Checking stack status..."
STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [ "$STACK_STATUS" == "ROLLBACK_COMPLETE" ]; then
    echo "⚠ Stack is in ROLLBACK_COMPLETE state. Deleting stack..."
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    
    echo "Waiting for stack deletion to complete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    
    echo "✓ Stack deleted successfully"
    echo ""
fi

# Deploy Step Functions using CloudFormation
echo "Deploying Step Functions state machine via CloudFormation..."
echo ""

# Capture the output and exit code
DEPLOY_OUTPUT=$(aws cloudformation deploy \
    --template-file infrastructure/step-functions-template.yaml \
    --stack-name "$STACK_NAME" \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        QueryDataLambdaArn="$QUERY_DATA_ARN" \
        AnalyzeDataLambdaArn="$ANALYZE_DATA_ARN" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" 2>&1)
DEPLOY_EXIT_CODE=$?

# Check if deployment succeeded or if no changes were needed
if [ $DEPLOY_EXIT_CODE -eq 0 ]; then
    echo "✓ CloudFormation deployment completed successfully"
elif echo "$DEPLOY_OUTPUT" | grep -q "No changes to deploy"; then
    echo "✓ No changes to deploy - stack is up to date"
else
    echo "✗ CloudFormation deployment failed:"
    echo "$DEPLOY_OUTPUT"
    exit 1
fi

echo ""

# Get the deployed state machine ARN
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
    --output text)

echo ""
echo "=========================================="
echo "Step Functions Deployed Successfully!"
echo "=========================================="
echo "Stack Name: $STACK_NAME"
echo "State Machine ARN: $STATE_MACHINE_ARN"
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "=========================================="

exit 0
