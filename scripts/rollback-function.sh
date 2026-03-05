#!/bin/bash
# Rollback Lambda Function to previous version
# Usage: ./rollback-function.sh <function-name> <version>

set -e

# Check arguments
if [ $# -lt 1 ]; then
    echo "Error: Function name required"
    echo "Usage: ./rollback-function.sh <function-name> [version]"
    echo "Example: ./rollback-function.sh retrieveIdList 5"
    echo ""
    echo "If version is not specified, will rollback to previous version"
    exit 1
fi

FUNCTION_NAME=$1
TARGET_VERSION=$2
REGION="${AWS_REGION:-us-east-1}"

echo "=========================================="
echo "Rolling back Lambda Function: $FUNCTION_NAME"
echo "=========================================="

# Get current version from 'live' alias
CURRENT_VERSION=$(aws lambda get-alias \
    --function-name "$FUNCTION_NAME" \
    --name live \
    --region "$REGION" \
    --query 'FunctionVersion' \
    --output text)

echo "Current version (live): $CURRENT_VERSION"

# If target version not specified, get previous version
if [ -z "$TARGET_VERSION" ]; then
    # List versions and get the one before current
    VERSIONS=$(aws lambda list-versions-by-function \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --query 'Versions[?Version!=`$LATEST`].Version' \
        --output text)
    
    # Find version before current
    PREV_VERSION=""
    for VERSION in $VERSIONS; do
        if [ "$VERSION" == "$CURRENT_VERSION" ]; then
            break
        fi
        PREV_VERSION=$VERSION
    done
    
    if [ -z "$PREV_VERSION" ]; then
        echo "Error: Could not determine previous version"
        exit 1
    fi
    
    TARGET_VERSION=$PREV_VERSION
fi

echo "Target version: $TARGET_VERSION"

# Confirm rollback
read -p "Are you sure you want to rollback from version $CURRENT_VERSION to $TARGET_VERSION? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rollback cancelled"
    exit 0
fi

# Update alias to point to target version
echo "Updating 'live' alias to version $TARGET_VERSION..."
aws lambda update-alias \
    --function-name "$FUNCTION_NAME" \
    --name live \
    --function-version "$TARGET_VERSION" \
    --region "$REGION"

# Verify rollback
NEW_CURRENT=$(aws lambda get-alias \
    --function-name "$FUNCTION_NAME" \
    --name live \
    --region "$REGION" \
    --query 'FunctionVersion' \
    --output text)

if [ "$NEW_CURRENT" == "$TARGET_VERSION" ]; then
    echo "=========================================="
    echo "Rollback successful!"
    echo "Function: $FUNCTION_NAME"
    echo "Previous version: $CURRENT_VERSION"
    echo "Current version: $NEW_CURRENT"
    echo "=========================================="
else
    echo "Error: Rollback verification failed"
    exit 1
fi

exit 0
