"""
Integration tests for ETL pipeline.

Tests complete flow: retrieveIdList → fetchData → cleanData → storeData
Uses mocked DynamoDB, External API, and RDS.
Verifies data flows correctly through all stages and correlation ID propagation.

Requirements: 13.2, 13.5
"""

import pytest
import sys
import json
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone
from decimal import Decimal

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
class TestETLPipelineIntegration:
    """Integration tests for complete ETL pipeline."""
    
    def test_complete_etl_flow(self):
        """
        Test complete ETL pipeline flow from DynamoDB to RDS.
        
        Verifies:
        - Data flows correctly through all stages
        - Correlation ID is propagated
        - Each stage transforms data appropriately
        - Final data is stored in database
        """
        # Import Lambda handlers
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        from src.fetchData.lambda_function import lambda_handler as fetch_handler
        from src.cleanData.lambda_function import lambda_handler as clean_handler
        from src.storeData.lambda_function import lambda_handler as store_handler
        
        # Create mock context
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Stage 1: retrieveIdList
        # Mock DynamoDB to return item IDs
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.return_value = [1001, 1002, 1003]
            
            retrieve_event = {"table_name": "test-table"}
            retrieve_response = retrieve_handler(retrieve_event, mock_context)
            
            # Verify response structure
            assert retrieve_response["statusCode"] == 200
            retrieve_body = json.loads(retrieve_response["body"])
            assert "item_ids" in retrieve_body
            assert "correlation_id" in retrieve_body
            assert retrieve_body["item_ids"] == [1001, 1002, 1003]
            
            # Extract correlation ID for propagation test
            correlation_id = retrieve_body["correlation_id"]
        
        # Stage 2: fetchData
        # Mock External API
        with patch('fetchData.lambda_function.ExternalAPIClient') as MockAPIClient:
            mock_api = MockAPIClient.return_value
            mock_api.fetch_market_data.return_value = [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1234,
                    'lastSoldPrice': 15000,
                    'lastSoldTime': '2024-01-15T10:30:00Z'
                },
                {
                    'itemId': 1002,
                    'sid': 0,
                    'currentStock': 30,
                    'totalTrades': 5678,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-15T10:31:00Z'
                },
                {
                    'itemId': 1003,
                    'sid': 1,
                    'currentStock': 10,
                    'totalTrades': 9012,
                    'lastSoldPrice': 25000,
                    'lastSoldTime': '2024-01-15T10:32:00Z'
                }
            ]
            
            fetch_event = {
                'item_ids': retrieve_body['item_ids'],
                'batch_size': 50,
                'correlation_id': correlation_id
            }
            fetch_response = fetch_handler(fetch_event, mock_context)
            
            # Verify response structure
            assert fetch_response["statusCode"] == 200
            fetch_body = json.loads(fetch_response["body"])
            assert "raw_data" in fetch_body
            assert "correlation_id" in fetch_body
            assert fetch_body["correlation_id"] == correlation_id  # Correlation ID propagated
            assert len(fetch_body["raw_data"]) == 3
        
        # Stage 3: cleanData
        clean_event = {
            'raw_data': fetch_body['raw_data'],
            'correlation_id': correlation_id
        }
        clean_response = clean_handler(clean_event, mock_context)
        
        # Verify response structure
        assert clean_response["statusCode"] == 200
        clean_body = json.loads(clean_response["body"])
        assert "cleaned_data" in clean_body
        assert "correlation_id" in clean_body
        assert clean_body["correlation_id"] == correlation_id  # Correlation ID propagated
        assert len(clean_body["cleaned_data"]) == 3
        
        # Verify data transformation
        first_record = clean_body["cleaned_data"][0]
        assert "item_id" in first_record  # Transformed from itemId
        assert "current_stock" in first_record  # Transformed from currentStock
        assert "last_sold_price" in first_record  # Transformed from lastSoldPrice
        
        # Stage 4: storeData
        # Mock database operations
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]  # scrape_id
        mock_cursor.fetchall.return_value = [(1001, 0, 1), (1002, 0, 2), (1003, 1, 3)]
        mock_cursor.rowcount = 3
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('storeData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict instead of Mock
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            store_event = {
                'cleaned_data': clean_body['cleaned_data'],
                'scrape_endpoint': 'https://api.example.com/market',
                'scrape_time': '2024-01-15T10:35:00Z',
                'correlation_id': correlation_id
            }
            store_response = store_handler(store_event, mock_context)
            
            # Verify response structure
            assert store_response["statusCode"] == 200
            store_body = json.loads(store_response["body"])
            assert "records_inserted" in store_body
            assert "correlation_id" in store_body
            assert store_body["correlation_id"] == correlation_id  # Correlation ID propagated
            assert store_body["records_inserted"] == 3
    
    def test_etl_pipeline_with_invalid_data_filtering(self):
        """
        Test ETL pipeline filters out invalid records in cleanData stage.
        
        Verifies that invalid records are filtered and don't reach storeData.
        """
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        from src.fetchData.lambda_function import lambda_handler as fetch_handler
        from src.cleanData.lambda_function import lambda_handler as clean_handler
        from src.storeData.lambda_function import lambda_handler as store_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Stage 1: retrieveIdList
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.return_value = [1001, 1002]
            
            retrieve_response = retrieve_handler({"table_name": "test-table"}, mock_context)
            retrieve_body = json.loads(retrieve_response["body"])
            correlation_id = retrieve_body["correlation_id"]
        
        # Stage 2: fetchData - return mix of valid and invalid data
        with patch('fetchData.lambda_function.ExternalAPIClient') as MockAPIClient:
            mock_api = MockAPIClient.return_value
            mock_api.fetch_market_data.return_value = [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1234,
                    'lastSoldPrice': 15000,
                    'lastSoldTime': '2024-01-15T10:30:00Z'
                },
                {
                    'itemId': 1002,
                    'sid': 0,
                    'currentStock': -10,  # Invalid: negative stock
                    'totalTrades': 5678,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-15T10:31:00Z'
                }
            ]
            
            fetch_event = {
                'item_ids': retrieve_body['item_ids'],
                'batch_size': 50,
                'correlation_id': correlation_id
            }
            fetch_response = fetch_handler(fetch_event, mock_context)
            fetch_body = json.loads(fetch_response["body"])
        
        # Stage 3: cleanData - should filter out invalid record
        clean_event = {
            'raw_data': fetch_body['raw_data'],
            'correlation_id': correlation_id
        }
        clean_response = clean_handler(clean_event, mock_context)
        clean_body = json.loads(clean_response["body"])
        
        # Verify only valid record remains
        assert len(clean_body["cleaned_data"]) == 1
        assert clean_body["metadata"]["valid_records"] == 1
        assert clean_body["metadata"]["invalid_records"] == 1
        assert clean_body["cleaned_data"][0]["item_id"] == 1001
    
    def test_etl_pipeline_correlation_id_propagation(self):
        """
        Test that correlation ID is propagated through entire ETL pipeline.
        
        Verifies correlation ID remains consistent across all stages.
        """
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        from src.fetchData.lambda_function import lambda_handler as fetch_handler
        from src.cleanData.lambda_function import lambda_handler as clean_handler
        from src.storeData.lambda_function import lambda_handler as store_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Provide initial correlation ID
        initial_correlation_id = "test-correlation-id-12345"
        
        # Stage 1: retrieveIdList with provided correlation ID
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.return_value = [1001]
            
            retrieve_event = {
                "table_name": "test-table",
                "correlation_id": initial_correlation_id
            }
            retrieve_response = retrieve_handler(retrieve_event, mock_context)
            retrieve_body = json.loads(retrieve_response["body"])
            
            # Should use provided correlation ID
            assert retrieve_body["correlation_id"] == initial_correlation_id
        
        # Stage 2: fetchData
        with patch('fetchData.lambda_function.ExternalAPIClient') as MockAPIClient:
            mock_api = MockAPIClient.return_value
            mock_api.fetch_market_data.return_value = [{
                'itemId': 1001,
                'sid': 0,
                'currentStock': 50,
                'totalTrades': 1234,
                'lastSoldPrice': 15000,
                'lastSoldTime': '2024-01-15T10:30:00Z'
            }]
            
            fetch_event = {
                'item_ids': [1001],
                'batch_size': 50,
                'correlation_id': initial_correlation_id
            }
            fetch_response = fetch_handler(fetch_event, mock_context)
            fetch_body = json.loads(fetch_response["body"])
            
            # Should propagate correlation ID
            assert fetch_body["correlation_id"] == initial_correlation_id
        
        # Stage 3: cleanData
        clean_event = {
            'raw_data': fetch_body['raw_data'],
            'correlation_id': initial_correlation_id
        }
        clean_response = clean_handler(clean_event, mock_context)
        clean_body = json.loads(clean_response["body"])
        
        # Should propagate correlation ID
        assert clean_body["correlation_id"] == initial_correlation_id
        
        # Stage 4: storeData
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]
        mock_cursor.fetchall.return_value = [(1001, 0, 1)]
        mock_cursor.rowcount = 1
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('storeData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.get_connection.return_value = mock_connection
            # Mock get_pool_stats to return proper dict
            mock_pool.get_pool_stats.return_value = {
                'pool_size': 5,
                'active': 1,
                'idle': 4
            }
            mock_get_pool.return_value = mock_pool
            
            store_event = {
                'cleaned_data': clean_body['cleaned_data'],
                'scrape_endpoint': 'https://api.example.com/market',
                'scrape_time': '2024-01-15T10:35:00Z',
                'correlation_id': initial_correlation_id
            }
            store_response = store_handler(store_event, mock_context)
            store_body = json.loads(store_response["body"])
            
            # Should propagate correlation ID through entire pipeline
            assert store_body["correlation_id"] == initial_correlation_id
    
    def test_etl_pipeline_empty_dynamodb_table(self):
        """
        Test ETL pipeline handles empty DynamoDB table gracefully.
        
        Verifies error handling when no item IDs are found.
        """
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Mock DynamoDB to return empty list
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.side_effect = ValueError("No item IDs found in table test-table")
            
            retrieve_event = {"table_name": "test-table"}
            retrieve_response = retrieve_handler(retrieve_event, mock_context)
            
            # Should return error response
            assert retrieve_response["statusCode"] == 500
            body = json.loads(retrieve_response["body"])
            assert "error" in body
