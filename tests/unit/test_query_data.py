"""
Unit tests for queryData Lambda function.

Tests query building for different parameter combinations, date range filtering,
limit enforcement, and error handling for invalid parameters.
"""

import pytest
import sys
import json
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

# Mock psycopg2 before importing
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

# Import from queryData Lambda function
from queryData.lambda_function import (
    build_query,
    handler,
    lambda_handler
)

from common.database import DatabasePool


class TestBuildQuery:
    """Test query building for different parameter combinations."""
    
    def test_query_with_item_id_only(self):
        """Test query building with only item_id."""
        query, params = build_query(
            item_id=1001,
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=100
        )
        
        # Verify query structure
        assert "SELECT" in query
        assert "FROM bdo_marketdata md" in query
        assert "JOIN bdo_item i" in query
        assert "JOIN bdo_marketscrape ms" in query
        assert "JOIN bdo_itemcategory ic" in query
        assert "WHERE" in query
        assert "i.item_id = %s" in query
        assert "ORDER BY" in query
        assert "LIMIT %s" in query
        
        # Verify parameters
        assert 1001 in params
        assert 100 in params
    
    def test_query_with_name_search(self):
        """Test query building with name search (ILIKE)."""
        query, params = build_query(
            name="Black Stone",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=50
        )
        
        # Verify ILIKE clause for partial match
        assert "i.name ILIKE %s" in query
        
        # Verify wildcard pattern in params
        assert any("%Black Stone%" in str(p) for p in params)
        assert 50 in params
    
    def test_query_with_category_filter(self):
        """Test query building with category filter."""
        query, params = build_query(
            category="Accessory",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=100
        )
        
        # Verify category filter
        assert "ic.name = %s" in query
        assert "Accessory" in params
    
    def test_query_with_date_range(self):
        """Test date range filtering."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        
        query, params = build_query(
            item_id=1001,
            start_date=start,
            end_date=end,
            limit=100
        )
        
        # Verify date range clauses
        assert "md.last_sold_time >= %s" in query
        assert "md.last_sold_time <= %s" in query
        
        # Verify dates in params
        assert start in params
        assert end in params
    
    def test_query_with_all_filters(self):
        """Test query building with all filters combined."""
        query, params = build_query(
            item_id=1001,
            name="Black Stone",
            category="Accessory",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=200
        )
        
        # Verify all filters present
        assert "i.item_id = %s" in query
        assert "i.name ILIKE %s" in query
        assert "ic.name = %s" in query
        assert "md.last_sold_time >= %s" in query
        assert "md.last_sold_time <= %s" in query
        
        # Verify all params present
        assert 1001 in params
        assert "Accessory" in params
        assert 200 in params
    
    def test_query_limit_enforcement(self):
        """Test limit enforcement in query."""
        query, params = build_query(
            item_id=1001,
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=500
        )
        
        # Verify LIMIT clause
        assert "LIMIT %s" in query
        assert 500 in params
        assert params[-1] == 500  # Limit should be last param
    
    def test_query_ordering(self):
        """Test query ordering by last_sold_time DESC."""
        query, params = build_query(
            item_id=1001,
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            limit=100
        )
        
        # Verify ORDER BY clause
        assert "ORDER BY md.last_sold_time DESC" in query


class TestQueryDataHandler:
    """Test queryData Lambda handler."""
    
    def test_handler_success_with_item_id(self):
        """Test successful handler execution with item_id."""
        event = {
            'item_id': 1001,
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Mock database pool
        mock_results = [
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 50,
                'total_trades': 1234,
                'last_sold_price': 15000,
                'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
                'scrape_time': datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc)
            }
        ]
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.execute_query.return_value = mock_results
            mock_get_pool.return_value = mock_pool
            
            result = handler(event, context, mock_logger)
            
            # Verify response structure
            assert 'data' in result
            assert 'count' in result
            assert 'query_params' in result
            assert result['count'] == 1
            assert len(result['data']) == 1
            
            # Verify datetime conversion to ISO format
            assert isinstance(result['data'][0]['last_sold_time'], str)
            assert isinstance(result['data'][0]['scrape_time'], str)
    
    def test_handler_success_with_name_search(self):
        """Test successful handler execution with name search."""
        event = {
            'name': 'Black Stone',
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 50
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        mock_results = [
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 50,
                'total_trades': 1234,
                'last_sold_price': 15000,
                'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
                'scrape_time': datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc)
            },
            {
                'item_id': 1002,
                'item_name': 'Black Stone (Armor)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 30,
                'total_trades': 567,
                'last_sold_price': 12000,
                'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
                'scrape_time': datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc)
            }
        ]
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.execute_query.return_value = mock_results
            mock_get_pool.return_value = mock_pool
            
            result = handler(event, context, mock_logger)
            
            # Verify multiple results
            assert result['count'] == 2
            assert len(result['data']) == 2
    
    def test_handler_empty_results(self):
        """Test handler with no matching results."""
        event = {
            'item_id': 9999,
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.execute_query.return_value = []
            mock_get_pool.return_value = mock_pool
            
            result = handler(event, context, mock_logger)
            
            # Verify empty results
            assert result['count'] == 0
            assert result['data'] == []
    
    def test_handler_validation_error_missing_required_field(self):
        """Test handler with missing required field."""
        event = {
            'item_id': 1001,
            # Missing start_date and end_date
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error
        with pytest.raises(Exception):
            handler(event, context, mock_logger)
    
    def test_handler_validation_error_invalid_date_range(self):
        """Test handler with end_date before start_date."""
        event = {
            'item_id': 1001,
            'start_date': '2024-01-31T00:00:00+00:00',
            'end_date': '2024-01-01T00:00:00+00:00',  # Before start_date
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error
        with pytest.raises(Exception):
            handler(event, context, mock_logger)
    
    def test_handler_validation_error_invalid_category(self):
        """Test handler with invalid category."""
        event = {
            'item_id': 1001,
            'category': 'InvalidCategory',
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error
        with pytest.raises(Exception):
            handler(event, context, mock_logger)
    
    def test_handler_validation_error_missing_item_and_name(self):
        """Test handler with neither item_id nor name specified."""
        event = {
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error (requires item_id or name)
        with pytest.raises(Exception):
            handler(event, context, mock_logger)
    
    def test_handler_validation_error_limit_too_high(self):
        """Test handler with limit exceeding maximum."""
        event = {
            'item_id': 1001,
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 2000  # Exceeds max of 1000
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error
        with pytest.raises(Exception):
            handler(event, context, mock_logger)
    
    def test_handler_validation_error_limit_too_low(self):
        """Test handler with limit below minimum."""
        event = {
            'item_id': 1001,
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 0  # Below min of 1
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_logger = MagicMock()
        
        # Should raise validation error
        with pytest.raises(Exception):
            handler(event, context, mock_logger)


class TestLambdaHandler:
    """Test Lambda handler with router integration."""
    
    def test_lambda_handler_success(self):
        """Test successful Lambda handler execution."""
        event = {
            'item_id': 1001,
            'start_date': '2024-01-01T00:00:00+00:00',
            'end_date': '2024-01-31T23:59:59+00:00',
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        mock_results = [
            {
                'item_id': 1001,
                'item_name': 'Black Stone (Weapon)',
                'sid': 0,
                'category': 'Buff',
                'current_stock': 50,
                'total_trades': 1234,
                'last_sold_price': 15000,
                'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
                'scrape_time': datetime(2024, 1, 15, 10, 35, 0, tzinfo=timezone.utc)
            }
        ]
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.execute_query.return_value = mock_results
            mock_get_pool.return_value = mock_pool
            
            result = lambda_handler(event, context)
            
            # Verify response structure from router
            assert result['statusCode'] == 200
            assert 'body' in result
            assert 'headers' in result
            
            # Verify body content
            body = json.loads(result['body'])
            assert 'data' in body
            assert 'count' in body
            assert 'correlation_id' in body
    
    def test_lambda_handler_validation_error(self):
        """Test Lambda handler with validation error."""
        event = {
            'item_id': 1001,
            # Missing required dates
            'limit': 100
        }
        
        context = Mock()
        context.function_name = 'queryData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return 400 error
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
