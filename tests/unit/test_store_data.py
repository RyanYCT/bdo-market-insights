"""
Unit tests for storeData Lambda function.

Tests MarketScrape creation, Item creation with category handling,
bulk insert logic, and transaction rollback on errors.
"""

import pytest
import sys
import json
from unittest.mock import Mock, MagicMock, patch, call
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

# Add lambda_layer to path
sys.path.insert(0, 'lambda_layer/python')

# Add storeData to path
from pathlib import Path
store_data_path = Path(__file__).parent.parent.parent / "storeData"
sys.path.insert(0, str(store_data_path))

from lambda_function import (
    get_or_create_market_scrape,
    get_or_create_items,
    bulk_insert_market_data,
    handler,
    lambda_handler
)

from common.database import DatabasePool
from common.schemas import MarketDataRecord
from common.logging import StructuredLogger


class TestGetOrCreateMarketScrape:
    """Test MarketScrape creation logic."""
    
    def test_create_new_market_scrape(self):
        """Test creating a new MarketScrape record."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                
                endpoint = "https://api.example.com/market"
                scrape_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                
                scrape_id = get_or_create_market_scrape(
                    pool,
                    endpoint,
                    scrape_time,
                    mock_logger
                )
                
                # Verify scrape_id returned
                assert scrape_id == 42
                
                # Verify SQL was executed with correct parameters
                assert mock_cursor.execute.called
                call_args = mock_cursor.execute.call_args[0]
                assert 'INSERT INTO bdo_marketscrape' in call_args[0]
                assert call_args[1] == (endpoint, scrape_time)
                
                # Verify commit was called
                assert mock_connection.commit.called
    
    def test_get_existing_market_scrape_on_conflict(self):
        """Test getting existing MarketScrape on conflict (duplicate scrape_time)."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        # Return existing ID
        mock_cursor.fetchone.return_value = (99,)
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                
                endpoint = "https://api.example.com/market"
                scrape_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                
                scrape_id = get_or_create_market_scrape(
                    pool,
                    endpoint,
                    scrape_time,
                    mock_logger
                )
                
                # Should return existing ID
                assert scrape_id == 99
                
                # Verify ON CONFLICT clause in query
                call_args = mock_cursor.execute.call_args[0]
                assert 'ON CONFLICT' in call_args[0]


class TestGetOrCreateItems:
    """Test Item creation with category handling."""
    
    def test_create_new_items(self):
        """Test creating new Item records."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        records = [
            MarketDataRecord(
                item_id=1001,
                sid=0,
                current_stock=50,
                total_trades=1234,
                last_sold_price=15000,
                last_sold_time=datetime.now(timezone.utc)
            ),
            MarketDataRecord(
                item_id=1002,
                sid=1,
                current_stock=30,
                total_trades=567,
                last_sold_price=20000,
                last_sold_time=datetime.now(timezone.utc)
            )
        ]
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        # Return item IDs after fetch
        mock_cursor.fetchall.return_value = [
            (1001, 0, 1),
            (1002, 1, 2)
        ]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        # Mock execute_values
        mock_execute_values = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                with patch('psycopg2.extras.execute_values', mock_execute_values):
                    pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                    
                    item_map = get_or_create_items(
                        pool,
                        records,
                        category_id=5,
                        logger=mock_logger
                    )
                    
                    # Verify item_map structure
                    assert (1001, 0) in item_map
                    assert (1002, 1) in item_map
                    assert item_map[(1001, 0)] == 1
                    assert item_map[(1002, 1)] == 2
                    
                    # Verify execute_values was called for bulk insert
                    assert mock_execute_values.called
                    
                    # Verify commit was called
                    assert mock_connection.commit.called
    
    def test_create_items_with_custom_category(self):
        """Test creating items with custom category_id."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        records = [
            MarketDataRecord(
                item_id=1001,
                sid=0,
                current_stock=50,
                total_trades=1234,
                last_sold_price=15000,
                last_sold_time=datetime.now(timezone.utc)
            )
        ]
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1001, 0, 1)]
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        mock_execute_values = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                with patch('psycopg2.extras.execute_values', mock_execute_values):
                    pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                    
                    # Use custom category_id = 3
                    item_map = get_or_create_items(
                        pool,
                        records,
                        category_id=3,
                        logger=mock_logger
                    )
                    
                    # Verify execute_values was called with category_id=3
                    assert mock_execute_values.called
                    call_args = mock_execute_values.call_args[0]
                    data_rows = call_args[2]
                    
                    # Each row should have category_id=3 as the 4th element
                    for row in data_rows:
                        assert row[3] == 3


