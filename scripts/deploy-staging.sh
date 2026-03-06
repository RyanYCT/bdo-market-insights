#!/bin/bash
# Deploy BDO Market Insights to Staging Environment
# This script handles the complete staging deployment including:
# - AWS Secrets Manager setup
# - Lambda Layer deployment
# - Lambda Functions deployment
# - Step Functions state machine
# - API Gateway configuration
# - X-Ray tracing enablement
# - CloudWatch alarms
# - EventBridge Scheduler configuration

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
else
    echo -e "${YELLOW}WARNING: $CONFIG_FILE not found${NC}"
    echo "Using environment variables or defaults..."
    echo ""
    echo "To create a configuration file:"
    echo "  1. cp config/deployment-config.example.sh config/deployment-config.sh"
    echo "  2. Edit config/deployment-config.sh with your settings"
    echo "  3. Run this script again"
    echo ""
    read -p "Continue with environment variables? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Configuration (with fallbacks to environment variables)
ENVIRONMENT="${ENVIRONMENT:-staging}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

# Validate prerequisites
echo -e "${BLUE}=========================================="
echo "BDO Market Insights - Staging Deployment"
echo "==========================================${NC}"
echo ""
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo ""

# Function to print section headers
print_section() {
    echo ""
    echo -e "${BLUE}=========================================="
    echo "$1"
    echo "==========================================${NC}"
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

# Check prerequisites
print_section "Step 1/9: Checking Prerequisites"

if ! command -v aws &> /dev/null; then
    print_error "AWS CLI not found. Please install AWS CLI v2."
    exit 1
fi
print_success "AWS CLI found"

if ! command -v python3 &> /dev/null; then
    print_error "Python 3 not found. Please install Python 3.11+."
    exit 1
fi
print_success "Python 3 found"

if [ -z "$ACCOUNT_ID" ]; then
    print_warning "AWS_ACCOUNT_ID not set. Attempting to retrieve..."
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    if [ -z "$ACCOUNT_ID" ]; then
        print_error "Could not determine AWS Account ID"
        exit 1
    fi
    print_success "AWS Account ID: $ACCOUNT_ID"
fi

# Verify AWS credentials
aws sts get-caller-identity > /dev/null 2>&1
if [ $? -eq 0 ]; then
    print_success "AWS credentials valid"
else
    print_error "AWS credentials invalid or not configured"
    exit 1
fi

# Step 2: Create/Update AWS Secrets Manager secrets
print_section "Step 2/9: Configuring AWS Secrets Manager"

echo "This step will create or update secrets in AWS Secrets Manager."
echo "You will need to provide:"
echo "  - PostgreSQL database credentials"
echo "  - External API authentication tokens"
echo ""

read -p "Do you want to configure Secrets Manager now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    # PostgreSQL credentials
    echo ""
    echo "PostgreSQL Database Credentials:"
    read -p "  Host: " DB_HOST
    read -p "  Port [5432]: " DB_PORT
    DB_PORT=${DB_PORT:-5432}
    read -p "  Database name: " DB_NAME
    read -p "  Username: " DB_USER
    read -sp "  Password: " DB_PASSWORD
    echo ""
    
    # Create PostgreSQL secret
    SECRET_NAME="bdo-db-credentials-${ENVIRONMENT}"
    SECRET_VALUE="{\"username\":\"${DB_USER}\",\"password\":\"${DB_PASSWORD}\",\"host\":\"${DB_HOST}\",\"port\":${DB_PORT},\"database\":\"${DB_NAME}\"}"
    
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" > /dev/null 2>&1; then
        print_warning "Secret $SECRET_NAME already exists. Updating..."
        aws secretsmanager update-secret \
            --secret-id "$SECRET_NAME" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" > /dev/null
    else
        aws secretsmanager create-secret \
            --name "$SECRET_NAME" \
            --description "PostgreSQL credentials for BDO Market Insights ${ENVIRONMENT}" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" > /dev/null
    fi
    print_success "PostgreSQL credentials stored in Secrets Manager"
    
    # External API token
    echo ""
    echo "External API Configuration:"
    read -p "  API Base URL: " API_URL
    read -sp "  API Token (if required): " API_TOKEN
    echo ""
    
    SECRET_NAME="bdo-external-api-${ENVIRONMENT}"
    SECRET_VALUE="{\"api_url\":\"${API_URL}\",\"api_token\":\"${API_TOKEN}\"}"
    
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" > /dev/null 2>&1; then
        print_warning "Secret $SECRET_NAME already exists. Updating..."
        aws secretsmanager update-secret \
            --secret-id "$SECRET_NAME" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" > /dev/null
    else
        aws secretsmanager create-secret \
            --name "$SECRET_NAME" \
            --description "External API credentials for BDO Market Insights ${ENVIRONMENT}" \
            --secret-string "$SECRET_VALUE" \
            --region "$REGION" > /dev/null
    fi
    print_success "External API credentials stored in Secrets Manager"
else
    print_warning "Skipping Secrets Manager configuration. Ensure secrets are already configured."
fi

# Step 3: Deploy Lambda Layer
print_section "Step 3/9: Deploying Lambda Layer"

if [ -f "./scripts/deploy-layer.sh" ]; then
    ./scripts/deploy-layer.sh
    print_success "Lambda Layer deployed"
else
    print_error "deploy-layer.sh not found"
    exit 1
fi

# Step 4: Deploy Lambda Functions
print_section "Step 4/9: Deploying Lambda Functions"

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
    if [ -f "./scripts/deploy-function.sh" ]; then
        ./scripts/deploy-function.sh "$FUNCTION"
        print_success "$FUNCTION deployed"
    else
        print_error "deploy-function.sh not found"
        exit 1
    fi
done

# Step 5: Update Step Functions State Machine
print_section "Step 5/9: Deploying Step Functions State Machine"

# Get Lambda ARNs for queryData and analyzeData
QUERY_DATA_ARN=$(aws lambda get-function --function-name queryData --region "$REGION" --query 'Configuration.FunctionArn' --output text)
ANALYZE_DATA_ARN=$(aws lambda get-function --function-name analyzeData --region "$REGION" --query 'Configuration.FunctionArn' --output text)

if [ -z "$QUERY_DATA_ARN" ] || [ -z "$ANALYZE_DATA_ARN" ]; then
    print_error "Could not retrieve Lambda ARNs for queryData or analyzeData"
    exit 1
fi

# Deploy Step Functions using CloudFormation
aws cloudformation deploy \
    --template-file infrastructure/step-functions-template.yaml \
    --stack-name "bdo-step-functions-${ENVIRONMENT}" \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        QueryDataLambdaArn="$QUERY_DATA_ARN" \
        AnalyzeDataLambdaArn="$ANALYZE_DATA_ARN" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION"

print_success "Step Functions state machine deployed"

# Step 6: Configure API Gateway
print_section "Step 6/9: Deploying API Gateway"

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

# Deploy API Gateway using CloudFormation
echo "Configuring CORS allowed origins..."
read -p "Enter allowed CORS origins (comma-separated) [https://staging.example.com]: " ALLOWED_ORIGINS
ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-https://staging.example.com}

aws cloudformation deploy \
    --template-file infrastructure/api-gateway-template.yaml \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --parameter-overrides \
        Environment="$ENVIRONMENT" \
        AllowedOrigins="$ALLOWED_ORIGINS" \
        StepFunctionsArn="$STEP_FUNCTIONS_ARN" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION"

print_success "API Gateway deployed"

# Retrieve and display API key
API_KEY_ID=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
    --output text)

