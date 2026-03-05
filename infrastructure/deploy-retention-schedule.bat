@echo off
REM Deploy EventBridge Scheduler and IAM permissions for BDO Market Insights Data Retention Lambda
REM Usage: deploy-retention-schedule.bat <environment> <function-arn>
REM Example: deploy-retention-schedule.bat dev arn:aws:lambda:us-east-1:123456789012:function:retainData

setlocal enabledelayedexpansion

REM Check arguments
if "%~2"=="" (
    echo Usage: %0 ^<environment^> ^<function-arn^>
    echo Example: %0 dev arn:aws:lambda:us-east-1:123456789012:function:retainData
    exit /b 1
)

set ENVIRONMENT=%1
set FUNCTION_ARN=%2
set STACK_NAME=bdo-retention-schedule-%ENVIRONMENT%

REM Optional parameters with defaults
if "%~3"=="" (set FUNCTION_NAME=retainData) else (set FUNCTION_NAME=%3)
if "%~4"=="" (set ARCHIVE_BUCKET=bdo-market-insights-archive) else (set ARCHIVE_BUCKET=%4)
if "%~5"=="" (set SCHEDULE_EXPRESSION=cron(0 2 1 * ? *)) else (set SCHEDULE_EXPRESSION=%5)
if "%~6"=="" (set RETENTION_DETAILED_DAYS=90) else (set RETENTION_DETAILED_DAYS=%6)
if "%~7"=="" (set RETENTION_SUMMARY_DAYS=730) else (set RETENTION_SUMMARY_DAYS=%7)

echo ==========================================
echo Deploying BDO Retention Schedule
echo ==========================================
echo Environment: %ENVIRONMENT%
echo Stack Name: %STACK_NAME%
echo Function ARN: %FUNCTION_ARN%
echo Function Name: %FUNCTION_NAME%
echo Archive Bucket: %ARCHIVE_BUCKET%-%ENVIRONMENT%
echo Schedule: %SCHEDULE_EXPRESSION%
echo Retention Detailed Days: %RETENTION_DETAILED_DAYS%
echo Retention Summary Days: %RETENTION_SUMMARY_DAYS%
echo ==========================================

REM Deploy CloudFormation stack
aws cloudformation deploy ^
  --template-file retention-schedule-template.yaml ^
  --stack-name %STACK_NAME% ^
  --parameter-overrides ^
    Environment=%ENVIRONMENT% ^
    RetainDataFunctionName=%FUNCTION_NAME% ^
    RetainDataFunctionArn=%FUNCTION_ARN% ^
    ArchiveBucketName=%ARCHIVE_BUCKET% ^
    ScheduleExpression="%SCHEDULE_EXPRESSION%" ^
    RetentionDetailedDays=%RETENTION_DETAILED_DAYS% ^
    RetentionSummaryDays=%RETENTION_SUMMARY_DAYS% ^
  --capabilities CAPABILITY_NAMED_IAM ^
  --tags ^
    Environment=%ENVIRONMENT% ^
    Application=BDO-Market-Insights ^
    ManagedBy=CloudFormation

if %errorlevel% equ 0 (
    echo.
    echo ==========================================
    echo Deployment successful!
    echo ==========================================
    
    REM Get outputs
    echo.
    echo Stack Outputs:
    aws cloudformation describe-stacks ^
      --stack-name %STACK_NAME% ^
      --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" ^
      --output table
    
    echo.
    echo Archive Bucket:
    for /f "delims=" %%i in ('aws cloudformation describe-stacks --stack-name %STACK_NAME% --query "Stacks[0].Outputs[?OutputKey==`ArchiveBucketName`].OutputValue" --output text') do set BUCKET_NAME=%%i
    echo   !BUCKET_NAME!
    
    echo.
    echo EventBridge Schedule:
    for /f "delims=" %%i in ('aws cloudformation describe-stacks --stack-name %STACK_NAME% --query "Stacks[0].Outputs[?OutputKey==`RetentionScheduleName`].OutputValue" --output text') do set SCHEDULE_NAME=%%i
    echo   !SCHEDULE_NAME!
    
    echo.
    echo Next Steps:
    echo 1. Verify the Lambda function has the correct IAM role attached
    echo 2. Update Lambda environment variables:
    echo    - ARCHIVE_BUCKET_NAME=!BUCKET_NAME!
    echo    - RETENTION_DETAILED_DAYS=%RETENTION_DETAILED_DAYS%
    echo    - RETENTION_SUMMARY_DAYS=%RETENTION_SUMMARY_DAYS%
    echo 3. Test the schedule by invoking the Lambda manually
    echo 4. Monitor CloudWatch alarms for retention failures
    
) else (
    echo.
    echo ==========================================
    echo Deployment failed!
    echo ==========================================
    exit /b 1
)

endlocal

