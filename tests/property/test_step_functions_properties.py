"""
Property-based tests for Step Functions state machine data flow.

Feature: bdo-market-insights-rewrite
Tests Properties 13, 14, 26, and 27 from the design document.
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from hypothesis import given, strategies as st, settings, assume
from typing import Dict, Any, List, Optional


# Mock Step Functions state machine execution
class StepFunctionsStateMachine:
    """
    Simulates Step Functions state machine execution for testing.
    
    This class mimics the BDO Market Insights Query Pipeline state machine
    with all its state transitions and data transformations.
    """
    
    def __init__(self, query_data_lambda=None, analyze_data_lambda=None):
        """
        Initialize state machine with Lambda function mocks.
        
        Args:
            query_data_lambda: Mock queryData Lambda function
            analyze_data_lambda: Mock analyzeData Lambda function
        """
        self.query_data_lambda = query_data_lambda or self._default_query_data_lambda
        self.analyze_data_lambda = analyze_data_lambda or self._default_analyze_data_lambda
        self.execution_history = []
    
    def _default_query_data_lambda(self, event):
        """Default mock queryData Lambda that returns sample data."""
        return {
            "statusCode": 200,
            "body": {
                "data": [
                    {
                        "item_id": event.get("item_id", 1001),
                        "item_name": "Test Item",
                        "current_stock": 50,
                        "total_trades": 1000,
                        "last_sold_price": 15000,
                        "last_sold_time": "2024-01-15T10:30:00Z"
                    }
                ],
                "count": 1
            }
        }
    
    def _default_analyze_data_lambda(self, event):
        """Default mock analyzeData Lambda that returns sample analysis."""
        return {
            "statusCode": 200,
            "body": {
                "item_id": event["market_data"][0]["item_id"],
                "item_name": event["market_data"][0]["item_name"],
                "date_range": {
                    "start": event["query_params"]["start_date"],
                    "end": event["query_params"]["end_date"]
                },
                "statistics": {
                    "avg_last_sold_price": 15000.0,
                    "min_last_sold_price": 14000,
                    "max_last_sold_price": 16000,
                    "avg_current_stock": 50.0,
                    "total_trades_sum": 1000
                },
                "profitability_score": 0.85,
                "price_trend": "stable",
                "correlation_id": event["correlation_id"]
            }
        }
    
    def execute(self, input_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the state machine with the given input.
        
        Args:
            input_event: API Gateway event with queryStringParameters and requestContext
        
        Returns:
            Final response from the state machine
        """
        self.execution_history = []
        
        try:
            # State: TransformInput
            transformed = self._transform_input(input_event)
            self.execution_history.append(("TransformInput", transformed))
            
            # State: ValidateQueryParams
            validated = self._validate_query_params(transformed)
            self.execution_history.append(("ValidateQueryParams", validated))
            
            # State: QueryData
            query_result = self._query_data(validated)
            self.execution_history.append(("QueryData", query_result))
            
            # State: CheckQueryDataSuccess
            if query_result["queryResult"]["statusCode"] != 200:
                error = self._handle_query_data_error(query_result, validated)
                self.execution_history.append(("HandleQueryDataError", error))
                return self._handle_error(error)
            
            # State: MergeForAnalysis
            merged = self._merge_for_analysis(query_result, validated)
            self.execution_history.append(("MergeForAnalysis", merged))
            
            # State: AnalyzeData
            analysis_result = self._analyze_data(merged)
            self.execution_history.append(("AnalyzeData", analysis_result))
            
            # State: CheckAnalyzeDataSuccess
            if analysis_result["analysisResult"]["statusCode"] != 200:
                error = self._handle_analyze_data_error(analysis_result, merged)
                self.execution_history.append(("HandleAnalyzeDataError", error))
                return self._handle_error(error)
            
            # State: FormatResponse
            response = self._format_response(analysis_result, merged)
            self.execution_history.append(("FormatResponse", response))
            
            return response
            
        except Exception as e:
            error = {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(e),
                    "correlationId": input_event.get("requestContext", {}).get("requestId", "unknown")
                }
            }
            return self._handle_error(error)
    
    def _transform_input(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """TransformInput state: Extract query parameters."""
        return {
            "queryParams": event.get("queryStringParameters", {}),
            "correlationId": event.get("requestContext", {}).get("requestId", "unknown")
        }
    
    def _validate_query_params(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """ValidateQueryParams state: Prepare parameters for Lambda."""
        query_params = event["queryParams"]
        
        # Convert limit from string to int if present
        limit = query_params.get("limit")
        if limit is not None:
            limit = int(limit) if isinstance(limit, str) else limit
        else:
            limit = 100  # Default
        
        return {
            "validatedParams": {
                "item_id": query_params.get("item_id"),
                "name": query_params.get("name"),
                "category": query_params.get("category"),
                "start_date": query_params.get("start_date"),
                "end_date": query_params.get("end_date"),
                "limit": limit,
                "correlation_id": event["correlationId"]
            }
        }
    
    def _query_data(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """QueryData state: Invoke queryData Lambda."""
        validated_params = event["validatedParams"]
        
        # Invoke Lambda
        lambda_response = self.query_data_lambda(validated_params)
        
        return {
            "validatedParams": validated_params,
            "queryResult": {
                "statusCode": lambda_response["statusCode"],
                "body": lambda_response["body"]
            }
        }
    
    def _handle_query_data_error(self, query_result: Dict[str, Any], 
                                  validated: Dict[str, Any]) -> Dict[str, Any]:
        """HandleQueryDataError state: Format error for non-200 response."""
        return {
            "error": {
                "code": "QUERY_DATA_ERROR",
                "message": "queryData Lambda returned non-200 status",
                "statusCode": query_result["queryResult"]["statusCode"],
                "details": query_result["queryResult"]["body"],
                "correlationId": validated["validatedParams"]["correlation_id"]
            }
        }
    
    def _merge_for_analysis(self, query_result: Dict[str, Any], 
                           validated: Dict[str, Any]) -> Dict[str, Any]:
        """MergeForAnalysis state: Combine results with original parameters."""
        validated_params = validated["validatedParams"]
        
        return {
            "market_data": query_result["queryResult"]["body"]["data"],
            "query_params": {
                "item_id": validated_params["item_id"],
                "name": validated_params["name"],
                "category": validated_params["category"],
                "start_date": validated_params["start_date"],
                "end_date": validated_params["end_date"],
                "limit": validated_params["limit"]
            },
            "correlation_id": validated_params["correlation_id"]
        }
    
    def _analyze_data(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """AnalyzeData state: Invoke analyzeData Lambda."""
        # Invoke Lambda
        lambda_response = self.analyze_data_lambda(event)
        
        return {
            "market_data": event["market_data"],
            "query_params": event["query_params"],
            "correlation_id": event["correlation_id"],
            "analysisResult": {
                "statusCode": lambda_response["statusCode"],
                "body": lambda_response["body"]
            }
        }
    
    def _handle_analyze_data_error(self, analysis_result: Dict[str, Any],
                                   merged: Dict[str, Any]) -> Dict[str, Any]:
        """HandleAnalyzeDataError state: Format error for non-200 response."""
        return {
            "error": {
                "code": "ANALYZE_DATA_ERROR",
                "message": "analyzeData Lambda returned non-200 status",
                "statusCode": analysis_result["analysisResult"]["statusCode"],
                "details": analysis_result["analysisResult"]["body"],
                "correlationId": merged["correlation_id"]
            }
        }
    
    def _format_response(self, analysis_result: Dict[str, Any],
                        merged: Dict[str, Any]) -> Dict[str, Any]:
        """FormatResponse state: Add headers and format final response."""
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "public, max-age=300",
                "X-Correlation-Id": merged["correlation_id"]
            },
            "body": analysis_result["analysisResult"]["body"]
        }
    
    def _handle_error(self, error_event: Dict[str, Any]) -> Dict[str, Any]:
        """HandleError state: Format error response."""
        error = error_event["error"]
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "X-Correlation-Id": error.get("correlationId", "unknown")
            },
            "body": {
                "error": {
                    "code": error.get("code", "INTERNAL_ERROR"),
                    "message": error.get("message", "An error occurred"),
                    "correlation_id": error.get("correlationId", "unknown")
                }
            }
        }


