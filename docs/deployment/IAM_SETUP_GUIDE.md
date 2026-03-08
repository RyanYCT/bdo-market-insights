# IAM Setup Guide

Complete guide for setting up IAM permissions for the BDO Market Insights project.

## Table of Contents

1. [Overview](#overview)
2. [Quick Setup](#quick-setup)
3. [Detailed Setup](#detailed-setup)
4. [Troubleshooting](#troubleshooting)
5. [Security Notes](#security-notes)

---

## Overview

The BDO Market Insights deployment requires specific IAM permissions to:
- Create and manage Lambda functions
- Modify IAM roles (to add CloudWatch metrics permissions)
- Deploy infrastructure via CloudFormation
- Configure monitoring and scheduling

### What You'll Do

1. Create an IAM group for project access
2. Attach IAM policy to the group
3. Add your user to the group
4. Run setup script to configure IAM roles
5. Continue with deployment

---

## Quick Setup

### Step 1: Prepare IAM Policy

Replace `YOUR_ACCOUNT_ID` in `iam-policy-template.json` with your AWS account ID:

```bash
# Get your account ID
aws sts get-caller-identity --query Account --output text

# Quick replacement (Linux/Mac)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed "s/YOUR_ACCOUNT_ID/$ACCOUNT_ID/g" iam-policy-template.json > iam-policy-configured.json

# Quick replacement (Windows PowerShell)
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
(Get-Content iam-policy-template.json) -replace 'YOUR_ACCOUNT_ID', $ACCOUNT_ID | Set-Content iam-policy-configured.json
```

### Step 2: Create IAM Group and Attach Policy

```bash
# Create IAM group for BDO Market Insights developers
aws iam create-group --group-name BDOMarketInsightsDevelopers

# Create managed policy
aws iam create-policy \
    --policy-name BDOMarketInsightsFullAccess \
    --policy-document file://iam-policy-configured.json \
    --description "Full access for BDO Market Insights project"

# Attach policy to group (replace YOUR_ACCOUNT_ID with your AWS account ID)
aws iam attach-group-policy \
    --group-name BDOMarketInsightsDevelopers \
    --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/BDOMarketInsightsFullAccess

# Add your user to the group
aws iam add-user-to-group \
    --group-name BDOMarketInsightsDevelopers \
    --user-name YOUR_IAM_USERNAME
```

### Step 3: Setup IAM Roles

Run the setup script to configure IAM roles and CloudWatch permissions:

```bash
./scripts/setup-iam-roles.sh
```

### Step 4: Continue Deployment

```bash
./scripts/deploy-staging.sh
```

---

## Detailed Setup

### What the Policy Provides

The IAM policy grants permissions for:

- ✅ **Lambda**: Create, update, deploy functions and layers
- ✅ **IAM**: Create and manage roles, add policies
- ✅ **DynamoDB**: Access tables for data storage
- ✅ **Step Functions**: Create and manage state machines
- ✅ **API Gateway**: Deploy and manage REST APIs
- ✅ **CloudFormation**: Deploy infrastructure as code
- ✅ **CloudWatch**: Create alarms, publish metrics, access logs
- ✅ **EventBridge Scheduler**: Schedule automated runs
- ✅ **X-Ray**: Enable distributed tracing
- ✅ **S3**: Store deployment artifacts
- ✅ **SNS**: Send notifications

All permissions are scoped to BDO-specific resources only.

### Policy Attachment Details

The setup uses AWS managed policies attached to an IAM group:

```bash
# Create the managed policy
aws iam create-policy \
    --policy-name BDOMarketInsightsFullAccess \
    --policy-document file://iam-policy-configured.json \
    --description "Full access for BDO Market Insights project"

# Attach to group
aws iam attach-group-policy \
    --group-name BDOMarketInsightsDevelopers \
    --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/BDOMarketInsightsFullAccess
```

### Setup Script Details

The `setup-iam-roles.sh` script will:

1. **Create Step Functions execution role**
   - Allows Step Functions to invoke Lambda functions
   - Grants CloudWatch Logs access
   - Enables X-Ray tracing

2. **Create EventBridge Scheduler role**
   - Allows EventBridge to invoke Lambda functions on schedule
   - Used for automated ETL pipeline runs

3. **Add CloudWatch metrics permissions**
   - Adds `cloudwatch:PutMetricData` to all Lambda execution roles
   - Fixes "Failed to emit metric" warnings
   - Scoped to `BDOMarketInsights/ETL` namespace only

Expected output:
```
==========================================
Setting up IAM Roles for BDO Market Insights
Environment: staging
Region: us-east-1
Account: YOUR_ACCOUNT_ID
==========================================

Creating IAM role: bdo-stepfunctions-execution-role-staging
Role created successfully

Creating IAM role: EventBridgeSchedulerRole
Role created successfully

==========================================
Configuring CloudWatch Metrics Permissions
==========================================

Processing: retrieveIdList
  Role: retrieveIdList-role-nc31m9mk
  ✓ CloudWatch metrics permission added

Processing: fetchData
  Role: fetchData-role-xyz123
  ✓ CloudWatch metrics permission added

...

==========================================
IAM Setup Complete!
==========================================

Lambda CloudWatch Metrics Permissions:
  - Successfully updated: 7
  - Failed: 0
  - Skipped (not deployed): 0
```

---

## Troubleshooting

### Issue: AccessDenied When Attaching Policy

**Symptom:**
```
An error occurred (AccessDenied) when calling the PutUserPolicy operation
```

**Solution:**
You need admin permissions to attach policies. Either:
1. Use an IAM user/role with admin access
2. Use AWS Console to attach the policy manually
3. Use root account (not recommended for regular use)

### Issue: Failed to Emit Metric

**Symptom:**
```
WARNING: Failed to emit metric: OperationLatency
Error: User is not authorized to perform: cloudwatch:PutMetricData
```

**Solution:**
Run the setup script to add CloudWatch permissions to all Lambda functions:
```bash
./scripts/setup-iam-roles.sh
```

### Issue: Lambda Functions Not Found

**Symptom:**
```
Processing: retrieveIdList
  ⚠ Function not found or has no role. Skipping.
```

**Solution:**
This is normal if Lambda functions haven't been deployed yet. Deploy them first:
```bash
./scripts/deploy-function.sh retrieveIdList
```

Then run the setup script again:
```bash
./scripts/setup-iam-roles.sh
```

### Manual Fix: AWS Console

If you prefer using the AWS Console:

**Step 1: Create Managed Policy**
1. Go to [AWS IAM Console](https://console.aws.amazon.com/iam/)
2. Click **Policies** → **Create policy**
3. Click **JSON** tab
4. Paste the contents of `iam-policy-configured.json`
5. Click **Next: Tags** (optional)
6. Click **Next: Review**
7. Policy name: `BDOMarketInsightsFullAccess`
8. Description: `Full access for BDO Market Insights project`
9. Click **Create policy**

**Step 2: Create Group and Attach Policy**
1. Click **User groups** → **Create group**
2. Group name: `BDOMarketInsightsDevelopers`
3. In **Attach permissions policies**, search for `BDOMarketInsightsFullAccess`
4. Check the box next to the policy
5. Click **Create group**

**Step 3: Add User to Group**
1. Go to **Users** → Select your IAM user
2. Click **Groups** tab → **Add user to groups**
3. Select `BDOMarketInsightsDevelopers`
4. Click **Add to groups**

### Manual Fix: CloudWatch Metrics Permission

If you need to manually add CloudWatch metrics permission to a Lambda role:

1. Go to [AWS IAM Console](https://console.aws.amazon.com/iam/)
2. Click **Roles** → Search for the Lambda role (e.g., `retrieveIdList-role-nc31m9mk`)
3. Click **Add permissions** → **Create inline policy**
4. Click **JSON** tab and paste:

```json
{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": "cloudwatch:PutMetricData",
        "Resource": "*",
        "Condition": {
            "StringEquals": {
                "cloudwatch:namespace": "BDOMarketInsights/ETL"
            }
        }
    }]
}
```

5. Name: `CloudWatchMetricsPolicy`
6. Click **Create policy**

---

## Security Notes

### PassRole Restriction

The policy includes a restricted `iam:PassRole` permission that only allows passing roles to specific AWS services:

```json
{
    "Sid": "IAMPassRoleToServices",
    "Effect": "Allow",
    "Action": "iam:PassRole",
    "Resource": [
        "arn:aws:iam::YOUR_ACCOUNT_ID:role/service-role/*",
        "arn:aws:iam::YOUR_ACCOUNT_ID:role/bdo-*",
        "arn:aws:iam::YOUR_ACCOUNT_ID:role/EventBridgeSchedulerRole",
        "arn:aws:iam::YOUR_ACCOUNT_ID:role/*-role-*"
    ],
    "Condition": {
        "StringEquals": {
            "iam:PassedToService": [
                "lambda.amazonaws.com",
                "states.amazonaws.com",
                "scheduler.amazonaws.com",
                "apigateway.amazonaws.com",
                "cloudformation.amazonaws.com"
            ]
        }
    }
}
```

This prevents privilege escalation by ensuring roles can only be passed to approved services.

### Resource Naming Conventions

The policy uses these resource patterns:

| Pattern | Matches | Purpose |
|---------|---------|---------|
| `bdo-*` | Resources starting with "bdo-" | Project-specific resources |
| `BDO-*` | Resources starting with "BDO-" | CloudFormation stacks |
| `*-role-*` | Lambda execution roles | Auto-generated Lambda roles |
| `service-role/*` | Service-linked roles | AWS-managed service roles |
| `market-data-*` | DynamoDB tables | Data storage tables |

### Best Practices

- ✅ **Least Privilege**: Only grants permissions needed for the project
- ✅ **Resource Scoped**: Limited to BDO-specific resources
- ✅ **Service Restricted**: PassRole limited to specific AWS services
- ✅ **Group-Based Access**: Permissions managed through groups, not individual users
- ✅ **Auditable**: All actions logged in CloudTrail
- ✅ **No Wildcards**: Avoids overly permissive wildcards where possible

### Compliance

These permissions align with:
- AWS Well-Architected Framework - Security Pillar
- AWS IAM Best Practices
- Principle of Least Privilege
- Defense in Depth strategy

---

## Verification

After setup is complete, verify everything works:

```bash
# Test 1: Check managed policy exists
aws iam get-policy \
    --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/BDOMarketInsightsFullAccess

# Test 2: Check group exists
aws iam get-group --group-name BDOMarketInsightsDevelopers

# Test 3: Check policy is attached to group
aws iam list-attached-group-policies --group-name BDOMarketInsightsDevelopers

# Test 4: Verify your user is in the group
aws iam get-group --group-name BDOMarketInsightsDevelopers --query 'Users[*].UserName'

# Test 5: Deploy a Lambda function
./scripts/deploy-function.sh retrieveIdList

# Test 6: Invoke Lambda function
aws lambda invoke \
    --function-name retrieveIdList \
    --payload '{}' \
    response.json

# Test 7: Check logs (should not see "Failed to emit metric")
aws logs tail /aws/lambda/retrieveIdList --since 5m
```

Expected results:
- ✅ Managed policy exists and is visible in IAM console
- ✅ Group exists with correct policy attached
- ✅ Your user is a member of the group
- ✅ All commands succeed without permission errors
- ✅ Lambda functions deploy successfully
- ✅ No "Failed to emit metric" warnings in logs
- ✅ CloudWatch metrics are published

---

## Next Steps

After IAM setup is complete:

1. ✅ IAM policy attached
2. ✅ IAM roles created
3. ✅ CloudWatch permissions added
4. → Continue with [Deployment Guide](DEPLOYMENT_GUIDE.md)
5. → Deploy to staging: `./scripts/deploy-staging.sh`

---

## Related Documentation

- [Deployment Guide](DEPLOYMENT_GUIDE.md) - Complete deployment instructions
- [iam-policy-template.json](../../iam-policy-template.json) - IAM policy template
- [scripts/README.md](../../scripts/README.md) - Deployment scripts reference
