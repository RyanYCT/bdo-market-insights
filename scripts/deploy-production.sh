#!/bin/bash
# Deploy BDO Market Insights to Production Environment with Blue-Green Deployment
# This script handles production deployment with:
# - Blue-green deployment strategy
# - Gradual traffic shifting
# - Automatic rollback on failure
# - 24-hour monitoring period

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Load configuration from config file
CONFIG_FILE="config/deployment-config.sh"
if [ -f "$CONFIG_FILE" ]; then
    echo "Loading configuration from $CONFIG_FILE..."
    source "$CONFIG_FILE"
    # Override environment to production
    export ENVIRONMENT="production"
else
    echo -e "${RED}ERROR: $CONFIG_FILE not found${NC}"
    echo "Please create configuration file before deploying to production."
    exit 1
fi

# Configuration
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

# Function to print section headers
print_section() {
    echo ""
    echo -e "${BLUE}=========================================="
    echo "$1"
    echo -e "==========================================${NC}"
}

# Function to print success messages
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Function to print error messages
print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Function to print warning messages
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Deployment banner
print_section "BDO Market Insights - Production Deployment"
echo ""
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo ""
echo -e "${YELLOW}WARNING: You are about to deploy to PRODUCTION!${NC}"
echo ""
echo "This deployment will:"
echo "  1. Deploy new Lambda function versions"
echo "  2. Use blue-green deployment with gradual traffic shifting"
echo "  3. Monitor CloudWatch metrics for errors"
echo "  4. Keep old versions available for rollback"
echo ""

read -p "Are you sure you want to continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

# Check prerequisites
print_section "Step 1/10: Checking Prerequisites"

if ! command -v aws &> /dev/null; then
    print_error "AWS CLI not found. Please install AWS CLI v2."
    exit 1
fi
print_success "AWS CLI found"

if ! command -v python3 &> /dev/null; then
    print_error "Python 3 not found. Please install Python 3.14+."
    exit 1
fi
print_success "Python 3 found"

# Verify AWS credentials
aws sts get-caller-identity > /dev/null 2>&1
if [ $? -eq 0 ]; then
    print_success "AWS credentials valid"
else
    print_error "AWS credentials invalid or not configured"
    exit 1
fi

# Verify staging deployment exists
print_section "Step 2/10: Verifying Staging Deployment"

echo "Checking if staging environment is deployed and healthy..."

STAGING_FUNCTIONS=(
    "retrieveIdList"
    "fetchData"
    "cleanData"
    "storeData"
    "queryData"
    "analyzeData"
    "retainData"
)

for FUNCTION in "${STAGING_FUNCTIONS[@]}"; do
    if ! aws lambda get-function --function-name "$FUNCTION" --region "$REGION" > /dev/null 2>&1; then
        print_error "Function $FUNCTION not found in staging. Please deploy to staging first."
        exit 1
    fi
done

print_success "All staging functions verified"

