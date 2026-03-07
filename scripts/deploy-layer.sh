#!/bin/bash
# Deploy Lambda Layer script
# This script builds and deploys the Lambda Layer to AWS

set -e

# Configuration
LAYER_NAME="bdo-market-insights-common"
PYTHON_VERSION="python3.14"
REGION="${AWS_REGION:-us-east-1}"

echo "=========================================="
echo "Deploying Lambda Layer: $LAYER_NAME"
echo "=========================================="

# Navigate to lambda_layer directory
cd lambda_layer

# Create temporary build directory
echo "Creating build directory..."
BUILD_DIR="build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/python"

# Clean previous package
echo "Cleaning previous builds..."
rm -f lambda-layer.zip

# Copy common code to build directory
echo "Copying common code..."
if [ -d "python/common" ]; then
    cp -r python/common "$BUILD_DIR/python/"
else
    echo "Error: python/common directory not found!"
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
# Check if Docker is available for Lambda-compatible builds
if command -v docker &> /dev/null; then
    echo "Using Docker to build Lambda-compatible packages..."
    
    # Prevent Git Bash from converting paths on Windows
    export MSYS_NO_PATHCONV=1
    
    docker run --rm \
        --entrypoint /bin/bash \
        -v "$(pwd):/var/task" \
        -w /var/task \
        public.ecr.aws/lambda/python:3.14 \
        -c "pip install -r requirements.txt -t ${BUILD_DIR}/python/ --upgrade"
else
    echo "Docker not found. Installing locally (may not be Lambda-compatible on Windows)..."
    pip install -r requirements.txt -t "$BUILD_DIR/python/" --upgrade --platform manylinux2014_x86_64 --only-binary=:all:
fi

# Create zip file
echo "Creating deployment package..."
# Stay in lambda_layer directory and zip the build/python directory
# This preserves the python/ directory structure required by Lambda layers

# Use PowerShell on Windows, zip on Unix
if command -v powershell.exe &> /dev/null; then
    echo "Using PowerShell to create zip..."
    powershell.exe -Command "Compress-Archive -Path ${BUILD_DIR}/python -DestinationPath lambda-layer.zip -Force"
elif command -v zip &> /dev/null; then
    echo "Using zip to create package..."
    cd "$BUILD_DIR"
    zip -r ../lambda-layer.zip python/ -q
    cd ..
else
    echo "Error: Neither zip nor PowerShell found!"
    exit 1
fi

# Get file size
if command -v du &> /dev/null; then
    SIZE=$(du -h lambda-layer.zip | cut -f1)
else
    # Use PowerShell on Windows
    SIZE=$(powershell.exe -Command "(Get-Item lambda-layer.zip).Length / 1MB | ForEach-Object { '{0:N2} MB' -f \$_ }")
fi
echo "Package size: $SIZE"

# Check if layer needs to be updated
echo "Checking if layer has changed..."
LAYER_CHANGED=false

# Get the latest layer version
LATEST_LAYER_VERSION=$(aws lambda list-layer-versions \
    --layer-name "$LAYER_NAME" \
    --region "$REGION" \
    --query 'LayerVersions[0].Version' \
    --output text 2>/dev/null || echo "")

