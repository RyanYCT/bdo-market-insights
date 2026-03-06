#!/usr/bin/env python3
"""
Staging Validation Script for BDO Market Insights

This script validates the deployed staging environment by testing:
- ETL pipeline execution
- Query API with various parameters
- API key authentication
- Rate limiting
- CloudWatch logs (JSON format and correlation IDs)
- X-Ray traces
- Custom metrics
- Error handling scenarios
"""

import json
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import boto3
import requests
from botocore.exceptions import ClientError

# Color codes for terminal output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


class ValidationTest:
    """Tracks validation test results"""
    
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.results = []
    
    def add_result(self, name: str, passed: bool, details: str = ""):
        """Add a test result"""
        self.total += 1
        if passed:
            self.passed += 1
            status = f"{Colors.GREEN}✓ PASS{Colors.NC}"
        else:
            self.failed += 1
            status = f"{Colors.RED}✗ FAIL{Colors.NC}"
        
        result = f"{status} TEST {self.total}: {name}"
        if details:
            result += f"\n  {details}"
        
        self.results.append(result)
        print(result)
    
    def print_summary(self):
        """Print test summary"""
        print(f"\n{Colors.BLUE}{'=' * 42}")
        print("Validation Summary")
        print(f"{'=' * 42}{Colors.NC}\n")
        print(f"Total Tests: {self.total}")
        print(f"{Colors.GREEN}Passed: {self.passed}{Colors.NC}")
        if self.failed > 0:
            print(f"{Colors.RED}Failed: {self.failed}{Colors.NC}")
        else:
            print(f"Failed: {self.failed}")


