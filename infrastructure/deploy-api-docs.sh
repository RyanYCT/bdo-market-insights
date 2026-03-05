#!/bin/bash

# Deploy API Documentation to S3
# This script uploads the OpenAPI specification and Swagger UI to the S3 bucket

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

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if environment is provided
if [ -z "$1" ]; then
    print_error "Environment not specified"
    echo "Usage: $0 <environment> [aws-region]"
    echo "Example: $0 dev us-east-1"
    exit 1
fi

ENVIRONMENT=$1
AWS_REGION=${2:-us-east-1}

print_info "Deploying API documentation for environment: $ENVIRONMENT"
print_info "AWS Region: $AWS_REGION"

# Get AWS Account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ -z "$ACCOUNT_ID" ]; then
    print_error "Failed to get AWS Account ID. Check your AWS credentials."
    exit 1
fi

print_info "AWS Account ID: $ACCOUNT_ID"

# Construct bucket name
BUCKET_NAME="bdo-api-docs-${ENVIRONMENT}-${ACCOUNT_ID}"
print_info "Target S3 bucket: $BUCKET_NAME"

# Check if bucket exists
if ! aws s3 ls "s3://${BUCKET_NAME}" --region "$AWS_REGION" 2>/dev/null; then
    print_error "S3 bucket $BUCKET_NAME does not exist"
    print_info "Please deploy the API Gateway CloudFormation stack first"
    exit 1
fi

# Get the API Gateway endpoint URL
API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
    --output text 2>/dev/null)

if [ -z "$API_ENDPOINT" ]; then
    print_warning "Could not retrieve API endpoint from CloudFormation stack"
    print_warning "OpenAPI spec will use placeholder URLs"
else
    print_info "API Endpoint: $API_ENDPOINT"
fi

# Update OpenAPI spec with actual server URLs
OPENAPI_SPEC="infrastructure/openapi-spec.yaml"
TEMP_SPEC="/tmp/openapi-spec-${ENVIRONMENT}.yaml"

if [ -f "$OPENAPI_SPEC" ]; then
    print_info "Updating OpenAPI spec with environment-specific URLs..."
    
    # Copy the spec to a temp file
    cp "$OPENAPI_SPEC" "$TEMP_SPEC"
    
    # Update server URLs if API endpoint is available
    if [ -n "$API_ENDPOINT" ]; then
        # Replace the servers section with actual endpoint
        sed -i.bak "s|url: https://api.example.com/dev|url: ${API_ENDPOINT}|g" "$TEMP_SPEC"
        sed -i.bak "s|url: https://api.example.com/staging|url: ${API_ENDPOINT}|g" "$TEMP_SPEC"
        sed -i.bak "s|url: https://api.example.com/prod|url: ${API_ENDPOINT}|g" "$TEMP_SPEC"
        rm -f "${TEMP_SPEC}.bak"
    fi
    
    SPEC_TO_UPLOAD="$TEMP_SPEC"
else
    print_error "OpenAPI spec file not found: $OPENAPI_SPEC"
    exit 1
fi

# Upload OpenAPI specification
print_info "Uploading OpenAPI specification..."
aws s3 cp "$SPEC_TO_UPLOAD" "s3://${BUCKET_NAME}/openapi.yaml" \
    --region "$AWS_REGION" \
    --content-type "application/x-yaml" \
    --cache-control "public, max-age=300" \
    --metadata "environment=${ENVIRONMENT}"

if [ $? -eq 0 ]; then
    print_info "✓ OpenAPI spec uploaded successfully"
else
    print_error "Failed to upload OpenAPI spec"
    exit 1
fi

# Upload Swagger UI
SWAGGER_UI_DIR="infrastructure/swagger-ui"
if [ -d "$SWAGGER_UI_DIR" ]; then
    print_info "Uploading Swagger UI..."
    
    aws s3 cp "${SWAGGER_UI_DIR}/index.html" "s3://${BUCKET_NAME}/index.html" \
        --region "$AWS_REGION" \
        --content-type "text/html" \
        --cache-control "public, max-age=300" \
        --metadata "environment=${ENVIRONMENT}"
    
    if [ $? -eq 0 ]; then
        print_info "✓ Swagger UI uploaded successfully"
    else
        print_error "Failed to upload Swagger UI"
        exit 1
    fi
else
    print_error "Swagger UI directory not found: $SWAGGER_UI_DIR"
    exit 1
fi

# Create a simple error page
print_info "Creating error page..."
cat > /tmp/error.html << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error - BDO Market Insights API</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #f5f5f5;
        }
        .error-container {
            text-align: center;
            padding: 40px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #dc3545;
            margin-bottom: 20px;
        }
        p {
            color: #666;
            margin-bottom: 30px;
        }
        a {
            color: #4990e2;
            text-decoration: none;
            font-weight: bold;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <h1>404 - Page Not Found</h1>
        <p>The page you're looking for doesn't exist.</p>
        <a href="/index.html">Go to API Documentation</a>
    </div>
</body>
</html>
EOF

aws s3 cp /tmp/error.html "s3://${BUCKET_NAME}/error.html" \
    --region "$AWS_REGION" \
    --content-type "text/html" \
    --cache-control "public, max-age=300"

rm -f /tmp/error.html

# Clean up temp files
rm -f "$TEMP_SPEC"

# Get the documentation URLs
DOCS_URL=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`APIDocsURL`].OutputValue' \
    --output text 2>/dev/null)

OPENAPI_URL=$(aws cloudformation describe-stacks \
    --stack-name "bdo-api-gateway-${ENVIRONMENT}" \
    --region "$AWS_REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`OpenAPISpecURL`].OutputValue' \
    --output text 2>/dev/null)

# Print summary
echo ""
print_info "=========================================="
print_info "API Documentation Deployment Complete!"
print_info "=========================================="
echo ""

if [ -n "$DOCS_URL" ]; then
    print_info "Swagger UI: $DOCS_URL"
else
    print_info "Swagger UI: https://<api-id>.execute-api.${AWS_REGION}.amazonaws.com/${ENVIRONMENT}/docs"
fi

if [ -n "$OPENAPI_URL" ]; then
    print_info "OpenAPI Spec: $OPENAPI_URL"
else
    print_info "OpenAPI Spec: https://<api-id>.execute-api.${AWS_REGION}.amazonaws.com/${ENVIRONMENT}/openapi.yaml"
fi

print_info "S3 Bucket: s3://${BUCKET_NAME}"
echo ""
print_info "You can now access the interactive API documentation through the Swagger UI URL above."
print_info "No API key is required to view the documentation."
echo ""