# Hypothesis strategies for generating test data

@st.composite
def api_gateway_event(draw):
    """Generate a valid API Gateway GET request event."""
    # Generate query parameters
    item_id = draw(st.one_of(
        st.none(),
        st.integers(min_value=1, max_value=10000).map(str)
    ))
    name = draw(st.one_of(
        st.none(),
        st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd', 'Zs')))
    ))
    
    # Ensure at least one of item_id or name is present
    if item_id is None and name is None:
        item_id = str(draw(st.integers(min_value=1, max_value=10000)))
    
    category = draw(st.one_of(
        st.none(),
        st.sampled_from(["Uncategorized", "Accessory", "Buff", "Costume"])
    ))
    
    # Generate date range
    start_date = draw(st.datetimes(
        min_value=datetime(2024, 1, 1),
        max_value=datetime(2024, 12, 31)
    ))
    end_date = draw(st.datetimes(
        min_value=start_date,
        max_value=start_date + timedelta(days=90)
    ))
    
    limit = draw(st.one_of(
        st.none(),
        st.integers(min_value=1, max_value=1000).map(str)
    ))
    
    request_id = draw(st.text(
        min_size=10,
        max_size=50,
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), blacklist_characters='-')
    ))
    
    query_params = {}
    if item_id is not None:
        query_params["item_id"] = item_id
    if name is not None:
        query_params["name"] = name
    if category is not None:
        query_params["category"] = category
    query_params["start_date"] = start_date.isoformat() + "Z"
    query_params["end_date"] = end_date.isoformat() + "Z"
    if limit is not None:
        query_params["limit"] = limit
    
    return {
        "queryStringParameters": query_params,
        "requestContext": {
            "requestId": request_id
        }
    }


