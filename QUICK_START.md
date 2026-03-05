# Quick Start Guide - Staging Deployment

## 🚀 Deploy in 3 Steps

### Step 1: Setup Configuration (One-time)
```bash
chmod +x scripts/setup-deployment-config.sh
./scripts/setup-deployment-config.sh
```

### Step 2: Verify AWS Access
```bash
source config/deployment-config.sh
aws sts get-caller-identity
```

### Step 3: Deploy to Staging
```bash
chmod +x scripts/deploy-staging.sh
./scripts/deploy-staging.sh
```

That's it! The script will deploy all components automatically.

---

## 📋 What Gets Deployed

1. ✅ AWS Secrets Manager (PostgreSQL + API credentials)
2. ✅ Lambda Layer (shared code)
3. ✅ 7 Lambda Functions (ETL + Query pipeline)
4. ✅ Step Functions (query orchestration)
5. ✅ API Gateway (REST API with API keys)
6. ✅ X-Ray Tracing (all components)
7. ✅ CloudWatch Alarms (monitoring)
8. ✅ EventBridge Scheduler (ETL + retention)

---

## 🔑 Configuration Methods

### Option A: AWS CLI Profile (Recommended)
```bash
aws configure --profile bdo-staging
# Then in config: export AWS_PROFILE="bdo-staging"
```

### Option B: AWS SSO (For Organizations)
```bash
aws configure sso --profile bdo-staging
aws sso login --profile bdo-staging
# Then in config: export AWS_PROFILE="bdo-staging"
```

### Option C: Environment Variables (Testing Only)
```bash
# In config: export AWS_ACCESS_KEY_ID="..."
# In config: export AWS_SECRET_ACCESS_KEY="..."
```

---

## 🔒 Security Checklist

- [ ] Configuration file created: `config/deployment-config.sh`
- [ ] File permissions set: `chmod 600 config/deployment-config.sh`
- [ ] AWS credentials configured (profile or keys)
- [ ] AWS access verified: `aws sts get-caller-identity`
- [ ] Configuration NOT committed to git (check `.gitignore`)

---

## 📝 Required Information

You'll need during setup:

### AWS Configuration
- AWS Region (e.g., `us-east-1`)
- AWS Account ID (12-digit number)
- AWS Credentials (profile or keys)

### Database Credentials (for Secrets Manager)
- PostgreSQL host
- PostgreSQL port (default: 5432)
- Database name
- Username
- Password

### API Configuration
- External API URL
- API token (if required)
- CORS allowed origins

### Monitoring (Optional)
- Alarm notification email
- Slack webhook URL

---

## 🧪 Verification Commands

```bash
# Check Lambda functions
aws lambda list-functions --query 'Functions[].FunctionName'

# Check Step Functions
aws stepfunctions list-state-machines

# Check API Gateway
aws apigateway get-rest-apis

# Check CloudWatch alarms
aws cloudwatch describe-alarms --alarm-name-prefix "bdo-"

# Check EventBridge schedules
aws scheduler list-schedules --name-prefix "bdo-"
```

---

## 🧪 Test API

```bash
# Get API endpoint from deployment output
API_ENDPOINT="https://xxx.execute-api.us-east-1.amazonaws.com/staging"
API_KEY="your-api-key-from-deployment"

# Test query
curl -X GET "${API_ENDPOINT}/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" \
  -H "x-api-key: ${API_KEY}"
```

---

## 📚 Documentation

- **CREDENTIALS_SETUP_GUIDE.md** - Detailed credentials setup
- **DEPLOYMENT_SECURITY_SUMMARY.md** - Security overview
- **STAGING_DEPLOYMENT_GUIDE.md** - Step-by-step deployment
- **STAGING_DEPLOYMENT_CHECKLIST.md** - Validation checklist
- **config/README.md** - Configuration reference

---

## 🆘 Troubleshooting

### Configuration file not found
```bash
cp config/deployment-config.example.sh config/deployment-config.sh
nano config/deployment-config.sh
```

### AWS credentials not found
```bash
aws configure --profile bdo-staging
```

### Permission denied
```bash
chmod 600 config/deployment-config.sh
chmod +x scripts/*.sh
```

### Deployment failed
```bash
# Check logs
aws logs tail /aws/lambda/retrieveIdList --follow

# Rollback if needed
./scripts/rollback-function.sh retrieveIdList
```

---

## 🎯 Next Steps

After successful deployment:

1. ✅ Complete **Task 27**: Run staging validation
2. ✅ Monitor CloudWatch metrics for 24 hours
3. ✅ Test API endpoints thoroughly
4. ✅ Document any issues
5. ✅ Prepare for production deployment

---

## 💡 Pro Tips

- Use AWS CLI profiles instead of hardcoding credentials
- Enable MFA on your AWS account
- Rotate credentials every 90 days
- Use different AWS accounts for staging and production
- Monitor CloudWatch alarms regularly
- Keep API keys secure
- Review CloudTrail logs for security audits

---

## 🔗 Quick Links

- [AWS Console](https://console.aws.amazon.com/)
- [CloudWatch Logs](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups)
- [X-Ray Service Map](https://console.aws.amazon.com/xray/home#/service-map)
- [Lambda Functions](https://console.aws.amazon.com/lambda/home#/functions)
- [API Gateway](https://console.aws.amazon.com/apigateway/home#/apis)

---

**Need Help?** Check the documentation or contact the DevOps team.

**Security Reminder:** Never commit `config/deployment-config.sh` to version control! 🔒
