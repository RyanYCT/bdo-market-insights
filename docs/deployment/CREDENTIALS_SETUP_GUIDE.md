# Credentials and Configuration Setup Guide

This guide explains how to securely configure AWS credentials and deployment settings for the BDO Market Insights project.

## 🔒 Security First

**CRITICAL:** Never commit credentials to version control!

- ✅ Configuration files are in `.gitignore`
- ✅ Use AWS CLI profiles (recommended)
- ✅ Use AWS SSO for organizations
- ✅ Store secrets in AWS Secrets Manager
- ❌ Never hardcode credentials in scripts
- ❌ Never commit `.env` or `deployment-config.sh` files

## Quick Start

### Option 1: Interactive Setup (Easiest)

```bash
# Run the interactive setup script
chmod +x scripts/setup-deployment-config.sh
./scripts/setup-deployment-config.sh
```

This script will:
1. Guide you through configuration
2. Create `config/deployment-config.sh`
3. Verify AWS access
4. Set proper file permissions

### Option 2: Manual Setup

```bash
# 1. Copy the example configuration
cp config/deployment-config.example.sh config/deployment-config.sh

# 2. Edit with your values
nano config/deployment-config.sh  # or vim, code, etc.

# 3. Set proper permissions
chmod 600 config/deployment-config.sh

# 4. Verify configuration
source config/deployment-config.sh
aws sts get-caller-identity
```

## AWS Credentials Configuration

### Method 1: AWS CLI Profile (RECOMMENDED)

This is the most secure method as credentials are stored in `~/.aws/credentials` outside your project directory.

#### Setup

```bash
# Configure AWS CLI profile
aws configure --profile bdo-staging

# You'll be prompted for:
# - AWS Access Key ID
# - AWS Secret Access Key
# - Default region (e.g., us-east-1)
# - Default output format (json)
```

#### In deployment-config.sh

```bash
export AWS_PROFILE="bdo-staging"
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
```

#### Verify

```bash
# Test the profile
aws sts get-caller-identity --profile bdo-staging

# Should output:
# {
#     "UserId": "AIDAI...",
#     "Account": "123456789012",
#     "Arn": "arn:aws:iam::123456789012:user/your-user"
# }
```

### Method 2: AWS SSO (RECOMMENDED for Organizations)

If your organization uses AWS SSO, this is the best method.

#### Setup

```bash
# Configure AWS SSO
aws configure sso --profile bdo-staging

# You'll be prompted for:
# - SSO start URL
# - SSO region
# - Account ID
# - Role name
# - CLI default region
# - CLI output format
```

#### In deployment-config.sh

```bash
export AWS_PROFILE="bdo-staging"
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
```

#### Before Each Deployment

```bash
# Login to AWS SSO (required before each deployment)
aws sso login --profile bdo-staging

# Then run deployment
./scripts/deploy-staging.sh
```

### Method 3: Environment Variables (NOT RECOMMENDED)

Only use this for testing or CI/CD environments.

#### In deployment-config.sh

```bash
export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
```

⚠️ **WARNING:** This stores credentials in your project directory. Use only for testing!

## Required Configuration Variables

### Minimal Configuration

```bash
# AWS Account
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
export AWS_PROFILE="bdo-staging"  # or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY

# Environment
export ENVIRONMENT="staging"
```

### Recommended Configuration

```bash
# AWS Account
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
export AWS_PROFILE="bdo-staging"

# Environment
export ENVIRONMENT="staging"

# API Gateway
export ALLOWED_CORS_ORIGINS="https://staging.example.com,https://staging-app.example.com"

# Monitoring
export ALARM_EMAIL="devops@example.com"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

# Lambda Configuration
export LAMBDA_TIMEOUT="300"
export LAMBDA_MEMORY_SIZE="512"
```

## Getting Your AWS Account ID

### Method 1: AWS CLI

```bash
aws sts get-caller-identity --query Account --output text
```

### Method 2: AWS Console

1. Log in to AWS Console
2. Click your username in top-right
3. Account ID is displayed in the dropdown

### Method 3: From IAM

1. Go to IAM Console
2. Account ID is shown at the top

## Environment-Specific Configuration

### Development Environment

```bash
# config/deployment-config.dev.sh
export ENVIRONMENT="dev"
export AWS_PROFILE="bdo-dev"
export AWS_REGION="us-east-1"
export ALLOWED_CORS_ORIGINS="http://localhost:3000"
```

### Staging Environment

```bash
# config/deployment-config.staging.sh
export ENVIRONMENT="staging"
export AWS_PROFILE="bdo-staging"
export AWS_REGION="us-east-1"
export ALLOWED_CORS_ORIGINS="https://staging.example.com"
```

### Production Environment

```bash
# config/deployment-config.prod.sh
export ENVIRONMENT="prod"
export AWS_PROFILE="bdo-prod"
export AWS_REGION="us-east-1"
export ALLOWED_CORS_ORIGINS="https://example.com,https://www.example.com"
```

## Verifying Your Configuration

### 1. Check Configuration File Exists

```bash
ls -la config/deployment-config.sh

# Should show:
# -rw------- 1 user user 1234 Mar 06 10:00 config/deployment-config.sh
# Note: 600 permissions (owner read/write only)
```

### 2. Load Configuration

```bash
source config/deployment-config.sh
```

### 3. Verify AWS Access

```bash
# Check AWS credentials
aws sts get-caller-identity

# Should output your account details
```

### 4. Verify Environment Variables

```bash
# Check all required variables are set
echo "Region: $AWS_REGION"
echo "Account: $AWS_ACCOUNT_ID"
echo "Environment: $ENVIRONMENT"
echo "Profile: $AWS_PROFILE"
```