# Property 13: Step Functions data flow integrity
# Property 26: GET parameter to JSON transformation
# **Validates: Requirements 8.3, 8.4, 18.1**

@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_transforms_get_parameters_to_json(event):
    """
    Property 26: For any API Gateway GET request with query parameters,
    the Step Functions should receive them as a properly formatted JSON object
    with all parameter names and values preserved.
    """
    state_machine = StepFunctionsStateMachine()
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Find TransformInput state in execution history
    transform_state = next(
        (state for state in state_machine.execution_history if state[0] == "TransformInput"),
        None
    )
    
    assert transform_state is not None, "TransformInput state should be executed"
    
    transformed_data = transform_state[1]
    
    # Verify query parameters are extracted as JSON object
    assert "queryParams" in transformed_data, \
        "Transformed data should contain queryParams"
    assert isinstance(transformed_data["queryParams"], dict), \
        "queryParams should be a JSON object (dict)"
    
    # Verify all original parameters are preserved
    original_params = event["queryStringParameters"]
    transformed_params = transformed_data["queryParams"]
    
    for key, value in original_params.items():
        assert key in transformed_params, \
            f"Parameter {key} should be preserved in transformation"
        assert transformed_params[key] == value, \
            f"Parameter {key} value should be preserved exactly"
    
    # Verify correlation ID is extracted
    assert "correlationId" in transformed_data, \
        "Transformed data should contain correlationId"
    assert transformed_data["correlationId"] == event["requestContext"]["requestId"], \
        "Correlation ID should match request ID from API Gateway"


@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_passes_parameters_to_query_data(event):
    """
    Property 13: For any GET request with query parameters,
    the Step Functions should transform parameters correctly for queryData Lambda.
    """
    # Mock queryData Lambda to capture input
    captured_input = {}
    
    def mock_query_data(input_event):
        captured_input.update(input_event)
        return {
            "statusCode": 200,
            "body": {
                "data": [],
                "count": 0
            }
        }
    
    state_machine = StepFunctionsStateMachine(query_data_lambda=mock_query_data)
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Verify queryData Lambda received correct parameters
    original_params = event["queryStringParameters"]
    
    # Check item_id
    if "item_id" in original_params:
        assert captured_input["item_id"] == original_params["item_id"], \
            "item_id should be passed to queryData"
    
    # Check name
    if "name" in original_params:
        assert captured_input["name"] == original_params["name"], \
            "name should be passed to queryData"
    
    # Check category
    if "category" in original_params:
        assert captured_input["category"] == original_params["category"], \
            "category should be passed to queryData"
    
    # Check dates
    assert captured_input["start_date"] == original_params["start_date"], \
        "start_date should be passed to queryData"
    assert captured_input["end_date"] == original_params["end_date"], \
        "end_date should be passed to queryData"
    
    # Check limit (should be converted to int)
    if "limit" in original_params:
        assert captured_input["limit"] == int(original_params["limit"]), \
            "limit should be converted to integer for queryData"
    else:
        assert captured_input["limit"] == 100, \
            "limit should default to 100 if not provided"
    
    # Check correlation_id
    assert captured_input["correlation_id"] == event["requestContext"]["requestId"], \
        "correlation_id should be passed to queryData"


