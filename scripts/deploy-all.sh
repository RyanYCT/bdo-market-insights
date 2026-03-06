#!/bin/bash
# Deploy all Lambda functions and infrastructure
# This script orchestrates the complete deployment process

set -e

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

echo "=========================================="
echo "BDO Market Insights - Full Deployment"
echo "=========================================="
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo ""

# Step 1: Deploy Lambda Layer
echo "Step 1/5: Deploying Lambda Layer..."
./scripts/deploy-layer.sh

# Step 2: Deploy Lambda Functions
echo ""
echo "Step 2/5: Deploying Lambda Functions..."
FUNCTIONS=(
    "src/retrieveIdList"
    "src/fetchData"
    "src/cleanData"
    "src/storeData"
    "src/queryData"
    "src/analyzeData"
    "src/retainData"
)

for FUNCTION in "${FUNCTIONS[@]}"; do
    echo ""
    echo "Deploying $FUNCTION..."
    ./scripts/deploy-function.sh "$FUNCTION"
done

# Step 3: Update Step Functions State Machine
echo ""
echo "Step 3/5: Updating Step Functions State Machine..."
./scripts/deploy-step-functions.sh

# Step 4: Deploy API Gateway
echo ""
echo "Step 4/5: Deploying API Gateway..."
./scripts/deploy-api-gateway.sh

# Step 5: Deploy CloudWatch Alarms
echo ""
echo "Step 5/5: Deploying CloudWatch Alarms..."
cd infrastructure
./deploy-alarms.sh
cd ..

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Verify Lambda functions in AWS Console"
echo "2. Test API Gateway endpoints"
echo "3. Check CloudWatch logs and metrics"
echo "4. Monitor CloudWatch alarms"
echo ""

exit 0
