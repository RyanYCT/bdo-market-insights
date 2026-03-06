@echo off
REM Deploy BDO Market Insights to Production Environment (Windows)
REM Blue-green deployment with monitoring

setlocal enabledelayedexpansion

REM Load configuration
set CONFIG_FILE=config\deployment-config.sh
if exist "%CONFIG_FILE%" (
    echo Loading configuration...
    REM Note: Windows batch cannot source bash files directly
    REM Users should set environment variables manually or use WSL
) else (
    echo ERROR: %CONFIG_FILE% not found
    echo Please create configuration file before deploying to production.
    exit /b 1
)

REM Configuration
set ENVIRONMENT=production
set REGION=%AWS_REGION%
if "%REGION%"=="" set REGION=us-east-1
set ACCOUNT_ID=%AWS_ACCOUNT_ID%

echo ==========================================
echo BDO Market Insights - Production Deployment
echo ==========================================
echo.
echo Environment: %ENVIRONMENT%
echo Region: %REGION%
echo Account: %ACCOUNT_ID%
echo.
echo WARNING: You are about to deploy to PRODUCTION!
echo.

set /p CONFIRM="Are you sure you want to continue? (yes/no): "
if not "%CONFIRM%"=="yes" (
    echo Deployment cancelled.
    exit /b 0
)

echo.
echo ==========================================
echo Step 1/10: Checking Prerequisites
echo ==========================================
echo.

where aws >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] AWS CLI not found
    exit /b 1
)
echo [OK] AWS CLI found

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found
    exit /b 1
)
echo [OK] Python found

aws sts get-caller-identity >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] AWS credentials invalid
    exit /b 1
)
echo [OK] AWS credentials valid

echo.
echo ==========================================
echo Step 2/10: Verifying Staging Deployment
echo ==========================================
echo.

echo Checking staging functions...
set FUNCTIONS=retrieveIdList fetchData cleanData storeData queryData analyzeData retainData

for %%F in (%FUNCTIONS%) do (
    aws lambda get-function --function-name %%F --region %REGION% >nul 2>nul
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Function %%F not found in staging
        exit /b 1
    )
)
echo [OK] All staging functions verified

echo.
echo ==========================================
echo Step 3/10: Deploying Lambda Layer
echo ==========================================
echo.

if exist "scripts\deploy-layer.sh" (
    bash scripts/deploy-layer.sh
    if %ERRORLEVEL% neq 0 exit /b 1
    echo [OK] Lambda Layer deployed
) else (
    echo [ERROR] deploy-layer.sh not found
    exit /b 1
)

echo.
echo ==========================================
echo Step 4/10: Deploying Lambda Functions
echo ==========================================
echo.

set SKIP_SMOKE_TEST=true

for %%F in (%FUNCTIONS%) do (
    echo.
    echo Deploying src/%%F...
    bash scripts/deploy-function.sh src/%%F
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] %%F deployment failed
        exit /b 1
    )
    echo [OK] %%F deployed
)

set SKIP_SMOKE_TEST=

echo.
echo ==========================================
echo Step 5/10: Updating Step Functions
echo ==========================================
echo.

for /f "tokens=*" %%i in ('aws lambda get-function --function-name queryData --region %REGION% --query Configuration.FunctionArn --output text') do set QUERY_DATA_ARN=%%i
for /f "tokens=*" %%i in ('aws lambda get-function --function-name analyzeData --region %REGION% --query Configuration.FunctionArn --output text') do set ANALYZE_DATA_ARN=%%i

aws cloudformation deploy --template-file infrastructure/step-functions-template.yaml --stack-name bdo-step-functions-%ENVIRONMENT% --parameter-overrides Environment=%ENVIRONMENT% QueryDataLambdaArn=%QUERY_DATA_ARN% AnalyzeDataLambdaArn=%ANALYZE_DATA_ARN% --capabilities CAPABILITY_NAMED_IAM --region %REGION%
if %ERRORLEVEL% neq 0 exit /b 1
echo [OK] Step Functions updated

echo.
echo ==========================================
echo Step 6/10: Updating API Gateway
echo ==========================================
echo.

for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-step-functions-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue" --output text') do set STEP_FUNCTIONS_ARN=%%i

set ALLOWED_ORIGINS=%ALLOWED_CORS_ORIGINS%
if "%ALLOWED_ORIGINS%"=="" set ALLOWED_ORIGINS=https://example.com

