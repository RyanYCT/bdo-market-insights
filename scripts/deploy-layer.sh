#!/bin/bash
# Deploy Lambda Layer script
# This script builds and deploys the Lambda Layer to AWS

set -e

# Configuration
LAYER_NAME="bdo-market-insights-common"
PYTHON_VERSION="python3.11"
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
        public.ecr.aws/lambda/python:3.11 \
        -c "pip install -r requirements.txt -t ${BUILD_DIR}/python/ --upgrade"
else
    echo "Docker not found. Installing locally (may not be Lambda-compatible on Windows)..."
    pip install -r requirements.txt -t "$BUILD_DIR/python/" --upgrade --platform manylinux2014_x86_64 --only-binary=:all:
fi

# Create zip file
echo "Creating deployment package..."
cd "$BUILD_DIR"

# Use PowerShell on Windows, zip on Unix
if command -v powershell.exe &> /dev/null; then
    echo "Using PowerShell to create zip..."
    powershell.exe -Command "Compress-Archive -Path python/* -DestinationPath ../lambda-layer.zip -Force"
elif command -v zip &> /dev/null; then
    echo "Using zip to create package..."
    zip -r ../lambda-layer.zip python/ -q
else
    echo "Error: Neither zip nor PowerShell found!"
    exit 1
fi

cd ..

# Get file size
if command -v du &> /dev/null; then
    SIZE=$(du -h lambda-layer.zip | cut -f1)
else
    # Use PowerShell on Windows
    SIZE=$(powershell.exe -Command "(Get-Item lambda-layer.zip).Length / 1MB | ForEach-Object { '{0:N2} MB' -f \$_ }")
fi
echo "Package size: $SIZE"

# Publish layer
echo "Publishing Lambda Layer..."
LAYER_VERSION=$(aws lambda publish-layer-version \
    --layer-name "$LAYER_NAME" \
    --description "Common utilities for BDO Market Insights - $(date +%Y-%m-%d)" \
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

# Return to root directory
cd ..

exit 0
