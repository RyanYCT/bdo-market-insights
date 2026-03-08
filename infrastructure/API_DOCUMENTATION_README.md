# API Documentation Deployment Guide

This guide explains how to deploy and manage the OpenAPI specification and Swagger UI for the BDO Market Insights API.

## Overview

The API documentation consists of:

1. **OpenAPI 3.0 Specification** (`openapi-spec.yaml`) - Machine-readable API definition
2. **Swagger UI** (`swagger-ui/index.html`) - Interactive API documentation interface

The documentation is hosted on S3 and served through API Gateway endpoints:
- `/docs` - Interactive Swagger UI
- `/openapi.yaml` - OpenAPI specification file

## Prerequisites

- AWS CLI configured with appropriate credentials
- API Gateway CloudFormation stack deployed (`bdo-api-gateway-{environment}`)

## Deployment

### Deploy API Gateway Stack First

Before deploying documentation, ensure the API Gateway stack is deployed:

```bash
# Deploy API Gateway stack
aws cloudformation deploy \
  --template-file infrastructure/api-gateway-template.yaml \
  --stack-name bdo-api-gateway-dev \
  --parameter-overrides \
    Environment=dev \
    AllowedOrigins="https://dev.example.com" \
    StepFunctionsArn="arn:aws:states:us-east-1:123456789012:stateMachine:bdo-query-pipeline-dev" \
  --capabilities CAPABILITY_NAMED_IAM
```

This creates:
- API Gateway REST API
- S3 bucket for documentation (`bdo-api-docs-{environment}-{account-id}`)
- `/docs` and `/openapi.yaml` endpoints

### Deploy Documentation Files

```bash
# Make script executable
chmod +x infrastructure/deploy-api-docs.sh

# Deploy to dev environment
./infrastructure/deploy-api-docs.sh dev us-east-1

# Deploy to staging
./infrastructure/deploy-api-docs.sh staging us-east-1

# Deploy to production
./infrastructure/deploy-api-docs.sh prod us-east-1
```

### What the Deployment Script Does

1. Validates AWS credentials and environment
2. Checks if the S3 bucket exists
3. Retrieves the API Gateway endpoint URL
4. Updates the OpenAPI spec with environment-specific URLs
5. Uploads the OpenAPI specification to S3
6. Uploads the Swagger UI HTML to S3
7. Creates an error page for 404s
8. Displays the documentation URLs

## Accessing Documentation

After deployment, you can access:

### Swagger UI (Interactive Documentation)

```
https://{api-id}.execute-api.{region}.amazonaws.com/{environment}/docs
```

Example:
```
https://abc123xyz.execute-api.us-east-1.amazonaws.com/dev/docs
```

Features:
- Interactive API testing
- Request/response examples
- Schema definitions
- API key management (stored in browser localStorage)

### OpenAPI Specification

```
https://{api-id}.execute-api.{region}.amazonaws.com/{environment}/openapi.yaml
```

Example:
```
https://abc123xyz.execute-api.us-east-1.amazonaws.com/dev/openapi.yaml
```

Use this URL to:
- Import into API testing tools (Postman, Insomnia)
- Generate client SDKs
- Integrate with API management platforms

## Using Swagger UI

### Setting Your API Key

1. Open the Swagger UI URL in your browser
2. Scroll to the "API Key Configuration" section
3. Enter your API key
4. Click "Save"

The API key is stored in your browser's localStorage and will be automatically included in all test requests.

### Testing Endpoints

1. Expand an endpoint (e.g., `GET /query`)
2. Click "Try it out"
3. Fill in the required parameters
4. Click "Execute"
5. View the response below

### Example Test Request

```
GET /query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z&limit=100
```

## Updating Documentation

### Update OpenAPI Specification

1. Edit `infrastructure/openapi-spec.yaml`
2. Validate the spec:
   ```bash
   # Using swagger-cli (install: npm install -g @apidevtools/swagger-cli)
   swagger-cli validate infrastructure/openapi-spec.yaml
   ```
3. Redeploy:
   ```bash
   ./infrastructure/deploy-api-docs.sh dev us-east-1
   ```

### Update Swagger UI

1. Edit `infrastructure/swagger-ui/index.html`
2. Redeploy:
   ```bash
   ./infrastructure/deploy-api-docs.sh dev us-east-1
   ```

## Manual S3 Upload

If you prefer to upload files manually:

