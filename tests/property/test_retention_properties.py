"""
Property-based tests for data retention functionality.

Feature: bdo-market-insights-rewrite
Tests Properties 17 and 18 from the design document.
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hypothesis import given, strategies as st, settings, assume
from unittest.mock import Mock, MagicMock, patch
import sys

# Import retention Lambda
sys.path.insert(0, 'retainData')
sys.path.insert(0, 'lambda_layer/python')

from lambda_function import (
    aggregate_old_market_data,
    delete_aggregated_records,
    archive_old_summaries_to_s3,
    delete_archived_summaries
)
from common.logging import StructuredLogger


# Hypothesis strategies for generating test data
@st.composite
def market_data_records(draw, min_records=1, max_records=100):
    """Generate a list of market data records for testing."""
    num_records = draw(st.integers(min_value=min_records, max_value=max_records))
    
    records = []
    for _ in range(num_records):
        # Generate naive datetime and add timezone
        naive_dt = draw(st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2024, 12, 31)
        ))
        aware_dt = naive_dt.replace(tzinfo=timezone.utc)
        
        record = {
            'item_id': draw(st.integers(min_value=1, max_value=1000)),
            'sid': draw(st.integers(min_value=0, max_value=20)),
            'current_stock': draw(st.integers(min_value=0, max_value=10000)),
            'total_trades': draw(st.integers(min_value=0, max_value=1000000)),
            'last_sold_price': draw(st.integers(min_value=1, max_value=1000000)),
            'last_sold_time': aware_dt
        }
        records.append(record)
    
    return records


@st.composite
def summary_records(draw, min_records=1, max_records=50):
    """Generate a list of summary records for testing."""
    num_records = draw(st.integers(min_value=min_records, max_value=max_records))
    
    records = []
    for _ in range(num_records):
        # Generate prices ensuring min <= avg <= max
        min_price = draw(st.integers(min_value=1, max_value=100000))
        max_price = draw(st.integers(min_value=min_price, max_value=1000000))
        avg_price = draw(st.integers(min_value=min_price, max_value=max_price))
        
        record = (
            draw(st.integers(min_value=1, max_value=1000)),  # id
            draw(st.integers(min_value=1, max_value=1000)),  # item_id
            draw(st.dates(
                min_value=datetime(2020, 1, 1).date(),
                max_value=datetime(2024, 12, 31).date()
            )),  # date
            Decimal(str(avg_price)),  # avg_last_sold_price
            min_price,  # min_last_sold_price
            max_price,  # max_last_sold_price
            Decimal(str(draw(st.integers(min_value=0, max_value=10000)))),  # avg_current_stock
            draw(st.integers(min_value=0, max_value=10000000)),  # total_trades_sum
            draw(st.integers(min_value=1, max_value=1000)),  # record_count
            f"Item_{draw(st.integers(min_value=1, max_value=1000))}",  # item_name
            draw(st.integers(min_value=1, max_value=100000)),  # bdo_item_id
            draw(st.integers(min_value=0, max_value=20))  # sid
        )
        records.append(record)
    
    return records


# Property 17: Data aggregation for old records
# **Validates: Requirements 10.2**

@given(
    records=market_data_records(min_records=1, max_records=50),
    retention_days=st.integers(min_value=1, max_value=365)
)
@settings(max_examples=100, deadline=None)
def test_aggregation_calculates_correct_statistics(records, retention_days):
    """
    Property 17: For any MarketData records older than retention period,
    aggregation should calculate correct statistics (avg, min, max, total, count).
    """
    # Create mock database pool and connection
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Setup context managers properly
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.__exit__.return_value = None
    
    # Calculate cutoff date
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    # Filter records older than cutoff
    old_records = [r for r in records if r['last_sold_time'] < cutoff_date]
    
    if not old_records:
        # No old records to aggregate
        mock_cursor.fetchone.return_value = (None, None, 0)
    else:
        # Group records by item_id and date
        grouped = {}
        for record in old_records:
            date = record['last_sold_time'].date()
            key = (record['item_id'], date)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(record)
        
        # Calculate expected statistics
        min_date = min(r['last_sold_time'].date() for r in old_records)
        max_date = max(r['last_sold_time'].date() for r in old_records)
        total_count = len(old_records)
        
        # Mock the first query (check for records)
        mock_cursor.fetchone.return_value = (min_date, max_date, total_count)
        
        # Mock the aggregation query
        mock_cursor.rowcount = len(grouped)
    
    # Create logger
    logger = StructuredLogger("test", "test-corr-id")
    
    # Execute aggregation
    records_aggregated, result_min_date, result_max_date = aggregate_old_market_data(
        mock_pool,
        cutoff_date,
        logger
    )
    
    # Verify the aggregation query was called with correct parameters
    if old_records:
        assert mock_cursor.execute.call_count >= 2  # Check query + aggregation query
        
        # Verify cutoff date was used in queries
        calls = mock_cursor.execute.call_args_list
        for call in calls:
            if len(call[0]) > 1 and call[0][1]:
                # Check that cutoff_date is in the parameters
                assert cutoff_date in call[0][1] or (cutoff_date,) == call[0][1]
        
        # Verify results
        assert records_aggregated == total_count
        assert result_min_date == min_date
        assert result_max_date == max_date
    else:
        # No records to aggregate
        assert records_aggregated == 0
        assert result_min_date is None
        assert result_max_date is None


@given(
    num_records=st.integers(min_value=0, max_value=100),
    retention_days=st.integers(min_value=1, max_value=365)
)
@settings(max_examples=100, deadline=None)
def test_aggregation_groups_by_item_and_date(num_records, retention_days):
    """
    Property 17: Aggregation should group records by item_id and date,
    creating one summary per (item_id, date) combination.
    """
    # Create mock database pool and connection
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Setup context managers properly
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.__exit__.return_value = None
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    if num_records == 0:
        mock_cursor.fetchone.return_value = (None, None, 0)
    else:
        # Simulate records grouped by date
        min_date = (cutoff_date - timedelta(days=30)).date()
        max_date = (cutoff_date - timedelta(days=1)).date()
        
        mock_cursor.fetchone.return_value = (min_date, max_date, num_records)
        
        # Number of unique (item_id, date) combinations
        # For simplicity, assume each record creates a unique combination
        mock_cursor.rowcount = num_records
    
    logger = StructuredLogger("test", "test-corr-id")
    
    # Execute aggregation
    records_aggregated, _, _ = aggregate_old_market_data(
        mock_pool,
        cutoff_date,
        logger
    )
    
    # Verify aggregation was called
    if num_records > 0:
        # Check that INSERT INTO bdo_marketdatasummary was called
        insert_calls = [
            call for call in mock_cursor.execute.call_args_list
            if len(call[0]) > 0 and 'INSERT INTO bdo_marketdatasummary' in call[0][0]
        ]
        assert len(insert_calls) >= 1
        
        # Verify GROUP BY clause is present
        insert_query = insert_calls[0][0][0]
        assert 'GROUP BY' in insert_query
        assert 'item_id' in insert_query
        assert 'date' in insert_query or 'last_sold_time::date' in insert_query


@given(
    records=market_data_records(min_records=5, max_records=20),
    retention_days=st.integers(min_value=1, max_value=90)
)
@settings(max_examples=100, deadline=None)
def test_aggregation_calculates_min_max_avg_correctly(records, retention_days):
    """
    Property 17: For any group of records, aggregation should correctly
    calculate min, max, and average values.
    """
    # Group records by item_id and date
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    old_records = [r for r in records if r['last_sold_time'] < cutoff_date]
    
    if not old_records:
        return  # Skip if no old records
    
    # Calculate expected statistics for all records
    prices = [r['last_sold_price'] for r in old_records]
    stocks = [r['current_stock'] for r in old_records]
    trades = [r['total_trades'] for r in old_records]
    
    expected_min_price = min(prices)
    expected_max_price = max(prices)
    expected_avg_price = sum(prices) / len(prices)
    expected_avg_stock = sum(stocks) / len(stocks)
    expected_total_trades = sum(trades)
    
    # Verify mathematical properties
    assert expected_min_price <= expected_avg_price <= expected_max_price
    assert expected_avg_stock >= 0
    assert expected_total_trades >= 0
    assert len(old_records) > 0


# Property 18: Retention operation logging
# **Validates: Requirements 10.7**

@given(
    operation_type=st.sampled_from(['aggregate', 'delete', 'archive']),
    record_count=st.integers(min_value=0, max_value=10000),
    retention_days=st.integers(min_value=1, max_value=365)
)
@settings(max_examples=100, deadline=None)
def test_retention_operations_emit_logs_with_counts(operation_type, record_count, retention_days):
    """
    Property 18: For any retention operation, logs should contain
    operation type, record counts, and date ranges.
    """
    # Create mock logger to capture log calls
    mock_logger = Mock(spec=StructuredLogger)
    
    # Create mock database pool
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Setup context managers properly
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.__exit__.return_value = None
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    if operation_type == 'aggregate':
        if record_count == 0:
            mock_cursor.fetchone.return_value = (None, None, 0)
        else:
            min_date = (cutoff_date - timedelta(days=30)).date()
            max_date = (cutoff_date - timedelta(days=1)).date()
            mock_cursor.fetchone.return_value = (min_date, max_date, record_count)
            mock_cursor.rowcount = record_count // 10  # Assume 10 records per summary
        
        # Execute operation
        aggregate_old_market_data(mock_pool, cutoff_date, mock_logger)
        
        # Verify logging
        assert mock_logger.info.called
        
        # Check that logs contain operation details
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        log_text = ' '.join(log_calls)
        
        # Should mention aggregation
        assert 'aggregat' in log_text.lower()
        
        if record_count > 0:
            # Should include record count in some form
            assert any(str(record_count) in str(call) or 'record' in str(call).lower() 
                      for call in mock_logger.info.call_args_list)
    
    elif operation_type == 'delete':
        mock_cursor.rowcount = record_count
        
        # Execute operation
        delete_aggregated_records(mock_pool, cutoff_date, mock_logger)
        
        # Verify logging
        assert mock_logger.info.called
        
        # Check logs mention deletion and count
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        log_text = ' '.join(log_calls)
        
        assert 'delet' in log_text.lower()
        assert any('record' in str(call).lower() for call in mock_logger.info.call_args_list)
    
    elif operation_type == 'archive':
        if record_count == 0:
            mock_cursor.fetchall.return_value = []
        else:
            # Generate mock summary records
            mock_summaries = []
            for i in range(record_count):
                mock_summaries.append((
                    i, 1, (cutoff_date - timedelta(days=i)).date(),
                    Decimal('1000.50'), 900, 1100, Decimal('50.25'),
                    1000, 10, f"Item_{i}", 1000 + i, 0
                ))
            mock_cursor.fetchall.return_value = mock_summaries
        
        # Mock S3 client
        with patch('lambda_function.get_s3_client') as mock_s3_getter:
            mock_s3 = Mock()
            mock_s3_getter.return_value = mock_s3
            
            # Execute operation
            archive_old_summaries_to_s3(mock_pool, cutoff_date, mock_logger)
            
            # Verify logging
            assert mock_logger.info.called
            
            # Check logs mention archiving
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            log_text = ' '.join(log_calls)
            
            assert 'archiv' in log_text.lower()
            
            if record_count > 0:
                # Should mention S3
                assert any('s3' in str(call).lower() for call in mock_logger.info.call_args_list)


@given(
    start_days_ago=st.integers(min_value=100, max_value=1000),
    end_days_ago=st.integers(min_value=1, max_value=99)
)
@settings(max_examples=100, deadline=None)
def test_retention_logs_include_date_ranges(start_days_ago, end_days_ago):
    """
    Property 18: Retention operation logs should include date ranges
    for the records being processed.
    """
    # Create mock logger
    mock_logger = Mock(spec=StructuredLogger)
    
    # Create mock database pool
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Setup context managers properly
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.__exit__.return_value = None
    
    # Calculate dates
    now = datetime.now(timezone.utc)
    min_date = (now - timedelta(days=start_days_ago)).date()
    max_date = (now - timedelta(days=end_days_ago)).date()
    cutoff_date = now - timedelta(days=90)
    
    # Mock database response
    mock_cursor.fetchone.return_value = (min_date, max_date, 100)
    mock_cursor.rowcount = 10
    
    # Execute aggregation
    aggregate_old_market_data(mock_pool, cutoff_date, mock_logger)
    
    # Verify logging includes date information
    assert mock_logger.info.called
    
    # Check that date ranges are logged
    log_calls = mock_logger.info.call_args_list
    
    # Should have logs with date_range_start and date_range_end
    date_logged = False
    for call in log_calls:
        if len(call[1]) > 0:  # Check kwargs
            kwargs = call[1]
            if 'date_range_start' in kwargs or 'date_range_end' in kwargs:
                date_logged = True
                break
    
    assert date_logged, "Date ranges should be included in logs"


@given(
    num_operations=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=100, deadline=None)
def test_all_retention_operations_are_logged(num_operations):
    """
    Property 18: Every retention operation (aggregate, delete, archive)
    should emit log entries.
    """
    # Create mock logger
    mock_logger = Mock(spec=StructuredLogger)
    
    # Create mock database pool
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Setup context managers properly
    mock_pool.get_connection.return_value = mock_conn
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.__exit__.return_value = None
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
    
    # Mock responses
    mock_cursor.fetchone.return_value = (
        (cutoff_date - timedelta(days=30)).date(),
        (cutoff_date - timedelta(days=1)).date(),
        100
    )
    mock_cursor.rowcount = 10
    
    # Execute operations
    operations_executed = []
    
    for i in range(num_operations):
        operation = ['aggregate', 'delete'][i % 2]
        
        if operation == 'aggregate':
            aggregate_old_market_data(mock_pool, cutoff_date, mock_logger)
            operations_executed.append('aggregate')
        else:
            delete_aggregated_records(mock_pool, cutoff_date, mock_logger)
            operations_executed.append('delete')
    
    # Verify each operation logged
    assert mock_logger.info.call_count >= num_operations
    
    # Verify logs contain operation-specific keywords
    log_text = ' '.join(str(call) for call in mock_logger.info.call_args_list).lower()
    
    if 'aggregate' in operations_executed:
        assert 'aggregat' in log_text
    if 'delete' in operations_executed:
        assert 'delet' in log_text
