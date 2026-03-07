@echo off
REM Deploy Lambda Layer script for Windows
REM This script builds and deploys the Lambda Layer to AWS using Docker

setlocal enabledelayedexpansion

REM Configuration
set LAYER_NAME=bdo-market-insights-common
set PYTHON_VERSION=python3.14
if "%AWS_REGION%"=="" set AWS_REGION=us-east-1
set REGION=%AWS_REGION%

echo ==========================================
echo Deploying Lambda Layer: %LAYER_NAME%
echo ==========================================

REM Navigate to lambda_layer directory
cd lambda_layer

REM Create temporary build directory
echo Creating build directory...
set BUILD_DIR=build
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
mkdir "%BUILD_DIR%\python"

REM Clean previous package
echo Cleaning previous builds...
if exist lambda-layer.zip del lambda-layer.zip

REM Copy common code to build directory
echo Copying common code...
if exist "python\common" (
    xcopy /E /I /Y "python\common" "%BUILD_DIR%\python\common"
) else (
    echo Error: python\common directory not found!
    exit /b 1
)

REM Check if Docker is available
docker --version >nul 2>&1
if %errorlevel% equ 0 (
    echo Using Docker to build Lambda-compatible packages...
    docker run --rm --entrypoint /bin/bash -v "%cd%:/var/task" -w /var/task public.ecr.aws/lambda/python:3.14 -c "pip install -r requirements.txt -t %BUILD_DIR%/python/ --upgrade"
) else (
    echo Docker not found. Attempting to install with platform-specific wheels...
    pip install -r requirements.txt -t "%BUILD_DIR%\python\" --upgrade --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.11
)

if %errorlevel% neq 0 (
    echo Error: Failed to install dependencies
    exit /b 1
)

REM Create zip file
echo Creating deployment package...
REM Stay in lambda_layer directory and zip the build/python directory
REM This preserves the python/ directory structure required by Lambda layers
powershell -command "Compress-Archive -Path %BUILD_DIR%\python -DestinationPath lambda-layer.zip -Force"

REM Get file size
for %%A in (lambda-layer.zip) do set SIZE=%%~zA
set /a SIZE_MB=SIZE/1024/1024
echo Package size: %SIZE_MB% MB

REM Publish layer
echo Publishing Lambda Layer...
for /f "tokens=*" %%i in ('aws lambda publish-layer-version --layer-name "%LAYER_NAME%" --description "Common utilities for BDO Market Insights ETL pipeline - Python 3.14 - %date%" --zip-file fileb://lambda-layer.zip --compatible-runtimes "%PYTHON_VERSION%" --region "%REGION%" --query "Version" --output text') do set LAYER_VERSION=%%i

echo ==========================================
echo Lambda Layer published successfully!
echo Layer Name: %LAYER_NAME%
echo Version: %LAYER_VERSION%
echo Region: %REGION%
echo ==========================================

REM Save layer version to file
echo %LAYER_VERSION% > layer-version.txt
echo Layer version saved to layer-version.txt

REM Return to root directory
cd ..

exit /b 0
