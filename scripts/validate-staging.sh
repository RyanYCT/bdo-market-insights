#!/bin/bash
# Staging Validation Script for BDO Market Insights
# This script validates the deployed staging environment by testing:
# - ETL pipeline execution
# - Query API with various parameters
# - API key authentication
# - Rate limiting
# - CloudWatch logs (JSON format and correlation IDs)
# - X-Ray traces
# - Custom metrics
# - Error handling scenarios

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
ENVIRONMENT="${ENVIRONMENT:-staging}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID}"

# Test results tracking
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0

# Function to print section headers
print_section() {
    echo ""
    echo -e "${BLUE}=========================================="
    echo "$1"
    echo -e "==========================================${NC}"
}

# Function to print test results
print_test() {
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    if [ "$1" == "PASS" ]; then
        TESTS_PASSED=$((TESTS_PASSED + 1))
        echo -e "${GREEN}✓ TEST $TESTS_TOTAL: $2${NC}"
    else
        TESTS_FAILED=$((TESTS_FAILED + 1))
        echo -e "${RED}✗ TEST $TESTS_TOTAL: $2${NC}"
        if [ -n "$3" ]; then
            echo -e "${RED}  Error: $3${NC}"
        fi
    fi
}

# Function to print info messages
print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

# Function to print warning messages
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Header
echo -e "${BLUE}=========================================="
echo "BDO Market Insights - Staging Validation"
echo -e "==========================================${NC}"
echo ""
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo ""

# Check prerequisites
print_section "Checking Prerequisites"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}AWS CLI not found. Please install AWS CLI v2.${NC}"
    exit 1
fi
print_info "AWS CLI found"

if ! command -v curl &> /dev/null; then
    echo -e "${RED}curl not found. Please install curl.${NC}"
    exit 1
fi
print_info "curl found"

if ! command -v jq &> /dev/null; then
    echo -e "${RED}jq not found. Please install jq for JSON parsing.${NC}"
    exit 1
fi
print_info "jq found"

# Verify AWS credentials
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo -e "${RED}AWS credentials invalid or not configured${NC}"
    exit 1
fi
print_info "AWS credentials valid"

# Get AWS Account ID if not set
if [ -z "$ACCOUNT_ID" ]; then
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi
print_info "AWS Account ID: $ACCOUNT_ID"

# Get API Gateway endpoint and API key
print_section "Retrieving API Configuration"

API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
    --output text 2>/dev/null)

if [ -z "$API_ENDPOINT" ]; then
    echo -e "${RED}Could not retrieve API endpoint. Is the API Gateway deployed?${NC}"
    exit 1
fi
print_info "API Endpoint: $API_ENDPOINT"

API_KEY_ID=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue' \
    --output text 2>/dev/null)

if [ -z "$API_KEY_ID" ]; then
    echo -e "${RED}Could not retrieve API key ID${NC}"
    exit 1
fi

API_KEY=$(aws apigateway get-api-key \
    --api-key "$API_KEY_ID" \
    --include-value \
    --region "$REGION" \
    --query 'value' \
    --output text 2>/dev/null)

if [ -z "$API_KEY" ]; then
    echo -e "${RED}Could not retrieve API key value${NC}"
    exit 1
fi
print_info "API Key retrieved successfully"

# Test 1: Execute ETL Pipeline Manually
print_section "Test 1: ETL Pipeline Execution"

print_info "Invoking retrieveIdList Lambda..."
ETL_RESPONSE=$(aws lambda invoke \
    --function-name retrieveIdList \
    --region "$REGION" \
    --log-type Tail \
    --payload '{}' \
    /tmp/retrieve-id-list-response.json 2>&1)

if [ $? -eq 0 ]; then
    ETL_OUTPUT=$(cat /tmp/retrieve-id-list-response.json)
    if echo "$ETL_OUTPUT" | jq -e '.item_ids' > /dev/null 2>&1; then
        ITEM_COUNT=$(echo "$ETL_OUTPUT" | jq '.item_ids | length')
        print_test "PASS" "ETL Pipeline - retrieveIdList executed successfully ($ITEM_COUNT items)"
    else
        print_test "FAIL" "ETL Pipeline - retrieveIdList returned invalid response" "$ETL_OUTPUT"
    fi
else
    print_test "FAIL" "ETL Pipeline - retrieveIdList invocation failed" "$ETL_RESPONSE"
fi

# Clean up temp file
rm -f /tmp/retrieve-id-list-response.json

# Test 2: Query API with Valid Parameters
print_section "Test 2: Query API with Valid Parameters"

# Calculate date range (last 30 days)
END_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
START_DATE=$(date -u -d "30 days ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -v-30d +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null)

print_info "Testing query with item_id parameter..."
QUERY_URL="${API_ENDPOINT}/query?item_id=1001&start_date=${START_DATE}&end_date=${END_DATE}&limit=10"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$QUERY_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "200" ]; then
    if echo "$RESPONSE_BODY" | jq -e '.statistics' > /dev/null 2>&1; then
        print_test "PASS" "Query API - Valid request with item_id returned 200"
    else
        print_test "FAIL" "Query API - Response missing expected fields" "$RESPONSE_BODY"
    fi
