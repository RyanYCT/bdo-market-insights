"""
Unit tests for cleanData Lambda function.

Tests data transformation logic, validation error handling,
and filtering of invalid records.
"""

import pytest
import sys
import json
from unittest.mock import Mock
from datetime import datetime, timezone

# Mock psycopg2 before importing common modules
from unittest.mock import MagicMock
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

# Add lambda_layer to path
sys.path.insert(0, 'lambda_layer/python')

# Add cleanData to path
from pathlib import Path
clean_data_path = Path(__file__).parent.parent.parent / "cleanData"
sys.path.insert(0, str(clean_data_path))

# Import from cleanData
from lambda_function import transform_raw_record, handler, lambda_handler

# Import from common
from common.logging import StructuredLogger


class TestTransformRawRecord:
    """Test raw record transformation logic."""
    
    def test_transform_basic_record(self):
        """Test transformation of a valid raw record."""
        raw_record = {
            'itemId': 1001,
            'sid': 0,
            'currentStock': 50,
            'totalTrades': 1234,
            'lastSoldPrice': 15000,
            'lastSoldTime': '2024-01-15T10:30:00Z'
        }
        
        result = transform_raw_record(raw_record)
        
        assert result['item_id'] == 1001
        assert result['sid'] == 0
        assert result['current_stock'] == 50
        assert result['total_trades'] == 1234
        assert result['last_sold_price'] == 15000
        assert isinstance(result['last_sold_time'], datetime)
    
    def test_transform_with_default_sid(self):
        """Test transformation when sid is missing (defaults to 0)."""
        raw_record = {
            'itemId': 1001,
            'currentStock': 50,
            'totalTrades': 1234,
            'lastSoldPrice': 15000,
            'lastSoldTime': '2024-01-15T10:30:00Z'
        }
        
        result = transform_raw_record(raw_record)
        
        assert result['sid'] == 0
    
    def test_transform_datetime_iso_format(self):
        """Test datetime parsing from ISO format."""
        raw_record = {
            'itemId': 1001,
            'sid': 0,
            'currentStock': 50,
            'totalTrades': 1234,
            'lastSoldPrice': 15000,
            'lastSoldTime': '2024-01-15T10:30:00+00:00'
        }
        
        result = transform_raw_record(raw_record)
        
        assert isinstance(result['last_sold_time'], datetime)
        assert result['last_sold_time'].year == 2024
        assert result['last_sold_time'].month == 1
        assert result['last_sold_time'].day == 15
    
    def test_transform_datetime_already_datetime(self):
        """Test when last_sold_time is already a datetime object."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        raw_record = {
            'itemId': 1001,
            'sid': 0,
            'currentStock': 50,
            'totalTrades': 1234,
            'lastSoldPrice': 15000,
            'lastSoldTime': dt
        }
        
        result = transform_raw_record(raw_record)
        
        assert result['last_sold_time'] == dt
    
    def test_transform_invalid_datetime(self):
        """Test handling of invalid datetime string."""
        raw_record = {
            'itemId': 1001,
            'sid': 0,
            'currentStock': 50,
            'totalTrades': 1234,
            'lastSoldPrice': 15000,
            'lastSoldTime': 'invalid-date'
        }
        
        result = transform_raw_record(raw_record)
        
        # Should set to None when parsing fails
        assert result['last_sold_time'] is None


class TestCleanDataHandler:
    """Test cleanData Lambda handler."""
    
    def test_handler_success_all_valid(self):
        """Test handler with all valid records."""
        event = {
            'raw_data': [
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
                    'sid': 1,
                    'currentStock': 30,
                    'totalTrades': 567,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-15T11:00:00Z'
                }
            ],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure
        body = json.loads(result['body'])
        assert 'cleaned_data' in body
        assert 'correlation_id' in body
        assert 'metadata' in body
        assert body['correlation_id'] == 'test-corr-id'
        assert len(body['cleaned_data']) == 2
        assert body['metadata']['valid_records'] == 2
        assert body['metadata']['invalid_records'] == 0
    
    def test_handler_filters_invalid_records(self):
        """Test that invalid records are filtered out."""
        event = {
            'raw_data': [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1234,
                    'lastSoldPrice': 15000,
                    'lastSoldTime': '2024-01-15T10:30:00Z'
                },
                {
                    # Invalid: negative current_stock
                    'itemId': 1002,
                    'sid': 0,
                    'currentStock': -10,
                    'totalTrades': 567,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-15T11:00:00Z'
                },
                {
                    # Invalid: missing required field
                    'itemId': 1003,
                    'sid': 0,
                    'totalTrades': 890,
                    'lastSoldPrice': 25000,
                    'lastSoldTime': '2024-01-15T12:00:00Z'
                }
            ],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify only valid record is included
        body = json.loads(result['body'])
        assert len(body['cleaned_data']) == 1
        assert body['cleaned_data'][0]['item_id'] == 1001
        assert body['metadata']['valid_records'] == 1
        assert body['metadata']['invalid_records'] == 2
        assert 1002 in body['metadata']['filtered_items']
        assert 1003 in body['metadata']['filtered_items']
    
    def test_handler_empty_raw_data(self):
        """Test handler with empty raw data list."""
        event = {
            'raw_data': [],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should succeed with empty cleaned_data
        body = json.loads(result['body'])
        assert body['cleaned_data'] == []
        assert body['metadata']['valid_records'] == 0
        assert body['metadata']['invalid_records'] == 0
    
    def test_handler_validation_error_missing_correlation_id(self):
        """Test handler with missing correlation_id."""
        event = {
            'raw_data': [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1234,
                    'lastSoldPrice': 15000,
                    'lastSoldTime': '2024-01-15T10:30:00Z'
                }
            ]
            # Missing correlation_id
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return validation error
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_validation_error_missing_raw_data(self):
        """Test handler with missing raw_data field."""
        event = {
            'correlation_id': 'test-corr-id'
            # Missing raw_data
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return validation error
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_filters_records_with_invalid_datetime(self):
        """Test that records with invalid datetime are filtered."""
        event = {
            'raw_data': [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': 1234,
                    'lastSoldPrice': 15000,
                    'lastSoldTime': 'not-a-valid-date'
                }
            ],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Record should be filtered due to invalid datetime
        body = json.loads(result['body'])
        assert len(body['cleaned_data']) == 0
        assert body['metadata']['invalid_records'] == 1
    
    def test_handler_filters_records_with_negative_values(self):
        """Test that records with negative values are filtered."""
        event = {
            'raw_data': [
                {
                    'itemId': 1001,
                    'sid': 0,
                    'currentStock': 50,
                    'totalTrades': -100,  # Invalid: negative
                    'lastSoldPrice': 15000,
                    'lastSoldTime': '2024-01-15T10:30:00Z'
                },
                {
                    'itemId': 1002,
                    'sid': 0,
                    'currentStock': 30,
                    'totalTrades': 567,
                    'lastSoldPrice': -5000,  # Invalid: negative
                    'lastSoldTime': '2024-01-15T11:00:00Z'
                }
            ],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Both records should be filtered
        body = json.loads(result['body'])
        assert len(body['cleaned_data']) == 0
        assert body['metadata']['invalid_records'] == 2
    
    def test_handler_mixed_valid_invalid_records(self):
        """Test handler with mix of valid and invalid records."""
        event = {
            'raw_data': [
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
                    'currentStock': -10,  # Invalid
                    'totalTrades': 567,
                    'lastSoldPrice': 20000,
                    'lastSoldTime': '2024-01-15T11:00:00Z'
                },
                {
                    'itemId': 1003,
                    'sid': 1,
                    'currentStock': 75,
                    'totalTrades': 890,
                    'lastSoldPrice': 25000,
                    'lastSoldTime': '2024-01-15T12:00:00Z'
                },
                {
                    'itemId': 1004,
                    'sid': 0,
                    'currentStock': 100,
                    'totalTrades': 456,
                    'lastSoldPrice': 30000,
                    'lastSoldTime': 'bad-date'  # Invalid
                }
            ],
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'cleanData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should have 2 valid records
        body = json.loads(result['body'])
        assert len(body['cleaned_data']) == 2
        assert body['metadata']['valid_records'] == 2
        assert body['metadata']['invalid_records'] == 2
        
        # Verify valid records are correct ones
        item_ids = [r['item_id'] for r in body['cleaned_data']]
        assert 1001 in item_ids
        assert 1003 in item_ids
        assert 1002 not in item_ids
        assert 1004 not in item_ids
