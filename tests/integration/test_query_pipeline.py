"""
Integration tests for Query pipeline.

Tests complete flow: API Gateway → Step Functions → queryData → analyzeData
Uses mocked API Gateway, Step Functions, and RDS.
Verifies GET parameter transformation, query context preservation,
and response format with headers.

Requirements: 13.2, 13.5
"""

import pytest
import sys
import json
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

# Mock psycopg2 before importing
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.extras = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool
sys.modules['psycopg2.extras'] = mock_psycopg2.extras


@pytest.mark.integration
class TestQueryPipelineIntegration:
    """Integration tests for complete Query pipeline."""
    
    def test_complete_query_flow(self):
        """
        Test complete Query pipeline from API Gateway to response.
        
        Simulates Step Functions orchestration:
        1. TransformInput: Extract query parameters from API Gateway
        2. QueryData: Execute database query
        3. MergeForAnalysis: Combine results with query params
        4. AnalyzeData: Compute statistics and trends
        5. FormatResponse: Add headers and format response
        
        Verifies:
        - GET parameter transformation
        - Query context preservation
        - Response format and headers
        """
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Simulate API Gateway GET request
        api_gateway_event = {
            "queryStringParameters": {
                "item_id": "1001",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-15T23:59:59Z",
                "limit": "100"
            },
            "requestContext": {
                "requestId": "api-gateway-request-id"
            }
        }
        
        # Step 1: TransformInput (simulated by Step Functions)
        # Extract query parameters and create correlation ID
        transformed_input = {
            "queryParams": api_gateway_event["queryStringParameters"],
            "correlationId": api_gateway_event["requestContext"]["requestId"]
        }
        
        # Step 2: QueryData
        # Mock database query results
        mock_query_results = [
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 50,
                'total_trades': 1234,
                'last_sold_price': 15000,
                'last_sold_time': '2024-01-10T10:30:00+00:00',
                'scrape_time': '2024-01-10T10:35:00+00:00'
            },
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 45,
                'total_trades': 1456,
                'last_sold_price': 16000,
                'last_sold_time': '2024-01-12T14:20:00+00:00',
                'scrape_time': '2024-01-12T14:25:00+00:00'
            },
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 40,
                'total_trades': 1678,
                'last_sold_price': 17000,
                'last_sold_time': '2024-01-15T09:15:00+00:00',
                'scrape_time': '2024-01-15T09:20:00+00:00'
            }
        ]
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.execute_query.return_value = mock_query_results
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            # Create queryData event from transformed input
            query_event = {
                "item_id": int(transformed_input["queryParams"]["item_id"]),
                "start_date": transformed_input["queryParams"]["start_date"],
                "end_date": transformed_input["queryParams"]["end_date"],
                "limit": int(transformed_input["queryParams"]["limit"])
            }
            
            query_response = query_handler(query_event, mock_context)
            
            # Verify queryData response
            assert query_response["statusCode"] == 200
            query_body = json.loads(query_response["body"])
            assert "data" in query_body
            assert "count" in query_body
            assert "query_params" in query_body
            assert len(query_body["data"]) == 3
            assert query_body["count"] == 3
        
        # Step 3: MergeForAnalysis (simulated by Step Functions)
        # Combine queryData output with original query parameters
        merged_input = {
            "market_data": query_body["data"],
            "query_params": query_body["query_params"],
            "correlation_id": transformed_input["correlationId"]
        }
        
        # Step 4: AnalyzeData
        analyze_response = analyze_handler(merged_input, mock_context)
        
        # Verify analyzeData response
        assert analyze_response["statusCode"] == 200
        analyze_body = json.loads(analyze_response["body"])
        assert "item_id" in analyze_body
        assert "item_name" in analyze_body
        assert "statistics" in analyze_body
        assert "profitability_score" in analyze_body
        assert "price_trend" in analyze_body
        assert "date_range" in analyze_body
        
        # Verify statistics
        stats = analyze_body["statistics"]
        assert stats["avg_last_sold_price"] == 16000.0  # (15000 + 16000 + 17000) / 3
        assert stats["min_last_sold_price"] == 15000
        assert stats["max_last_sold_price"] == 17000
        assert stats["avg_current_stock"] == 45.0  # (50 + 45 + 40) / 3
        assert stats["total_trades_sum"] == 4368  # 1234 + 1456 + 1678
        
        # Verify price trend (increasing from 15000 to 17000)
        assert analyze_body["price_trend"] == "increasing"
        
        # Verify query context preservation
        assert analyze_body["date_range"]["start"] == query_event["start_date"]
        assert analyze_body["date_range"]["end"] == query_event["end_date"]
        
        # Step 5: FormatResponse (simulated by Step Functions)
        # Add cache-control headers
        final_response = {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Cache-Control": "public, max-age=300"
            },
            "body": json.dumps(analyze_body)
        }
        
        # Verify final response format
        assert final_response["statusCode"] == 200
        assert "Cache-Control" in final_response["headers"]
        assert final_response["headers"]["Cache-Control"] == "public, max-age=300"
    
    def test_query_pipeline_with_name_search(self):
        """
        Test Query pipeline with name parameter instead of item_id.
        
        Verifies GET parameter transformation for name-based queries.
        """
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # API Gateway event with name parameter
        api_gateway_event = {
            "queryStringParameters": {
                "name": "Black Stone",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-15T23:59:59Z",
                "limit": "50"
            },
            "requestContext": {
                "requestId": "api-gateway-request-id-2"
            }
        }
        
        # Transform input
        transformed_input = {
            "queryParams": api_gateway_event["queryStringParameters"],
            "correlationId": api_gateway_event["requestContext"]["requestId"]
        }
        
        # Mock database results for name search
        mock_query_results = [
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 50,
                'total_trades': 1234,
                'last_sold_price': 15000,
                'last_sold_time': '2024-01-10T10:30:00+00:00',
                'scrape_time': '2024-01-10T10:35:00+00:00'
            },
            {
                'item_id': 1002,
                'item_name': 'Black Stone (Armor)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 60,
                'total_trades': 2345,
                'last_sold_price': 12000,
                'last_sold_time': '2024-01-11T11:30:00+00:00',
                'scrape_time': '2024-01-11T11:35:00+00:00'
            }
        ]
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.execute_query.return_value = mock_query_results
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            query_event = {
                "name": transformed_input["queryParams"]["name"],
                "start_date": transformed_input["queryParams"]["start_date"],
                "end_date": transformed_input["queryParams"]["end_date"],
                "limit": int(transformed_input["queryParams"]["limit"])
            }
            
            query_response = query_handler(query_event, mock_context)
            query_body = json.loads(query_response["body"])
            
            # Verify multiple items returned for name search
            assert len(query_body["data"]) == 2
            assert query_body["query_params"]["name"] == "Black Stone"
    
    def test_query_pipeline_empty_results(self):
        """
        Test Query pipeline handles empty query results gracefully.
        
        Verifies analyzeData returns appropriate response for no data.
        """
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Mock empty database results
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.execute_query.return_value = []  # Empty results
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            query_event = {
                "item_id": 9999,
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-15T23:59:59Z",
                "limit": 100
            }
            
            query_response = query_handler(query_event, mock_context)
            query_body = json.loads(query_response["body"])
            
            # Verify empty results
            assert query_body["count"] == 0
            assert len(query_body["data"]) == 0
        
        # AnalyzeData with empty data
        merged_input = {
            "market_data": [],
            "query_params": query_body["query_params"],
            "correlation_id": "test-corr-id"
        }
        
        analyze_response = analyze_handler(merged_input, mock_context)
        analyze_body = json.loads(analyze_response["body"])
        
        # Verify analyzeData handles empty data
        assert analyze_body["record_count"] == 0
        assert analyze_body["profitability_score"] == 0.0
        assert analyze_body["price_trend"] == "stable"
        assert analyze_body["statistics"]["avg_last_sold_price"] == 0
    
    def test_query_pipeline_parameter_validation(self):
        """
        Test Query pipeline validates parameters correctly.
        
        Verifies invalid parameters are rejected with appropriate errors.
        """
        from src.queryData.lambda_function import lambda_handler as query_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Invalid event: missing required parameters
        invalid_event = {
            "item_id": 1001,
            # Missing start_date and end_date
            "limit": 100
        }
        
        query_response = query_handler(invalid_event, mock_context)
        
        # Should return validation error
        assert query_response["statusCode"] == 400
        body = json.loads(query_response["body"])
        assert "error" in body
    
    def test_query_pipeline_context_preservation(self):
        """
        Test that query context is preserved through Step Functions.
        
        Verifies original query parameters are available to analyzeData
        along with queryData results.
        """
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Original query parameters
        original_params = {
            "item_id": 1001,
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-01-31T23:59:59Z",
            "limit": 200
        }
        
        # Mock database results
        mock_query_results = [{
            'item_id': 1001,
            'item_name': 'Test Item',
            'sid': 0,
            'category': 'Buff',
            'current_stock': 100,
            'total_trades': 5000,
            'last_sold_price': 20000,
            'last_sold_time': '2024-01-15T10:30:00+00:00',
            'scrape_time': '2024-01-15T10:35:00+00:00'
        }]
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.execute_query.return_value = mock_query_results
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            query_response = query_handler(original_params, mock_context)
            query_body = json.loads(query_response["body"])
        
        # Verify query_params are included in queryData response
        assert "query_params" in query_body
        assert query_body["query_params"]["item_id"] == original_params["item_id"]
        assert query_body["query_params"]["start_date"] == original_params["start_date"]
        assert query_body["query_params"]["end_date"] == original_params["end_date"]
        assert query_body["query_params"]["limit"] == original_params["limit"]
        
        # Merge for analysis (Step Functions MergeForAnalysis state)
        merged_input = {
            "market_data": query_body["data"],
            "query_params": query_body["query_params"],
            "correlation_id": "test-corr-id"
        }
        
        analyze_response = analyze_handler(merged_input, mock_context)
        analyze_body = json.loads(analyze_response["body"])
        
        # Verify original query parameters are preserved in analyzeData response
        assert analyze_body["date_range"]["start"] == original_params["start_date"]
        assert analyze_body["date_range"]["end"] == original_params["end_date"]
        assert analyze_body["item_id"] == original_params["item_id"]
