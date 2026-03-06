#!/bin/bash
# Monitor Production Deployment Health
# This script checks CloudWatch metrics and logs for production health

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="production"

# Time range for metrics (default: last hour)
TIME_RANGE_MINUTES="${1:-60}"
START_TIME=$(($(date +%s) - TIME_RANGE_MINUTES * 60))000
END_TIME=$(date +%s)000

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

print_section "BDO Market Insights - Production Health Check"

echo ""
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Time Range: Last $TIME_RANGE_MINUTES minutes"
echo "Start Time: $(date -d @$((START_TIME / 1000)) 2>/dev/null || date -r $((START_TIME / 1000)))"
echo ""

# Check Lambda Functions
print_section "Lambda Function Health"

FUNCTIONS=(
    "retrieveIdList"
    "fetchData"
    "cleanData"
    "storeData"
    "queryData"
    "analyzeData"
    "retainData"
)

TOTAL_ERRORS=0
TOTAL_INVOCATIONS=0

for FUNCTION in "${FUNCTIONS[@]}"; do
    echo ""
    echo "Checking $FUNCTION..."
    
    # Get error count
    ERROR_COUNT=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/Lambda \
        --metric-name Errors \
        --dimensions Name=FunctionName,Value=$FUNCTION \
        --start-time $(date -u -d @$((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --end-time $(date -u -d @$((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --period 3600 \
        --statistics Sum \
        --region "$REGION" \
        --query 'Datapoints[0].Sum' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$ERROR_COUNT" == "None" ]; then
        ERROR_COUNT=0
    fi
    
    # Get invocation count
    INVOCATION_COUNT=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/Lambda \
        --metric-name Invocations \
        --dimensions Name=FunctionName,Value=$FUNCTION \
        --start-time $(date -u -d @$((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --end-time $(date -u -d @$((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --period 3600 \
        --statistics Sum \
        --region "$REGION" \
        --query 'Datapoints[0].Sum' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$INVOCATION_COUNT" == "None" ]; then
        INVOCATION_COUNT=0
    fi
    
    # Calculate error rate
    if [ "$INVOCATION_COUNT" -gt 0 ]; then
        ERROR_RATE=$(echo "scale=2; $ERROR_COUNT * 100 / $INVOCATION_COUNT" | bc)
    else
        ERROR_RATE=0
    fi
    
    TOTAL_ERRORS=$((TOTAL_ERRORS + ERROR_COUNT))
    TOTAL_INVOCATIONS=$((TOTAL_INVOCATIONS + INVOCATION_COUNT))
    
    echo "  Invocations: $INVOCATION_COUNT"
    echo "  Errors: $ERROR_COUNT"
    echo "  Error Rate: ${ERROR_RATE}%"
    
    if [ "$ERROR_COUNT" -eq 0 ]; then
        print_success "$FUNCTION is healthy"
    elif (( $(echo "$ERROR_RATE < 5" | bc -l) )); then
        print_warning "$FUNCTION has ${ERROR_RATE}% error rate (acceptable)"
    else
        print_error "$FUNCTION has ${ERROR_RATE}% error rate (exceeds 5% threshold)"
    fi
done

# Overall Lambda health
echo ""
if [ "$TOTAL_INVOCATIONS" -gt 0 ]; then
    OVERALL_ERROR_RATE=$(echo "scale=2; $TOTAL_ERRORS * 100 / $TOTAL_INVOCATIONS" | bc)
else
    OVERALL_ERROR_RATE=0
fi

echo "Overall Lambda Health:"
echo "  Total Invocations: $TOTAL_INVOCATIONS"
echo "  Total Errors: $TOTAL_ERRORS"
echo "  Overall Error Rate: ${OVERALL_ERROR_RATE}%"

if (( $(echo "$OVERALL_ERROR_RATE < 5" | bc -l) )); then
    print_success "Overall Lambda health is good"
else
    print_error "Overall Lambda error rate exceeds 5% threshold"
fi

# Check API Gateway
print_section "API Gateway Health"

API_ID=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIId`].OutputValue' \
    --output text 2>/dev/null || echo "")

if [ -n "$API_ID" ]; then
    # Get 5xx errors
    API_5XX_COUNT=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/ApiGateway \
        --metric-name 5XXError \
        --dimensions Name=ApiId,Value=$API_ID \
        --start-time $(date -u -d @$((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --end-time $(date -u -d @$((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --period 3600 \
        --statistics Sum \
        --region "$REGION" \
        --query 'Datapoints[0].Sum' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$API_5XX_COUNT" == "None" ]; then
        API_5XX_COUNT=0
    fi
    
    # Get request count
    API_REQUEST_COUNT=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/ApiGateway \
        --metric-name Count \
        --dimensions Name=ApiId,Value=$API_ID \
        --start-time $(date -u -d @$((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --end-time $(date -u -d @$((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --period 3600 \
        --statistics Sum \
        --region "$REGION" \
        --query 'Datapoints[0].Sum' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$API_REQUEST_COUNT" == "None" ]; then
        API_REQUEST_COUNT=0
    fi
    
    # Get latency (p95)
    API_LATENCY_P95=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/ApiGateway \
        --metric-name Latency \
        --dimensions Name=ApiId,Value=$API_ID \
        --start-time $(date -u -d @$((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((START_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --end-time $(date -u -d @$((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -r $((END_TIME / 1000)) +%Y-%m-%dT%H:%M:%S) \
        --period 3600 \
        --statistics 'p95' \
        --region "$REGION" \
        --query 'Datapoints[0]."p95"' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$API_LATENCY_P95" == "None" ]; then
        API_LATENCY_P95=0
    fi
    
    echo ""
    echo "API Gateway Metrics:"
    echo "  Total Requests: $API_REQUEST_COUNT"
    echo "  5xx Errors: $API_5XX_COUNT"
    echo "  P95 Latency: ${API_LATENCY_P95}ms"
    
    if [ "$API_5XX_COUNT" -eq 0 ]; then
        print_success "No API Gateway 5xx errors"
    else
        print_warning "API Gateway has $API_5XX_COUNT 5xx errors"
    fi
    
    if (( $(echo "$API_LATENCY_P95 < 2000" | bc -l) )); then
        print_success "API latency is within target (< 2s)"
    else
        print_error "API latency exceeds 2s target"
    fi
else
    print_warning "Could not retrieve API Gateway ID"
fi

# Check CloudWatch Alarms
print_section "CloudWatch Alarms"

ALARM_STATES=$(aws cloudwatch describe-alarms \
    --alarm-name-prefix "bdo-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'MetricAlarms[*].[AlarmName,StateValue]' \
    --output text 2>/dev/null || echo "")

if [ -n "$ALARM_STATES" ]; then
    ALARM_COUNT=0
    OK_COUNT=0
    
    echo ""
    echo "$ALARM_STATES" | while read -r ALARM_NAME STATE; do
        echo "  $ALARM_NAME: $STATE"
        if [ "$STATE" == "ALARM" ]; then
            ALARM_COUNT=$((ALARM_COUNT + 1))
        elif [ "$STATE" == "OK" ]; then
            OK_COUNT=$((OK_COUNT + 1))
        fi
    done
    
    echo ""
    TOTAL_ALARMS=$(echo "$ALARM_STATES" | wc -l)
    ALARM_COUNT=$(echo "$ALARM_STATES" | grep -c "ALARM" || echo "0")
    
    if [ "$ALARM_COUNT" -eq 0 ]; then
        print_success "All alarms are in OK state"
    else
        print_error "$ALARM_COUNT alarms are in ALARM state"
    fi
else
    print_warning "No CloudWatch alarms found"
fi

# Check recent errors in logs
print_section "Recent Error Logs"

echo ""
echo "Checking for ERROR level logs in the last $TIME_RANGE_MINUTES minutes..."
echo ""

TOTAL_LOG_ERRORS=0

for FUNCTION in "${FUNCTIONS[@]}"; do
    LOG_GROUP="/aws/lambda/$FUNCTION"
    
    ERROR_LOGS=$(aws logs filter-log-events \
        --log-group-name "$LOG_GROUP" \
        --start-time "$START_TIME" \
        --end-time "$END_TIME" \
        --filter-pattern "ERROR" \
        --region "$REGION" \
        --query 'events | length(@)' \
        --output text 2>/dev/null || echo "0")
    
    if [ "$ERROR_LOGS" -gt 0 ]; then
        echo "  $FUNCTION: $ERROR_LOGS ERROR logs"
        TOTAL_LOG_ERRORS=$((TOTAL_LOG_ERRORS + ERROR_LOGS))
    fi
done

echo ""
if [ "$TOTAL_LOG_ERRORS" -eq 0 ]; then
    print_success "No ERROR level logs found"
else
    print_warning "Found $TOTAL_LOG_ERRORS ERROR level logs"
    echo ""
    echo "To view detailed logs, run:"
    echo "  aws logs tail /aws/lambda/<function-name> --follow --region $REGION"
fi

# Summary
print_section "Health Check Summary"

echo ""
echo "Production Health Status:"
echo "  Lambda Functions: $TOTAL_INVOCATIONS invocations, $TOTAL_ERRORS errors (${OVERALL_ERROR_RATE}%)"
echo "  API Gateway: $API_REQUEST_COUNT requests, $API_5XX_COUNT 5xx errors"
echo "  CloudWatch Alarms: $ALARM_COUNT in ALARM state"
echo "  Error Logs: $TOTAL_LOG_ERRORS ERROR level logs"
echo ""

# Overall health assessment
HEALTH_ISSUES=0

if (( $(echo "$OVERALL_ERROR_RATE >= 5" | bc -l) )); then
    HEALTH_ISSUES=$((HEALTH_ISSUES + 1))
fi

if [ "$API_5XX_COUNT" -gt 10 ]; then
    HEALTH_ISSUES=$((HEALTH_ISSUES + 1))
fi

if [ "$ALARM_COUNT" -gt 0 ]; then
    HEALTH_ISSUES=$((HEALTH_ISSUES + 1))
fi

if [ "$HEALTH_ISSUES" -eq 0 ]; then
    print_success "Production is healthy!"
elif [ "$HEALTH_ISSUES" -eq 1 ]; then
    print_warning "Production has minor issues - monitor closely"
else
    print_error "Production has multiple issues - consider rollback"
fi

echo ""

exit 0
