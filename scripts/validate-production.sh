#!/bin/bash
# Validate Production Deployment
# This script performs comprehensive validation of production deployment

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="production"

# Functions
print_section() {
    echo ""
    echo -e "${BLUE}=========================================="
    echo "$1"
    echo -e "==========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_section "BDO Market Insights - Production Validation"

echo ""
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo ""

VALIDATION_PASSED=0
VALIDATION_FAILED=0

# Validate Lambda Functions
print_section "Validating Lambda Functions"

FUNCTIONS=(
    "retrieveIdList"
    "fetchData"
    "cleanData"
    "storeData"
    "queryData"
    "analyzeData"
    "retainData"
)

for FUNCTION in "${FUNCTIONS[@]}"; do
    echo ""
    echo "Checking $FUNCTION..."
    
    # Check function exists
    if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" > /dev/null 2>&1; then
        print_success "Function exists"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        
        # Check X-Ray is enabled
        XRAY_MODE=$(aws lambda get-function-configuration \
            --function-name "$FUNCTION" \
            --region "$REGION" \
            --query 'TracingConfig.Mode' \
            --output text)
        
        if [ "$XRAY_MODE" == "Active" ]; then
            print_success "X-Ray tracing enabled"
            VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        else
            print_error "X-Ray tracing not enabled"
            VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
        fi
        
        # Check layer is attached
        LAYER_COUNT=$(aws lambda get-function-configuration \
            --function-name "$FUNCTION" \
            --region "$REGION" \
            --query 'Layers | length(@)' \
            --output text)
        
        if [ "$LAYER_COUNT" -gt 0 ]; then
            print_success "Lambda Layer attached"
            VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        else
            print_warning "No Lambda Layer attached"
        fi
        
        # Check 'live' alias exists
        if aws lambda get-alias \
            --function-name "$FUNCTION" \
            --name live \
            --region "$REGION" > /dev/null 2>&1; then
            print_success "'live' alias configured"
            VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        else
            print_error "'live' alias not found"
            VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
        fi
    else
        print_error "Function not found"
        VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
    fi
done

# Validate Step Functions
print_section "Validating Step Functions"

STACK_NAME="bdo-step-functions-${ENVIRONMENT}"
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "Step Functions stack exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    
    # Get state machine ARN
    STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
        --output text)
    
    if [ -n "$STATE_MACHINE_ARN" ]; then
        print_success "State machine ARN retrieved"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        
        # Check state machine status
        STATE_MACHINE_STATUS=$(aws stepfunctions describe-state-machine \
            --state-machine-arn "$STATE_MACHINE_ARN" \
            --region "$REGION" \
            --query 'status' \
            --output text)
        
        if [ "$STATE_MACHINE_STATUS" == "ACTIVE" ]; then
            print_success "State machine is active"
            VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        else
            print_error "State machine status: $STATE_MACHINE_STATUS"
            VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
        fi
    else
        print_error "Could not retrieve state machine ARN"
        VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
    fi
else
    print_error "Step Functions stack not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Validate API Gateway
print_section "Validating API Gateway"

API_STACK_NAME="bdo-api-gateway-${ENVIRONMENT}"
if aws cloudformation describe-stacks --stack-name "$API_STACK_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "API Gateway stack exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    
    # Get API endpoint
    API_ENDPOINT=$(aws cloudformation describe-stacks \
        --stack-name "$API_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
        --output text)
    
    if [ -n "$API_ENDPOINT" ]; then
        print_success "API endpoint: $API_ENDPOINT"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        
        # Test API endpoint (without API key - should return 401)
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_ENDPOINT/query?item_id=1" || echo "000")
        
        if [ "$HTTP_CODE" == "401" ] || [ "$HTTP_CODE" == "403" ]; then
            print_success "API authentication is enforced"
            VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
        else
            print_warning "API returned HTTP $HTTP_CODE (expected 401/403)"
        fi
    else
        print_error "Could not retrieve API endpoint"
        VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
    fi
    
    # Check API key exists
    API_KEY_ID=$(aws cloudformation describe-stacks \
        --stack-name "$API_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$API_KEY_ID" ]; then
        print_success "API key configured"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    else
        print_warning "API key not found in stack outputs"
    fi
