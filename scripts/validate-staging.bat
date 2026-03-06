@echo off
REM Staging Validation Script for BDO Market Insights (Windows)
REM This script validates the deployed staging environment

setlocal enabledelayedexpansion

REM Configuration
set ENVIRONMENT=staging
if not "%ENVIRONMENT%"=="" set ENVIRONMENT=%ENVIRONMENT%
set REGION=us-east-1
if not "%AWS_REGION%"=="" set REGION=%AWS_REGION%

REM Test results tracking
set TESTS_PASSED=0
set TESTS_FAILED=0
set TESTS_TOTAL=0

echo ==========================================
echo BDO Market Insights - Staging Validation
echo ==========================================
echo.
echo Environment: %ENVIRONMENT%
echo Region: %REGION%
echo.

REM Check prerequisites
echo ==========================================
echo Checking Prerequisites
echo ==========================================
echo.

where aws >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] AWS CLI not found. Please install AWS CLI v2.
    exit /b 1
)
echo [INFO] AWS CLI found

where curl >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] curl not found. Please install curl.
    exit /b 1
)
echo [INFO] curl found

where jq >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] jq not found. Please install jq for JSON parsing.
    echo Download from: https://stedolan.github.io/jq/download/
    exit /b 1
)
echo [INFO] jq found

REM Verify AWS credentials
aws sts get-caller-identity >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] AWS credentials invalid or not configured
    exit /b 1
)
echo [INFO] AWS credentials valid

REM Get AWS Account ID
for /f "tokens=*" %%i in ('aws sts get-caller-identity --query Account --output text') do set ACCOUNT_ID=%%i
echo [INFO] AWS Account ID: %ACCOUNT_ID%

REM Get API Gateway endpoint
echo.
echo ==========================================
echo Retrieving API Configuration
echo ==========================================
echo.

for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue" --output text 2^>nul') do set API_ENDPOINT=%%i

if "%API_ENDPOINT%"=="" (
    echo [ERROR] Could not retrieve API endpoint. Is the API Gateway deployed?
    exit /b 1
)
echo [INFO] API Endpoint: %API_ENDPOINT%

REM Get API Key
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`APIKey`].OutputValue" --output text 2^>nul') do set API_KEY_ID=%%i

if "%API_KEY_ID%"=="" (
    echo [ERROR] Could not retrieve API key ID
    exit /b 1
)

for /f "tokens=*" %%i in ('aws apigateway get-api-key --api-key %API_KEY_ID% --include-value --region %REGION% --query value --output text 2^>nul') do set API_KEY=%%i

if "%API_KEY%"=="" (
    echo [ERROR] Could not retrieve API key value
    exit /b 1
)
echo [INFO] API Key retrieved successfully

REM Test 1: Execute ETL Pipeline
echo.
echo ==========================================
echo Test 1: ETL Pipeline Execution
echo ==========================================
echo.

echo [INFO] Invoking retrieveIdList Lambda...
aws lambda invoke --function-name retrieveIdList --region %REGION% --log-type Tail --payload {} %TEMP%\retrieve-id-list-response.json >nul 2>&1

if %errorlevel% equ 0 (
    type %TEMP%\retrieve-id-list-response.json | jq -e ".item_ids" >nul 2>&1
    if !errorlevel! equ 0 (
        set /a TESTS_TOTAL+=1
        set /a TESTS_PASSED+=1
        echo [PASS] TEST !TESTS_TOTAL!: ETL Pipeline - retrieveIdList executed successfully
    ) else (
        set /a TESTS_TOTAL+=1
        set /a TESTS_FAILED+=1
        echo [FAIL] TEST !TESTS_TOTAL!: ETL Pipeline - retrieveIdList returned invalid response
    )
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: ETL Pipeline - retrieveIdList invocation failed
)

del %TEMP%\retrieve-id-list-response.json >nul 2>&1

REM Calculate date range
for /f "tokens=*" %%i in ('powershell -Command "(Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')"') do set END_DATE=%%i
for /f "tokens=*" %%i in ('powershell -Command "(Get-Date).AddDays(-30).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')"') do set START_DATE=%%i

REM Test 2: Query API with Valid Parameters
echo.
echo ==========================================
echo Test 2: Query API with Valid Parameters
echo ==========================================
echo.

echo [INFO] Testing query with item_id parameter...
set QUERY_URL=%API_ENDPOINT%/query?item_id=1001^&start_date=%START_DATE%^&end_date=%END_DATE%^&limit=10

curl -s -w "\n%%{http_code}" -X GET "%QUERY_URL%" -H "x-api-key: %API_KEY%" -H "Content-Type: application/json" > %TEMP%\query-response.txt 2>&1

for /f "tokens=*" %%i in ('powershell -Command "Get-Content %TEMP%\query-response.txt | Select-Object -Last 1"') do set HTTP_CODE=%%i

if "%HTTP_CODE%"=="200" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: Query API - Valid request with item_id returned 200
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: Query API - Expected 200, got %HTTP_CODE%
)

del %TEMP%\query-response.txt >nul 2>&1

