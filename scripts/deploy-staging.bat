@echo off
REM Deploy BDO Market Insights to Staging Environment (Windows)
REM This script handles the complete staging deployment

setlocal enabledelayedexpansion

REM Configuration
set ENVIRONMENT=staging
set REGION=%AWS_REGION%
if "%REGION%"=="" set REGION=us-east-1
set ACCOUNT_ID=%AWS_ACCOUNT_ID%

echo ==========================================
echo BDO Market Insights - Staging Deployment
echo ==========================================
echo.
echo Environment: %ENVIRONMENT%
echo Region: %REGION%
echo Account: %ACCOUNT_ID%
echo.

REM Check prerequisites
echo ==========================================
echo Step 1/9: Checking Prerequisites
echo ==========================================
echo.

where aws >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] AWS CLI not found. Please install AWS CLI v2.
    exit /b 1
)
echo [OK] AWS CLI found

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+.
    exit /b 1
)
echo [OK] Python found

if "%ACCOUNT_ID%"=="" (
    echo [WARNING] AWS_ACCOUNT_ID not set. Attempting to retrieve...
    for /f "tokens=*" %%i in ('aws sts get-caller-identity --query Account --output text') do set ACCOUNT_ID=%%i
    if "!ACCOUNT_ID!"=="" (
        echo [ERROR] Could not determine AWS Account ID
        exit /b 1
    )
    echo [OK] AWS Account ID: !ACCOUNT_ID!
)

REM Verify AWS credentials
aws sts get-caller-identity >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] AWS credentials invalid or not configured
    exit /b 1
)
echo [OK] AWS credentials valid

REM Step 2: Secrets Manager
echo.
echo ==========================================
echo Step 2/9: Configuring AWS Secrets Manager
echo ==========================================
echo.
echo This step will create or update secrets in AWS Secrets Manager.
echo You will need to provide:
echo   - PostgreSQL database credentials
echo   - External API authentication tokens
echo.

set /p CONFIGURE_SECRETS="Do you want to configure Secrets Manager now? (y/n): "
if /i "%CONFIGURE_SECRETS%"=="y" (
    echo.
    echo PostgreSQL Database Credentials:
    set /p DB_HOST="  Host: "
    set /p DB_PORT="  Port [5432]: "
    if "%DB_PORT%"=="" set DB_PORT=5432
    set /p DB_NAME="  Database name: "
    set /p DB_USER="  Username: "
    set /p DB_PASSWORD="  Password: "
    
    REM Create PostgreSQL secret
    set SECRET_NAME=bdo-db-credentials-%ENVIRONMENT%
    set SECRET_VALUE={"username":"%DB_USER%","password":"%DB_PASSWORD%","host":"%DB_HOST%","port":%DB_PORT%,"database":"%DB_NAME%"}
    
    aws secretsmanager describe-secret --secret-id %SECRET_NAME% --region %REGION% >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        echo [WARNING] Secret %SECRET_NAME% already exists. Updating...
        aws secretsmanager update-secret --secret-id %SECRET_NAME% --secret-string "%SECRET_VALUE%" --region %REGION% >nul
    ) else (
        aws secretsmanager create-secret --name %SECRET_NAME% --description "PostgreSQL credentials for BDO Market Insights %ENVIRONMENT%" --secret-string "%SECRET_VALUE%" --region %REGION% >nul
    )
    echo [OK] PostgreSQL credentials stored in Secrets Manager
    
    REM External API token
    echo.
    echo External API Configuration:
    set /p API_URL="  API Base URL: "
    set /p API_TOKEN="  API Token (if required): "
    
    set SECRET_NAME=bdo-external-api-%ENVIRONMENT%
    set SECRET_VALUE={"api_url":"%API_URL%","api_token":"%API_TOKEN%"}
    
    aws secretsmanager describe-secret --secret-id %SECRET_NAME% --region %REGION% >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        echo [WARNING] Secret %SECRET_NAME% already exists. Updating...
        aws secretsmanager update-secret --secret-id %SECRET_NAME% --secret-string "%SECRET_VALUE%" --region %REGION% >nul
    ) else (
        aws secretsmanager create-secret --name %SECRET_NAME% --description "External API credentials for BDO Market Insights %ENVIRONMENT%" --secret-string "%SECRET_VALUE%" --region %REGION% >nul
    )
    echo [OK] External API credentials stored in Secrets Manager
) else (
    echo [WARNING] Skipping Secrets Manager configuration. Ensure secrets are already configured.
)

REM Step 3: Deploy Lambda Layer
echo.
echo ==========================================
echo Step 3/9: Deploying Lambda Layer
echo ==========================================
echo.

