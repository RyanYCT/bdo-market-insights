# Configuration Management

This directory contains configuration templates for deployment. These files help keep sensitive credentials and environment-specific settings private while allowing deployment scripts to be committed to version control.

## Quick Start

### 1. Copy the Example Configuration

```bash
# Copy the example file
cp config/deployment-config.example.sh config/deployment-config.sh

# Edit with your actual values
nano config/deployment-config.sh  # or vim, code, etc.
```

### 2. Configure AWS Credentials

Choose one of the following methods:

#### Option A: AWS CLI Profile (RECOMMENDED)

```bash
# Configure AWS CLI profile
aws configure --profile bdo-staging

# The profile will be stored in ~/.aws/credentials and ~/.aws/config
# This keeps credentials out of your project directory

# In deployment-config.sh, set:
export AWS_PROFILE="bdo-staging"
```

#### Option B: AWS SSO (RECOMMENDED for Organizations)

```bash
# Configure AWS SSO
aws configure sso --profile bdo-staging

# Login before deployment
aws sso login --profile bdo-staging

# In deployment-config.sh, set:
export AWS_PROFILE="bdo-staging"
```

#### Option C: Environment Variables (NOT RECOMMENDED)

```bash
# In deployment-config.sh, set:
export AWS_ACCESS_KEY_ID="your-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
```

### 3. Set Required Configuration

Edit `config/deployment-config.sh` and set:

```bash
# Required
export AWS_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"
export ENVIRONMENT="staging"

# Optional but recommended
export ALLOWED_CORS_ORIGINS="https://staging.example.com"
export ALARM_EMAIL="devops@example.com"
```

### 4. Verify Configuration

```bash
# Source the configuration
source config/deployment-config.sh

# Verify AWS access
aws sts get-caller-identity

# Should output your account details
```

## Configuration Files

### deployment-config.example.sh
**Purpose:** Template for deployment configuration

**Status:** Committed to version control

**Usage:** Copy to `deployment-config.sh` and customize

### deployment-config.sh
**Purpose:** Actual deployment configuration with your credentials

**Status:** ⚠️ NEVER commit to version control (in .gitignore)

**Usage:** Sourced by deployment scripts

## Security Best Practices

### ✅ DO

