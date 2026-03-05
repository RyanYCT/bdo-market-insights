# CI/CD Pipeline Documentation

## Overview

This directory contains GitHub Actions workflows for the BDO Market Insights project. The CI/CD pipeline automates testing, security scanning, and deployment to AWS.

## Workflows

### ci-cd.yml

The main CI/CD pipeline that runs on every push and pull request. It consists of the following jobs:

#### 1. Code Quality Checks (lint)
- **Black**: Code formatting verification
- **Flake8**: Linting and style checking
- **MyPy**: Static type checking
- **Bandit**: Security vulnerability scanning

#### 2. Unit Tests (unit-tests)
- Runs all unit tests with pytest
- Generates coverage reports
- Requires minimum 80% code coverage
- Uploads coverage to Codecov (optional)

#### 3. Property-Based Tests (property-tests)
- Runs property-based tests using Hypothesis
- Executes 200+ iterations per property in CI
- Shows detailed statistics

#### 4. Integration Tests (integration-tests)
- Tests complete ETL and Query pipelines
- Validates component interactions
- Uses mocked AWS services

#### 5. Deploy Lambda Layer (deploy-lambda-layer)
- Only runs on main branch pushes
- Builds and publishes Lambda Layer
- Saves layer version for function deployment

#### 6. Deploy Lambda Functions (deploy-lambda-functions)
- Blue-green deployment strategy
- Updates function code and layer
- Runs smoke tests
- Automatic rollback on failure

#### 7. Deploy Step Functions (deploy-step-functions)
- Updates state machine definition
- Replaces placeholders with actual ARNs

#### 8. Deploy API Gateway (deploy-api-gateway)
- Deploys API Gateway using CloudFormation
- Creates new deployment stage

#### 9. Send Notification (notify)
- Sends deployment status notification
- Can be configured for Slack, email, etc.

## Required Secrets

Configure these secrets in your GitHub repository settings:

- `AWS_ACCESS_KEY_ID`: AWS access key for deployment
- `AWS_SECRET_ACCESS_KEY`: AWS secret key for deployment
- `AWS_REGION`: AWS region (e.g., us-east-1)
- `AWS_ACCOUNT_ID`: AWS account ID
- `SLACK_WEBHOOK_URL`: (Optional) Slack webhook for notifications

## Local Development

Use the Makefile for local testing:

```bash
# Install dependencies
make install

# Run linting
make lint

# Format code
make format

# Run security scan
make security

# Run all tests
make test-all

# Run tests with coverage
make coverage

# Clean build artifacts
make clean
```

## Deployment Process

### Automatic Deployment (Main Branch)

1. Push to main branch triggers the pipeline
2. All tests must pass (lint, unit, property, integration)
3. Lambda Layer is deployed first
4. Lambda functions are deployed with blue-green strategy
5. Step Functions state machine is updated
6. API Gateway is deployed
7. Notification is sent

### Manual Deployment

Use the `workflow_dispatch` trigger to manually run the pipeline:

1. Go to Actions tab in GitHub
2. Select "CI/CD Pipeline"
3. Click "Run workflow"
4. Select branch and run

## Blue-Green Deployment

Lambda functions use aliases for blue-green deployment:

- **New version**: Deployed and tested
- **Live alias**: Points to the new version after successful smoke test
- **Rollback**: Automatically reverts to previous version on failure

## Rollback Procedure

If deployment fails:

1. Automatic rollback occurs for Lambda functions
2. Check CloudWatch logs for error details
3. Fix issues and push new commit
4. Pipeline runs again automatically

For manual rollback:

```bash
# Rollback Lambda function to previous version
aws lambda update-alias \
  --function-name <function-name> \
  --name live \
  --function-version <previous-version>

# Rollback Step Functions
aws stepfunctions update-state-machine \
  --state-machine-arn <arn> \
  --definition file://previous-definition.json
```

## Testing Strategy

### Unit Tests
- Test individual components in isolation
- Mock external dependencies
- Fast execution (< 1 minute)

### Property-Based Tests
- Test universal properties across many inputs
- 100+ iterations locally, 200+ in CI
- Finds edge cases automatically

### Integration Tests
- Test complete workflows
- Use mocked AWS services (moto)
- Verify data flow between components

## Coverage Requirements

- Minimum 80% code coverage required
- Coverage reports uploaded to Codecov
- HTML reports available as artifacts

## Troubleshooting

### Pipeline Failures

**Linting failures:**
- Run `make format` to auto-fix formatting
- Run `make lint` locally before pushing

**Test failures:**
- Check test logs in GitHub Actions
- Run tests locally: `make test-all`
- Use `pytest -v -s` for detailed output

**Deployment failures:**
- Check AWS CloudWatch logs
- Verify AWS credentials are correct
- Ensure IAM permissions are sufficient

### Common Issues

**Coverage below 80%:**
- Add tests for uncovered code
- Check coverage report: `make coverage`

**Type checking errors:**
- Add type hints to functions
- Use `# type: ignore` for third-party libraries

**Security scan failures:**
- Review Bandit output
- Fix security issues or add `# nosec` comment with justification

## Best Practices

1. **Always run tests locally before pushing**
   ```bash
   make lint
   make test-all
   ```

2. **Keep commits atomic and focused**
   - One feature/fix per commit
   - Clear commit messages

3. **Use feature branches**
   - Create branch from main
   - Open PR for review
   - Merge after approval and passing tests

4. **Monitor deployments**
   - Check CloudWatch logs after deployment
   - Verify metrics in CloudWatch dashboard
   - Test API endpoints manually

5. **Update documentation**
   - Keep README up to date
   - Document configuration changes
   - Update API documentation

## Monitoring

After deployment, monitor:

- **CloudWatch Logs**: Lambda execution logs
- **CloudWatch Metrics**: Custom metrics and alarms
- **X-Ray**: Distributed tracing
- **API Gateway**: Request/response logs

## Support

For issues or questions:
1. Check this documentation
2. Review CloudWatch logs
3. Check GitHub Actions logs
4. Contact the development team
