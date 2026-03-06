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
pip install -r requirements.txt -t "$BUILD_DIR/python/" --upgrade

# Create zip file
echo "Creating deployment package..."
cd "$BUILD_DIR"
zip -r ../lambda-layer.zip python/ -q
cd ..

# Get file size
SIZE=$(du -h lambda-layer.zip | cut -f1)
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