REM Test 3: API Key Authentication
echo.
echo ==========================================
echo Test 3: API Key Authentication
echo ==========================================
echo.

echo [INFO] Testing request without API key...
curl -s -w "\n%%{http_code}" -X GET "%QUERY_URL%" -H "Content-Type: application/json" > %TEMP%\auth-response.txt 2>&1

for /f "tokens=*" %%i in ('powershell -Command "Get-Content %TEMP%\auth-response.txt | Select-Object -Last 1"') do set HTTP_CODE=%%i

if "%HTTP_CODE%"=="403" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: API Authentication - Request without API key rejected
) else if "%HTTP_CODE%"=="401" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: API Authentication - Request without API key rejected
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: API Authentication - Expected 401/403, got %HTTP_CODE%
)

del %TEMP%\auth-response.txt >nul 2>&1

REM Test 4: CloudWatch Logs
echo.
echo ==========================================
echo Test 4: CloudWatch Logs Validation
echo ==========================================
echo.

echo [INFO] Checking CloudWatch logs for retrieveIdList...
set LOG_GROUP=/aws/lambda/retrieveIdList

for /f "tokens=*" %%i in ('aws logs describe-log-streams --log-group-name %LOG_GROUP% --order-by LastEventTime --descending --max-items 1 --region %REGION% --query "logStreams[0].logStreamName" --output text 2^>nul') do set LOG_STREAM=%%i

if not "%LOG_STREAM%"=="" if not "%LOG_STREAM%"=="None" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: CloudWatch Logs - Log streams found
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: CloudWatch Logs - Could not retrieve log streams
)

REM Test 5: X-Ray Traces
echo.
echo ==========================================
echo Test 5: X-Ray Traces Validation
echo ==========================================
echo.

echo [INFO] Checking X-Ray configuration...
for /f "tokens=*" %%i in ('aws lambda get-function-configuration --function-name retrieveIdList --region %REGION% --query "TracingConfig.Mode" --output text 2^>nul') do set XRAY_MODE=%%i

if "%XRAY_MODE%"=="Active" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: X-Ray Traces - X-Ray tracing is enabled
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: X-Ray Traces - X-Ray tracing is not enabled
)

REM Test 6: Custom Metrics
echo.
echo ==========================================
echo Test 6: Custom Metrics Validation
echo ==========================================
echo.

echo [INFO] Checking CloudWatch custom metrics...
aws cloudwatch list-metrics --namespace "BDO/MarketInsights" --region %REGION% --output json > %TEMP%\metrics.json 2>&1

if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('type %TEMP%\metrics.json ^| jq ".Metrics | length"') do set METRIC_COUNT=%%i
    if !METRIC_COUNT! gtr 0 (
        set /a TESTS_TOTAL+=1
        set /a TESTS_PASSED+=1
        echo [PASS] TEST !TESTS_TOTAL!: Custom Metrics - Found !METRIC_COUNT! custom metrics
    ) else (
        set /a TESTS_TOTAL+=1
        set /a TESTS_FAILED+=1
        echo [FAIL] TEST !TESTS_TOTAL!: Custom Metrics - No custom metrics found
    )
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: Custom Metrics - Could not retrieve metrics
)

del %TEMP%\metrics.json >nul 2>&1

REM Test 7: Error Handling
echo.
echo ==========================================
echo Test 7: Error Handling Validation
echo ==========================================
echo.

echo [INFO] Testing invalid query parameters...
set ERROR_URL=%API_ENDPOINT%/query?item_id=invalid^&start_date=%START_DATE%^&end_date=%END_DATE%

curl -s -w "\n%%{http_code}" -X GET "%ERROR_URL%" -H "x-api-key: %API_KEY%" -H "Content-Type: application/json" > %TEMP%\error-response.txt 2>&1

for /f "tokens=*" %%i in ('powershell -Command "Get-Content %TEMP%\error-response.txt | Select-Object -Last 1"') do set HTTP_CODE=%%i

if "%HTTP_CODE%"=="400" (
    set /a TESTS_TOTAL+=1
    set /a TESTS_PASSED+=1
    echo [PASS] TEST !TESTS_TOTAL!: Error Handling - Invalid parameters return 400
) else (
    set /a TESTS_TOTAL+=1
    set /a TESTS_FAILED+=1
    echo [FAIL] TEST !TESTS_TOTAL!: Error Handling - Expected 400, got %HTTP_CODE%
)

del %TEMP%\error-response.txt >nul 2>&1

REM Test Summary
echo.
echo ==========================================
echo Validation Summary
echo ==========================================
echo.
echo Total Tests: %TESTS_TOTAL%
echo Passed: %TESTS_PASSED%
echo Failed: %TESTS_FAILED%
echo.

if %TESTS_FAILED% equ 0 (
    echo ==========================================
    echo All validation tests passed!
    echo ==========================================
    echo.
    echo Staging environment is ready for use.
    exit /b 0
) else (
    echo ==========================================
    echo Some validation tests failed
    echo ==========================================
    echo.
    echo Please review the failed tests above.
    exit /b 1
)