if [ -n "$LATEST_LAYER_VERSION" ] && [ "$LATEST_LAYER_VERSION" != "None" ]; then
    echo "Found existing layer version: $LATEST_LAYER_VERSION"
    
    # Get the download URL for the current layer
    LAYER_URL=$(aws lambda get-layer-version \
        --layer-name "$LAYER_NAME" \
        --version-number "$LATEST_LAYER_VERSION" \
        --region "$REGION" \
        --query 'Content.Location' \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$LAYER_URL" ] && [ "$LAYER_URL" != "None" ]; then
        echo "Downloading current layer for comparison..."
        
        # Download current layer
        if command -v curl &> /dev/null; then
            curl -s "$LAYER_URL" -o current-layer.zip
        elif command -v wget &> /dev/null; then
            wget -q "$LAYER_URL" -O current-layer.zip
        else
            # Use PowerShell on Windows
            powershell.exe -Command "Invoke-WebRequest -Uri '$LAYER_URL' -OutFile current-layer.zip" > /dev/null 2>&1
        fi
        
        if [ -f current-layer.zip ]; then
            # Extract both layers for comparison
            mkdir -p compare-current compare-new
            
            if command -v unzip &> /dev/null; then
                unzip -q current-layer.zip -d compare-current 2>/dev/null || true
                unzip -q lambda-layer.zip -d compare-new 2>/dev/null || true
            else
                # Use PowerShell on Windows
                powershell.exe -Command "Expand-Archive -Path current-layer.zip -DestinationPath compare-current -Force" 2>/dev/null || true
                powershell.exe -Command "Expand-Archive -Path lambda-layer.zip -DestinationPath compare-new -Force" 2>/dev/null || true
            fi
            
            # Compare requirements.txt to check if dependencies changed
            DEPS_CHANGED=false
            if [ -f requirements.txt ]; then
                # Create a hash of requirements.txt
                if command -v sha256sum &> /dev/null; then
                    NEW_REQ_HASH=$(sha256sum requirements.txt | cut -d' ' -f1)
                elif command -v shasum &> /dev/null; then
                    NEW_REQ_HASH=$(shasum -a 256 requirements.txt | cut -d' ' -f1)
                else
                    NEW_REQ_HASH=$(powershell.exe -Command "(Get-FileHash -Algorithm SHA256 requirements.txt).Hash.ToLower()")
                fi
                
                # Check if we have a saved hash from last deployment
                # .last-requirements-hash stores the SHA256 of requirements.txt from the last successful deployment
                # This allows us to detect if dependencies have changed without downloading the entire layer
                if [ -f .last-requirements-hash ]; then
                    LAST_REQ_HASH=$(cat .last-requirements-hash)
                    if [ "$NEW_REQ_HASH" != "$LAST_REQ_HASH" ]; then
                        echo "Dependencies have changed (requirements.txt modified)."
                        DEPS_CHANGED=true
                    else
                        echo "Dependencies unchanged (requirements.txt identical)."
                    fi
                else
                    echo "No previous requirements hash found. Assuming dependencies changed."
                    DEPS_CHANGED=true
                fi
            fi
            
            # Compare common code directory if it exists
            CODE_CHANGED=false
            if [ -d "python/common" ]; then
                # Compare the common directory between current and new
                if [ -d "compare-current/python/common" ] && [ -d "compare-new/python/common" ]; then
                    if command -v diff &> /dev/null; then
                        if diff -r -q compare-current/python/common compare-new/python/common > /dev/null 2>&1; then
                            echo "Common code unchanged."
                        else
                            echo "Common code has changed."
                            CODE_CHANGED=true
                        fi
                    else
                        # Use PowerShell on Windows - compare directory hashes
                        CURRENT_HASH=$(powershell.exe -Command "Get-ChildItem -Path compare-current/python/common -Recurse -File | Get-FileHash | Select-Object -ExpandProperty Hash | Sort-Object | Get-Unique | ForEach-Object { \$_ } | Out-String | Get-FileHash -Algorithm SHA256 | Select-Object -ExpandProperty Hash")
                        NEW_HASH=$(powershell.exe -Command "Get-ChildItem -Path compare-new/python/common -Recurse -File | Get-FileHash | Select-Object -ExpandProperty Hash | Sort-Object | Get-Unique | ForEach-Object { \$_ } | Out-String | Get-FileHash -Algorithm SHA256 | Select-Object -ExpandProperty Hash")
                        
                        if [ "$CURRENT_HASH" == "$NEW_HASH" ]; then
                            echo "Common code unchanged."
                        else
                            echo "Common code has changed."
                            CODE_CHANGED=true
                        fi
                    fi
                else
                    echo "Could not compare common code directories. Assuming changed."
                    CODE_CHANGED=true
                fi
            fi
            
            # Determine if layer changed
            if [ "$DEPS_CHANGED" = true ] || [ "$CODE_CHANGED" = true ]; then
                LAYER_CHANGED=true
            fi
            
            # Cleanup comparison directories
            rm -rf compare-current compare-new current-layer.zip
        else
            echo "Could not download current layer. Assuming layer changed."
            LAYER_CHANGED=true
        fi
    else
        echo "Could not get layer download URL. Assuming layer changed."
        LAYER_CHANGED=true
    fi
else
    echo "No existing layer found. This is the first deployment."
    LAYER_CHANGED=true
fi

if [ "$LAYER_CHANGED" = true ]; then
    # Publish layer
    echo "Publishing Lambda Layer..."
    LAYER_VERSION=$(aws lambda publish-layer-version \
        --layer-name "$LAYER_NAME" \
        --description "Common utilities for BDO Market Insights ETL pipeline - Python 3.14 - $(date +%Y-%m-%d)" \
        --zip-file fileb://lambda-layer.zip \
        --compatible-runtimes "$PYTHON_VERSION" \
        --region "$REGION" \
        --query 'Version' \
        --output text)

    echo "=========================================="
    echo "Lambda Layer published successfully!"
    echo "Layer Name: $LAYER_NAME"
    echo "Version: $LAYER_VERSION"
    echo "Region: $REGION"
    echo "=========================================="

    # Save layer version to file
    echo "$LAYER_VERSION" > layer-version.txt
    echo "Layer version saved to layer-version.txt"
    
    # Save requirements hash for next comparison
    # This file is used to detect dependency changes in future deployments
    # See lambda_layer/README.md for more information
    if [ -f requirements.txt ]; then
        if command -v sha256sum &> /dev/null; then
            sha256sum requirements.txt | cut -d' ' -f1 > .last-requirements-hash
        elif command -v shasum &> /dev/null; then
            shasum -a 256 requirements.txt | cut -d' ' -f1 > .last-requirements-hash
        else
            powershell.exe -Command "(Get-FileHash -Algorithm SHA256 requirements.txt).Hash.ToLower()" > .last-requirements-hash
        fi
        echo "Requirements hash saved for future change detection."
    fi
else
    echo "=========================================="
    echo "Layer has not changed. Skipping deployment."
    echo "Layer Name: $LAYER_NAME"
    echo "Current Version: $LATEST_LAYER_VERSION"
    echo "Region: $REGION"
    echo "=========================================="
    
    # Ensure layer-version.txt reflects current version
    echo "$LATEST_LAYER_VERSION" > layer-version.txt
    echo "Layer version file updated to current version: $LATEST_LAYER_VERSION"
    
    LAYER_VERSION=$LATEST_LAYER_VERSION
fi

# Return to root directory
cd ..

exit 0
