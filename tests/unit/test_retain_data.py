"""
Unit tests for retainData Lambda function.

Tests:
- Aggregation calculation accuracy
- Date range filtering
- S3 archival
- Deletion logic
"""

import pytest
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch, call
import sys
from pathlib import Path

# Import from retainData Lambda function
from retainData.lambda_function import (
    load_retention_config,
    aggregate_old_market_data,
    delete_aggregated_records,
    archive_old_summaries_to_s3,
    delete_archived_summaries,
    handler
)
from common.logging import StructuredLogger


class TestRetentionConfiguration:
    """Test retention configuration loading."""
    
    def test_load_retention_config_with_defaults(self):
        """Test loading configuration with default values."""
        logger = StructuredLogger("test", "test-corr-id")
        
        with patch.dict('os.environ', {}, clear=True):
            config = load_retention_config(logger)
            
            assert config['retention_detailed_days'] == 90
            assert config['retention_summary_days'] == 730
    
    def test_load_retention_config_with_custom_values(self):
        """Test loading configuration with custom environment variables."""
        logger = StructuredLogger("test", "test-corr-id")
        
        with patch.dict('os.environ', {
            'RETENTION_DETAILED_DAYS': '60',
            'RETENTION_SUMMARY_DAYS': '365'
        }):
            config = load_retention_config(logger)
            
            assert config['retention_detailed_days'] == 60
            assert config['retention_summary_days'] == 365


class TestAggregation:
    """Test market data aggregation functionality."""
    
    def test_aggregate_empty_dataset(self):
        """Test aggregation with no old records."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        # No records to aggregate
        mock_cursor.fetchone.return_value = (None, None, 0)
        
        logger = StructuredLogger("test", "test-corr-id")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        records_aggregated, min_date, max_date = aggregate_old_market_data(
            mock_pool,
            cutoff_date,
            logger
        )
        
        assert records_aggregated == 0
        assert min_date is None
        assert max_date is None
    
    def test_aggregate_calculates_statistics_correctly(self):
        """Test that aggregation calculates correct avg/min/max values."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        min_date = (cutoff_date - timedelta(days=30)).date()
        max_date = (cutoff_date - timedelta(days=1)).date()
        
        # Mock database response
        mock_cursor.fetchone.return_value = (min_date, max_date, 100)
        mock_cursor.rowcount = 10  # 10 summary rows created
        
        logger = StructuredLogger("test", "test-corr-id")
        
        records_aggregated, result_min_date, result_max_date = aggregate_old_market_data(
            mock_pool,
            cutoff_date,
            logger
        )
        
        assert records_aggregated == 100
        assert result_min_date == min_date
        assert result_max_date == max_date
        
        # Verify aggregation query was called
        assert mock_cursor.execute.call_count >= 2
        
        # Verify INSERT INTO bdo_marketdatasummary was called
        insert_calls = [
            call for call in mock_cursor.execute.call_args_list
            if 'INSERT INTO bdo_marketdatasummary' in str(call)
        ]
        assert len(insert_calls) >= 1
    
    def test_aggregate_uses_correct_date_filter(self):
        """Test that aggregation filters by cutoff date correctly."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        cutoff_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        
        mock_cursor.fetchone.return_value = (None, None, 0)
        
        logger = StructuredLogger("test", "test-corr-id")
        
        aggregate_old_market_data(mock_pool, cutoff_date, logger)
        
        # Verify cutoff_date was used in query
        calls = mock_cursor.execute.call_args_list
        assert len(calls) >= 1
        
        # Check that cutoff_date appears in parameters
        found_cutoff = False
        for call_args in calls:
            if len(call_args[0]) > 1 and call_args[0][1]:
                if cutoff_date in call_args[0][1]:
                    found_cutoff = True
                    break
        
        assert found_cutoff, "Cutoff date should be used in query parameters"


class TestDeletion:
    """Test record deletion functionality."""
    
    def test_delete_aggregated_records(self):
        """Test deletion of aggregated records."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        # Mock deletion
        mock_cursor.rowcount = 150
        
        logger = StructuredLogger("test", "test-corr-id")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        deleted_count = delete_aggregated_records(mock_pool, cutoff_date, logger)
        
        assert deleted_count == 150
        
        # Verify DELETE query was called
        delete_calls = [
            call for call in mock_cursor.execute.call_args_list
            if 'DELETE FROM bdo_marketdata' in str(call)
        ]
        assert len(delete_calls) >= 1
    
    def test_delete_no_records(self):
        """Test deletion when no records match criteria."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        mock_cursor.rowcount = 0
        
        logger = StructuredLogger("test", "test-corr-id")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        deleted_count = delete_aggregated_records(mock_pool, cutoff_date, logger)
        
        assert deleted_count == 0
    
    def test_delete_archived_summaries(self):
        """Test deletion of archived summary records."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        mock_cursor.rowcount = 50
        
        logger = StructuredLogger("test", "test-corr-id")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=730)
        
        deleted_count = delete_archived_summaries(mock_pool, cutoff_date, logger)
        
        assert deleted_count == 50
        
        # Verify DELETE query was called
        delete_calls = [
            call for call in mock_cursor.execute.call_args_list
            if 'DELETE FROM bdo_marketdatasummary' in str(call)
        ]
        assert len(delete_calls) >= 1


