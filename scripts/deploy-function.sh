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

# Check if code has changed by comparing with deployed version
echo "Checking if code has changed..."
CODE_UPDATED=false

# Try to download current function code
CURRENT_CODE_URL=$(aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'Code.Location' \
    --output text 2>/dev/null || echo "")

if [ -n "$CURRENT_CODE_URL" ] && [ "$CURRENT_CODE_URL" != "None" ]; then
    echo "Downloading current deployed code for comparison..."
    
    # Download current code
    if command -v curl &> /dev/null; then
        curl -s "$CURRENT_CODE_URL" -o current-code.zip
    elif command -v wget &> /dev/null; then
        wget -q "$CURRENT_CODE_URL" -O current-code.zip
    else
        # Use PowerShell on Windows
        powershell.exe -Command "Invoke-WebRequest -Uri '$CURRENT_CODE_URL' -OutFile current-code.zip" > /dev/null 2>&1
    fi
    
    if [ -f current-code.zip ]; then
        # Compare file contents by extracting and comparing
        mkdir -p compare-current compare-new
        
        # Extract both zips
        if command -v unzip &> /dev/null; then
            unzip -q current-code.zip -d compare-current 2>/dev/null || true
            unzip -q "$FUNCTION_NAME.zip" -d compare-new 2>/dev/null || true
        else
            # Use PowerShell on Windows
            powershell.exe -Command "Expand-Archive -Path current-code.zip -DestinationPath compare-current -Force" 2>/dev/null || true
            powershell.exe -Command "Expand-Archive -Path $FUNCTION_NAME.zip -DestinationPath compare-new -Force" 2>/dev/null || true
        fi
        
        # Compare the lambda_function.py files (main code)
        if [ -f compare-current/lambda_function.py ] && [ -f compare-new/lambda_function.py ]; then
            if command -v diff &> /dev/null; then
                if diff -q compare-current/lambda_function.py compare-new/lambda_function.py > /dev/null 2>&1; then
                    echo "Code has not changed (lambda_function.py is identical)."
                    CODE_UPDATED=false
                else
                    echo "Code has changed (lambda_function.py differs)."
                    CODE_UPDATED=true
                fi
            else
                # Use PowerShell Compare-Object on Windows
                DIFF_RESULT=$(powershell.exe -Command "if ((Get-FileHash compare-current/lambda_function.py).Hash -eq (Get-FileHash compare-new/lambda_function.py).Hash) { 'same' } else { 'different' }")
                if [ "$DIFF_RESULT" == "same" ]; then
                    echo "Code has not changed (lambda_function.py is identical)."
                    CODE_UPDATED=false
                else
                    echo "Code has changed (lambda_function.py differs)."
                    CODE_UPDATED=true
                fi
            fi
        else
            echo "Could not extract files for comparison. Assuming code changed."
            CODE_UPDATED=true
        fi
        
        # Cleanup comparison directories
        rm -rf compare-current compare-new current-code.zip
    else
        echo "Could not download current code. Assuming code changed."
        CODE_UPDATED=true
    fi
else
    echo "Function not found or first deployment. Will deploy code."
    CODE_UPDATED=true
fi

if [ "$CODE_UPDATED" = true ]; then
    echo "Updating function code..."
    
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
