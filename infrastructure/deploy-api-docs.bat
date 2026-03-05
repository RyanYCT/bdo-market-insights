@echo off
REM Deploy API Documentation to S3
REM This script uploads the OpenAPI specification and Swagger UI to the S3 bucket

setlocal enabledelayedexpansion

REM Check if environment is provided
if "%1"=="" (
    echo [ERROR] Environment not specified
    echo Usage: %0 ^<environment^> [aws-region]
    echo Example: %0 dev us-east-1
    exit /b 1
)

set ENVIRONMENT=%1
set AWS_REGION=%2
if "%AWS_REGION%"=="" set AWS_REGION=us-east-1

echo [INFO] Deploying API documentation for environment: %ENVIRONMENT%
echo [INFO] AWS Region: %AWS_REGION%

REM Get AWS Account ID
for /f "tokens=*" %%i in ('aws sts get-caller-identity --query Account --output text') do set ACCOUNT_ID=%%i
if "%ACCOUNT_ID%"=="" (
    echo [ERROR] Failed to get AWS Account ID. Check your AWS credentials.
    exit /b 1
)

echo [INFO] AWS Account ID: %ACCOUNT_ID%

REM Construct bucket name
set BUCKET_NAME=bdo-api-docs-%ENVIRONMENT%-%ACCOUNT_ID%
echo [INFO] Target S3 bucket: %BUCKET_NAME%

REM Check if bucket exists
aws s3 ls s3://%BUCKET_NAME% --region %AWS_REGION% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] S3 bucket %BUCKET_NAME% does not exist
    echo [INFO] Please deploy the API Gateway CloudFormation stack first
    exit /b 1
)

REM Get the API Gateway endpoint URL
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %AWS_REGION% --query "Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue" --output text 2^>nul') do set API_ENDPOINT=%%i

if "%API_ENDPOINT%"=="" (
    echo [WARNING] Could not retrieve API endpoint from CloudFormation stack
    echo [WARNING] OpenAPI spec will use placeholder URLs
) else (
    echo [INFO] API Endpoint: %API_ENDPOINT%
)

REM Update OpenAPI spec with actual server URLs
set OPENAPI_SPEC=infrastructure\openapi-spec.yaml
set TEMP_SPEC=%TEMP%\openapi-spec-%ENVIRONMENT%.yaml

if not exist "%OPENAPI_SPEC%" (
    echo [ERROR] OpenAPI spec file not found: %OPENAPI_SPEC%
    exit /b 1
)

echo [INFO] Updating OpenAPI spec with environment-specific URLs...
copy /Y "%OPENAPI_SPEC%" "%TEMP_SPEC%" >nul

REM Update server URLs if API endpoint is available
if not "%API_ENDPOINT%"=="" (
    powershell -Command "(Get-Content '%TEMP_SPEC%') -replace 'url: https://api.example.com/dev', 'url: %API_ENDPOINT%' -replace 'url: https://api.example.com/staging', 'url: %API_ENDPOINT%' -replace 'url: https://api.example.com/prod', 'url: %API_ENDPOINT%' | Set-Content '%TEMP_SPEC%'"
)

REM Upload OpenAPI specification
echo [INFO] Uploading OpenAPI specification...
aws s3 cp "%TEMP_SPEC%" s3://%BUCKET_NAME%/openapi.yaml --region %AWS_REGION% --content-type "application/x-yaml" --cache-control "public, max-age=300" --metadata "environment=%ENVIRONMENT%"

if errorlevel 1 (
    echo [ERROR] Failed to upload OpenAPI spec
    exit /b 1
)
echo [INFO] OpenAPI spec uploaded successfully

REM Upload Swagger UI
set SWAGGER_UI_DIR=infrastructure\swagger-ui
if not exist "%SWAGGER_UI_DIR%" (
    echo [ERROR] Swagger UI directory not found: %SWAGGER_UI_DIR%
    exit /b 1
)

echo [INFO] Uploading Swagger UI...
aws s3 cp "%SWAGGER_UI_DIR%\index.html" s3://%BUCKET_NAME%/index.html --region %AWS_REGION% --content-type "text/html" --cache-control "public, max-age=300" --metadata "environment=%ENVIRONMENT%"

if errorlevel 1 (
    echo [ERROR] Failed to upload Swagger UI
    exit /b 1
)
echo [INFO] Swagger UI uploaded successfully

REM Create a simple error page
echo [INFO] Creating error page...
(
echo ^<!DOCTYPE html^>
echo ^<html lang="en"^>
echo ^<head^>
echo     ^<meta charset="UTF-8"^>
echo     ^<meta name="viewport" content="width=device-width, initial-scale=1.0"^>
echo     ^<title^>Error - BDO Market Insights API^</title^>
echo     ^<style^>
echo         body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f5f5f5; }
echo         .error-container { text-align: center; padding: 40px; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
echo         h1 { color: #dc3545; margin-bottom: 20px; }
echo         p { color: #666; margin-bottom: 30px; }
echo         a { color: #4990e2; text-decoration: none; font-weight: bold; }
echo         a:hover { text-decoration: underline; }
echo     ^</style^>
echo ^</head^>
echo ^<body^>
echo     ^<div class="error-container"^>
echo         ^<h1^>404 - Page Not Found^</h1^>
echo         ^<p^>The page you're looking for doesn't exist.^</p^>
echo         ^<a href="/index.html"^>Go to API Documentation^</a^>
echo     ^</div^>
echo ^</body^>
echo ^</html^>
) > %TEMP%\error.html

aws s3 cp %TEMP%\error.html s3://%BUCKET_NAME%/error.html --region %AWS_REGION% --content-type "text/html" --cache-control "public, max-age=300"

del /f /q %TEMP%\error.html >nul 2>&1

REM Clean up temp files
del /f /q "%TEMP_SPEC%" >nul 2>&1

REM Get the documentation URLs
for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %AWS_REGION% --query "Stacks[0].Outputs[?OutputKey==`APIDocsURL`].OutputValue" --output text 2^>nul') do set DOCS_URL=%%i

for /f "tokens=*" %%i in ('aws cloudformation describe-stacks --stack-name bdo-api-gateway-%ENVIRONMENT% --region %AWS_REGION% --query "Stacks[0].Outputs[?OutputKey==`OpenAPISpecURL`].OutputValue" --output text 2^>nul') do set OPENAPI_URL=%%i

REM Print summary
echo.
echo [INFO] ==========================================
echo [INFO] API Documentation Deployment Complete!
echo [INFO] ==========================================
echo.

if not "%DOCS_URL%"=="" (
    echo [INFO] Swagger UI: %DOCS_URL%
) else (
    echo [INFO] Swagger UI: https://^<api-id^>.execute-api.%AWS_REGION%.amazonaws.com/%ENVIRONMENT%/docs
)

if not "%OPENAPI_URL%"=="" (
    echo [INFO] OpenAPI Spec: %OPENAPI_URL%
) else (
    echo [INFO] OpenAPI Spec: https://^<api-id^>.execute-api.%AWS_REGION%.amazonaws.com/%ENVIRONMENT%/openapi.yaml
)

echo [INFO] S3 Bucket: s3://%BUCKET_NAME%
echo.
echo [INFO] You can now access the interactive API documentation through the Swagger UI URL above.
echo [INFO] No API key is required to view the documentation.
echo.

endlocal
