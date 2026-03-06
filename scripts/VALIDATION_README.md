# Staging Validation Scripts

This directory contains scripts for validating the BDO Market Insights staging environment after deployment.

## Available Scripts

### 1. validate-staging.sh (Linux/macOS)
Bash script for validating the staging environment on Unix-like systems.

**Usage:**
```bash
bash scripts/validate-staging.sh
```

**Requirements:**
- AWS CLI v2
- curl
- jq (JSON processor)
- Bash 4.0+

### 2. validate-staging.bat (Windows)
Batch script for validating the staging environment on Windows.

**Usage:**
```cmd
scripts\validate-staging.bat
```

**Requirements:**
- AWS CLI v2
- curl (available in Git Bash or standalone)
- jq (download from https://stedolan.github.io/jq/download/)
- Windows Command Prompt or PowerShell

### 3. validate_staging.py (Cross-platform)
Python script for validating the staging environment. Works on all platforms.

**Usage:**
```bash
# Default (staging environment, us-east-1 region)
python scripts/validate_staging.py

# Custom environment and region
python scripts/validate_staging.py --environment prod --region us-west-2
```

**Requirements:**
- Python 3.8+
- boto3 (AWS SDK for Python)
- requests (HTTP library)

**Installation:**
```bash
pip install boto3 requests
```

## What Gets Validated

All scripts validate the following aspects of the staging environment:

1. **ETL Pipeline Execution**
   - Manual invocation of retrieveIdList Lambda
   - Validates response format and data

2. **Query API Functionality**
   - Valid requests with various parameters (item_id, name, category)
   - Response format and structure
   - Different parameter combinations

3. **API Authentication**
   - Requests without API key (should be rejected)
   - Requests with invalid API key (should be rejected)
   - Requests with valid API key (should succeed)

4. **Rate Limiting**
   - Multiple rapid requests to test rate limiting
   - Verification of 429 (Too Many Requests) responses

5. **CloudWatch Logs**
   - JSON format validation
   - Correlation ID presence
   - Structured logging verification

6. **X-Ray Traces**
   - Trace generation verification
   - Complete execution paths
   - Error detection in traces

7. **Custom CloudWatch Metrics**
   - Metric emission verification
   - Namespace validation (BDO/MarketInsights)
   - Metric count and types

8. **Error Handling**
   - Invalid query parameters (should return 400)
   - Missing required parameters (should return 400)
   - Invalid date ranges (should return 400)
   - Error response format validation

## Prerequisites

### AWS Configuration

Ensure your AWS credentials are configured:

```bash
# Configure AWS CLI
aws configure

# Or set environment variables
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

### Required AWS Permissions

The validation scripts require the following AWS permissions:

- `lambda:InvokeFunction` - To invoke Lambda functions
- `logs:DescribeLogStreams` - To list CloudWatch log streams
- `logs:GetLogEvents` - To read CloudWatch logs
- `xray:GetTraceSummaries` - To retrieve X-Ray traces
- `cloudwatch:ListMetrics` - To list custom metrics
- `cloudformation:DescribeStacks` - To get stack outputs
- `apigateway:GetApiKey` - To retrieve API key values
- `sts:GetCallerIdentity` - To verify AWS credentials

### Installing Dependencies

**For Bash/Batch scripts:**

1. Install AWS CLI v2:
   - https://aws.amazon.com/cli/

2. Install curl:
   - Linux: Usually pre-installed
   - macOS: Pre-installed
   - Windows: Download from https://curl.se/windows/ or use Git Bash

3. Install jq:
   - Linux: `sudo apt-get install jq` or `sudo yum install jq`
   - macOS: `brew install jq`
   - Windows: Download from https://stedolan.github.io/jq/download/

**For Python script:**

```bash
# Install Python dependencies
pip install boto3 requests

# Or use requirements file
pip install -r requirements-dev.txt
```

## Running the Validation

### Quick Start

**Linux/macOS:**
```bash
# Make script executable
chmod +x scripts/validate-staging.sh

# Run validation
bash scripts/validate-staging.sh
```

**Windows:**
```cmd
# Run validation
scripts\validate-staging.bat
```

**Python (all platforms):**
```bash
# Run validation
python scripts/validate_staging.py
```

### Custom Configuration

**Environment Variables:**

```bash
# Set custom environment
export ENVIRONMENT=staging
export AWS_REGION=us-east-1

# Run validation
bash scripts/validate-staging.sh
```

**Python Arguments:**

```bash
# Validate production environment in us-west-2
python scripts/validate_staging.py --environment prod --region us-west-2
```

## Understanding the Output

### Test Results

Each test displays a result:
- `✓ PASS` - Test passed successfully (green)
- `✗ FAIL` - Test failed (red)

### Summary

At the end, a summary is displayed:
```
==========================================
Validation Summary
==========================================

Total Tests: 15
Passed: 14
Failed: 1
```

### Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

## Troubleshooting

### Common Issues

**Issue: "AWS CLI not found"**
- Solution: Install AWS CLI v2 from https://aws.amazon.com/cli/

**Issue: "AWS credentials invalid"**
- Solution: Run `aws configure` to set up credentials

**Issue: "Could not retrieve API endpoint"**
- Solution: Ensure API Gateway stack is deployed
- Check stack name: `bdo-api-gateway-staging`

**Issue: "jq not found"**
- Solution: Install jq JSON processor
  - Linux: `sudo apt-get install jq`
  - macOS: `brew install jq`
  - Windows: Download from https://stedolan.github.io/jq/download/

**Issue: "No log streams found"**
- Solution: Invoke Lambda functions to generate logs
- Wait a few minutes for logs to appear

**Issue: "No X-Ray traces found"**
- Solution: Make API requests to generate traces
- Ensure X-Ray is enabled on Lambda functions

**Issue: Python script fails with "ModuleNotFoundError"**
- Solution: Install required packages: `pip install boto3 requests`

### Detailed Debugging

For more detailed information:

1. **Check CloudWatch Logs:**
   ```bash
   aws logs tail /aws/lambda/retrieveIdList --follow
   ```

2. **View X-Ray Service Map:**
   - Open AWS X-Ray Console
   - Navigate to Service Map
   - Select appropriate time range

3. **Check Lambda Function Status:**
   ```bash
   aws lambda get-function --function-name retrieveIdList
   ```

4. **Verify API Gateway Configuration:**
   ```bash
   aws cloudformation describe-stacks --stack-name bdo-api-gateway-staging
   ```

## Next Steps

After successful validation:

1. Review the validation report
2. Document any issues found
3. Update configuration if needed
4. Run load testing (optional)
5. Schedule production deployment
6. Prepare rollback plan

## Additional Resources

- [Staging Validation Guide](../docs/STAGING_VALIDATION_GUIDE.md) - Detailed validation procedures
- [Deployment Guide](../docs/deployment/STAGING_DEPLOYMENT_GUIDE.md) - Staging deployment instructions
- [Architecture Documentation](../docs/architecture/) - System architecture details
- [README](../README.md) - Main project documentation

## Support

If you encounter issues:

1. Check the troubleshooting section above
2. Review CloudWatch logs for detailed errors
3. Consult the Staging Validation Guide
4. Check AWS service health dashboard

## Contributing

To improve the validation scripts:

1. Test your changes thoroughly
2. Update documentation
3. Ensure cross-platform compatibility
4. Add new test cases as needed