### 5. Test AWS Permissions

```bash
# Test Lambda permissions
aws lambda list-functions --max-items 1

# Test CloudFormation permissions
aws cloudformation list-stacks --max-items 1

# Test Secrets Manager permissions
aws secretsmanager list-secrets --max-items 1
```

## CI/CD Configuration

### GitHub Actions

Store credentials as GitHub Secrets:

1. Go to repository Settings → Secrets and variables → Actions
2. Add secrets:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION`
   - `AWS_ACCOUNT_ID`

```yaml
# .github/workflows/deploy.yml
name: Deploy to Staging

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ secrets.AWS_REGION }}
      
      - name: Deploy
        env:
          AWS_ACCOUNT_ID: ${{ secrets.AWS_ACCOUNT_ID }}
          ENVIRONMENT: staging
        run: |
          ./scripts/deploy-staging.sh
```

### GitLab CI

Store credentials as GitLab CI/CD Variables:

1. Go to Settings → CI/CD → Variables
2. Add variables:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION`
   - `AWS_ACCOUNT_ID`

```yaml
# .gitlab-ci.yml
deploy:
  stage: deploy
  image: amazon/aws-cli
  script:
    - aws sts get-caller-identity
    - ./scripts/deploy-staging.sh
  only:
    - main
```

## Troubleshooting

### Configuration File Not Found

```bash
ERROR: config/deployment-config.sh not found
```

**Solution:**
```bash
# Run interactive setup
./scripts/setup-deployment-config.sh

# Or copy example manually
cp config/deployment-config.example.sh config/deployment-config.sh
```

### AWS Credentials Not Found

```bash
Unable to locate credentials
```

**Solution:**
```bash
# Check AWS CLI configuration
aws configure list

# Or configure profile
aws configure --profile bdo-staging
```

### Invalid AWS Account ID

```bash
ERROR: AWS_ACCOUNT_ID must be set
```

**Solution:**
```bash
# Get your account ID
aws sts get-caller-identity --query Account --output text

# Add to config file
echo 'export AWS_ACCOUNT_ID="123456789012"' >> config/deployment-config.sh
```

### Permission Denied

```bash
An error occurred (AccessDenied) when calling the ListFunctions operation
```

**Solution:** Your IAM user/role needs additional permissions. See [Required AWS Permissions](#required-aws-permissions).

### File Permissions Too Open

```bash
WARNING: UNPROTECTED PRIVATE KEY FILE!
```

**Solution:**
```bash
# Set proper permissions
chmod 600 config/deployment-config.sh
```

## Required AWS Permissions

Your IAM user/role needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:PublishLayerVersion",
        "lambda:GetFunction",
        "lambda:GetLayerVersion",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "iam:GetRole",
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DescribeStacks",
        "apigateway:*",
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:DescribeStateMachine",
        "logs:CreateLogGroup",
        "logs:PutRetentionPolicy",
        "secretsmanager:CreateSecret",
        "secretsmanager:UpdateSecret",
        "secretsmanager:GetSecretValue",
        "scheduler:CreateSchedule",
        "scheduler:UpdateSchedule",
        "scheduler:GetSchedule",
        "cloudwatch:PutMetricAlarm",
        "s3:CreateBucket",
        "s3:PutObject",
        "s3:PutBucketPolicy",
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords"
      ],
      "Resource": "*"
    }
  ]
}
```

## Security Best Practices

### ✅ DO

1. **Use AWS CLI profiles** - Keeps credentials outside project
2. **Use AWS SSO** - Best for organizations
3. **Rotate credentials regularly** - Every 90 days minimum
4. **Use IAM roles in CI/CD** - Avoid long-lived credentials
5. **Enable MFA** - For AWS Console access
6. **Use least privilege** - Only grant necessary permissions
7. **Audit access** - Review CloudTrail logs
8. **Store secrets in Secrets Manager** - Not in config files
9. **Use different accounts** - Separate dev/staging/prod
10. **Set file permissions** - `chmod 600` for config files

### ❌ DON'T

1. **Never commit credentials** - Check .gitignore
2. **Never share credentials** - Each person should have their own
3. **Never use root account** - Create IAM users
4. **Never hardcode secrets** - Use Secrets Manager
5. **Never log credentials** - Sanitize logs
6. **Never email credentials** - Use secure channels
7. **Never reuse credentials** - Across environments
8. **Never store in plain text** - Encrypt at rest
9. **Never use same key** - For multiple purposes
10. **Never skip MFA** - For production access

## Getting Help

### Check Configuration

```bash
# Verify configuration is loaded
source config/deployment-config.sh
env | grep AWS

# Test AWS access
aws sts get-caller-identity

# Check IAM permissions
aws iam get-user
```

### Common Issues

1. **Configuration not loading** - Check file path and permissions
2. **AWS access denied** - Verify credentials and permissions
3. **Account ID mismatch** - Ensure correct account
4. **Region errors** - Verify region is correct
5. **Profile not found** - Check `~/.aws/config`

### Support Resources

- [AWS CLI Configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
- [AWS SSO Setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)

### Contact

For deployment configuration issues:
- Check `config/README.md`
- Review this guide
- Contact DevOps team

## Next Steps

After configuration is complete:

1. ✅ Verify AWS access
2. ✅ Test configuration
3. ✅ Review security settings
4. ✅ Run deployment script
5. ✅ Monitor deployment

```bash
# Run deployment
./scripts/deploy-staging.sh
```

---

**Remember:** Security is everyone's responsibility. Keep credentials safe!
