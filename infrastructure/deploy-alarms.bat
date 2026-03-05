@echo off
REM BDO Market Insights - CloudWatch Alarms Deployment Script (Windows)
REM This script deploys CloudWatch alarms for monitoring the BDO Market Insights system

setlocal enabledelayedexpansion

REM Default values
set REGION=us-east-1
set ENVIRONMENT=
set EMAIL=

REM Parse command line arguments
:parse_args
if "%~1"=="" goto validate_args
if /i "%~1"=="-e" (
    set ENVIRONMENT=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--environment" (
    set ENVIRONMENT=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="-r" (
    set REGION=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--region" (
    set REGION=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="-m" (
    set EMAIL=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--email" (
    set EMAIL=%~2
    shift
    shift
    goto parse_args
)
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
echo [ERROR] Unknown option: %~1
goto usage

:validate_args
if "%ENVIRONMENT%"=="" (
    echo [ERROR] Environment is required
    goto usage
)

if not "%ENVIRONMENT%"=="dev" if not "%ENVIRONMENT%"=="staging" if not "%ENVIRONMENT%"=="prod" (
    echo [ERROR] Environment must be one of: dev, staging, prod
    exit /b 1
)

REM Set stack name
set STACK_NAME=bdo-cloudwatch-alarms-%ENVIRONMENT%

REM Set Lambda function names based on environment
set RETRIEVE_ID_LIST_FUNCTION=retrieveIdList-%ENVIRONMENT%
set FETCH_DATA_FUNCTION=fetchData-%ENVIRONMENT%
set CLEAN_DATA_FUNCTION=cleanData-%ENVIRONMENT%
set STORE_DATA_FUNCTION=storeData-%ENVIRONMENT%
set QUERY_DATA_FUNCTION=queryData-%ENVIRONMENT%
set ANALYZE_DATA_FUNCTION=analyzeData-%ENVIRONMENT%

echo [INFO] Deploying CloudWatch alarms for BDO Market Insights
echo [INFO] Environment: %ENVIRONMENT%
echo [INFO] Region: %REGION%
echo [INFO] Stack Name: %STACK_NAME%

if not "%EMAIL%"=="" (
    echo [INFO] Email notifications: %EMAIL%
) else (
    echo [WARNING] No email address provided - alarms will be created without email notifications
)

REM Build parameter overrides
set PARAMETERS=Environment=%ENVIRONMENT%
set PARAMETERS=%PARAMETERS% RetrieveIdListFunctionName=%RETRIEVE_ID_LIST_FUNCTION%
set PARAMETERS=%PARAMETERS% FetchDataFunctionName=%FETCH_DATA_FUNCTION%
set PARAMETERS=%PARAMETERS% CleanDataFunctionName=%CLEAN_DATA_FUNCTION%
set PARAMETERS=%PARAMETERS% StoreDataFunctionName=%STORE_DATA_FUNCTION%
set PARAMETERS=%PARAMETERS% QueryDataFunctionName=%QUERY_DATA_FUNCTION%
set PARAMETERS=%PARAMETERS% AnalyzeDataFunctionName=%ANALYZE_DATA_FUNCTION%

if not "%EMAIL%"=="" (
    set PARAMETERS=%PARAMETERS% AlarmEmailAddress=%EMAIL%
)

REM Check if AWS CLI is installed
where aws >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] AWS CLI is not installed. Please install it first.
    exit /b 1
)

REM Check if template file exists
set TEMPLATE_FILE=cloudwatch-alarms-template.yaml
if not exist "%TEMPLATE_FILE%" (
    echo [ERROR] Template file not found: %TEMPLATE_FILE%
    echo [ERROR] Please run this script from the infrastructure directory
    exit /b 1
)

REM Validate CloudFormation template
echo [INFO] Validating CloudFormation template...
aws cloudformation validate-template --template-body file://%TEMPLATE_FILE% --region %REGION% >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [INFO] Template validation successful
) else (
    echo [ERROR] Template validation failed
    exit /b 1
)

REM Deploy CloudFormation stack
echo [INFO] Deploying CloudFormation stack...
aws cloudformation deploy ^
    --template-file %TEMPLATE_FILE% ^
    --stack-name %STACK_NAME% ^
    --parameter-overrides %PARAMETERS% ^
    --capabilities CAPABILITY_NAMED_IAM ^
    --region %REGION% ^
    --no-fail-on-empty-changeset

if %ERRORLEVEL% equ 0 (
    echo [INFO] Stack deployment successful
) else (
    echo [ERROR] Stack deployment failed
    exit /b 1
)

REM Get stack outputs
echo [INFO] Retrieving stack outputs...
for /f "delims=" %%i in ('aws cloudformation describe-stacks --stack-name %STACK_NAME% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicArn`].OutputValue" --output text') do set TOPIC_ARN=%%i
for /f "delims=" %%i in ('aws cloudformation describe-stacks --stack-name %STACK_NAME% --region %REGION% --query "Stacks[0].Outputs[?OutputKey==`AlarmNotificationTopicName`].OutputValue" --output text') do set TOPIC_NAME=%%i

echo.
echo [INFO] Deployment complete!
echo.
echo [INFO] Stack Outputs:
echo   SNS Topic ARN: %TOPIC_ARN%
echo   SNS Topic Name: %TOPIC_NAME%
echo.

if not "%EMAIL%"=="" (
    echo [WARNING] IMPORTANT: Check your email ^(%EMAIL%^) and confirm the SNS subscription
    echo [WARNING] You will not receive alarm notifications until you confirm the subscription
    echo.
)

REM List created alarms
echo [INFO] Created alarms:
aws cloudwatch describe-alarms --alarm-name-prefix "bdo-" --region %REGION% --query "MetricAlarms[?contains(AlarmName, '%ENVIRONMENT%')].AlarmName" --output table

echo.
echo [INFO] To view alarm status, run:
echo   aws cloudwatch describe-alarms --alarm-name-prefix "bdo-" --region %REGION%
echo.
echo [INFO] To test an alarm, see the CLOUDWATCH_ALARMS_README.md file

goto :eof

:usage
echo Usage: %~nx0 [OPTIONS]
echo.
echo Deploy CloudWatch alarms for BDO Market Insights
echo.
echo OPTIONS:
echo     -e, --environment ENV       Environment (dev, staging, prod) [required]
echo     -r, --region REGION         AWS region (default: us-east-1)
echo     -m, --email EMAIL           Email address for alarm notifications
echo     -h, --help                  Display this help message
echo.
echo EXAMPLES:
echo     REM Deploy to dev environment with email notifications
echo     %~nx0 --environment dev --email devops@example.com
echo.
echo     REM Deploy to production in a specific region
echo     %~nx0 --environment prod --region us-west-2 --email alerts@example.com
echo.
echo     REM Deploy without email notifications
echo     %~nx0 --environment staging
exit /b 1
