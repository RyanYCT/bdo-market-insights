#!/bin/bash
# Deploy Lambda Function script with blue-green deployment
# Usage: ./deploy-function.sh <function-name>

set -e

# Check arguments
if [ $# -eq 0 ]; then
    echo "Error: Function name required"
    echo "Usage: ./deploy-function.sh <function-name>"
    echo "Example: ./deploy-function.sh retrieveIdList"
    exit 1
fi

FUNCTION_NAME=$1
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

echo "=========================================="
echo "Deploying Lambda Function: $FUNCTION_NAME"
echo "=========================================="

# Check if function directory exists
if [ ! -d "$FUNCTION_NAME" ]; then
    echo "Error: Function directory '$FUNCTION_NAME' not found"
    exit 1
fi

# Get current function version for rollback
echo "Getting current function configuration..."
CURRENT_VERSION=$(aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Configuration.Version' \
    --output text)
echo "Current version: $CURRENT_VERSION"

# Build deployment package
echo "Building deployment package..."
cd "$FUNCTION_NAME"
mkdir -p ../build
cp lambda_function.py ../build/

# Install function-specific dependencies if they exist
if [ -f requirements.txt ]; then
    echo "Installing function dependencies..."
    pip install -r requirements.txt -t ../build/ --upgrade
fi

cd ../build
zip -r ../"$FUNCTION_NAME".zip . -q
cd ..

# Get package size
SIZE=$(du -h "$FUNCTION_NAME.zip" | cut -f1)
echo "Package size: $SIZE"

# Update function code
echo "Updating function code..."
aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb://"$FUNCTION_NAME".zip \
    --region "$REGION" \
    --publish > /dev/null

# Wait for update to complete
echo "Waiting for function update to complete..."
aws lambda wait function-updated \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION"

# Get layer version if available
if [ -f lambda_layer/layer-version.txt ]; then
    LAYER_VERSION=$(cat lambda_layer/layer-version.txt)
    LAYER_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:layer:bdo-market-insights-common:$LAYER_VERSION"
    
    echo "Updating function configuration with layer version $LAYER_VERSION..."
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --layers "$LAYER_ARN" \
        --region "$REGION" > /dev/null
    
    # Wait for configuration update
    echo "Waiting for configuration update to complete..."
    aws lambda wait function-updated \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION"
fi

# Get new version
NEW_VERSION=$(aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Configuration.Version' \
    --output text)
echo "New version: $NEW_VERSION"

# Update or create 'live' alias
echo "Updating 'live' alias to point to version $NEW_VERSION..."
aws lambda update-alias \
    --function-name "$FUNCTION_NAME" \
    --name live \
    --function-version "$NEW_VERSION" \
    --region "$REGION" > /dev/null 2>&1 \
    || aws lambda create-alias \
        --function-name "$FUNCTION_NAME" \
        --name live \
        --function-version "$NEW_VERSION" \
        --region "$REGION" > /dev/null

# Run smoke test
echo "Running smoke test..."
SMOKE_TEST_RESULT=$(aws lambda invoke \
    --function-name "$FUNCTION_NAME:live" \
    --payload '{"test": true}' \
    --region "$REGION" \
    --log-type Tail \
    response.json 2>&1 || echo "FAILED")

if [[ "$SMOKE_TEST_RESULT" == *"FAILED"* ]]; then
    echo "=========================================="
    echo "ERROR: Smoke test failed!"
    echo "Rolling back to version $CURRENT_VERSION..."
    echo "=========================================="
    
    aws lambda update-alias \
        --function-name "$FUNCTION_NAME" \
        --name live \
        --function-version "$CURRENT_VERSION" \
        --region "$REGION"
    
    echo "Rollback complete. Function is running version $CURRENT_VERSION"
    exit 1
fi

# Display response
echo "Smoke test response:"
cat response.json
echo ""

# Cleanup
rm -rf build/
rm -f "$FUNCTION_NAME.zip"
rm -f response.json

echo "=========================================="
echo "Deployment successful!"
echo "Function: $FUNCTION_NAME"
echo "Version: $NEW_VERSION"
echo "Alias 'live' points to: $NEW_VERSION"
echo "Previous version: $CURRENT_VERSION"
echo "=========================================="

exit 0
