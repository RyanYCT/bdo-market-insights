#!/bin/bash

# BDO Market Insights - CloudWatch Alarms Deployment Script
# This script deploys CloudWatch alarms for monitoring the BDO Market Insights system

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to display usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy CloudWatch alarms for BDO Market Insights

OPTIONS:
    -e, --environment ENV       Environment (dev, staging, prod) [required]
    -r, --region REGION         AWS region (default: us-east-1)
    -m, --email EMAIL           Email address for alarm notifications
    -h, --help                  Display this help message

EXAMPLES:
    # Deploy to dev environment with email notifications
    $0 --environment dev --email devops@example.com

    # Deploy to production in a specific region
    $0 --environment prod --region us-west-2 --email alerts@example.com

    # Deploy without email notifications
    $0 --environment staging

EOF
    exit 1
}

# Default values
REGION="us-east-1"
ENVIRONMENT=""
EMAIL=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -m|--email)
            EMAIL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate required parameters
if [ -z "$ENVIRONMENT" ]; then
    print_error "Environment is required"
    usage
fi

# Validate environment value
if [[ ! "$ENVIRONMENT" =~ ^(dev|staging|prod)$ ]]; then
    print_error "Environment must be one of: dev, staging, prod"
    exit 1
fi

# Set stack name
STACK_NAME="bdo-cloudwatch-alarms-${ENVIRONMENT}"

# Set Lambda function names based on environment
RETRIEVE_ID_LIST_FUNCTION="retrieveIdList-${ENVIRONMENT}"
FETCH_DATA_FUNCTION="fetchData-${ENVIRONMENT}"
CLEAN_DATA_FUNCTION="cleanData-${ENVIRONMENT}"
STORE_DATA_FUNCTION="storeData-${ENVIRONMENT}"
QUERY_DATA_FUNCTION="queryData-${ENVIRONMENT}"
ANALYZE_DATA_FUNCTION="analyzeData-${ENVIRONMENT}"

print_info "Deploying CloudWatch alarms for BDO Market Insights"
print_info "Environment: $ENVIRONMENT"
print_info "Region: $REGION"
print_info "Stack Name: $STACK_NAME"

if [ -n "$EMAIL" ]; then
    print_info "Email notifications: $EMAIL"
else
    print_warning "No email address provided - alarms will be created without email notifications"
fi

# Build parameter overrides
PARAMETERS="Environment=${ENVIRONMENT}"
PARAMETERS="${PARAMETERS} RetrieveIdListFunctionName=${RETRIEVE_ID_LIST_FUNCTION}"
PARAMETERS="${PARAMETERS} FetchDataFunctionName=${FETCH_DATA_FUNCTION}"
PARAMETERS="${PARAMETERS} CleanDataFunctionName=${CLEAN_DATA_FUNCTION}"
PARAMETERS="${PARAMETERS} StoreDataFunctionName=${STORE_DATA_FUNCTION}"
PARAMETERS="${PARAMETERS} QueryDataFunctionName=${QUERY_DATA_FUNCTION}"
PARAMETERS="${PARAMETERS} AnalyzeDataFunctionName=${ANALYZE_DATA_FUNCTION}"

if [ -n "$EMAIL" ]; then
    PARAMETERS="${PARAMETERS} AlarmEmailAddress=${EMAIL}"
fi

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    print_error "AWS CLI is not installed. Please install it first."
    exit 1
fi

# Check if template file exists
TEMPLATE_FILE="cloudwatch-alarms-template.yaml"
if [ ! -f "$TEMPLATE_FILE" ]; then
    print_error "Template file not found: $TEMPLATE_FILE"
    print_error "Please run this script from the infrastructure directory"
    exit 1
fi

# Validate CloudFormation template
print_info "Validating CloudFormation template..."
if aws cloudformation validate-template \
    --template-body file://${TEMPLATE_FILE} \
    --region ${REGION} > /dev/null 2>&1; then
    print_info "Template validation successful"
else
    print_error "Template validation failed"
    exit 1
fi

# Check if stack exists
print_info "Checking if stack exists..."
if aws cloudformation describe-stacks \
    --stack-name ${STACK_NAME} \
    --region ${REGION} > /dev/null 2>&1; then
    print_info "Stack exists - updating..."
    ACTION="update"
else
    print_info "Stack does not exist - creating..."
    ACTION="create"
fi

# Deploy CloudFormation stack
print_info "Deploying CloudFormation stack..."
if aws cloudformation deploy \
    --template-file ${TEMPLATE_FILE} \
    --stack-name ${STACK_NAME} \
    --parameter-overrides ${PARAMETERS} \
    --capabilities CAPABILITY_NAMED_IAM \
    --region ${REGION} \
    --no-fail-on-empty-changeset; then
    print_info "Stack deployment successful"
else
    print_error "Stack deployment failed"
    exit 1
fi

# Get stack outputs
print_info "Retrieving stack outputs..."
TOPIC_ARN=$(aws cloudformation describe-stacks \
    --stack-name ${STACK_NAME} \
    --region ${REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicArn`].OutputValue' \
    --output text)

TOPIC_NAME=$(aws cloudformation describe-stacks \
    --stack-name ${STACK_NAME} \
    --region ${REGION} \
    --query 'Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicName`].OutputValue' \
    --output text)

print_info "Deployment complete!"
echo ""
print_info "Stack Outputs:"
echo "  SNS Topic ARN: ${TOPIC_ARN}"
echo "  SNS Topic Name: ${TOPIC_NAME}"
echo ""

if [ -n "$EMAIL" ]; then
    print_warning "IMPORTANT: Check your email (${EMAIL}) and confirm the SNS subscription"
    print_warning "You will not receive alarm notifications until you confirm the subscription"
fi

# List created alarms
print_info "Created alarms:"
aws cloudwatch describe-alarms \
    --alarm-name-prefix "bdo-" \
    --region ${REGION} \
    --query "MetricAlarms[?contains(AlarmName, '${ENVIRONMENT}')].AlarmName" \
    --output table

print_info "To view alarm status, run:"
echo "  aws cloudwatch describe-alarms --alarm-name-prefix \"bdo-\" --region ${REGION}"
echo ""
print_info "To test an alarm, see the CLOUDWATCH_ALARMS_README.md file"
