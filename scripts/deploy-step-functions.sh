#!/bin/bash
# Deploy Step Functions State Machine
# This script updates the Step Functions state machine definition

set -e

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"
STATE_MACHINE_NAME="bdo-market-insights-query"

echo "=========================================="
echo "Deploying Step Functions State Machine"
echo "=========================================="

# Check if state machine definition exists
if [ ! -f infrastructure/step-functions-state-machine.json ]; then
    echo "Error: State machine definition not found"
    exit 1
fi

# Create temporary file with substitutions
TEMP_FILE=$(mktemp)
cp infrastructure/step-functions-state-machine.json "$TEMP_FILE"

# Replace placeholders
echo "Replacing placeholders..."
sed -i "s/REGION/$REGION/g" "$TEMP_FILE"
sed -i "s/ACCOUNT/$ACCOUNT_ID/g" "$TEMP_FILE"

# Validate JSON
echo "Validating state machine definition..."
if ! python3 -m json.tool "$TEMP_FILE" > /dev/null 2>&1; then
    echo "Error: Invalid JSON in state machine definition"
    rm "$TEMP_FILE"
    exit 1
fi

# Get state machine ARN
STATE_MACHINE_ARN="arn:aws:states:$REGION:$ACCOUNT_ID:stateMachine:$STATE_MACHINE_NAME"

# Check if state machine exists
if aws stepfunctions describe-state-machine \
    --state-machine-arn "$STATE_MACHINE_ARN" \
    --region "$REGION" > /dev/null 2>&1; then
    
    echo "Updating existing state machine..."
    aws stepfunctions update-state-machine \
        --state-machine-arn "$STATE_MACHINE_ARN" \
        --definition file://"$TEMP_FILE" \
        --region "$REGION"
else
    echo "Error: State machine does not exist. Please create it first using CloudFormation."
    rm "$TEMP_FILE"
    exit 1
fi

# Cleanup
rm "$TEMP_FILE"

echo "=========================================="
echo "State Machine updated successfully!"
echo "Name: $STATE_MACHINE_NAME"
echo "ARN: $STATE_MACHINE_ARN"
echo "=========================================="

exit 0
