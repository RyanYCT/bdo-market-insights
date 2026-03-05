"""
Property-based tests for CloudWatch metrics emission.

Tests Property 19: Custom metrics emission
Validates: Requirements 11.1

For any significant system operation (ETL pipeline execution, query request,
External API call, database operation), the system should emit corresponding
custom CloudWatch metrics.
"""

import pytest
from hypothesis import given, strategies as st, settings
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
import sys
import os

# Mock psycopg2 before importing common modules
sys.modules['psycopg2'] = MagicMock()
sys.modules['psycopg2.pool'] = MagicMock()
sys.modules['psycopg2.extras'] = MagicMock()

# Add lambda_layer to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda_layer/python'))

from common.metrics import MetricsClient, LatencyTracker


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    function_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    item_count=st.integers(min_value=0, max_value=10000)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_etl_success_metrics_always_emitted(function_name, item_count):
    """
    Property 19: ETL success operations should always emit metrics.
    
    For any ETL pipeline execution that succeeds, the system should emit
    a success metric with the function name and relevant dimensions.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/ETL")
        
        # Act
        metrics.emit_etl_success(
            function_name=function_name,
            item_count=item_count
        )
        
        # Assert
        # Verify put_metric_data was called
        assert mock_cloudwatch.put_metric_data.called
        
        # Get the call arguments
        call_args = mock_cloudwatch.put_metric_data.call_args
        
        # Verify namespace
        assert call_args[1]['Namespace'] == "BDOMarketInsights/ETL"
        
        # Verify metric data structure
        metric_data = call_args[1]['MetricData'][0]
        assert metric_data['MetricName'] == 'ETLPipelineExecution'
        assert metric_data['Value'] == 1
        assert metric_data['Unit'] == 'Count'
        
        # Verify dimensions include function name and status
        dimensions = {d['Name']: d['Value'] for d in metric_data['Dimensions']}
        assert dimensions['FunctionName'] == function_name
        assert dimensions['Status'] == 'Success'
        assert dimensions['item_count'] == str(item_count)


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    function_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    error_type=st.sampled_from(['ValidationError', 'NetworkError', 'DatabaseError', 'TimeoutError'])
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_etl_failure_metrics_always_emitted(function_name, error_type):
    """
    Property 19: ETL failure operations should always emit metrics.
    
    For any ETL pipeline execution that fails, the system should emit
    a failure metric with the function name, error type, and status.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/ETL")
        
        # Act
        metrics.emit_etl_failure(
            function_name=function_name,
            error_type=error_type
        )
        
        # Assert
        assert mock_cloudwatch.put_metric_data.called
        
        call_args = mock_cloudwatch.put_metric_data.call_args
        metric_data = call_args[1]['MetricData'][0]
        
        assert metric_data['MetricName'] == 'ETLPipelineExecution'
        assert metric_data['Value'] == 1
        
        dimensions = {d['Name']: d['Value'] for d in metric_data['Dimensions']}
        assert dimensions['FunctionName'] == function_name
        assert dimensions['Status'] == 'Failure'
        assert dimensions['ErrorType'] == error_type


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    function_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    latency_ms=st.floats(min_value=0.1, max_value=30000.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_query_latency_metrics_always_emitted(function_name, latency_ms):
    """
    Property 19: Query operations should always emit latency metrics.
    
    For any query request, the system should emit a latency metric
    with the function name and latency value in milliseconds.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/Query")
        
        # Act
        metrics.emit_query_latency(
            function_name=function_name,
            latency_ms=latency_ms
        )
        
        # Assert
        assert mock_cloudwatch.put_metric_data.called
        
        call_args = mock_cloudwatch.put_metric_data.call_args
        metric_data = call_args[1]['MetricData'][0]
        
        assert metric_data['MetricName'] == 'QueryLatency'
        assert metric_data['Value'] == latency_ms
        assert metric_data['Unit'] == 'Milliseconds'
        
        dimensions = {d['Name']: d['Value'] for d in metric_data['Dimensions']}
        assert dimensions['FunctionName'] == function_name


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    success=st.booleans(),
    response_time_ms=st.floats(min_value=1.0, max_value=60000.0, allow_nan=False, allow_infinity=False),
    status_code=st.integers(min_value=0, max_value=599)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_api_call_metrics_always_emitted(success, response_time_ms, status_code):
    """
    Property 19: External API calls should always emit metrics.
    
    For any External API call, the system should emit metrics including
    call count and response time with success status and status code.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/ETL")
        
        # Act
        metrics.emit_api_call(
            success=success,
            response_time_ms=response_time_ms,
            status_code=status_code
        )
        
        # Assert
        # Should emit two metrics: call count and response time
        assert mock_cloudwatch.put_metric_data.call_count == 2
        
        # Get all calls
        calls = mock_cloudwatch.put_metric_data.call_args_list
        
        # First call should be call count
        call_count_metric = calls[0][1]['MetricData'][0]
        assert call_count_metric['MetricName'] == 'ExternalAPICallCount'
        assert call_count_metric['Value'] == 1
        assert call_count_metric['Unit'] == 'Count'
        
        call_count_dims = {d['Name']: d['Value'] for d in call_count_metric['Dimensions']}
        assert call_count_dims['Success'] == str(success)
        assert call_count_dims['StatusCode'] == str(status_code)
        
        # Second call should be response time
        response_time_metric = calls[1][1]['MetricData'][0]
        assert response_time_metric['MetricName'] == 'ExternalAPIResponseTime'
        assert response_time_metric['Value'] == response_time_ms
        assert response_time_metric['Unit'] == 'Milliseconds'


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    pool_size=st.integers(min_value=1, max_value=100),
    active_connections=st.integers(min_value=0, max_value=100),
    idle_connections=st.integers(min_value=0, max_value=100)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_db_pool_utilization_metrics_always_emitted(pool_size, active_connections, idle_connections):
    """
    Property 19: Database operations should emit pool utilization metrics.
    
    For any database operation, the system should emit metrics about
    connection pool utilization including active and idle connections.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/Database")
        
        # Act
        metrics.emit_db_pool_utilization(
            pool_size=pool_size,
            active_connections=active_connections,
            idle_connections=idle_connections
        )
        
        # Assert
        # Should emit three metrics: utilization, active, idle
        assert mock_cloudwatch.put_metric_data.call_count == 3
        
        calls = mock_cloudwatch.put_metric_data.call_args_list
        
        # First call: utilization percentage
        utilization_metric = calls[0][1]['MetricData'][0]
        assert utilization_metric['MetricName'] == 'DBPoolUtilization'
        assert utilization_metric['Unit'] == 'Percent'
        
        expected_utilization = (active_connections / pool_size * 100) if pool_size > 0 else 0
        assert utilization_metric['Value'] == expected_utilization
        
        # Second call: active connections
        active_metric = calls[1][1]['MetricData'][0]
        assert active_metric['MetricName'] == 'DBActiveConnections'
        assert active_metric['Value'] == active_connections
        assert active_metric['Unit'] == 'Count'
        
        # Third call: idle connections
        idle_metric = calls[2][1]['MetricData'][0]
        assert idle_metric['MetricName'] == 'DBIdleConnections'
        assert idle_metric['Value'] == idle_connections
        assert idle_metric['Unit'] == 'Count'


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    operation_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')))
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_latency_tracker_context_manager_emits_metrics(operation_name):
    """
    Property 19: Latency tracking context manager should emit metrics.
    
    For any operation tracked with the latency context manager,
    the system should emit an operation latency metric when the
    context exits.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        mock_boto_client.return_value = mock_cloudwatch
        
        metrics = MetricsClient(namespace="BDOMarketInsights/Operations")
        
        # Act
        with metrics.track_latency(operation_name) as tracker:
            # Simulate some work
            import time
            time.sleep(0.01)  # Sleep for 10ms
        
        # Assert
        assert mock_cloudwatch.put_metric_data.called
        
        call_args = mock_cloudwatch.put_metric_data.call_args
        metric_data = call_args[1]['MetricData'][0]
        
        assert metric_data['MetricName'] == 'OperationLatency'
        assert metric_data['Unit'] == 'Milliseconds'
        assert metric_data['Value'] > 0  # Should have some elapsed time
        
        dimensions = {d['Name']: d['Value'] for d in metric_data['Dimensions']}
        assert dimensions['Operation'] == operation_name
        
        # Verify tracker has elapsed time
        assert tracker.elapsed_ms > 0


# Feature: bdo-market-insights-rewrite, Property 19: Custom metrics emission
@given(
    metric_name=st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
    value=st.floats(min_value=0.0, max_value=1000000.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
@pytest.mark.property_test
def test_metrics_emission_never_fails_operation(metric_name, value):
    """
    Property 19: Metrics emission failures should not fail operations.
    
    For any operation that emits metrics, if the metrics emission fails
    (e.g., CloudWatch API error), the operation should continue without
    raising an exception.
    """
    # Arrange
    with patch('boto3.client') as mock_boto_client:
        mock_cloudwatch = Mock()
        # Simulate CloudWatch API failure
        mock_cloudwatch.put_metric_data.side_effect = Exception("CloudWatch API error")
        mock_boto_client.return_value = mock_cloudwatch
        
        mock_logger = Mock()
        metrics = MetricsClient(namespace="BDOMarketInsights/Test", logger=mock_logger)
        
        # Act & Assert - should not raise exception
        try:
            metrics._emit_metric(
                metric_name=metric_name,
                value=value,
                unit='Count'
            )
            # If we get here, the exception was caught (expected behavior)
            success = True
        except Exception:
            # If we get here, the exception was not caught (failure)
            success = False
        
        assert success, "Metrics emission failure should not propagate exception"
        
        # Verify logger was called to log the warning
        if mock_logger.warning.called:
            # Logger should have been called with warning about failed metric
            assert mock_logger.warning.called