# Property 13: Step Functions data flow integrity
# Property 27: Query context preservation
# **Validates: Requirements 8.4, 18.3**

@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_merges_query_data_results_with_parameters(event):
    """
    Property 13: For any GET request, the Step Functions should merge
    queryData output with original parameters before passing to analyzeData.
    """
    # Mock queryData to return specific data
    mock_market_data = [
        {
            "item_id": 1001,
            "item_name": "Test Item",
            "current_stock": 50,
            "total_trades": 1000,
            "last_sold_price": 15000,
            "last_sold_time": "2024-01-15T10:30:00Z"
        }
    ]
    
    def mock_query_data(input_event):
        return {
            "statusCode": 200,
            "body": {
                "data": mock_market_data,
                "count": len(mock_market_data)
            }
        }
    
    # Mock analyzeData to capture input
    captured_analyze_input = {}
    
    def mock_analyze_data(input_event):
        captured_analyze_input.update(input_event)
        return {
            "statusCode": 200,
            "body": {
                "item_id": 1001,
                "statistics": {},
                "correlation_id": input_event["correlation_id"]
            }
        }
    
    state_machine = StepFunctionsStateMachine(
        query_data_lambda=mock_query_data,
        analyze_data_lambda=mock_analyze_data
    )
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Verify analyzeData received both market_data and query_params
    assert "market_data" in captured_analyze_input, \
        "analyzeData should receive market_data from queryData"
    assert "query_params" in captured_analyze_input, \
        "analyzeData should receive original query_params"
    assert "correlation_id" in captured_analyze_input, \
        "analyzeData should receive correlation_id"
    
    # Verify market_data matches queryData output
    assert captured_analyze_input["market_data"] == mock_market_data, \
        "market_data should match queryData output"
    
    # Verify query_params are preserved
    original_params = event["queryStringParameters"]
    analyze_params = captured_analyze_input["query_params"]
    
    if "item_id" in original_params:
        assert analyze_params["item_id"] == original_params["item_id"], \
            "item_id should be preserved in query_params"
    
    if "name" in original_params:
        assert analyze_params["name"] == original_params["name"], \
            "name should be preserved in query_params"
    
    assert analyze_params["start_date"] == original_params["start_date"], \
        "start_date should be preserved in query_params"
    assert analyze_params["end_date"] == original_params["end_date"], \
        "end_date should be preserved in query_params"


@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_preserves_query_context_throughout_execution(event):
    """
    Property 27: For any query request flowing through Step Functions,
    the original query parameters should be available to analyzeData Lambda
    along with the queryData results.
    """
    # Track all data passed through the pipeline
    pipeline_data = {
        "query_data_input": None,
        "analyze_data_input": None
    }
    
    def mock_query_data(input_event):
        pipeline_data["query_data_input"] = input_event.copy()
        return {
            "statusCode": 200,
            "body": {
                "data": [{"item_id": 1001, "price": 15000}],
                "count": 1
            }
        }
    
    def mock_analyze_data(input_event):
        pipeline_data["analyze_data_input"] = input_event.copy()
        return {
            "statusCode": 200,
            "body": {
                "item_id": 1001,
                "statistics": {},
                "correlation_id": input_event["correlation_id"]
            }
        }
    
    state_machine = StepFunctionsStateMachine(
        query_data_lambda=mock_query_data,
        analyze_data_lambda=mock_analyze_data
    )
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Verify query context is preserved from queryData to analyzeData
    query_data_params = pipeline_data["query_data_input"]
    analyze_data_params = pipeline_data["analyze_data_input"]["query_params"]
    
    # All parameters passed to queryData should be available in analyzeData
    assert analyze_data_params["item_id"] == query_data_params["item_id"], \
        "item_id should be preserved from queryData to analyzeData"
    assert analyze_data_params["start_date"] == query_data_params["start_date"], \
        "start_date should be preserved from queryData to analyzeData"
    assert analyze_data_params["end_date"] == query_data_params["end_date"], \
        "end_date should be preserved from queryData to analyzeData"
    assert analyze_data_params["limit"] == query_data_params["limit"], \
        "limit should be preserved from queryData to analyzeData"
    
    # Correlation ID should be preserved throughout
    assert pipeline_data["analyze_data_input"]["correlation_id"] == \
           pipeline_data["query_data_input"]["correlation_id"], \
        "correlation_id should be preserved throughout execution"