```bash
# Set variables
ENVIRONMENT=dev
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="bdo-api-docs-${ENVIRONMENT}-${ACCOUNT_ID}"

# Upload OpenAPI spec
aws s3 cp infrastructure/openapi-spec.yaml \
  s3://${BUCKET_NAME}/openapi.yaml \
  --content-type "application/x-yaml" \
  --cache-control "public, max-age=300"

# Upload Swagger UI
aws s3 cp infrastructure/swagger-ui/index.html \
  s3://${BUCKET_NAME}/index.html \
  --content-type "text/html" \
  --cache-control "public, max-age=300"
```

## Troubleshooting

### Documentation Not Loading

1. **Check S3 bucket exists:**
   ```bash
   aws s3 ls s3://bdo-api-docs-dev-{account-id}
   ```

2. **Verify files are uploaded:**
   ```bash
   aws s3 ls s3://bdo-api-docs-dev-{account-id}/ --recursive
   ```

3. **Check bucket policy allows public read:**
   ```bash
   aws s3api get-bucket-policy --bucket bdo-api-docs-dev-{account-id}
   ```

### CORS Errors

If you see CORS errors in the browser console:

1. Check the S3 bucket CORS configuration
2. Verify the API Gateway CORS settings
3. Ensure the `Access-Control-Allow-Origin` header is set correctly

### OpenAPI Spec Not Found

1. Verify the file was uploaded:
   ```bash
   aws s3 ls s3://bdo-api-docs-dev-{account-id}/openapi.yaml
   ```

2. Check the Swagger UI is pointing to the correct URL
3. Verify the API Gateway `/openapi.yaml` endpoint is configured

### API Key Not Working in Swagger UI

1. Check browser console for errors
2. Verify localStorage is enabled in your browser
3. Clear localStorage and re-enter the API key:
   ```javascript
   // In browser console
   localStorage.removeItem('bdo-api-key');
   ```

## Validation

### Validate OpenAPI Specification

Using swagger-cli:
```bash
npm install -g @apidevtools/swagger-cli
swagger-cli validate infrastructure/openapi-spec.yaml
```

Using online validator:
- Visit https://editor.swagger.io/
- Paste the contents of `openapi-spec.yaml`
- Check for validation errors

### Test Documentation Endpoints

```bash
# Get API endpoint
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name bdo-api-gateway-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

# Test Swagger UI endpoint
curl -I "${API_ENDPOINT}/docs"

# Test OpenAPI spec endpoint
curl "${API_ENDPOINT}/openapi.yaml"
```

## Best Practices

1. **Version Control**: Keep `openapi-spec.yaml` in version control
2. **Validation**: Always validate the OpenAPI spec before deploying
3. **Examples**: Include realistic examples in the spec
4. **Descriptions**: Provide clear descriptions for all endpoints and parameters
5. **Error Responses**: Document all possible error responses
6. **Changelog**: Maintain a changelog for API changes

## Security Considerations

1. **Public Access**: The documentation endpoints (`/docs` and `/openapi.yaml`) are publicly accessible without API keys
2. **API Keys**: Never hardcode API keys in the documentation
3. **Sensitive Data**: Don't include sensitive data in examples
4. **CORS**: Only allow trusted origins in production

## Integration with CI/CD

Add documentation deployment to your CI/CD pipeline:

```yaml
# Example GitHub Actions workflow
- name: Deploy API Documentation
  run: |
    ./infrastructure/deploy-api-docs.sh ${{ env.ENVIRONMENT }} us-east-1
  env:
    ENVIRONMENT: ${{ github.ref == 'refs/heads/main' && 'prod' || 'dev' }}
```

## Cleanup

To remove the documentation:

```bash
# Delete files from S3
BUCKET_NAME="bdo-api-docs-dev-${ACCOUNT_ID}"
aws s3 rm s3://${BUCKET_NAME}/ --recursive

# The bucket itself will be deleted when the CloudFormation stack is deleted
aws cloudformation delete-stack --stack-name bdo-api-gateway-dev
```

## Additional Resources

- [OpenAPI Specification](https://swagger.io/specification/)
- [Swagger UI Documentation](https://swagger.io/tools/swagger-ui/)
- [AWS API Gateway Documentation](https://docs.aws.amazon.com/apigateway/)
- [AWS S3 Static Website Hosting](https://docs.aws.amazon.com/AmazonS3/latest/userguide/WebsiteHosting.html)

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review CloudWatch logs for API Gateway
3. Check S3 bucket access logs
4. Contact the development team with the correlation_id from error responses