if exist "scripts\deploy-layer.sh" (
    bash scripts/deploy-layer.sh
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Lambda Layer deployment failed
        exit /b 1
    )
    echo [OK] Lambda Layer deployed
) else (
    echo [ERROR] deploy-layer.sh not found
    exit /b 1
)

REM Step 4: Deploy Lambda Functions
echo.
echo ==========================================
echo Step 4/9: Deploying Lambda Functions
echo ==========================================
echo.

set FUNCTIONS=retrieveIdList fetchData cleanData storeData queryData analyzeData retainData

for %%F in (%FUNCTIONS%) do (
    echo.
    echo Deploying %%F...
    if exist "scripts\deploy-function.sh" (
        bash scripts/deploy-function.sh %%F
        if !ERRORLEVEL! neq 0 (
            echo [ERROR] %%F deployment failed
            exit /b 1
        )
        echo [OK] %%F deployed
    ) else (
        echo [ERROR] deploy-function.sh not found
        exit /b 1
    )
)

REM Step 5: Deploy Step Functions
echo.
echo ==========================================
echo Step 5/9: Deploying Step Functions State Machine
echo ==========================================
echo.

REM Get Lambda ARNs
for /f "tokens=*" %%i in ('aws lambda get-function --function-name queryData --region %REGION% --query Configuration.FunctionArn --output text') do set QUERY_DATA_ARN=%%i
for /f "tokens=*" %%i in ('aws lambda get-function --function-name analyzeData --region %REGION% --query Configuration.FunctionArn --output text') do set ANALYZE_DATA_ARN=%%i

if "%QUERY_DATA_ARN%"=="" (
    echo [ERROR] Could not retrieve queryData Lambda ARN
    exit /b 1
)
if "%ANALYZE_DATA_ARN%"=="" (
    echo [ERROR] Could not retrieve analyzeData Lambda ARN
    exit /b 1
)

aws cloudformation deploy --template-file infrastructure/step-functions-template.yaml --stack-name bdo-step-functions-%ENVIRONMENT% --parameter-overrides Environment=%ENVIRONMENT% QueryDataLambdaArn=%QUERY_DATA_ARN% AnalyzeDataLambdaArn=%ANALYZE_DATA_ARN% --capabilities CAPABILITY_NAMED_IAM --region %REGION%
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Step Functions deployment failed
    exit /b 1
)
echo [OK] Step Functions state machine deployed

REM Step 6: Deploy API Gateway
echo.
echo ==========================================
echo Step 6/9: Deploying API Gateway
echo ==========================================
echo.

REM Get Step Functions ARN
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-step-functions-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue" --output text') do set STEP_FUNCTIONS_ARN=%%i

if "%STEP_FUNCTIONS_ARN%"=="" (
    echo [ERROR] Could not retrieve Step Functions ARN
    exit /b 1
)

echo Configuring CORS allowed origins...
set /p ALLOWED_ORIGINS="Enter allowed CORS origins (comma-separated) [https://staging.example.com]: "
if "%ALLOWED_ORIGINS%"=="" set ALLOWED_ORIGINS=https://staging.example.com

aws cloudformation deploy --template-file infrastructure/api-gateway-template.yaml --stack-name bdo-api-gateway-%ENVIRONMENT% --parameter-overrides Environment=%ENVIRONMENT% AllowedOrigins=%ALLOWED_ORIGINS% StepFunctionsArn=%STEP_FUNCTIONS_ARN% --capabilities CAPABILITY_NAMED_IAM --region %REGION%
if %ERRORLEVEL% neq 0 (
    echo [ERROR] API Gateway deployment failed
    exit /b 1
)
echo [OK] API Gateway deployed

REM Retrieve API key
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue" --output text') do set API_KEY_ID=%%i

if not "%API_KEY_ID%"=="" (
    for /f "tokens=*" %%i in ('aws apigateway get-api-key --api-key %API_KEY_ID% --include-value --region %REGION% --query value --output text') do set API_KEY_VALUE=%%i
    echo.
    echo [WARNING] IMPORTANT: Save this API key securely!
    echo API Key: !API_KEY_VALUE!
    echo.
)

REM Step 7: Enable X-Ray
echo.
echo ==========================================
echo Step 7/9: Enabling X-Ray Tracing
echo ==========================================
echo.

for %%F in (%FUNCTIONS%) do (
    aws lambda update-function-configuration --function-name %%F --tracing-config Mode=Active --region %REGION% >nul
    echo [OK] X-Ray enabled for %%F
)