class TestBulkInsertMarketData:
    """Test bulk insert logic for MarketData."""
    
    def test_bulk_insert_success(self):
        """Test successful bulk insert of MarketData records."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        records = [
            MarketDataRecord(
                item_id=1001,
                sid=0,
                current_stock=50,
                total_trades=1234,
                last_sold_price=15000,
                last_sold_time=datetime.now(timezone.utc)
            ),
            MarketDataRecord(
                item_id=1002,
                sid=1,
                current_stock=30,
                total_trades=567,
                last_sold_price=20000,
                last_sold_time=datetime.now(timezone.utc)
            )
        ]
        
        item_map = {
            (1001, 0): 1,
            (1002, 1): 2
        }
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        mock_execute_values = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                with patch('psycopg2.extras.execute_values', mock_execute_values):
                    pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                    
                    inserted = bulk_insert_market_data(
                        pool,
                        records,
                        item_map,
                        scrape_id=42,
                        logger=mock_logger
                    )
                    
                    # Verify correct number of records inserted
                    assert inserted == 2
                    
                    # Verify execute_values was called
                    assert mock_execute_values.called
                    
                    # Verify commit was called
                    assert mock_connection.commit.called
    
    def test_bulk_insert_skips_missing_items(self):
        """Test that records with missing items in map are skipped."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        records = [
            MarketDataRecord(
                item_id=1001,
                sid=0,
                current_stock=50,
                total_trades=1234,
                last_sold_price=15000,
                last_sold_time=datetime.now(timezone.utc)
            ),
            MarketDataRecord(
                item_id=1002,
                sid=1,
                current_stock=30,
                total_trades=567,
                last_sold_price=20000,
                last_sold_time=datetime.now(timezone.utc)
            )
        ]
        
        # Only include first item in map
        item_map = {
            (1001, 0): 1
        }
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        mock_execute_values = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                with patch('psycopg2.extras.execute_values', mock_execute_values):
                    pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                    
                    inserted = bulk_insert_market_data(
                        pool,
                        records,
                        item_map,
                        scrape_id=42,
                        logger=mock_logger
                    )
                    
                    # Only 1 record should be inserted
                    assert inserted == 1
                    
                    # Verify warning was logged for missing item
                    assert mock_logger.warning.called
    
    def test_bulk_insert_empty_records(self):
        """Test bulk insert with empty records list."""
        db_credentials = {
            "username": "test_user",
            "password": "test_pass",
            "host": "localhost",
            "port": "5432",
            "database": "testdb"
        }
        
        records = []
        item_map = {}
        
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.isolation_level = 1
        
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connection.cursor.return_value.__exit__.return_value = None
        
        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_connection
        
        mock_logger = MagicMock()
        
        with patch('boto3.client') as mock_boto_client:
            mock_secrets_client = MagicMock()
            mock_boto_client.return_value = mock_secrets_client
            mock_secrets_client.get_secret_value.return_value = {
                'SecretString': json.dumps(db_credentials)
            }
            
            with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
                mock_pool_class.return_value = mock_pool
                
                pool = DatabasePool("test-secret", pool_size=5, logger=mock_logger)
                
                inserted = bulk_insert_market_data(
                    pool,
                    records,
                    item_map,
                    scrape_id=42,
                    logger=mock_logger
                )
                
                # Should return 0
                assert inserted == 0
                
                # Verify warning was logged
                assert mock_logger.warning.called


class TestStoreDataHandler:
    """Test storeData Lambda handler."""
    
    def test_handler_success(self):
        """Test successful handler execution."""
        event = {
            'cleaned_data': [
                {
                    'item_id': 1001,
                    'sid': 0,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_price': 15000,
                    'last_sold_time': '2024-01-15T10:30:00+00:00'
                }
            ],
            'scrape_endpoint': 'https://api.example.com/market',
            'scrape_time': '2024-01-15T10:30:00+00:00',
            'correlation_id': 'test-corr-id'
        }
        
        context = Mock()
        context.function_name = 'storeData'
        context.aws_request_id = 'test-request-id'
        
        # Mock all database operations
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_get_pool.return_value = mock_pool
            
            with patch('lambda_function.get_or_create_market_scrape', return_value=42):
                with patch('lambda_function.get_or_create_items', return_value={(1001, 0): 1}):
                    with patch('lambda_function.bulk_insert_market_data', return_value=1):
                        result = lambda_handler(event, context)
                        
                        # Verify response structure
                        assert result['statusCode'] == 200
                        body = json.loads(result['body'])
                        assert body['records_inserted'] == 1
                        assert body['scrape_id'] == 42
                        assert body['correlation_id'] == 'test-corr-id'
    
    def test_handler_empty_data(self):
        """Test handler with empty cleaned_data."""
        event = {
            'cleaned_data': [],
            'scrape_endpoint': 'https://api.example.com/market',
            'scrape_time': '2024-01-15T10:30:00+00:00',
            'correlation_id': 'test-corr-id'
        }
        
        context = Mock()
        context.function_name = 'storeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should succeed with 0 records
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['records_inserted'] == 0
        assert 'No data to store' in body['message']
    
    def test_handler_validation_error(self):
        """Test handler with invalid input."""
        event = {
            'cleaned_data': [
                {
                    'item_id': 1001,
                    'sid': 0,
                    'current_stock': -50,  # Invalid: negative
                    'total_trades': 1234,
                    'last_sold_price': 15000,
                    'last_sold_time': '2024-01-15T10:30:00+00:00'
                }
            ],
            'scrape_endpoint': 'https://api.example.com/market',
            'scrape_time': '2024-01-15T10:30:00+00:00',
            'correlation_id': 'test-corr-id'
        }
        
        context = Mock()
        context.function_name = 'storeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return validation error
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_missing_required_field(self):
        """Test handler with missing required field."""
        event = {
            'cleaned_data': [
                {
                    'item_id': 1001,
                    'sid': 0,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_price': 15000,
                    'last_sold_time': '2024-01-15T10:30:00+00:00'
                }
            ],
            # Missing scrape_endpoint
            'scrape_time': '2024-01-15T10:30:00+00:00',
            'correlation_id': 'test-corr-id'
        }
        
        context = Mock()
        context.function_name = 'storeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return validation error
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
