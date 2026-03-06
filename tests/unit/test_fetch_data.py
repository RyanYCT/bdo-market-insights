"""
Unit tests for fetchData Lambda function.

Tests External API error handling, rate limit header parsing,
and circuit breaker integration.
"""

import pytest
import sys
import json
import urllib.error
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

# Mock psycopg2 before importing common modules
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

# Import from fetchData Lambda function
from fetchData.lambda_function import (
    ExternalAPIClient,
    RateLimiter,
    split_into_batches,
    handler,
    lambda_handler
)

# Import from common
from common.logging import StructuredLogger
from common.circuit_breaker import CircuitBreaker
from common.retry import NetworkError, RateLimitError, TemporaryUnavailableError


class TestExternalAPIClient:
    """Test ExternalAPIClient class."""
    
    def test_fetch_market_data_success(self):
        """Test successful API call."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        mock_circuit_breaker = Mock(spec=CircuitBreaker)
        
        # Mock successful API response
        mock_response = {
            'items': [
                {'itemId': 1001, 'price': 15000, 'stock': 50},
                {'itemId': 1002, 'price': 20000, 'stock': 30}
            ]
        }
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
            logger=mock_logger
        )
        
        # Mock circuit breaker call
        mock_circuit_breaker.call.return_value = mock_response
        
        result = client.fetch_market_data([1001, 1002])
        
        # Verify results
        assert len(result) == 2
        assert result[0]['itemId'] == 1001
        assert result[1]['itemId'] == 1002
        
        # Verify rate limiter was called
        mock_rate_limiter.wait_if_needed.assert_called_once()
        mock_rate_limiter.record_call.assert_called_once()
    
    def test_fetch_market_data_empty_list(self):
        """Test handling of empty item ID list."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        mock_circuit_breaker = Mock(spec=CircuitBreaker)
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
            logger=mock_logger
        )
        
        result = client.fetch_market_data([])
        
        # Should return empty list
        assert result == []
        
        # Should log warning
        mock_logger.warning.assert_called()
    
    def test_metrics_emission_on_success(self):
        """Test that metrics are emitted on successful API calls."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        mock_circuit_breaker = Mock(spec=CircuitBreaker)
        mock_cloudwatch = Mock()
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
            logger=mock_logger
        )
        
        # Mock successful response
        mock_response = {'items': [{'itemId': 1001}]}
        
        with patch('lambda_function.cloudwatch', mock_cloudwatch):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_http_response = Mock()
                mock_http_response.getcode.return_value = 200
                mock_http_response.read.return_value = json.dumps(mock_response).encode('utf-8')
                mock_http_response.__enter__ = Mock(return_value=mock_http_response)
                mock_http_response.__exit__ = Mock(return_value=False)
                mock_urlopen.return_value = mock_http_response
                
                mock_circuit_breaker.call.side_effect = lambda func, *args: func(*args)
                
                client.fetch_market_data([1001])
        
        # Verify CloudWatch metrics were emitted
        assert mock_cloudwatch.put_metric_data.called
    
    def test_metrics_emission_on_error(self):
        """Test that metrics are emitted on API errors."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        mock_circuit_breaker = Mock(spec=CircuitBreaker)
        mock_cloudwatch = Mock()
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
            logger=mock_logger
        )
        
        mock_error = urllib.error.HTTPError(
            url="https://api.example.com/na/items?ids=1001",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=None
        )
        
        with patch('lambda_function.cloudwatch', mock_cloudwatch):
            with patch('urllib.request.urlopen', side_effect=mock_error):
                mock_circuit_breaker.call.side_effect = lambda func, *args: func(*args)
                
                with pytest.raises(NetworkError):
                    client.fetch_market_data([1001])
        
        # Verify CloudWatch metrics were emitted even on error
        assert mock_cloudwatch.put_metric_data.called


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration with API client."""
    
    def test_circuit_breaker_opens_after_failures(self):
        """Test that circuit breaker opens after threshold failures."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        
        # Create real circuit breaker with low threshold
        circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            timeout=60,
            logger=mock_logger
        )
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=circuit_breaker,
            logger=mock_logger
        )
        
        # Mock API to always fail
        mock_error = urllib.error.URLError("Connection refused")
        
        with patch('urllib.request.urlopen', side_effect=mock_error):
            # Make 3 failing calls to open circuit
            for i in range(3):
                with pytest.raises(NetworkError):
                    client.fetch_market_data([1001])
        
        # Circuit should now be OPEN
        from common.circuit_breaker import CircuitState
        assert circuit_breaker.state == CircuitState.OPEN
    
    def test_circuit_breaker_fails_fast_when_open(self):
        """Test that circuit breaker fails fast when open."""
        mock_logger = Mock(spec=StructuredLogger)
        mock_rate_limiter = Mock(spec=RateLimiter)
        
        circuit_breaker = CircuitBreaker(
            failure_threshold=2,
            timeout=60,
            logger=mock_logger
        )
        
        client = ExternalAPIClient(
            base_url="https://api.example.com",
            region="na",
            rate_limiter=mock_rate_limiter,
            circuit_breaker=circuit_breaker,
            logger=mock_logger
        )
        
        # Open the circuit
        mock_error = urllib.error.URLError("Connection refused")
        
        with patch('urllib.request.urlopen', side_effect=mock_error):
            for i in range(2):
                with pytest.raises(NetworkError):
                    client.fetch_market_data([1001])
        
        # Next call should fail fast with CircuitBreakerError
        from common.circuit_breaker import CircuitBreakerError
        
        with pytest.raises(CircuitBreakerError):
            client.fetch_market_data([1001])
        
        # Verify logger was called
        mock_logger.error.assert_called()