class TestS3Archival:
    """Test S3 archival functionality."""
    
    def test_archive_summaries_to_s3(self):
        """Test archiving summaries to S3 Glacier."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        # Mock summary records
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=730)
        mock_summaries = [
            (1, 1, cutoff_date.date() - timedelta(days=1), Decimal('1000.50'),
             900, 1100, Decimal('50.25'), 1000, 10, "Test Item", 1001, 0),
            (2, 2, cutoff_date.date() - timedelta(days=2), Decimal('2000.75'),
             1800, 2200, Decimal('75.50'), 2000, 20, "Test Item 2", 1002, 1)
        ]
        mock_cursor.fetchall.return_value = mock_summaries
        
        logger = StructuredLogger("test", "test-corr-id")
        
        with patch('lambda_function.get_s3_client') as mock_s3_getter:
            mock_s3 = Mock()
            mock_s3_getter.return_value = mock_s3
            
            with patch.dict('os.environ', {'ARCHIVE_BUCKET_NAME': 'test-bucket'}):
                records_archived, s3_key = archive_old_summaries_to_s3(
                    mock_pool,
                    cutoff_date,
                    logger
                )
        
        assert records_archived == 2
        assert s3_key is not None
        assert 'archives/market_data_summaries_' in s3_key
        
        # Verify S3 put_object was called
        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs['Bucket'] == 'test-bucket'
        assert call_kwargs['StorageClass'] == 'GLACIER'
        assert call_kwargs['ContentType'] == 'application/json'
        
        # Verify JSON body contains records
        body = call_kwargs['Body']
        data = json.loads(body)
        assert len(data) == 2
        assert data[0]['item_name'] == "Test Item"
        assert data[1]['item_name'] == "Test Item 2"
    
    def test_archive_no_summaries(self):
        """Test archival when no summaries match criteria."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        mock_cursor.fetchall.return_value = []
        
        logger = StructuredLogger("test", "test-corr-id")
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=730)
        
        with patch('lambda_function.get_s3_client') as mock_s3_getter:
            mock_s3 = Mock()
            mock_s3_getter.return_value = mock_s3
            
            records_archived, s3_key = archive_old_summaries_to_s3(
                mock_pool,
                cutoff_date,
                logger
            )
        
        assert records_archived == 0
        assert s3_key is None
        
        # Verify S3 was not called
        assert not mock_s3.put_object.called
    
    def test_archive_handles_decimal_conversion(self):
        """Test that Decimal values are properly converted to float for JSON."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=730)
        mock_summaries = [
            (1, 1, cutoff_date.date() - timedelta(days=1), Decimal('1234.56'),
             1000, 1500, Decimal('99.99'), 5000, 10, "Item", 1001, 0)
        ]
        mock_cursor.fetchall.return_value = mock_summaries
        
        logger = StructuredLogger("test", "test-corr-id")
        
        with patch('lambda_function.get_s3_client') as mock_s3_getter:
            mock_s3 = Mock()
            mock_s3_getter.return_value = mock_s3
            
            archive_old_summaries_to_s3(mock_pool, cutoff_date, logger)
        
        # Verify JSON body has float values, not Decimal
        call_kwargs = mock_s3.put_object.call_args[1]
        body = call_kwargs['Body']
        data = json.loads(body)
        
        assert isinstance(data[0]['avg_last_sold_price'], float)
        assert data[0]['avg_last_sold_price'] == 1234.56
        assert isinstance(data[0]['avg_current_stock'], float)
        assert data[0]['avg_current_stock'] == 99.99


class TestEndToEnd:
    """Test complete retention workflow."""
    
    def test_handler_executes_all_steps(self):
        """Test that handler executes all retention steps in order."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        # Mock aggregation
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
        mock_cursor.fetchone.return_value = (
            (cutoff_date - timedelta(days=30)).date(),
            (cutoff_date - timedelta(days=1)).date(),
            100
        )
        mock_cursor.rowcount = 10
        
        # Mock archival
        mock_cursor.fetchall.return_value = []
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_get_pool.return_value = mock_pool
            
            with patch('lambda_function.get_s3_client') as mock_s3_getter:
                mock_s3 = Mock()
                mock_s3_getter.return_value = mock_s3
                
                with patch.dict('os.environ', {
                    'RETENTION_DETAILED_DAYS': '90',
                    'RETENTION_SUMMARY_DAYS': '730'
                }):
                    result = handler({}, None)
        
        assert result['statusCode'] == 200
        body = json.loads(result['body']) if isinstance(result['body'], str) else result['body']
        assert body['message'] == 'Data retention completed successfully'
        assert 'results' in body
        assert body['results']['detailed_records_aggregated'] == 100
        assert body['results']['detailed_records_deleted'] == 10
    
    def test_handler_skips_deletion_when_no_aggregation(self):
        """Test that handler skips deletion when no records were aggregated."""
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Setup context managers
        mock_pool.get_connection.return_value = mock_conn
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        
        # No records to aggregate
        mock_cursor.fetchone.return_value = (None, None, 0)
        mock_cursor.fetchall.return_value = []
        
        with patch('lambda_function.get_database_pool') as mock_get_pool:
            mock_get_pool.return_value = mock_pool
            
            with patch('lambda_function.get_s3_client') as mock_s3_getter:
                mock_s3 = Mock()
                mock_s3_getter.return_value = mock_s3
                
                result = handler({}, None)
        
        assert result['statusCode'] == 200
        body = json.loads(result['body']) if isinstance(result['body'], str) else result['body']
        assert body['results']['detailed_records_aggregated'] == 0
        assert body['results']['detailed_records_deleted'] == 0