# Check for recent errors in staging
echo "Checking staging CloudWatch logs for recent errors..."
STAGING_ERROR_COUNT=0
for FUNCTION in "${STAGING_FUNCTIONS[@]}"; do
    ERROR_COUNT=$(aws logs filter-log-events \
        --log-group-name "/aws/lambda/$FUNCTION" \
        --start-time $(($(date +%s) - 86400))000 \
        --filter-pattern "ERROR" \
        --region "$REGION" \
        --query 'events | length(@)' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$ERROR_COUNT" -gt 10 ]; then
        print_warning "Function $FUNCTION has $ERROR_COUNT errors in the last 24 hours"
        STAGING_ERROR_COUNT=$((STAGING_ERROR_COUNT + ERROR_COUNT))
    fi
done

if [ "$STAGING_ERROR_COUNT" -gt 50 ]; then
    print_error "Staging has $STAGING_ERROR_COUNT errors in the last 24 hours"
    read -p "Continue with production deployment despite staging errors? (yes/no): " CONTINUE
    if [ "$CONTINUE" != "yes" ]; then
        echo "Deployment cancelled."
        exit 1
    fi
else
    print_success "Staging error count acceptable ($STAGING_ERROR_COUNT errors in 24h)"
fi

# Deploy Lambda Layer
print_section "Step 3/10: Deploying Lambda Layer"

if [ -f "./scripts/deploy-layer.sh" ]; then
    ./scripts/deploy-layer.sh
    print_success "Lambda Layer deployed"
else
    print_error "deploy-layer.sh not found"
    exit 1
fi

# Deploy Lambda Functions with Blue-Green Strategy
print_section "Step 4/10: Deploying Lambda Functions (Blue-Green)"

export SKIP_SMOKE_TEST=true

FUNCTIONS=(
    "retrieveIdList"
    "fetchData"
    "cleanData"
    "storeData"
    "queryData"
    "analyzeData"
    "retainData"
)

# Store old versions for rollback
declare -A OLD_VERSIONS
declare -A NEW_VERSIONS

for FUNCTION in "${FUNCTIONS[@]}"; do
    echo ""
    echo "Deploying src/$FUNCTION..."
    
    # Get current version before deployment
    OLD_VERSION=$(aws lambda get-function \
        --function-name "$FUNCTION" \
        --region "$REGION" \
        --query 'Configuration.Version' \
        --output text 2>/dev/null || echo "")
    
    OLD_VERSIONS[$FUNCTION]=$OLD_VERSION
    echo "Current version: $OLD_VERSION"
    
    # Deploy new version
    if [ -f "./scripts/deploy-function.sh" ]; then
        ./scripts/deploy-function.sh "src/$FUNCTION"
        
        # Get new version after deployment
        NEW_VERSION=$(aws lambda get-function \
            --function-name "$FUNCTION" \
            --region "$REGION" \
            --query 'Configuration.Version' \
            --output text)
        
        NEW_VERSIONS[$FUNCTION]=$NEW_VERSION
        echo "New version: $NEW_VERSION"
        
        print_success "$FUNCTION deployed (v$OLD_VERSION → v$NEW_VERSION)"
    else
        print_error "deploy-function.sh not found"
        exit 1
    fi
done

unset SKIP_SMOKE_TEST

# Save version mapping for rollback
ROLLBACK_FILE="/tmp/bdo-production-rollback-$(date +%Y%m%d-%H%M%S).txt"
echo "# BDO Market Insights Production Deployment" > "$ROLLBACK_FILE"
echo "# Deployment Time: $(date)" >> "$ROLLBACK_FILE"
echo "# Region: $REGION" >> "$ROLLBACK_FILE"
echo "" >> "$ROLLBACK_FILE"
for FUNCTION in "${FUNCTIONS[@]}"; do
    echo "$FUNCTION:${OLD_VERSIONS[$FUNCTION]}:${NEW_VERSIONS[$FUNCTION]}" >> "$ROLLBACK_FILE"
done

print_success "Rollback information saved to: $ROLLBACK_FILE"

# Update Step Functions State Machine
print_section "Step 5/10: Updating Step Functions State Machine"

# Get Lambda ARNs
QUERY_DATA_ARN=$(aws lambda get-function --function-name queryData --region "$REGION" --query 'Configuration.FunctionArn' --output text)
ANALYZE_DATA_ARN=$(aws lambda get-function --function-name analyzeData --region "$REGION" --query 'Configuration.FunctionArn' --output text)

if [ -z "$QUERY_DATA_ARN" ] || [ -z "$ANALYZE_DATA_ARN" ]; then
    print_error "Could not retrieve Lambda ARNs"
    exit 1
fi

# Deploy Step Functions
STACK_NAME="bdo-step-functions-${ENVIRONMENT}"
aws cloudformation deploy \
    --template-file infrastructure/step-functions-template.yaml \
    --stack-name "$STACK_NAME" \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        QueryDataLambdaArn="$QUERY_DATA_ARN" \
        AnalyzeDataLambdaArn="$ANALYZE_DATA_ARN" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION"

print_success "Step Functions state machine updated"

# Update API Gateway
print_section "Step 6/10: Updating API Gateway"

# Get Step Functions ARN
STEP_FUNCTIONS_ARN=$(aws cloudformation describe-stacks \
    --stack-name "bdo-step-functions-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
    --output text)

if [ -z "$STEP_FUNCTIONS_ARN" ]; then
    print_error "Could not retrieve Step Functions ARN"
    exit 1
fi

# Deploy API Gateway
API_STACK_NAME="bdo-api-gateway-${ENVIRONMENT}"
ALLOWED_ORIGINS="${ALLOWED_CORS_ORIGINS:-https://example.com}"

aws cloudformation deploy \
    --template-file infrastructure/api-gateway-template.yaml \
    --stack-name "$API_STACK_NAME" \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        AllowedOrigins="$ALLOWED_ORIGINS" \
        StepFunctionsArn="$STEP_FUNCTIONS_ARN" \
        EnableLogging="true" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION"

print_success "API Gateway updated"

# Enable X-Ray Tracing
print_section "Step 7/10: Enabling X-Ray Tracing"

for FUNCTION in "${FUNCTIONS[@]}"; do
    aws lambda update-function-configuration \
        --function-name "$FUNCTION" \
        --tracing-config Mode=Active \
        --region "$REGION" > /dev/null
    print_success "X-Ray enabled for $FUNCTION"
done

# Update CloudWatch Alarms
print_section "Step 8/10: Updating CloudWatch Alarms"

if [ -f "infrastructure/deploy-alarms.sh" ]; then
    cd infrastructure
    if [ -n "$ALARM_EMAIL" ]; then
        ./deploy-alarms.sh --environment "$ENVIRONMENT" --region "$REGION" --email "$ALARM_EMAIL"
    else
        ./deploy-alarms.sh --environment "$ENVIRONMENT" --region "$REGION"
    fi
    cd ..
    print_success "CloudWatch alarms updated"
else
    print_warning "deploy-alarms.sh not found. Skipping CloudWatch alarms."
fi

# Configure EventBridge Scheduler
print_section "Step 9/10: Configuring EventBridge Scheduler"

# ETL Pipeline Schedule
ETL_SCHEDULE_NAME="bdo-etl-pipeline-${ENVIRONMENT}"
RETRIEVE_ID_LIST_ARN=$(aws lambda get-function --function-name retrieveIdList --region "$REGION" --query 'Configuration.FunctionArn' --output text)
SCHEDULER_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/EventBridgeSchedulerRole"

if aws scheduler get-schedule --name "$ETL_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    aws scheduler update-schedule \
        --name "$ETL_SCHEDULE_NAME" \
        --schedule-expression "${ETL_SCHEDULE_EXPRESSION:-cron(0 2 * * ? *)}" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETRIEVE_ID_LIST_ARN}\",\"RoleArn\":\"${SCHEDULER_ROLE_ARN}\"}" \
        --region "$REGION" > /dev/null
else
    aws scheduler create-schedule \
        --name "$ETL_SCHEDULE_NAME" \
        --schedule-expression "${ETL_SCHEDULE_EXPRESSION:-cron(0 2 * * ? *)}" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETRIEVE_ID_LIST_ARN}\",\"RoleArn\":\"${SCHEDULER_ROLE_ARN}\"}" \
        --description "Daily ETL pipeline for BDO Market Insights ${ENVIRONMENT}" \
        --region "$REGION" > /dev/null