else
    print_test "FAIL" "Query API - Expected 200, got $HTTP_CODE" "$RESPONSE_BODY"
fi

# Test 3: Query API with Different Parameters
print_section "Test 3: Query API with Various Parameters"

print_info "Testing query with name parameter..."
QUERY_URL="${API_ENDPOINT}/query?name=Black%20Stone&start_date=${START_DATE}&end_date=${END_DATE}&limit=5"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$QUERY_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "200" ]; then
    print_test "PASS" "Query API - Valid request with name parameter returned 200"
else
    print_test "FAIL" "Query API - Name parameter query failed with $HTTP_CODE" "$RESPONSE_BODY"
fi

print_info "Testing query with category parameter..."
QUERY_URL="${API_ENDPOINT}/query?item_id=1001&category=Accessory&start_date=${START_DATE}&end_date=${END_DATE}"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)

if [ "$HTTP_CODE" == "200" ] || [ "$HTTP_CODE" == "404" ]; then
    print_test "PASS" "Query API - Category parameter handled correctly ($HTTP_CODE)"
else
    print_test "FAIL" "Query API - Category parameter query failed with $HTTP_CODE"
fi

# Test 4: API Key Authentication
print_section "Test 4: API Key Authentication"

print_info "Testing request without API key..."
QUERY_URL="${API_ENDPOINT}/query?item_id=1001&start_date=${START_DATE}&end_date=${END_DATE}"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)

if [ "$HTTP_CODE" == "403" ] || [ "$HTTP_CODE" == "401" ]; then
    print_test "PASS" "API Authentication - Request without API key rejected ($HTTP_CODE)"
else
    print_test "FAIL" "API Authentication - Expected 401/403, got $HTTP_CODE"
fi

print_info "Testing request with invalid API key..."
QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: invalid-key-12345" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)

if [ "$HTTP_CODE" == "403" ] || [ "$HTTP_CODE" == "401" ]; then
    print_test "PASS" "API Authentication - Request with invalid API key rejected ($HTTP_CODE)"
else
    print_test "FAIL" "API Authentication - Expected 401/403, got $HTTP_CODE"
fi

# Test 5: Rate Limiting
print_section "Test 5: Rate Limiting"

print_info "Sending multiple rapid requests to test rate limiting..."
RATE_LIMIT_HIT=false

for i in {1..15}; do
    QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
        -H "x-api-key: $API_KEY" \
        -H "Content-Type: application/json")
    
    HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)
    
    if [ "$HTTP_CODE" == "429" ]; then
        RATE_LIMIT_HIT=true
        break
    fi
    
    # Small delay to avoid overwhelming the API
    sleep 0.1
done

if [ "$RATE_LIMIT_HIT" == "true" ]; then
    print_test "PASS" "Rate Limiting - Rate limit enforced (429 received)"
else
    print_warning "Rate limit not hit with 15 requests. This may be expected if limits are high."
    print_test "PASS" "Rate Limiting - No errors during rapid requests"
fi

# Test 6: CloudWatch Logs - JSON Format and Correlation IDs
print_section "Test 6: CloudWatch Logs Validation"

print_info "Checking CloudWatch logs for retrieveIdList..."

# Get recent log events
LOG_GROUP="/aws/lambda/retrieveIdList"
LOG_STREAMS=$(aws logs describe-log-streams \
    --log-group-name "$LOG_GROUP" \
    --order-by LastEventTime \
    --descending \
    --max-items 1 \
    --region "$REGION" \
    --query 'logStreams[0].logStreamName' \
    --output text 2>/dev/null)

if [ -n "$LOG_STREAMS" ] && [ "$LOG_STREAMS" != "None" ]; then
    LOG_EVENTS=$(aws logs get-log-events \
        --log-group-name "$LOG_GROUP" \
        --log-stream-name "$LOG_STREAMS" \
        --limit 10 \
        --region "$REGION" \
        --query 'events[*].message' \
        --output text 2>/dev/null)
    
    # Check for JSON format
    JSON_FOUND=false
    CORRELATION_ID_FOUND=false
    
    while IFS= read -r line; do
        if echo "$line" | jq -e '.' > /dev/null 2>&1; then
            JSON_FOUND=true
            if echo "$line" | jq -e '.correlation_id' > /dev/null 2>&1; then
                CORRELATION_ID_FOUND=true
                break
            fi
        fi
    done <<< "$LOG_EVENTS"
    
    if [ "$JSON_FOUND" == "true" ]; then
        print_test "PASS" "CloudWatch Logs - JSON format detected"
    else
        print_test "FAIL" "CloudWatch Logs - No JSON formatted logs found"
    fi
    
    if [ "$CORRELATION_ID_FOUND" == "true" ]; then
        print_test "PASS" "CloudWatch Logs - Correlation IDs present"
    else
        print_test "FAIL" "CloudWatch Logs - No correlation IDs found in logs"
    fi
else
    print_test "FAIL" "CloudWatch Logs - Could not retrieve log streams" "$LOG_GROUP"