class TestBatchProcessing:
    """Test batch processing logic."""
    
    def test_split_into_batches_basic(self):
        """Test basic batch splitting."""
        item_ids = list(range(1, 101))  # 100 items
        batches = split_into_batches(item_ids, batch_size=25, max_batch_size=50)
        
        assert len(batches) == 4
        assert all(len(batch) == 25 for batch in batches)
    
    def test_split_into_batches_enforces_max(self):
        """Test that max_batch_size is enforced."""
        item_ids = list(range(1, 101))
        batches = split_into_batches(item_ids, batch_size=75, max_batch_size=50)
        
        # Should use max_batch_size instead of batch_size
        assert len(batches) == 2
        assert all(len(batch) == 50 for batch in batches)
    
    def test_split_into_batches_uneven_division(self):
        """Test batch splitting with uneven division."""
        item_ids = list(range(1, 26))  # 25 items
        batches = split_into_batches(item_ids, batch_size=10, max_batch_size=50)
        
        assert len(batches) == 3
        assert len(batches[0]) == 10
        assert len(batches[1]) == 10
        assert len(batches[2]) == 5  # Remainder


class TestLambdaHandler:
    """Test Lambda handler function."""
    
    def test_handler_success(self):
        """Test successful handler execution."""
        event = {
            'item_ids': [1001, 1002, 1003],
            'batch_size': 50,
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'fetchData'
        context.aws_request_id = 'test-request-id'
        
        # Mock API client
        with patch('lambda_function.ExternalAPIClient') as MockClient:
            mock_client = MockClient.return_value
            mock_client.fetch_market_data.return_value = [
                {'itemId': 1001, 'price': 15000},
                {'itemId': 1002, 'price': 20000},
                {'itemId': 1003, 'price': 25000}
            ]
            
            result = lambda_handler(event, context)
        
        # Verify result structure
        body = json.loads(result['body'])
        assert 'raw_data' in body
        assert 'correlation_id' in body
        assert 'metadata' in body
        assert body['correlation_id'] == 'test-corr-id'
        assert len(body['raw_data']) == 3
    
    def test_handler_validation_error(self):
        """Test handler with invalid input."""
        event = {
            # Missing required fields
            'batch_size': 50
        }
        context = Mock()
        context.function_name = 'fetchData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Should return error response
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_with_multiple_batches(self):
        """Test handler processing multiple batches."""
        # Create event with enough items to require multiple batches
        item_ids = list(range(1, 151))  # 150 items
        event = {
            'item_ids': item_ids,
            'batch_size': 50,
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'fetchData'
        context.aws_request_id = 'test-request-id'
        
        with patch('lambda_function.ExternalAPIClient') as MockClient:
            mock_client = MockClient.return_value
            # Return different data for each batch
            mock_client.fetch_market_data.side_effect = [
                [{'itemId': i} for i in range(1, 51)],
                [{'itemId': i} for i in range(51, 101)],
                [{'itemId': i} for i in range(101, 151)]
            ]
            
            result = lambda_handler(event, context)
        
        # Verify all batches were processed
        body = json.loads(result['body'])
        assert body['metadata']['batches_processed'] == 3
        assert len(body['raw_data']) == 150
    
    def test_handler_circuit_breaker_stops_processing(self):
        """Test that circuit breaker stops batch processing."""
        item_ids = list(range(1, 201))  # 200 items, 4 batches
        event = {
            'item_ids': item_ids,
            'batch_size': 50,
            'correlation_id': 'test-corr-id'
        }
        context = Mock()
        context.function_name = 'fetchData'
        context.aws_request_id = 'test-request-id'
        
        from common.circuit_breaker import CircuitBreakerError
        
        with patch('lambda_function.ExternalAPIClient') as MockClient:
            mock_client = MockClient.return_value
            # First batch succeeds, second raises CircuitBreakerError
            mock_client.fetch_market_data.side_effect = [
                [{'itemId': i} for i in range(1, 51)],
                CircuitBreakerError("Circuit is open")
            ]
            
            result = lambda_handler(event, context)
        
        # Should have processed only 1 batch before circuit opened
        body = json.loads(result['body'])
        assert body['metadata']['batches_processed'] == 1
        assert body['metadata']['batches_failed'] == 3  # Remaining batches
        assert len(body['raw_data']) == 50  # Only first batch data
