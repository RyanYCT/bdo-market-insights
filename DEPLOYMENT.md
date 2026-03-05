# Deployment Guide

## Overview

This document describes the deployment process for the BDO Market Insights serverless application. The system uses a CI/CD pipeline with GitHub Actions for automated deployments and provides manual deployment scripts for local use.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Automated Deployment (CI/CD)](#automated-deployment-cicd)
3. [Manual Deployment](#manual-deployment)
4. [Blue-Green Deployment](#blue-green-deployment)
5. [Rollback Procedures](#rollback-procedures)
6. [Monitoring](#monitoring)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Tools

- AWS CLI v2 (configured with appropriate credentials)
- Python 3.11+
- Git
- Make (optional, for convenience commands)

### AWS Permissions

The deployment user/role needs the following permissions:

- Lambda: Full access (create, update, publish functions and layers)
- IAM: Create and manage roles for Lambda functions
- CloudFormation: Full access for stack management
- API Gateway: Full access
- Step Functions: Full access
- CloudWatch: Create and manage alarms, log groups
- S3: Access to deployment buckets (if using)

### Environment Variables

Set these environment variables before deployment:

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=123456789012
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
```

## Automated Deployment (CI/CD)

### GitHub Actions Pipeline

The CI/CD pipeline automatically deploys to production when code is pushed to the `main` branch.

#### Pipeline Stages

1. **Code Quality Checks**
   - Black (formatting)
   - Flake8 (linting)
   - MyPy (type checking)
   - Bandit (security scanning)

2. **Testing**
   - Unit tests (80% coverage required)
   - Property-based tests (200+ iterations)
   - Integration tests

3. **Deployment**
   - Lambda Layer
   - Lambda Functions (blue-green)
   - Step Functions
   - API Gateway

4. **Notification**
   - Deployment status notification

#### Triggering Deployment

**Automatic (recommended):**
```bash
git checkout main
git pull origin main
git merge develop
git push origin main
```

**Manual trigger:**
1. Go to GitHub Actions tab
2. Select "CI/CD Pipeline"
3. Click "Run workflow"
4. Select branch and run

### Required GitHub Secrets

Configure these in your repository settings (Settings → Secrets and variables → Actions):

```
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
AWS_ACCOUNT_ID
SLACK_WEBHOOK_URL (optional)
```

## Manual Deployment

### Full Deployment

Deploy everything (layer, functions, infrastructure):

```bash
# Make scripts executable
chmod +x scripts/*.sh

# Run full deployment
./scripts/deploy-all.sh
```

### Individual Components

#### Deploy Lambda Layer Only

```bash
./scripts/deploy-layer.sh
```

This will:
- Build the Lambda Layer with dependencies
- Publish to AWS Lambda
- Save version number to `lambda_layer/layer-version.txt`

#### Deploy Single Lambda Function

```bash
./scripts/deploy-function.sh <function-name>
```

Example:
```bash
./scripts/deploy-function.sh retrieveIdList
```

This will:
- Build deployment package
- Update function code
- Update layer configuration
- Run smoke test
- Update 'live' alias (blue-green)
- Rollback automatically on failure

#### Deploy All Lambda Functions

```bash
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    ./scripts/deploy-function.sh $func
done
```

#### Deploy Step Functions

```bash
./scripts/deploy-step-functions.sh
```

#### Deploy API Gateway

```bash
./scripts/deploy-api-gateway.sh
```

#### Deploy CloudWatch Alarms

```bash
cd infrastructure
./deploy-alarms.sh
cd ..
```

## Blue-Green Deployment

### How It Works

Lambda functions use aliases for blue-green deployment:

1. **New version deployed**: Code is updated and published as a new version
2. **Smoke test**: New version is tested with a test invocation
3. **Alias update**: If test passes, 'live' alias points to new version
4. **Automatic rollback**: If test fails, alias stays on previous version

### Alias Strategy

- **$LATEST**: Always points to the most recent code (not used in production)
- **Numbered versions**: Immutable versions (1, 2, 3, etc.)
- **live alias**: Points to the current production version

### Traffic Shifting (Advanced)

For gradual rollout, you can shift traffic between versions:

```bash
# Shift 10% of traffic to new version
aws lambda update-alias \
  --function-name retrieveIdList \
  --name live \
  --function-version 5 \
  --routing-config AdditionalVersionWeights={"4"=0.9}

# After validation, shift 100% to new version
aws lambda update-alias \
  --function-name retrieveIdList \
  --name live \
  --function-version 5 \
  --routing-config AdditionalVersionWeights={}
```

## Rollback Procedures

### Automatic Rollback

The deployment scripts automatically rollback on smoke test failure. No manual intervention needed.

### Manual Rollback

#### Rollback Lambda Function

```bash
# Rollback to previous version (automatic detection)
./scripts/rollback-function.sh <function-name>

# Rollback to specific version
./scripts/rollback-function.sh <function-name> <version-number>
```

Example:
```bash
# Rollback to previous version
./scripts/rollback-function.sh retrieveIdList

# Rollback to version 3
./scripts/rollback-function.sh retrieveIdList 3
```

#### Rollback Step Functions

```bash
# Restore previous state machine definition
aws stepfunctions update-state-machine \
  --state-machine-arn arn:aws:states:REGION:ACCOUNT:stateMachine:bdo-market-insights-query \
  --definition file://infrastructure/step-functions-state-machine.json.backup
```

#### Rollback API Gateway

```bash
# Redeploy previous stage
aws apigateway create-deployment \
  --rest-api-id <api-id> \
  --stage-name prod \
  --description "Rollback deployment"
```

### Emergency Rollback

If you need to quickly rollback all functions:

```bash
# Create rollback script
cat > rollback-all.sh << 'EOF'
#!/bin/bash
for func in retrieveIdList fetchData cleanData storeData queryData analyzeData retainData; do
    echo "Rolling back $func..."
    ./scripts/rollback-function.sh $func
done
EOF

chmod +x rollback-all.sh
./rollback-all.sh
```

## Monitoring

### Post-Deployment Checks

After deployment, verify:

1. **Lambda Functions**
   ```bash
   # Check function status
   aws lambda get-function --function-name retrieveIdList
   
   # Check recent invocations
   aws logs tail /aws/lambda/retrieveIdList --follow
   ```

2. **CloudWatch Metrics**
   - Lambda invocations
   - Error rates
   - Duration
   - Throttles

3. **CloudWatch Alarms**
   ```bash
   # Check alarm status
   aws cloudwatch describe-alarms --alarm-names "bdo-market-insights-*"
   ```

4. **API Gateway**
   ```bash
   # Test endpoint
   curl -X GET "https://API_ID.execute-api.REGION.amazonaws.com/prod/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
     -H "x-api-key: YOUR_API_KEY"
   ```

5. **X-Ray Traces**
   - Check AWS X-Ray console for traces
   - Verify service map shows all components

### Monitoring Dashboard

Access CloudWatch dashboard:
```
https://console.aws.amazon.com/cloudwatch/home?region=REGION#dashboards:name=bdo-market-insights
```

## Troubleshooting

### Common Issues

#### Deployment Fails: "Function not found"

**Cause**: Function doesn't exist yet

**Solution**: Create function first using CloudFormation or AWS Console

#### Deployment Fails: "Insufficient permissions"

**Cause**: IAM permissions missing

**Solution**: Add required permissions to deployment role

#### Smoke Test Fails

**Cause**: Function code has errors

**Solution**: 
1. Check CloudWatch logs
2. Fix code issues
3. Redeploy

#### Layer Version Mismatch

**Cause**: Functions using old layer version

**Solution**:
```bash
# Redeploy layer first
./scripts/deploy-layer.sh

# Then redeploy functions
./scripts/deploy-function.sh <function-name>
```

#### API Gateway 502 Error

**Cause**: Lambda function timeout or error

**Solution**:
1. Check Lambda logs in CloudWatch
2. Increase timeout if needed
3. Fix function errors

### Debug Mode

Enable verbose output:

```bash
# For deployment scripts
set -x
./scripts/deploy-function.sh retrieveIdList

# For AWS CLI
export AWS_DEBUG=1
aws lambda get-function --function-name retrieveIdList
```

### Getting Help

1. Check CloudWatch Logs
2. Review X-Ray traces
3. Check GitHub Actions logs
4. Review this documentation
5. Contact development team

## Best Practices

### Before Deployment

1. **Run tests locally**
   ```bash
   make test-all
   make lint
   ```

2. **Review changes**
   ```bash
   git diff main..develop
   ```

3. **Update documentation**
   - Update README if needed
   - Update API documentation
   - Update CHANGELOG

### During Deployment

1. **Monitor logs**
   ```bash
   # Watch deployment logs
   aws logs tail /aws/lambda/retrieveIdList --follow
   ```

2. **Check metrics**
   - Watch CloudWatch dashboard
   - Monitor error rates
   - Check latency

3. **Verify functionality**
   - Test API endpoints
   - Run smoke tests
   - Check data flow

### After Deployment

1. **Monitor for 24 hours**
   - Watch CloudWatch alarms
   - Check error rates
   - Monitor performance

2. **Document changes**
   - Update CHANGELOG
   - Document any issues
   - Share with team

3. **Keep rollback ready**
   - Know previous version numbers
   - Have rollback scripts ready
   - Monitor for issues

## Deployment Checklist

- [ ] All tests passing locally
- [ ] Code reviewed and approved
- [ ] Documentation updated
- [ ] Environment variables configured
- [ ] AWS credentials configured
- [ ] Backup of current configuration
- [ ] Deployment window scheduled
- [ ] Team notified
- [ ] Monitoring dashboard open
- [ ] Rollback plan ready
- [ ] Deploy Lambda Layer
- [ ] Deploy Lambda Functions
- [ ] Deploy Step Functions
- [ ] Deploy API Gateway
- [ ] Deploy CloudWatch Alarms
- [ ] Verify all components
- [ ] Run smoke tests
- [ ] Monitor for 1 hour
- [ ] Document deployment
- [ ] Notify team of completion

## Support

For deployment issues:
1. Check this guide
2. Review CloudWatch logs
3. Check GitHub Actions logs
4. Contact DevOps team