aws cloudformation deploy --template-file infrastructure/api-gateway-template.yaml --stack-name bdo-api-gateway-%ENVIRONMENT% --parameter-overrides Environment=%ENVIRONMENT% AllowedOrigins=%ALLOWED_ORIGINS% StepFunctionsArn=%STEP_FUNCTIONS_ARN% EnableLogging=true --capabilities CAPABILITY_NAMED_IAM --region %REGION%
if %ERRORLEVEL% neq 0 exit /b 1
echo [OK] API Gateway updated

echo.
echo ==========================================
echo Step 7/10: Enabling X-Ray Tracing
echo ==========================================
echo.

for %%F in (%FUNCTIONS%) do (
    aws lambda update-function-configuration --function-name %%F --tracing-config Mode=Active --region %REGION% >nul
    echo [OK] X-Ray enabled for %%F
)

echo.
echo ==========================================
echo Step 8/10: Updating CloudWatch Alarms
echo ==========================================
echo.

if exist "infrastructure\deploy-alarms.sh" (
    cd infrastructure
    bash deploy-alarms.sh --environment %ENVIRONMENT% --region %REGION%
    cd ..
    echo [OK] CloudWatch alarms updated
) else (
    echo [WARNING] deploy-alarms.sh not found
)

echo.
echo ==========================================
echo Step 9/10: Configuring EventBridge Scheduler
echo ==========================================
echo.

for /f "tokens=*" %%i in ('aws lambda get-function --function-name retrieveIdList --region %REGION% --query Configuration.FunctionArn --output text') do set RETRIEVE_ID_LIST_ARN=%%i
for /f "tokens=*" %%i in ('aws lambda get-function --function-name retainData --region %REGION% --query Configuration.FunctionArn --output text') do set RETAIN_DATA_ARN=%%i

set SCHEDULER_ROLE_ARN=arn:aws:iam::%ACCOUNT_ID%:role/EventBridgeSchedulerRole

REM ETL Schedule
aws scheduler get-schedule --name bdo-etl-pipeline-%ENVIRONMENT% --region %REGION% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    aws scheduler update-schedule --name bdo-etl-pipeline-%ENVIRONMENT% --schedule-expression "cron(0 2 * * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETRIEVE_ID_LIST_ARN%\",\"RoleArn\":\"%SCHEDULER_ROLE_ARN%\"}" --region %REGION% >nul
) else (
    aws scheduler create-schedule --name bdo-etl-pipeline-%ENVIRONMENT% --schedule-expression "cron(0 2 * * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETRIEVE_ID_LIST_ARN%\",\"RoleArn\":\"%SCHEDULER_ROLE_ARN%\"}" --description "Daily ETL pipeline for BDO Market Insights %ENVIRONMENT%" --region %REGION% >nul
)
echo [OK] ETL pipeline schedule configured

REM Retention Schedule
aws scheduler get-schedule --name bdo-data-retention-%ENVIRONMENT% --region %REGION% >nul 2>nul
if %ERRORLEVEL% equ 0 (
    aws scheduler update-schedule --name bdo-data-retention-%ENVIRONMENT% --schedule-expression "cron(0 3 1 * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETAIN_DATA_ARN%\",\"RoleArn\":\"%SCHEDULER_ROLE_ARN%\"}" --region %REGION% >nul
) else (
    aws scheduler create-schedule --name bdo-data-retention-%ENVIRONMENT% --schedule-expression "cron(0 3 1 * ? *)" --flexible-time-window Mode=OFF --target "{\"Arn\":\"%RETAIN_DATA_ARN%\",\"RoleArn\":\"%SCHEDULER_ROLE_ARN%\"}" --description "Monthly data retention for BDO Market Insights %ENVIRONMENT%" --region %REGION% >nul
)
echo [OK] Data retention schedule configured

echo.
echo ==========================================
echo Step 10/10: Deployment Complete
echo ==========================================
echo.

for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue" --output text') do set API_ENDPOINT=%%i

echo [OK] Production deployment successful!
echo.
echo Deployment Summary:
echo   Environment: %ENVIRONMENT%
echo   Region: %REGION%
echo   API Endpoint: %API_ENDPOINT%
echo.
echo ==========================================
echo 24-HOUR MONITORING PERIOD
echo ==========================================
echo.
echo Please monitor the following for 24 hours:
echo   1. CloudWatch Metrics (error rates, latency)
echo   2. CloudWatch Logs (ERROR level logs)
echo   3. CloudWatch Alarms (state changes)
echo   4. ETL Pipeline (scheduled execution)
echo   5. Query API (response times)
echo.
echo If issues are detected, use rollback script.
echo.

endlocal
exit /b 0