if [ -n "$API_KEY_ID" ]; then
    API_KEY_VALUE=$(aws apigateway get-api-key \
        --api-key "$API_KEY_ID" \
        --include-value \
        --region "$REGION" \
        --query 'value' \
        --output text)
    
    echo ""
    print_warning "IMPORTANT: Save this API key securely!"
    echo "API Key: $API_KEY_VALUE"
    echo ""
fi

# Step 7: Enable X-Ray Tracing
print_section "Step 7/9: Enabling X-Ray Tracing"

for FUNCTION in "${FUNCTIONS[@]}"; do
    aws lambda update-function-configuration \
        --function-name "$FUNCTION" \
        --tracing-config Mode=Active \
        --region "$REGION" > /dev/null
    print_success "X-Ray enabled for $FUNCTION"
done

# Step 8: Create CloudWatch Alarms
print_section "Step 8/9: Deploying CloudWatch Alarms"

if [ -f "infrastructure/deploy-alarms.sh" ]; then
    cd infrastructure
    ./deploy-alarms.sh
    cd ..
    print_success "CloudWatch alarms deployed"
else
    print_warning "deploy-alarms.sh not found. Skipping CloudWatch alarms."
fi

# Step 9: Configure EventBridge Scheduler
print_section "Step 9/9: Configuring EventBridge Scheduler"