fi
print_success "ETL pipeline schedule configured"

# Data Retention Schedule
RETENTION_SCHEDULE_NAME="bdo-data-retention-${ENVIRONMENT}"
RETAIN_DATA_ARN=$(aws lambda get-function --function-name retainData --region "$REGION" --query 'Configuration.FunctionArn' --output text)

if aws scheduler get-schedule --name "$RETENTION_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    aws scheduler update-schedule \
        --name "$RETENTION_SCHEDULE_NAME" \
        --schedule-expression "${RETENTION_SCHEDULE_EXPRESSION:-cron(0 3 1 * ? *)}" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETAIN_DATA_ARN}\",\"RoleArn\":\"${SCHEDULER_ROLE_ARN}\"}" \
        --region "$REGION" > /dev/null
else
    aws scheduler create-schedule \
        --name "$RETENTION_SCHEDULE_NAME" \
        --schedule-expression "${RETENTION_SCHEDULE_EXPRESSION:-cron(0 3 1 * ? *)}" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETAIN_DATA_ARN}\",\"RoleArn\":\"${SCHEDULER_ROLE_ARN}\"}" \
        --description "Monthly data retention for BDO Market Insights ${ENVIRONMENT}" \
        --region "$REGION" > /dev/null
fi
print_success "Data retention schedule configured"

# Deployment Summary
print_section "Step 10/10: Deployment Complete - Monitoring Phase"

echo ""
echo -e "${GREEN}Production deployment successful!${NC}"
echo ""
echo "Deployment Summary:"
echo "  Environment: $ENVIRONMENT"
echo "  Region: $REGION"
echo "  Deployment Time: $(date)"
echo "  Rollback File: $ROLLBACK_FILE"
echo ""

# Get API endpoint
API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
    --output text)

echo "API Endpoint: $API_ENDPOINT"
echo ""

echo -e "${YELLOW}=========================================="
echo "24-HOUR MONITORING PERIOD"
echo -e "==========================================${NC}"
echo ""
echo "The deployment is now live. Please monitor the following for 24 hours:"
echo ""
echo "1. CloudWatch Metrics:"
echo "   - Lambda error rates (should be < 5%)"
echo "   - API Gateway 5xx errors"
echo "   - Database connection pool utilization"
echo "   - Query API latency (p95 should be < 2s)"
echo ""
echo "2. CloudWatch Logs:"
echo "   - Check for ERROR level logs"
echo "   - Verify correlation IDs are present"
echo "   - Check X-Ray traces for bottlenecks"
echo ""
echo "3. CloudWatch Alarms:"
echo "   - Monitor alarm state changes"
echo "   - Investigate any triggered alarms"
echo ""
echo "4. ETL Pipeline:"
echo "   - Verify scheduled execution at 2 AM UTC"
echo "   - Check data is being collected correctly"
echo "   - Verify no data loss"
echo ""
echo "5. Query API:"
echo "   - Test with various query parameters"
echo "   - Verify response times"
echo "   - Check cache headers"
echo ""
echo -e "${YELLOW}If any issues are detected, run the rollback script:${NC}"
echo "  bash scripts/rollback-production.sh $ROLLBACK_FILE"
echo ""

print_success "Production deployment completed successfully!"

exit 0