REM Step 8: Deploy CloudWatch Alarms
echo.
echo ==========================================
echo Step 8/9: Deploying CloudWatch Alarms
echo ==========================================
echo.

if exist "infrastructure\deploy-alarms.sh" (
    cd infrastructure
    bash deploy-alarms.sh
    cd ..
    echo [OK] CloudWatch alarms deployed
) else (
    echo [WARNING] deploy-alarms.sh not found. Skipping CloudWatch alarms.
)

REM Step 9: Configure EventBridge Scheduler
echo.
echo ==========================================
echo Step 9/9: Configuring EventBridge Scheduler
echo ==========================================
echo.

echo Configuring EventBridge schedules for ETL and retention...

REM ETL Pipeline Schedule
set ETL_SCHEDULE_NAME=bdo-etl-pipeline-%ENVIRONMENT%
for /f "tokens=*" %%i in ('aws lambda get-function --function-name retrieveIdList --region %REGION% --query Configuration.FunctionArn --output text') do set RETRIEVE_ID_LIST_ARN=%%i

aws scheduler get-schedule --name %ETL_SCHEDULE_NAME% --region %REGION% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [WARNING] ETL schedule already exists. Updating...
    aws scheduler update-schedule --name %ETL_SCHEDULE_NAME% --schedule-expression "cron(0 2 * * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETRIEVE_ID_LIST_ARN%\",\"RoleArn\":\"arn:aws:iam::%ACCOUNT_ID%:role/EventBridgeSchedulerRole\"}" --region %REGION% >nul
) else (
    aws scheduler create-schedule --name %ETL_SCHEDULE_NAME% --schedule-expression "cron(0 2 * * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETRIEVE_ID_LIST_ARN%\",\"RoleArn\":\"arn:aws:iam::%ACCOUNT_ID%:role/EventBridgeSchedulerRole\"}" --description "Daily ETL pipeline for BDO Market Insights %ENVIRONMENT%" --region %REGION% >nul
)
echo [OK] ETL pipeline schedule configured (daily at 2 AM UTC)

REM Data Retention Schedule
set RETENTION_SCHEDULE_NAME=bdo-data-retention-%ENVIRONMENT%
for /f "tokens=*" %%i in ('aws lambda get-function --function-name retainData --region %REGION% --query Configuration.FunctionArn --output text') do set RETAIN_DATA_ARN=%%i

aws scheduler get-schedule --name %RETENTION_SCHEDULE_NAME% --region %REGION% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [WARNING] Retention schedule already exists. Updating...
    aws scheduler update-schedule --name %RETENTION_SCHEDULE_NAME% --schedule-expression "cron(0 3 1 * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETAIN_DATA_ARN%\",\"RoleArn\":\"arn:aws:iam::%ACCOUNT_ID%:role/EventBridgeSchedulerRole\"}" --region %REGION% >nul
) else (
    aws scheduler create-schedule --name %RETENTION_SCHEDULE_NAME% --schedule-expression "cron(0 3 1 * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETAIN_DATA_ARN%\",\"RoleArn\":\"arn:aws:iam::%ACCOUNT_ID%:role/EventBridgeSchedulerRole\"}" --description "Monthly data retention for BDO Market Insights %ENVIRONMENT%" --region %REGION% >nul
)
echo [OK] Data retention schedule configured (monthly on 1st at 3 AM UTC)

REM Deployment Summary
echo.
echo ==========================================
echo Deployment Complete!
echo ==========================================
echo.
echo [OK] All components deployed successfully!
echo.
echo Deployment Summary:
echo   Environment: %ENVIRONMENT%
echo   Region: %REGION%
echo   Account: %ACCOUNT_ID%
echo.

REM Get API endpoint
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue" --output text') do set API_ENDPOINT=%%i

echo API Endpoint: %API_ENDPOINT%
echo API Documentation: %API_ENDPOINT%/docs
echo OpenAPI Spec: %API_ENDPOINT%/openapi.yaml
echo.

echo Next Steps:
echo   1. Test API Gateway endpoint with the provided API key
echo   2. Verify Lambda functions in AWS Console
echo   3. Check CloudWatch logs for any errors
echo   4. Monitor CloudWatch alarms
echo   5. Verify X-Ray traces are being generated
echo   6. Test ETL pipeline manually (optional)
echo.

echo To test the API:
echo   curl -X GET "%API_ENDPOINT%/query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-31T23:59:59Z" ^
echo     -H "x-api-key: %API_KEY_VALUE%"
echo.

echo [OK] Staging deployment completed successfully!

endlocal
exit /b 0