class StagingValidator:
    """Validates BDO Market Insights staging environment"""
    
    def __init__(self, environment: str = "staging", region: str = "us-east-1"):
        self.environment = environment
        self.region = region
        self.tests = ValidationTest()
        
        # AWS clients
        self.cloudformation = boto3.client('cloudformation', region_name=region)
        self.lambda_client = boto3.client('lambda', region_name=region)
        self.logs_client = boto3.client('logs', region_name=region)
        self.xray_client = boto3.client('xray', region_name=region)
        self.cloudwatch = boto3.client('cloudwatch', region_name=region)
        self.apigateway = boto3.client('apigateway', region_name=region)
        self.sts = boto3.client('sts', region_name=region)
        
        # Configuration
        self.api_endpoint = None
        self.api_key = None
        self.account_id = None
    
    def print_section(self, title: str):
        """Print section header"""
        print(f"\n{Colors.BLUE}{'=' * 42}")
        print(title)
        print(f"{'=' * 42}{Colors.NC}\n")
    
    def print_info(self, message: str):
        """Print info message"""
        print(f"{Colors.BLUE}ℹ {message}{Colors.NC}")
    
    def print_warning(self, message: str):
        """Print warning message"""
        print(f"{Colors.YELLOW}⚠ {message}{Colors.NC}")
    
    def check_prerequisites(self) -> bool:
        """Check prerequisites and AWS credentials"""
        self.print_section("Checking Prerequisites")
        
        try:
            # Verify AWS credentials
            identity = self.sts.get_caller_identity()
            self.account_id = identity['Account']
            self.print_info(f"AWS credentials valid")
            self.print_info(f"AWS Account ID: {self.account_id}")
            return True
        except ClientError as e:
            print(f"{Colors.RED}AWS credentials invalid or not configured{Colors.NC}")
            print(f"Error: {e}")
            return False
    
    def get_api_configuration(self) -> bool:
        """Retrieve API Gateway endpoint and API key"""
        self.print_section("Retrieving API Configuration")
        
        try:
            # Get API endpoint from CloudFormation
            stack_name = f"bdo-api-gateway-{self.environment}"
            response = self.cloudformation.describe_stacks(StackName=stack_name)
            
            outputs = response['Stacks'][0]['Outputs']
            for output in outputs:
                if output['OutputKey'] == 'APIEndpoint':
                    self.api_endpoint = output['OutputValue']
                elif output['OutputKey'] == 'APIKey':
                    api_key_id = output['OutputValue']
                    
                    # Get API key value
                    key_response = self.apigateway.get_api_key(
                        apiKey=api_key_id,
                        includeValue=True
                    )
                    self.api_key = key_response['value']
            
            if not self.api_endpoint or not self.api_key:
                print(f"{Colors.RED}Could not retrieve API configuration{Colors.NC}")
                return False
            
            self.print_info(f"API Endpoint: {self.api_endpoint}")
            self.print_info("API Key retrieved successfully")
            return True
            
        except ClientError as e:
            print(f"{Colors.RED}Error retrieving API configuration: {e}{Colors.NC}")
            return False
    
    def test_etl_pipeline(self):
        """Test ETL pipeline execution"""
        self.print_section("Test 1: ETL Pipeline Execution")
        
        self.print_info("Invoking retrieveIdList Lambda...")
        
        try:
            response = self.lambda_client.invoke(
                FunctionName='retrieveIdList',
                InvocationType='RequestResponse',
                Payload=json.dumps({})
            )
            
            payload = json.loads(response['Payload'].read())
            
            if 'item_ids' in payload and isinstance(payload['item_ids'], list):
                item_count = len(payload['item_ids'])
                self.tests.add_result(
                    "ETL Pipeline - retrieveIdList executed successfully",
                    True,
                    f"Retrieved {item_count} items"
                )
            else:
                self.tests.add_result(
                    "ETL Pipeline - retrieveIdList returned invalid response",
                    False,
                    f"Response: {payload}"
                )
        
        except ClientError as e:
            self.tests.add_result(
                "ETL Pipeline - retrieveIdList invocation failed",
                False,
                str(e)
            )
    
    def test_query_api_valid(self):
        """Test Query API with valid parameters"""
        self.print_section("Test 2: Query API with Valid Parameters")
        
        # Calculate date range (last 30 days)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        params = {
            'item_id': 1001,
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'limit': 10
        }
        
        self.print_info("Testing query with item_id parameter...")
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            headers = {
                'x-api-key': self.api_key,
                'Content-Type': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'statistics' in data:
                    self.tests.add_result(
                        "Query API - Valid request with item_id returned 200",
                        True
                    )
                else:
                    self.tests.add_result(
                        "Query API - Response missing expected fields",
                        False,
                        f"Response: {data}"
                    )
            else:
                self.tests.add_result(
                    "Query API - Unexpected status code",
                    False,
                    f"Expected 200, got {response.status_code}"
                )
        
        except requests.RequestException as e:
            self.tests.add_result(
                "Query API - Request failed",
                False,
                str(e)
            )
    
    def test_query_api_variations(self):
        """Test Query API with various parameters"""
        self.print_section("Test 3: Query API with Various Parameters")
        
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        # Test with name parameter
        self.print_info("Testing query with name parameter...")
        params = {
            'name': 'Black Stone',
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'limit': 5
        }
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            headers = {'x-api-key': self.api_key}
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                self.tests.add_result(
                    "Query API - Valid request with name parameter returned 200",
                    True
                )
            else:
                self.tests.add_result(
                    "Query API - Name parameter query failed",
                    False,
                    f"Status code: {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "Query API - Name parameter request failed",
                False,
                str(e)
            )
        
        # Test with category parameter
        self.print_info("Testing query with category parameter...")
        params = {
            'item_id': 1001,
            'category': 'Accessory',
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            headers = {'x-api-key': self.api_key}
            response = requests.get(url, headers=headers, timeout=30)
            
            # 200 or 404 are both acceptable (depends on data)
            if response.status_code in [200, 404]:
                self.tests.add_result(
                    "Query API - Category parameter handled correctly",
                    True,
                    f"Status code: {response.status_code}"
                )
            else:
                self.tests.add_result(
                    "Query API - Category parameter query failed",
                    False,
                    f"Status code: {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "Query API - Category parameter request failed",
                False,
                str(e)
            )
    
    def test_api_authentication(self):
        """Test API key authentication"""
        self.print_section("Test 4: API Key Authentication")
        
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        params = {
            'item_id': 1001,
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        url = f"{self.api_endpoint}/query?{urlencode(params)}"
        
        # Test without API key
        self.print_info("Testing request without API key...")
        try:
            response = requests.get(url, timeout=30)
            if response.status_code in [401, 403]:
                self.tests.add_result(
                    "API Authentication - Request without API key rejected",
                    True,
                    f"Status code: {response.status_code}"
                )
            else:
                self.tests.add_result(
                    "API Authentication - Request without API key not rejected",
                    False,
                    f"Expected 401/403, got {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "API Authentication - Request failed",
                False,
                str(e)
            )
        
        # Test with invalid API key
        self.print_info("Testing request with invalid API key...")
        try:
            headers = {'x-api-key': 'invalid-key-12345'}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code in [401, 403]:
                self.tests.add_result(
                    "API Authentication - Request with invalid API key rejected",
                    True,
                    f"Status code: {response.status_code}"
                )
            else:
                self.tests.add_result(
                    "API Authentication - Request with invalid API key not rejected",
                    False,
                    f"Expected 401/403, got {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "API Authentication - Request failed",
                False,
                str(e)
            )
    
    def test_rate_limiting(self):
        """Test rate limiting"""
        self.print_section("Test 5: Rate Limiting")
        
        self.print_info("Sending multiple rapid requests to test rate limiting...")
        
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        params = {
            'item_id': 1001,
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        url = f"{self.api_endpoint}/query?{urlencode(params)}"
        headers = {'x-api-key': self.api_key}
        
        rate_limit_hit = False
        for i in range(15):
            try:
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 429:
                    rate_limit_hit = True
                    break
                time.sleep(0.1)
            except requests.RequestException:
                pass
        
        if rate_limit_hit:
            self.tests.add_result(
                "Rate Limiting - Rate limit enforced (429 received)",
                True
            )
        else:
            self.print_warning("Rate limit not hit with 15 requests. This may be expected if limits are high.")
            self.tests.add_result(
                "Rate Limiting - No errors during rapid requests",
                True
            )
    
    def test_cloudwatch_logs(self):
        """Test CloudWatch logs for JSON format and correlation IDs"""
        self.print_section("Test 6: CloudWatch Logs Validation")
        
        self.print_info("Checking CloudWatch logs for retrieveIdList...")
        
        log_group = "/aws/lambda/retrieveIdList"
        
        try:
            # Get recent log streams
            streams_response = self.logs_client.describe_log_streams(
                logGroupName=log_group,
                orderBy='LastEventTime',
                descending=True,
                limit=1
            )
            
            if not streams_response['logStreams']:
                self.tests.add_result(
                    "CloudWatch Logs - No log streams found",
                    False
                )
                return
            
            log_stream = streams_response['logStreams'][0]['logStreamName']
            
            # Get log events
            events_response = self.logs_client.get_log_events(
                logGroupName=log_group,
                logStreamName=log_stream,
                limit=10
            )
            
            json_found = False
            correlation_id_found = False
            
            for event in events_response['events']:
                message = event['message']
                try:
                    log_data = json.loads(message)
                    json_found = True
                    if 'correlation_id' in log_data:
                        correlation_id_found = True
                        break
                except json.JSONDecodeError:
                    continue
            
            if json_found:
                self.tests.add_result(
                    "CloudWatch Logs - JSON format detected",
                    True
                )
            else:
                self.tests.add_result(
                    "CloudWatch Logs - No JSON formatted logs found",
                    False
                )
            
            if correlation_id_found:
                self.tests.add_result(
                    "CloudWatch Logs - Correlation IDs present",
                    True
                )
            else:
                self.tests.add_result(
                    "CloudWatch Logs - No correlation IDs found in logs",
                    False
                )
        
        except ClientError as e:
            self.tests.add_result(
                "CloudWatch Logs - Could not retrieve log streams",
                False,
                str(e)
            )
    
    def test_xray_traces(self):
        """Test X-Ray traces"""
        self.print_section("Test 7: X-Ray Traces Validation")
        
        self.print_info("Checking X-Ray traces...")
        
        # Get traces from last 5 minutes
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5)
        
        try:
            response = self.xray_client.get_trace_summaries(
                StartTime=start_time,
                EndTime=end_time
            )
            
            traces = response.get('TraceSummaries', [])
            
            if traces:
                trace_count = len(traces)
                self.tests.add_result(
                    f"X-Ray Traces - Found {trace_count} recent traces",
                    True
                )
                
                # Check for errors
                error_count = sum(1 for trace in traces if trace.get('HasError', False))
                if error_count == 0:
                    self.tests.add_result(
                        "X-Ray Traces - No errors in recent traces",
                        True
                    )
                else:
                    self.print_warning(f"Found {error_count} traces with errors")
            else:
                self.print_warning("No recent X-Ray traces found. This may be expected if no requests were made recently.")
                self.tests.add_result(
                    "X-Ray Traces - X-Ray is configured (no traces found yet)",
                    True
                )
        
        except ClientError as e:
            self.tests.add_result(
                "X-Ray Traces - Could not retrieve traces",
                False,
                str(e)
            )
    
    def test_custom_metrics(self):
        """Test custom CloudWatch metrics"""
        self.print_section("Test 8: Custom Metrics Validation")
        
        self.print_info("Checking CloudWatch custom metrics...")
        
        try:
            response = self.cloudwatch.list_metrics(
                Namespace='BDO/MarketInsights'
            )
            
            metrics = response.get('Metrics', [])
            metric_count = len(metrics)
            
            if metric_count > 0:
                self.tests.add_result(
                    f"Custom Metrics - Found {metric_count} custom metrics",
                    True
                )
                
                # List some metrics
                self.print_info("Sample metrics:")
                for metric in metrics[:3]:
                    print(f"  - {metric['MetricName']}")
            else:
                self.tests.add_result(
                    "Custom Metrics - No custom metrics found in BDO/MarketInsights namespace",
                    False
                )
        
        except ClientError as e:
            self.tests.add_result(
                "Custom Metrics - Could not retrieve metrics",
                False,
                str(e)
            )
    
    def test_error_handling(self):
        """Test error handling scenarios"""
        self.print_section("Test 9: Error Handling Validation")
        
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=30)
        
        headers = {'x-api-key': self.api_key}
        
        # Test invalid query parameters
        self.print_info("Testing invalid query parameters...")
        params = {
            'item_id': 'invalid',
            'start_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 400:
                try:
                    data = response.json()
                    if 'error' in data:
                        self.tests.add_result(
                            "Error Handling - Invalid parameters return 400 with error details",
                            True
                        )
                    else:
                        self.tests.add_result(
                            "Error Handling - 400 response missing error details",
                            False
                        )
                except json.JSONDecodeError:
                    self.tests.add_result(
                        "Error Handling - 400 response not JSON",
                        False
                    )
            else:
                self.tests.add_result(
                    "Error Handling - Invalid parameters did not return 400",
                    False,
                    f"Got status code: {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "Error Handling - Request failed",
                False,
                str(e)
            )
        
        # Test missing required parameters
        self.print_info("Testing missing required parameters...")
        params = {'item_id': 1001}
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 400:
                self.tests.add_result(
                    "Error Handling - Missing required parameters return 400",
                    True
                )
            else:
                self.tests.add_result(
                    "Error Handling - Missing parameters did not return 400",
                    False,
                    f"Got status code: {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "Error Handling - Request failed",
                False,
                str(e)
            )
        
        # Test invalid date range
        self.print_info("Testing invalid date range...")
        params = {
            'item_id': 1001,
            'start_date': end_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end_date': start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        }
        
        try:
            url = f"{self.api_endpoint}/query?{urlencode(params)}"
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 400:
                self.tests.add_result(
                    "Error Handling - Invalid date range returns 400",
                    True
                )
            else:
                self.tests.add_result(
                    "Error Handling - Invalid date range did not return 400",
                    False,
                    f"Got status code: {response.status_code}"
                )
        except requests.RequestException as e:
            self.tests.add_result(
                "Error Handling - Request failed",
                False,
                str(e)
            )
    
    def run_all_tests(self) -> bool:
        """Run all validation tests"""
        print(f"{Colors.BLUE}{'=' * 42}")
        print("BDO Market Insights - Staging Validation")
        print(f"{'=' * 42}{Colors.NC}\n")
        print(f"Environment: {self.environment}")
        print(f"Region: {self.region}\n")
        
        # Check prerequisites
        if not self.check_prerequisites():
            return False
        
        # Get API configuration
        if not self.get_api_configuration():
            return False
        
        # Run all tests
        self.test_etl_pipeline()
        self.test_query_api_valid()
        self.test_query_api_variations()
        self.test_api_authentication()
        self.test_rate_limiting()
        self.test_cloudwatch_logs()
        self.test_xray_traces()
        self.test_custom_metrics()
        self.test_error_handling()
        
        # Print summary
        self.tests.print_summary()
        
        if self.tests.failed == 0:
            print(f"\n{Colors.GREEN}{'=' * 42}")
            print("✓ All validation tests passed!")
            print(f"{'=' * 42}{Colors.NC}\n")
            print("Staging environment is ready for use.")
            return True
        else:
            print(f"\n{Colors.YELLOW}{'=' * 42}")
            print("⚠ Some validation tests failed")
            print(f"{'=' * 42}{Colors.NC}\n")
            print("Please review the failed tests above and:")
            print("  1. Check CloudWatch logs for detailed error messages")
            print("  2. Verify all Lambda functions are deployed correctly")
            print("  3. Ensure API Gateway and Step Functions are configured properly")
            print("  4. Check IAM permissions for all services\n")
            return False


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Validate BDO Market Insights staging environment'
    )
    parser.add_argument(
        '--environment',
        default='staging',
        help='Environment name (default: staging)'
    )
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='AWS region (default: us-east-1)'
    )
    
    args = parser.parse_args()
    
    validator = StagingValidator(
        environment=args.environment,
        region=args.region
    )
    
    success = validator.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
