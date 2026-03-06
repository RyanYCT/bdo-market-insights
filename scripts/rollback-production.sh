#!/bin/bash
# Rollback Production Deployment
# This script rolls back Lambda functions to previous versions

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check arguments
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: Rollback file required${NC}"
    echo "Usage: ./rollback-production.sh <rollback-file>"
    echo ""
    echo "Rollback files are created during deployment and stored in /tmp/"
    echo "Example: ./rollback-production.sh /tmp/bdo-production-rollback-20240315-143022.txt"
    exit 1
fi

ROLLBACK_FILE=$1
REGION="${AWS_REGION:-us-east-1}"

# Function to print messages
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

# Verify rollback file exists
if [ ! -f "$ROLLBACK_FILE" ]; then
    print_error "Rollback file not found: $ROLLBACK_FILE"
    exit 1
fi

print_section "BDO Market Insights - Production Rollback"

echo ""
echo "Rollback File: $ROLLBACK_FILE"
echo "Region: $REGION"
echo ""
echo -e "${YELLOW}WARNING: This will rollback all Lambda functions to previous versions!${NC}"
echo ""

# Display rollback information
echo "Functions to rollback:"
grep -v "^#" "$ROLLBACK_FILE" | grep -v "^$" | while IFS=: read -r FUNCTION OLD_VERSION NEW_VERSION; do
    echo "  $FUNCTION: v$NEW_VERSION → v$OLD_VERSION"
done

echo ""
read -p "Are you sure you want to rollback? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Rollback cancelled."
    exit 0
fi

# Perform rollback
print_section "Rolling Back Lambda Functions"

ROLLBACK_COUNT=0
FAILED_COUNT=0

grep -v "^#" "$ROLLBACK_FILE" | grep -v "^$" | while IFS=: read -r FUNCTION OLD_VERSION NEW_VERSION; do
    echo ""
    echo "Rolling back $FUNCTION..."
    echo "  Current: v$NEW_VERSION"
    echo "  Target: v$OLD_VERSION"
    
    # Update alias to point to old version
    if aws lambda update-alias \
        --function-name "$FUNCTION" \
        --name live \
        --function-version "$OLD_VERSION" \
        --region "$REGION" > /dev/null 2>&1; then
        
        print_success "$FUNCTION rolled back successfully"
        ROLLBACK_COUNT=$((ROLLBACK_COUNT + 1))
    else
        print_error "$FUNCTION rollback failed"
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
done

# Summary
print_section "Rollback Complete"

echo ""
if [ $FAILED_COUNT -eq 0 ]; then
    echo -e "${GREEN}All functions rolled back successfully!${NC}"
else
    echo -e "${YELLOW}Rollback completed with $FAILED_COUNT failures${NC}"
    echo "Please check CloudWatch logs and AWS Console for details."
fi

echo ""
echo "Next steps:"
echo "  1. Verify Lambda functions in AWS Console"
echo "  2. Test API Gateway endpoints"
echo "  3. Check CloudWatch logs for errors"
echo "  4. Monitor CloudWatch metrics"
echo "  5. Investigate root cause of deployment issues"
echo ""

print_success "Rollback process completed"

exit 0
