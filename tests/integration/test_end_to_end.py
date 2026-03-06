"""
End-to-end integration tests.

Tests full ETL pipeline followed by query to verify:
- Data inserted by ETL can be queried
- Analysis results are accurate
- Complete system integration works correctly

Requirements: 13.5
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
@pytest.mark.e2e
class TestEndToEnd:
    """End-to-end integration tests for complete system."""
    
    def test_etl_then_query_full_flow(self):
        """
        Test complete flow: ETL pipeline inserts data, then query retrieves and analyzes it.
        
        This test simulates:
        1. ETL pipeline collecting and storing market data
        2. Query pipeline retrieving and analyzing that data
        3. Verifying analysis results match the inserted data
        """
        # Import all Lambda handlers
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        from src.fetchData.lambda_function import lambda_handler as fetch_handler
        from src.cleanData.lambda_function import lambda_handler as clean_handler
        from src.storeData.lambda_function import lambda_handler as store_handler
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Shared in-memory "database" to simulate data persistence
        stored_market_data = []
        
        # ===== PHASE 1: ETL PIPELINE =====
        
        # Step 1: retrieveIdList
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.return_value = [1001, 1002]
            
            retrieve_response = retrieve_handler({"table_name": "test-table"}, mock_context)
            retrieve_body = json.loads(retrieve_response["body"])
            correlation_id = retrieve_body["correlation_id"]
        
        # Step 2: fetchData
        test_scrape_time = datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc)
        
        with patch('fetchData.lambda_function.ExternalAPIClient') as MockAPIClient:
            mock_api = MockAPIClient.return_value
            mock_api.fetch_market_data.return_value = [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1000,
                    'lastSoldPrice': 10000,
                    'lastSoldTime': '2024-01-10T10:00:00Z'
                },
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 45,
                    'totalTrades': 1200,
                    'lastSoldPrice': 12000,
                    'lastSoldTime': '2024-01-12T12:00:00Z'
                },
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 40,
                    'totalTrades': 1400,
                    'lastSoldPrice': 14000,
                    'lastSoldTime': '2024-01-14T14:00:00Z'
                },
                {
                    'itemId': 1002,
                    'sid': 0,
                    'currentStock': 100,
                    'totalTrades': 2000,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-10T10:00:00Z'
                }
            ]
            
            fetch_event = {
                'item_ids': retrieve_body['item_ids'],
                'batch_size': 50,
                'correlation_id': correlation_id
            }
            fetch_response = fetch_handler(fetch_event, mock_context)
            fetch_body = json.loads(fetch_response["body"])
        
        # Step 3: cleanData
        clean_event = {
            'raw_data': fetch_body['raw_data'],
            'correlation_id': correlation_id
        }
        clean_response = clean_handler(clean_event, mock_context)
        clean_body = json.loads(clean_response["body"])
        
        # Verify all records are valid
        assert clean_body["metadata"]["valid_records"] == 4
        assert clean_body["metadata"]["invalid_records"] == 0
        
        # Step 4: storeData
        # Mock database to store data in our in-memory structure
        def mock_store_data(cleaned_data):
            """Simulate storing data to database."""
            for record in cleaned_data:
                stored_market_data.append({
                    'item_id': record['item_id'],
                    'item_name': f"Item_{record['item_id']}",
                    'sid': record['sid'],
                    'category': 'Buff',
                    'current_stock': record['current_stock'],
                    'total_trades': record['total_trades'],
                    'last_sold_price': record['last_sold_price'],
                    'last_sold_time': record['last_sold_time'],
                    'scrape_time': test_scrape_time.isoformat()
                })
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]  # scrape_id
        mock_cursor.fetchall.return_value = [(1001, 0, 1), (1002, 0, 2)]
        mock_cursor.rowcount = 4
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('storeData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.get_connection.return_value = mock_connection
            mock_get_pool.return_value = mock_pool
            
            # Store cleaned data in our in-memory structure
            mock_store_data(clean_body['cleaned_data'])
            
            store_event = {
                'cleaned_data': clean_body['cleaned_data'],
                'scrape_endpoint': 'https://api.example.com/market',
                'scrape_time': test_scrape_time.isoformat(),
                'correlation_id': correlation_id
            }
            store_response = store_handler(store_event, mock_context)
            store_body = json.loads(store_response["body"])
            
            # Verify data was stored
            assert store_body["records_inserted"] == 4
        
        # ===== PHASE 2: QUERY PIPELINE =====
        
        # Now query the data we just inserted
        # Step 1: queryData - retrieve item 1001
        def mock_query_database(item_id, start_date, end_date):
            """Simulate querying database for stored data."""
            results = []
            for record in stored_market_data:
                if record['item_id'] == item_id:
                    # Parse dates for comparison
                    record_time = datetime.fromisoformat(record['last_sold_time'].replace('Z', '+00:00'))
                    start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    
                    if start <= record_time <= end:
                        results.append(record)
            
            return results
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
            # Query for item 1001
            query_results = mock_query_database(
                item_id=1001,
                start_date='2024-01-01T00:00:00Z',
                end_date='2024-01-31T23:59:59Z'
            )
            
            mock_pool = Mock()
            mock_pool.execute_query.return_value = query_results
            mock_pool.get_connection.return_value = mock_connection
            mock_get_pool.return_value = mock_pool
            
            query_event = {
                "item_id": 1001,
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-31T23:59:59Z",
                "limit": 100
            }
            
            query_response = query_handler(query_event, mock_context)
            query_body = json.loads(query_response["body"])
            
            # Verify we got the 3 records for item 1001
            assert query_body["count"] == 3
            assert len(query_body["data"]) == 3
        
        # Step 2: analyzeData
        merged_input = {
            "market_data": query_body["data"],
            "query_params": query_body["query_params"],
            "correlation_id": "query-correlation-id"
        }
        
        analyze_response = analyze_handler(merged_input, mock_context)
        analyze_body = json.loads(analyze_response["body"])
        
        # ===== VERIFICATION =====
        
        # Verify analysis results match the data we inserted
        assert analyze_body["item_id"] == 1001
        assert analyze_body["record_count"] == 3
        
        # Verify statistics are calculated correctly from inserted data
        # Prices: 10000, 12000, 14000
        stats = analyze_body["statistics"]
        assert stats["avg_last_sold_price"] == 12000.0  # (10000 + 12000 + 14000) / 3
        assert stats["min_last_sold_price"] == 10000
        assert stats["max_last_sold_price"] == 14000
        
        # Stocks: 50, 45, 40
        assert stats["avg_current_stock"] == 45.0  # (50 + 45 + 40) / 3
        
        # Trades: 1000, 1200, 1400
        assert stats["total_trades_sum"] == 3600  # 1000 + 1200 + 1400
        
        # Verify price trend (increasing from 10000 to 14000)
        assert analyze_body["price_trend"] == "increasing"
        
        # Verify profitability score is positive (prices increasing)
        assert analyze_body["profitability_score"] > 0
    
    def test_etl_then_query_multiple_items(self):
        """
        Test ETL pipeline with multiple items, then query each separately.
        
        Verifies that data for different items is stored and retrieved correctly.
        """
        from src.retrieveIdList.lambda_function import lambda_handler as retrieve_handler
        from src.fetchData.lambda_function import lambda_handler as fetch_handler
        from src.cleanData.lambda_function import lambda_handler as clean_handler
        from src.storeData.lambda_function import lambda_handler as store_handler
        from src.queryData.lambda_function import lambda_handler as query_handler
        from src.analyzeData.lambda_function import lambda_handler as analyze_handler
        
        mock_context = Mock()
        mock_context.function_name = "test-function"
        mock_context.aws_request_id = "test-request-id"
        
        # Shared in-memory "database"
        stored_market_data = []
        
        # ===== ETL PIPELINE =====
        
        # retrieveIdList
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockDynamoService:
            mock_dynamo = MockDynamoService.return_value
            mock_dynamo.get_item_ids.return_value = [2001, 2002, 2003]
            
            retrieve_response = retrieve_handler({"table_name": "test-table"}, mock_context)
            retrieve_body = json.loads(retrieve_response["body"])
        
        # fetchData - different data for each item
        with patch('fetchData.lambda_function.ExternalAPIClient') as MockAPIClient:
            mock_api = MockAPIClient.return_value
            mock_api.fetch_market_data.return_value = [
                # Item 2001: stable prices
                {'itemId': 2001, 'sid': 0, 'currentStock': 100, 'totalTrades': 1000,
                 'lastSoldPrice': 5000, 'lastSoldTime': '2024-01-10T10:00:00Z'},
                {'itemId': 2001, 'sid': 0, 'currentStock': 100, 'totalTrades': 1100,
                 'lastSoldPrice': 5000, 'lastSoldTime': '2024-01-12T10:00:00Z'},
                
                # Item 2002: increasing prices
                {'itemId': 2002, 'sid': 0, 'currentStock': 50, 'totalTrades': 2000,
                 'lastSoldPrice': 10000, 'lastSoldTime': '2024-01-10T10:00:00Z'},
                {'itemId': 2002, 'sid': 0, 'currentStock': 40, 'totalTrades': 2200,
                 'lastSoldPrice': 15000, 'lastSoldTime': '2024-01-12T10:00:00Z'},
                
                # Item 2003: decreasing prices
                {'itemId': 2003, 'sid': 0, 'currentStock': 200, 'totalTrades': 3000,
                 'lastSoldPrice': 20000, 'lastSoldTime': '2024-01-10T10:00:00Z'},
                {'itemId': 2003, 'sid': 0, 'currentStock': 250, 'totalTrades': 3100,
                 'lastSoldPrice': 15000, 'lastSoldTime': '2024-01-12T10:00:00Z'},
            ]
            
            fetch_response = fetch_handler({
                'item_ids': retrieve_body['item_ids'],
                'batch_size': 50,
                'correlation_id': retrieve_body['correlation_id']
            }, mock_context)
            fetch_body = json.loads(fetch_response["body"])
        
        # cleanData
        clean_response = clean_handler({
            'raw_data': fetch_body['raw_data'],
            'correlation_id': retrieve_body['correlation_id']
        }, mock_context)
        clean_body = json.loads(clean_response["body"])
        
        # storeData
        def mock_store_data(cleaned_data):
            for record in cleaned_data:
                stored_market_data.append({
                    'item_id': record['item_id'],
                    'item_name': f"Item_{record['item_id']}",
                    'sid': record['sid'],
                    'category': 'Buff',
                    'current_stock': record['current_stock'],
                    'total_trades': record['total_trades'],
                    'last_sold_price': record['last_sold_price'],
                    'last_sold_time': record['last_sold_time'],
                    'scrape_time': '2024-01-15T10:35:00+00:00'
                })
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [1]
        mock_cursor.fetchall.return_value = [(2001, 0, 1), (2002, 0, 2), (2003, 0, 3)]
        mock_cursor.rowcount = 6
        mock_cursor.__enter__ = Mock(return_value=mock_cursor)
        mock_cursor.__exit__ = Mock(return_value=False)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = Mock(return_value=mock_connection)
        mock_connection.__exit__ = Mock(return_value=False)
        
        with patch('storeData.lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = Mock()
            mock_pool.get_connection.return_value = mock_connection
            mock_get_pool.return_value = mock_pool
            
            mock_store_data(clean_body['cleaned_data'])
            
            store_response = store_handler({
                'cleaned_data': clean_body['cleaned_data'],
                'scrape_endpoint': 'https://api.example.com/market',
                'scrape_time': '2024-01-15T10:35:00Z',
                'correlation_id': retrieve_body['correlation_id']
            }, mock_context)
        
        # ===== QUERY EACH ITEM =====
        
        def query_and_analyze(item_id):
            """Helper to query and analyze a specific item."""
            def mock_query_database(query_item_id):
                return [r for r in stored_market_data if r['item_id'] == query_item_id]
            
            mock_connection = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.__enter__ = Mock(return_value=mock_cursor)
            mock_cursor.__exit__ = Mock(return_value=False)
            mock_connection.cursor.return_value = mock_cursor
            mock_connection.__enter__ = Mock(return_value=mock_connection)
            mock_connection.__exit__ = Mock(return_value=False)
            
            with patch('queryData.lambda_function.get_database_pool') as mock_get_pool:
                query_results = mock_query_database(item_id)
                
                mock_pool = Mock()
                mock_pool.execute_query.return_value = query_results
                mock_pool.get_connection.return_value = mock_connection
                mock_get_pool.return_value = mock_pool
                
                query_response = query_handler({
                    "item_id": item_id,
                    "start_date": "2024-01-01T00:00:00Z",
                    "end_date": "2024-01-31T23:59:59Z",
                    "limit": 100
                }, mock_context)
                query_body = json.loads(query_response["body"])
            
            analyze_response = analyze_handler({
                "market_data": query_body["data"],
                "query_params": query_body["query_params"],
                "correlation_id": "test-corr-id"
            }, mock_context)
            
            return json.loads(analyze_response["body"])
        
        # Query item 2001 (stable prices)
        result_2001 = query_and_analyze(2001)
        assert result_2001["item_id"] == 2001
        assert result_2001["price_trend"] == "stable"
        assert result_2001["statistics"]["avg_last_sold_price"] == 5000.0
        
        # Query item 2002 (increasing prices)
        result_2002 = query_and_analyze(2002)
        assert result_2002["item_id"] == 2002
        assert result_2002["price_trend"] == "increasing"
        assert result_2002["statistics"]["min_last_sold_price"] == 10000
        assert result_2002["statistics"]["max_last_sold_price"] == 15000
        
        # Query item 2003 (decreasing prices)
        result_2003 = query_and_analyze(2003)
        assert result_2003["item_id"] == 2003
        assert result_2003["price_trend"] == "decreasing"
        assert result_2003["statistics"]["min_last_sold_price"] == 15000
        assert result_2003["statistics"]["max_last_sold_price"] == 20000