# Property 14: Cache-control headers on GET responses
# **Validates: Requirements 8.6**

@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_includes_cache_control_headers_on_success(event):
    """
    Property 14: For any successful GET request to query endpoints,
    the response should include appropriate cache-control headers.
    """
    state_machine = StepFunctionsStateMachine()
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Verify response structure
    assert "statusCode" in response, "Response should have statusCode"
    assert "headers" in response, "Response should have headers"
    assert "body" in response, "Response should have body"
    
    # Verify successful status code
    if response["statusCode"] == 200:
        headers = response["headers"]
        
        # Verify Cache-Control header is present
        assert "Cache-Control" in headers, \
            "Successful response should include Cache-Control header"
        
        # Verify Cache-Control value
        cache_control = headers["Cache-Control"]
        assert "public" in cache_control, \
            "Cache-Control should specify public caching"
        assert "max-age" in cache_control, \
            "Cache-Control should specify max-age"
        
        # Verify max-age value (should be 300 seconds = 5 minutes)
        assert "max-age=300" in cache_control, \
            "Cache-Control max-age should be 300 seconds"
        
        # Verify Content-Type header
        assert "Content-Type" in headers, \
            "Response should include Content-Type header"
        assert headers["Content-Type"] == "application/json", \
            "Content-Type should be application/json"
        
        # Verify X-Correlation-Id header
        assert "X-Correlation-Id" in headers, \
            "Response should include X-Correlation-Id header for tracing"
        assert headers["X-Correlation-Id"] == event["requestContext"]["requestId"], \
            "X-Correlation-Id should match original request ID"


@given(event=api_gateway_event())
@settings(max_examples=100)
def test_step_functions_error_responses_include_correlation_id(event):
    """
    Property 14: For any error response, the headers should include
    the correlation ID for tracing and debugging.
    """
    # Mock queryData to return error
    def mock_query_data_error(input_event):
        return {
            "statusCode": 400,
            "body": {
                "error": "Invalid parameters"
            }
        }
    
    state_machine = StepFunctionsStateMachine(query_data_lambda=mock_query_data_error)
    
    # Execute state machine
    response = state_machine.execute(event)
    
    # Verify error response structure
    assert response["statusCode"] == 500, \
        "Error response should have 500 status code"
    assert "headers" in response, \
        "Error response should have headers"
    
    headers = response["headers"]
    
    # Verify X-Correlation-Id header is present in error response
    assert "X-Correlation-Id" in headers, \
        "Error response should include X-Correlation-Id header"
    assert headers["X-Correlation-Id"] == event["requestContext"]["requestId"], \
        "X-Correlation-Id should match original request ID even in errors"
    
    # Verify Content-Type header
    assert "Content-Type" in headers, \
        "Error response should include Content-Type header"
    assert headers["Content-Type"] == "application/json", \
        "Content-Type should be application/json"
    
    # Verify error body includes correlation_id
    assert "body" in response, \
        "Error response should have body"
    assert "error" in response["body"], \
        "Error response body should contain error object"
    assert "correlation_id" in response["body"]["error"], \
        "Error object should include correlation_id"


@given(
    events=st.lists(api_gateway_event(), min_size=1, max_size=20)
)
@settings(max_examples=100)
def test_step_functions_maintains_data_flow_integrity_across_multiple_requests(events):
    """
    Property 13: For any sequence of GET requests, the Step Functions
    should maintain data flow integrity for each request independently.
    """
    state_machine = StepFunctionsStateMachine()
    
    # Execute multiple requests
    responses = []
    for event in events:
        response = state_machine.execute(event)
        responses.append((event, response))
    
    # Verify each response corresponds to its request
    for event, response in responses:
        original_request_id = event["requestContext"]["requestId"]
        
        if response["statusCode"] == 200:
            # Verify correlation ID matches
            assert response["headers"]["X-Correlation-Id"] == original_request_id, \
                "Each response should have correlation ID matching its request"
            
            # Verify response body contains expected data
            assert "body" in response, \
                "Response should have body"
            assert "correlation_id" in response["body"], \
                "Response body should contain correlation_id"
            assert response["body"]["correlation_id"] == original_request_id, \
                "Response body correlation_id should match request"
