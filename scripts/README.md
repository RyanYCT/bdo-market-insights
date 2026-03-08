# Deployment Scripts

This directory contains shell scripts for deploying the BDO Market Insights application to AWS.

## Scripts Overview

### setup-iam-roles.sh
Sets up IAM roles and permissions required for the BDO Market Insights application.

**Usage:**
```bash
./scripts/setup-iam-roles.sh
```

**What it does:**
- Creates Step Functions execution role
- Creates EventBridge Scheduler role
- Adds CloudWatch metrics permissions to all Lambda functions
- Configures necessary IAM policies

**When to run:**
- Before initial deployment
- After deploying new Lambda functions
- When Lambda functions need CloudWatch metrics permissions

**Environment Variables:**
```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
export ENVIRONMENT=staging  # or production
```

**Note:** This script requires IAM admin permissions to create and modify roles.

### deploy-all.sh
Orchestrates the complete deployment process:
1. Deploy Lambda Layer
2. Deploy all Lambda Functions
3. Update Step Functions
4. Deploy API Gateway
5. Deploy CloudWatch Alarms

**Usage:**
```bash
./scripts/deploy-all.sh
```

### deploy-layer.sh
Builds and deploys the Lambda Layer containing common code and dependencies.

**Usage:**
```bash
./scripts/deploy-layer.sh
```

**Output:**
- Publishes layer to AWS Lambda
- Saves version number to `lambda_layer/layer-version.txt`

### deploy-function.sh
Deploys a single Lambda function with blue-green deployment strategy.

**Usage:**
```bash
./scripts/deploy-function.sh <function-name>
```

**Example:**
```bash
./scripts/deploy-function.sh retrieveIdList
```

**Features:**
- Builds deployment package
- Updates function code
- Attaches latest layer version
- Runs smoke test
- Updates 'live' alias
- Automatic rollback on failure

### deploy-step-functions.sh
Updates the Step Functions state machine definition.

**Usage:**
```bash
./scripts/deploy-step-functions.sh
```

**Features:**
- Replaces placeholders (REGION, ACCOUNT)
- Validates JSON syntax
- Updates existing state machine

### deploy-api-gateway.sh
Deploys API Gateway using CloudFormation template.

**Usage:**
```bash
./scripts/deploy-api-gateway.sh
```

**Features:**
- Deploys CloudFormation stack
- Creates API deployment
- Outputs API endpoint URL

### rollback-function.sh
Rolls back a Lambda function to a previous version.

**Usage:**
```bash
# Rollback to previous version
./scripts/rollback-function.sh <function-name>

# Rollback to specific version
./scripts/rollback-function.sh <function-name> <version-number>
```

**Example:**
```bash
./scripts/rollback-function.sh retrieveIdList
./scripts/rollback-function.sh retrieveIdList 5
```

**Features:**
- Interactive confirmation
- Automatic previous version detection
- Updates 'live' alias
- Verification of rollback

### notify-deployment.sh
Sends deployment notifications to configured channels.

**Usage:**
```bash
./scripts/notify-deployment.sh <status> <message> [commit-sha] [author]
```

**Example:**
```bash
./scripts/notify-deployment.sh success "Deployment completed" abc123 "John Doe"
```

**Supported Channels:**
- Slack (via webhook)
- Email (via AWS SES)
- SNS (via AWS SNS topic)

## Prerequisites

### Required Tools
- AWS CLI v2
- Python 3.14+
- jq (for JSON parsing)
- zip

### Environment Variables

Set these before running scripts:

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
```

### Optional (for notifications):
```bash
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
export NOTIFICATION_EMAIL=team@example.com
export SNS_TOPIC_ARN=arn:aws:sns:region:account:topic-name
```

## Making Scripts Executable

Before first use:

```bash
chmod +x scripts/*.sh
```

## Common Workflows

### Initial Deployment

```bash
# 1. Setup IAM roles and permissions
./scripts/setup-iam-roles.sh

# 2. Deploy layer
./scripts/deploy-layer.sh

# 3. Deploy all functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    ./scripts/deploy-function.sh $func
done

# 4. Add CloudWatch permissions (if not done in step 1)
./scripts/setup-iam-roles.sh

# 5. Deploy infrastructure
./scripts/deploy-step-functions.sh
./scripts/deploy-api-gateway.sh
cd infrastructure && ./deploy-alarms.sh && cd ..
```

### Add CloudWatch Metrics Permissions

If you encounter "Failed to emit metric" errors:

```bash
# Run the setup script to add permissions to all Lambda functions
./scripts/setup-iam-roles.sh
```

### Update Single Function

```bash
./scripts/deploy-function.sh retrieveIdList
```

### Update All Functions

```bash
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    ./scripts/deploy-function.sh $func
done
```

### Emergency Rollback

```bash
# Rollback single function
./scripts/rollback-function.sh retrieveIdList

# Rollback all functions
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    ./scripts/rollback-function.sh $func
done
```

## Script Behavior

### Error Handling

All scripts use `set -e` to exit on error. This ensures:
- Failed commands stop execution
- Errors are not silently ignored
- Deployment state remains consistent

### Logging

Scripts output detailed logs including:
- Current operation
- Progress indicators
- Success/failure messages
- Relevant ARNs and IDs

### Idempotency

Scripts are designed to be idempotent:
- Can be run multiple times safely
- Update existing resources
- Don't fail if resources already exist

## Troubleshooting

### Script Fails: "Permission denied"

Make scripts executable:
```bash
chmod +x scripts/*.sh
```

### Script Fails: "AWS CLI not found"

Install AWS CLI:
```bash
# macOS
brew install awscli

# Linux
pip install awscli

# Windows
# Download from https://aws.amazon.com/cli/
```

### Script Fails: "Function not found"

Create the function first using CloudFormation or AWS Console.

### Deployment Hangs

Check AWS service status and CloudWatch logs.

### Rollback Fails

Manually update alias:
```bash
aws lambda update-alias \
  --function-name <function-name> \
  --name live \
  --function-version <version>
```

## Best Practices

1. **Test locally first**
   ```bash
   make test-all
   make lint
   ```

2. **Deploy to staging first**
   ```bash
   export AWS_REGION=us-east-1
   export ENVIRONMENT=staging
   ./scripts/deploy-all.sh
   ```

3. **Monitor during deployment**
   - Watch CloudWatch logs
   - Check CloudWatch metrics
   - Monitor alarms

4. **Keep rollback ready**
   - Know current version numbers
   - Have rollback commands ready
   - Monitor for issues

5. **Document changes**
   - Update CHANGELOG
   - Document any issues
   - Share with team

## Support

For issues with deployment scripts:
1. Check script output for errors
2. Review CloudWatch logs
3. Check AWS service status
4. Review DEPLOYMENT.md
5. Contact DevOps team