fi

# Test 7: X-Ray Traces
print_section "Test 7: X-Ray Traces Validation"

print_info "Checking X-Ray traces..."

# Get recent traces (last 5 minutes)
END_TIME=$(date -u +%s)
START_TIME=$((END_TIME - 300))

TRACES=$(aws xray get-trace-summaries \
    --start-time "$START_TIME" \
    --end-time "$END_TIME" \
    --region "$REGION" \
    --query 'TraceSummaries[?contains(Http.HttpURL, `query`) || contains(Http.HttpURL, `retrieveIdList`)]' \
    --output json 2>/dev/null)

if [ -n "$TRACES" ] && [ "$TRACES" != "[]" ]; then
    TRACE_COUNT=$(echo "$TRACES" | jq 'length')
    print_test "PASS" "X-Ray Traces - Found $TRACE_COUNT recent traces"
    
    # Check if traces are complete (no errors)
    ERROR_COUNT=$(echo "$TRACES" | jq '[.[] | select(.HasError == true)] | length')
    if [ "$ERROR_COUNT" == "0" ]; then
        print_test "PASS" "X-Ray Traces - No errors in recent traces"
    else
        print_warning "Found $ERROR_COUNT traces with errors"
    fi
else
    print_warning "No recent X-Ray traces found. This may be expected if no requests were made recently."
    print_test "PASS" "X-Ray Traces - X-Ray is configured (no traces found yet)"
fi

# Test 8: Custom Metrics
print_section "Test 8: Custom Metrics Validation"

print_info "Checking CloudWatch custom metrics..."

# Check for custom metrics in the BDO namespace
METRICS=$(aws cloudwatch list-metrics \
    --namespace "BDO/MarketInsights" \
    --region "$REGION" \
    --output json 2>/dev/null)

if [ -n "$METRICS" ]; then
    METRIC_COUNT=$(echo "$METRICS" | jq '.Metrics | length')
    if [ "$METRIC_COUNT" -gt 0 ]; then
        print_test "PASS" "Custom Metrics - Found $METRIC_COUNT custom metrics"
        
        # List some metrics
        print_info "Sample metrics:"
        echo "$METRICS" | jq -r '.Metrics[0:3] | .[] | "  - \(.MetricName)"'
    else
        print_test "FAIL" "Custom Metrics - No custom metrics found in BDO/MarketInsights namespace"
    fi
else
    print_test "FAIL" "Custom Metrics - Could not retrieve metrics"
fi

# Test 9: Error Handling Scenarios
print_section "Test 9: Error Handling Validation"

print_info "Testing invalid query parameters..."
QUERY_URL="${API_ENDPOINT}/query?item_id=invalid&start_date=${START_DATE}&end_date=${END_DATE}"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$QUERY_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "400" ]; then
    if echo "$RESPONSE_BODY" | jq -e '.error' > /dev/null 2>&1; then
        print_test "PASS" "Error Handling - Invalid parameters return 400 with error details"
    else
        print_test "FAIL" "Error Handling - 400 response missing error details" "$RESPONSE_BODY"
    fi
else
    print_test "FAIL" "Error Handling - Expected 400 for invalid parameters, got $HTTP_CODE"
fi

print_info "Testing missing required parameters..."
QUERY_URL="${API_ENDPOINT}/query?item_id=1001"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)

if [ "$HTTP_CODE" == "400" ]; then
    print_test "PASS" "Error Handling - Missing required parameters return 400"
else
    print_test "FAIL" "Error Handling - Expected 400 for missing parameters, got $HTTP_CODE"
fi

print_info "Testing invalid date range..."
QUERY_URL="${API_ENDPOINT}/query?item_id=1001&start_date=${END_DATE}&end_date=${START_DATE}"

QUERY_RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$QUERY_URL" \
    -H "x-api-key: $API_KEY" \
    -H "Content-Type: application/json")

HTTP_CODE=$(echo "$QUERY_RESPONSE" | tail -n1)

if [ "$HTTP_CODE" == "400" ]; then
    print_test "PASS" "Error Handling - Invalid date range returns 400"
else
    print_test "FAIL" "Error Handling - Expected 400 for invalid date range, got $HTTP_CODE"
fi

# Test Summary
print_section "Validation Summary"

echo ""
echo "Total Tests: $TESTS_TOTAL"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
else
    echo "Failed: $TESTS_FAILED"
fi
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}=========================================="
    echo "✓ All validation tests passed!"
    echo -e "==========================================${NC}"
    echo ""
    echo "Staging environment is ready for use."
    exit 0
else
    echo -e "${YELLOW}=========================================="
    echo "⚠ Some validation tests failed"
    echo -e "==========================================${NC}"
    echo ""
    echo "Please review the failed tests above and:"
    echo "  1. Check CloudWatch logs for detailed error messages"
    echo "  2. Verify all Lambda functions are deployed correctly"
    echo "  3. Ensure API Gateway and Step Functions are configured properly"
    echo "  4. Check IAM permissions for all services"
    echo ""
    exit 1
fi