1. **Use AWS CLI profiles** instead of hardcoding credentials
2. **Use AWS SSO** if your organization supports it
3. **Keep deployment-config.sh private** (it's in .gitignore)
4. **Rotate credentials regularly**
5. **Use IAM roles** when running in CI/CD
6. **Store secrets in AWS Secrets Manager**, not in config files
7. **Use different AWS accounts** for staging and production
8. **Enable MFA** for AWS accounts
9. **Use least privilege IAM policies**
10. **Audit AWS CloudTrail logs** regularly

### ❌ DON'T

1. **Never commit deployment-config.sh** to version control
2. **Never hardcode credentials** in deployment scripts
3. **Never share credentials** via email or chat
4. **Never use root AWS account** for deployments
5. **Never commit .env files** with real values
6. **Never log credentials** in deployment output
7. **Never store credentials** in CI/CD logs
8. **Never use the same credentials** for multiple environments

## Environment-Specific Configuration

### Development
```bash
cp config/deployment-config.example.sh config/deployment-config.dev.sh
# Edit for dev environment
export ENVIRONMENT="dev"
export AWS_PROFILE="bdo-dev"
```

### Staging
```bash
cp config/deployment-config.example.sh config/deployment-config.staging.sh
# Edit for staging environment
export ENVIRONMENT="staging"
export AWS_PROFILE="bdo-staging"
```

### Production
```bash
cp config/deployment-config.example.sh config/deployment-config.prod.sh
# Edit for production environment
export ENVIRONMENT="prod"
export AWS_PROFILE="bdo-prod"
```

## Using Configuration in Deployment Scripts

### Method 1: Source Configuration File

```bash
#!/bin/bash
# Load configuration
if [ -f "config/deployment-config.sh" ]; then
    source config/deployment-config.sh
else
    echo "ERROR: config/deployment-config.sh not found"
    echo "Copy config/deployment-config.example.sh to config/deployment-config.sh"
    exit 1
fi

# Use configuration
echo "Deploying to $ENVIRONMENT in $AWS_REGION"
```

### Method 2: Environment-Specific Configuration

```bash
#!/bin/bash
# Load environment-specific configuration
ENV=${1:-staging}
CONFIG_FILE="config/deployment-config.${ENV}.sh"

if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "ERROR: $CONFIG_FILE not found"
    exit 1
fi
```

## CI/CD Configuration

For CI/CD pipelines (GitHub Actions, GitLab CI, etc.), use:

### GitHub Actions

```yaml
# .github/workflows/deploy.yml
env:
  AWS_REGION: ${{ secrets.AWS_REGION }}
  AWS_ACCOUNT_ID: ${{ secrets.AWS_ACCOUNT_ID }}
  ENVIRONMENT: staging

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ secrets.AWS_REGION }}
```

### GitLab CI

```yaml
# .gitlab-ci.yml
variables:
  AWS_REGION: $AWS_REGION
  AWS_ACCOUNT_ID: $AWS_ACCOUNT_ID
  ENVIRONMENT: staging

deploy:
  script:
    - aws sts get-caller-identity
    - ./scripts/deploy-staging.sh
```

## Troubleshooting

### Configuration File Not Found

```bash
ERROR: config/deployment-config.sh not found
```

**Solution:** Copy the example file and customize it
```bash
cp config/deployment-config.example.sh config/deployment-config.sh
```

### AWS Credentials Not Found

```bash
ERROR: Unable to locate credentials
```

**Solution:** Configure AWS CLI or set environment variables
```bash
aws configure --profile bdo-staging
export AWS_PROFILE="bdo-staging"
```

### Invalid AWS Account ID

```bash
ERROR: AWS_ACCOUNT_ID must be set
```

**Solution:** Set AWS_ACCOUNT_ID in deployment-config.sh
```bash
export AWS_ACCOUNT_ID="123456789012"
```

### Permission Denied

```bash
ERROR: User is not authorized to perform: lambda:UpdateFunctionCode
```

**Solution:** Ensure your IAM user/role has required permissions

## Required AWS Permissions

The deployment user/role needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:*",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "cloudformation:*",
        "apigateway:*",
        "states:*",
        "logs:*",
        "secretsmanager:*",
        "scheduler:*",
        "cloudwatch:*",
        "s3:*",
        "xray:*"
      ],
      "Resource": "*"
    }
  ]
}
```

## Configuration Variables Reference

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| AWS_REGION | AWS region for deployment | us-east-1 |
| AWS_ACCOUNT_ID | AWS account ID | 123456789012 |
| ENVIRONMENT | Environment name | staging |

### AWS Credentials (choose one)

| Variable | Description | Example |
|----------|-------------|---------|
| AWS_PROFILE | AWS CLI profile name | bdo-staging |
| AWS_ACCESS_KEY_ID | AWS access key | AKIAIOSFODNN7EXAMPLE |
| AWS_SECRET_ACCESS_KEY | AWS secret key | wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| ALLOWED_CORS_ORIGINS | CORS allowed origins | https://example.com |
| LAMBDA_TIMEOUT | Lambda timeout in seconds | 300 |
| LAMBDA_MEMORY_SIZE | Lambda memory in MB | 512 |
| ALARM_EMAIL | Email for alarm notifications | devops@example.com |
| SLACK_WEBHOOK_URL | Slack webhook for notifications | https://hooks.slack.com/... |

## Support

For configuration issues:
1. Check this README
2. Verify AWS credentials: `aws sts get-caller-identity`
3. Check .gitignore includes config files
4. Review deployment script logs
5. Contact DevOps team

## References

- [AWS CLI Configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
- [AWS SSO Configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
