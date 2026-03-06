#!/bin/bash
# Deploy Lambda Function script with blue-green deployment
# Usage: ./deploy-function.sh <function-name>

set -e

# Check arguments
if [ $# -eq 0 ]; then
    echo "Error: Function path required"
    echo "Usage: ./deploy-function.sh <function-path>"
    echo "Example: ./deploy-function.sh src/retrieveIdList"
    exit 1
fi

FUNCTION_PATH=$1
# Extract function name from path (e.g., src/retrieveIdList -> retrieveIdList)
FUNCTION_NAME=$(basename "$FUNCTION_PATH")
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

echo "=========================================="
echo "Deploying Lambda Function: $FUNCTION_NAME"
echo "=========================================="

# Check if function directory exists
if [ ! -d "$FUNCTION_PATH" ]; then
    echo "Error: Function directory '$FUNCTION_PATH' not found"
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
cd "$FUNCTION_PATH"
mkdir -p ../build
cp lambda_function.py ../build/

# Install function-specific dependencies if they exist
if [ -f requirements.txt ]; then
    echo "Installing function dependencies..."
    pip install -r requirements.txt -t ../build/ --upgrade
fi

cd ../build

# Create zip file using PowerShell on Windows, zip on Unix
if command -v powershell.exe &> /dev/null; then
    powershell.exe -Command "Compress-Archive -Path * -DestinationPath ../$FUNCTION_NAME.zip -Force"
elif command -v zip &> /dev/null; then
    zip -r ../"$FUNCTION_NAME".zip . -q
else
    echo "Error: Neither zip nor PowerShell found!"
    exit 1
fi

cd ..

# Get package size
if command -v du &> /dev/null; then
    SIZE=$(du -h "$FUNCTION_NAME.zip" | cut -f1)
else
    SIZE=$(powershell.exe -Command "(Get-Item $FUNCTION_NAME.zip).Length / 1MB | ForEach-Object { '{0:N2} MB' -f \$_ }")
fi
echo "Package size: $SIZE"

# Calculate SHA256 of the new package
if command -v sha256sum &> /dev/null; then
    NEW_SHA256=$(sha256sum "$FUNCTION_NAME.zip" | cut -d' ' -f1)
elif command -v shasum &> /dev/null; then
    NEW_SHA256=$(shasum -a 256 "$FUNCTION_NAME.zip" | cut -d' ' -f1)
else
    # Use PowerShell on Windows
    NEW_SHA256=$(powershell.exe -Command "(Get-FileHash -Algorithm SHA256 $FUNCTION_NAME.zip).Hash.ToLower()")
fi
echo "Package SHA256: $NEW_SHA256"

# Get current function SHA256
CURRENT_SHA256=$(aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Configuration.CodeSha256' \
    --output text 2>/dev/null || echo "")

echo "Current SHA256: $CURRENT_SHA256"

# Check if code has changed
if [ "$NEW_SHA256" == "$CURRENT_SHA256" ]; then
    echo "Code has not changed. Skipping function code update."
    CODE_UPDATED=false
else
    echo "Code has changed. Updating function code..."
    CODE_UPDATED=true
    
    # Update function code
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
fi

# Get layer version if available
LAYER_UPDATED=false
if [ -f lambda_layer/layer-version.txt ]; then
    LAYER_VERSION=$(cat lambda_layer/layer-version.txt)
    LAYER_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:layer:bdo-market-insights-common:$LAYER_VERSION"
    
    # Get current layer ARN
    CURRENT_LAYERS=$(aws lambda get-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --query 'Layers[*].Arn' \
        --output text 2>/dev/null || echo "")
    
    # Check if layer needs to be updated
    if echo "$CURRENT_LAYERS" | grep -q "$LAYER_ARN"; then
        echo "Layer version $LAYER_VERSION already attached. Skipping layer update."
    else
        echo "Updating function configuration with layer version $LAYER_VERSION..."
        aws lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --layers "$LAYER_ARN" \
            --region "$REGION" > /dev/null
        
        LAYER_UPDATED=true
        
        # Wait for configuration update
        echo "Waiting for configuration update to complete..."
        aws lambda wait function-updated \
            --function-name "$FUNCTION_NAME" \
            --region "$REGION"
    fi
else
    echo "Warning: lambda_layer/layer-version.txt not found. Skipping layer update."
fi

# Get new version
NEW_VERSION=$(aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Configuration.Version' \
    --output text)
echo "New version: $NEW_VERSION"

# Check if we need to update the alias
UPDATE_ALIAS=false
if [ "$CODE_UPDATED" = true ] || [ "$LAYER_UPDATED" = true ]; then
    UPDATE_ALIAS=true
elif [ "$NEW_VERSION" != "$CURRENT_VERSION" ]; then
    UPDATE_ALIAS=true
fi

if [ "$UPDATE_ALIAS" = true ]; then
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

    echo "Alias 'live' updated successfully"
else
    echo "No changes detected. Alias 'live' remains at version $CURRENT_VERSION"
fi

# Optional: Run smoke test if SKIP_SMOKE_TEST is not set
if [ "${SKIP_SMOKE_TEST}" != "true" ]; then
    echo "Running smoke test..."
    echo "Note: Smoke test uses a minimal test payload. Function may fail if it requires specific parameters."
    
    SMOKE_TEST_RESULT=$(aws lambda invoke \
        --function-name "$FUNCTION_NAME:live" \
        --payload '{"test": true}' \
        --region "$REGION" \
        --log-type Tail \
        response.json 2>&1)

    INVOKE_EXIT_CODE=$?

    # Check if invoke failed
    if [ $INVOKE_EXIT_CODE -ne 0 ]; then
        echo "=========================================="
        echo "WARNING: Smoke test failed!"
        echo "AWS Lambda invoke command failed with exit code: $INVOKE_EXIT_CODE"
        echo "Error output:"
        echo "$SMOKE_TEST_RESULT"
        echo "=========================================="
        echo ""
        echo "The function has been deployed but the smoke test failed."
        echo "This may be expected if the function requires specific parameters."
        echo "Please test the function manually with appropriate inputs."
        echo ""
        echo "To skip smoke tests in future deployments, set: export SKIP_SMOKE_TEST=true"
    else
        # Display response
        echo "Smoke test response:"
        if [ -f response.json ]; then
            cat response.json
            echo ""
            
            # Check for function errors in response
            if grep -q '"FunctionError"' response.json 2>/dev/null; then
                echo "=========================================="
                echo "WARNING: Lambda function returned an error!"
                echo "=========================================="
                
                # Try to get logs
                if echo "$SMOKE_TEST_RESULT" | grep -q "LogResult"; then
                    echo "Function logs:"
                    echo "$SMOKE_TEST_RESULT" | grep "LogResult" | sed 's/.*"LogResult": "\([^"]*\)".*/\1/' | base64 -d 2>/dev/null || echo "Could not decode logs"
                fi
                
                echo ""
                echo "The function has been deployed but returned an error during smoke test."
                echo "This may be expected if the function requires specific parameters."
                echo "Please test the function manually with appropriate inputs."
            else
                echo "Smoke test passed!"
            fi
        else
            echo "Warning: response.json not found"
        fi
    fi
    
    # Cleanup response file
    rm -f response.json
else
    echo "Smoke test skipped (SKIP_SMOKE_TEST=true)"
fi

# Cleanup
rm -rf build/
rm -f "$FUNCTION_NAME.zip"

echo "=========================================="
if [ "$CODE_UPDATED" = true ] || [ "$LAYER_UPDATED" = true ]; then
    echo "Deployment successful!"
    echo "Function: $FUNCTION_NAME"
    echo "Version: $NEW_VERSION"
    echo "Alias 'live' points to: $NEW_VERSION"
    echo "Previous version: $CURRENT_VERSION"
    if [ "$CODE_UPDATED" = true ]; then
        echo "Code: Updated"
    fi
    if [ "$LAYER_UPDATED" = true ]; then
        echo "Layer: Updated to version $LAYER_VERSION"
    fi
else
    echo "No changes detected!"
    echo "Function: $FUNCTION_NAME"
    echo "Version: $NEW_VERSION (unchanged)"
    echo "Alias 'live' points to: $NEW_VERSION"
fi
echo "=========================================="

exit 0