echo "Configuring EventBridge schedules for ETL and retention..."

# ETL Pipeline Schedule (daily at 2 AM UTC)
ETL_SCHEDULE_NAME="bdo-etl-pipeline-${ENVIRONMENT}"
RETRIEVE_ID_LIST_ARN=$(aws lambda get-function --function-name retrieveIdList --region "$REGION" --query 'Configuration.FunctionArn' --output text)

# Check if schedule exists
if aws scheduler get-schedule --name "$ETL_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_warning "ETL schedule already exists. Updating..."
    aws scheduler update-schedule \
        --name "$ETL_SCHEDULE_NAME" \
        --schedule-expression "cron(0 2 * * ? *)" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETRIEVE_ID_LIST_ARN}\",\"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/EventBridgeSchedulerRole\"}" \
        --region "$REGION" > /dev/null
else
    aws scheduler create-schedule \
        --name "$ETL_SCHEDULE_NAME" \
        --schedule-expression "cron(0 2 * * ? *)" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETRIEVE_ID_LIST_ARN}\",\"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/EventBridgeSchedulerRole\"}" \
        --description "Daily ETL pipeline for BDO Market Insights ${ENVIRONMENT}" \
        --region "$REGION" > /dev/null
fi
print_success "ETL pipeline schedule configured (daily at 2 AM UTC)"

# Data Retention Schedule (monthly on 1st at 3 AM UTC)
RETENTION_SCHEDULE_NAME="bdo-data-retention-${ENVIRONMENT}"
RETAIN_DATA_ARN=$(aws lambda get-function --function-name retainData --region "$REGION" --query 'Configuration.FunctionArn' --output text)

if aws scheduler get-schedule --name "$RETENTION_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_warning "Retention schedule already exists. Updating..."
    aws scheduler update-schedule \
        --name "$RETENTION_SCHEDULE_NAME" \
        --schedule-expression "cron(0 3 1 * ? *)" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETAIN_DATA_ARN}\",\"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/EventBridgeSchedulerRole\"}" \
        --region "$REGION" > /dev/null
else
    aws scheduler create-schedule \
        --name "$RETENTION_SCHEDULE_NAME" \
        --schedule-expression "cron(0 3 1 * ? *)" \
        --flexible-time-window Mode=OFF \
        --target "{\"Arn\":\"${RETAIN_DATA_ARN}\",\"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/EventBridgeSchedulerRole\"}" \
        --description "Monthly data retention for BDO Market Insights ${ENVIRONMENT}" \
        --region "$REGION" > /dev/null
fi
print_success "Data retention schedule configured (monthly on 1st at 3 AM UTC)"

# Deployment Summary
print_section "Deployment Complete!"

echo ""
echo -e "${GREEN}All components deployed successfully!${NC}"
echo ""
echo "Deployment Summary:"
echo "  Environment: $ENVIRONMENT"
echo "  Region: $REGION"
echo "  Account: $ACCOUNT_ID"
echo ""

# Get API endpoint
API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
    --output text)

echo "API Endpoint: $API_ENDPOINT"
echo "API Documentation: ${API_ENDPOINT}/docs"
echo "OpenAPI Spec: ${API_ENDPOINT}/openapi.yaml"
echo ""

echo "Next Steps:"
echo "  1. Test API Gateway endpoint with the provided API key"
echo "  2. Verify Lambda functions in AWS Console"
echo "  3. Check CloudWatch logs for any errors"
echo "  4. Monitor CloudWatch alarms"
echo "  5. Verify X-Ray traces are being generated"
echo "  6. Test ETL pipeline manually (optional)"
echo ""

echo "To test the API:"
echo "  curl -X GET \"${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z\" \\"
echo "    -H \"x-api-key: ${API_KEY_VALUE}\""
echo ""

print_success "Staging deployment completed successfully!"

exit 0