else
    print_error "API Gateway stack not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Validate EventBridge Scheduler
print_section "Validating EventBridge Scheduler"

# Check ETL schedule
ETL_SCHEDULE_NAME="bdo-etl-pipeline-${ENVIRONMENT}"
if aws scheduler get-schedule --name "$ETL_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "ETL pipeline schedule exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    
    # Check schedule state
    SCHEDULE_STATE=$(aws scheduler get-schedule \
        --name "$ETL_SCHEDULE_NAME" \
        --region "$REGION" \
        --query 'State' \
        --output text)
    
    if [ "$SCHEDULE_STATE" == "ENABLED" ]; then
        print_success "ETL schedule is enabled"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    else
        print_warning "ETL schedule state: $SCHEDULE_STATE"
    fi
else
    print_error "ETL pipeline schedule not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Check retention schedule
RETENTION_SCHEDULE_NAME="bdo-data-retention-${ENVIRONMENT}"
if aws scheduler get-schedule --name "$RETENTION_SCHEDULE_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "Data retention schedule exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    
    SCHEDULE_STATE=$(aws scheduler get-schedule \
        --name "$RETENTION_SCHEDULE_NAME" \
        --region "$REGION" \
        --query 'State' \
        --output text)
    
    if [ "$SCHEDULE_STATE" == "ENABLED" ]; then
        print_success "Retention schedule is enabled"
        VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
    else
        print_warning "Retention schedule state: $SCHEDULE_STATE"
    fi
else
    print_error "Data retention schedule not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Validate CloudWatch Alarms
print_section "Validating CloudWatch Alarms"

ALARM_COUNT=$(aws cloudwatch describe-alarms \
    --alarm-name-prefix "bdo-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'MetricAlarms | length(@)' \
    --output text 2>/dev/null || echo "0")

if [ "$ALARM_COUNT" -gt 0 ]; then
    print_success "Found $ALARM_COUNT CloudWatch alarms"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
else
    print_warning "No CloudWatch alarms found"
fi

# Validate Secrets Manager
print_section "Validating Secrets Manager"

# Check database credentials
DB_SECRET_NAME="bdo-db-credentials-${ENVIRONMENT}"
if aws secretsmanager describe-secret --secret-id "$DB_SECRET_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "Database credentials secret exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
else
    print_error "Database credentials secret not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Check external API credentials
API_SECRET_NAME="bdo-external-api-${ENVIRONMENT}"
if aws secretsmanager describe-secret --secret-id "$API_SECRET_NAME" --region "$REGION" > /dev/null 2>&1; then
    print_success "External API credentials secret exists"
    VALIDATION_PASSED=$((VALIDATION_PASSED + 1))
else
    print_error "External API credentials secret not found"
    VALIDATION_FAILED=$((VALIDATION_FAILED + 1))
fi

# Summary
print_section "Validation Summary"

echo ""
echo "Validation Results:"
echo "  Passed: $VALIDATION_PASSED"
echo "  Failed: $VALIDATION_FAILED"
echo ""

if [ "$VALIDATION_FAILED" -eq 0 ]; then
    print_success "All validations passed!"
    echo ""
    echo "Production deployment is ready for monitoring."
    echo ""
    echo "Next steps:"
    echo "  1. Monitor CloudWatch metrics: bash scripts/monitor-production.sh"
    echo "  2. Test ETL pipeline manually (optional)"
    echo "  3. Test Query API with valid API key"
    echo "  4. Monitor for 24 hours before considering deployment complete"
    echo ""
    exit 0
else
    print_error "Some validations failed!"
    echo ""
    echo "Please address the failed validations before proceeding."
    echo ""
    exit 1
fi
