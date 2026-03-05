"""
Unit tests for analyzeData Lambda function.

Tests statistics calculation, profitability score computation, price trend detection,
and edge cases (empty data, single record).
"""

import pytest
import sys
from unittest.mock import Mock, MagicMock
from datetime import datetime, timezone

# Mock psycopg2 before importing
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

# Add lambda_layer to path
sys.path.insert(0, 'lambda_layer/python')

# Add analyzeData to path
from pathlib import Path
analyze_data_path = Path(__file__).parent.parent.parent / "analyzeData"
sys.path.insert(0, str(analyze_data_path))

from lambda_function import (
    calculate_statistics,
    compute_profitability_score,
    determine_price_trend,
    handler,
    lambda_handler
)


class TestCalculateStatistics:
    """Test statistics calculation."""
    
    def test_statistics_with_multiple_records(self):
        """Test statistics calculation with multiple records."""
        market_data = [
            {
                'last_sold_price': 15000,
                'current_stock': 50,
                'total_trades': 1234
            },
            {
                'last_sold_price': 16000,
                'current_stock': 45,
                'total_trades': 1567
            },
            {
                'last_sold_price': 14000,
                'current_stock': 55,
                'total_trades': 1100
            }
        ]
        
        stats = calculate_statistics(market_data)
        
        # Verify averages
        assert stats['avg_last_sold_price'] == 15000.0  # (15000 + 16000 + 14000) / 3
        assert stats['avg_current_stock'] == 50.0  # (50 + 45 + 55) / 3
        
        # Verify min/max
        assert stats['min_last_sold_price'] == 14000
        assert stats['max_last_sold_price'] == 16000
        
        # Verify sum
        assert stats['total_trades_sum'] == 3901  # 1234 + 1567 + 1100
    
    def test_statistics_with_single_record(self):
        """Test statistics calculation with single record."""
        market_data = [
            {
                'last_sold_price': 15000,
                'current_stock': 50,
                'total_trades': 1234
            }
        ]
        
        stats = calculate_statistics(market_data)
        
        # All values should equal the single record
        assert stats['avg_last_sold_price'] == 15000
        assert stats['min_last_sold_price'] == 15000
        assert stats['max_last_sold_price'] == 15000
        assert stats['avg_current_stock'] == 50
        assert stats['total_trades_sum'] == 1234
    
    def test_statistics_with_empty_data(self):
        """Test statistics calculation with empty data."""
        market_data = []
        
        stats = calculate_statistics(market_data)
        
        # All values should be zero
        assert stats['avg_last_sold_price'] == 0
        assert stats['min_last_sold_price'] == 0
        assert stats['max_last_sold_price'] == 0
        assert stats['avg_current_stock'] == 0
        assert stats['total_trades_sum'] == 0
    
    def test_statistics_with_zero_values(self):
        """Test statistics calculation with zero values."""
        market_data = [
            {
                'last_sold_price': 0,
                'current_stock': 0,
                'total_trades': 0
            },
            {
                'last_sold_price': 0,
                'current_stock': 0,
                'total_trades': 0
            }
        ]
        
        stats = calculate_statistics(market_data)
        
        # All values should be zero
        assert stats['avg_last_sold_price'] == 0
        assert stats['min_last_sold_price'] == 0
        assert stats['max_last_sold_price'] == 0
        assert stats['avg_current_stock'] == 0
        assert stats['total_trades_sum'] == 0


class TestComputeProfitabilityScore:
    """Test profitability score computation."""
    
    def test_profitability_score_high_growth(self):
        """Test profitability score with high price growth."""
        market_data = [
            {
                'last_sold_price': 10000,
                'current_stock': 100,
                'total_trades': 10000
            },
            {
                'last_sold_price': 20000,  # 100% increase
                'current_stock': 100,
                'total_trades': 10000
            }
        ]
        
        score = compute_profitability_score(market_data)
        
        # High price increase + good volume + good stock
        # Price: (20000-10000)/10000 * 0.5 = 0.5
        # Trade: 20000/10000 * 0.3 = 0.3 (capped at 0.3)
        # Stock: 100/100 * 0.2 = 0.2 (capped at 0.2)
        # Total: 0.5 + 0.3 + 0.2 = 1.0
        assert score == 1.0
    
    def test_profitability_score_moderate_growth(self):
        """Test profitability score with moderate price growth."""
        market_data = [
            {
                'last_sold_price': 10000,
                'current_stock': 50,
                'total_trades': 5000
            },
            {
                'last_sold_price': 12000,  # 20% increase
                'current_stock': 50,
                'total_trades': 5000
            }
        ]
        
        score = compute_profitability_score(market_data)
        
        # Moderate price increase + moderate volume + moderate stock
        # Price: (12000-10000)/10000 * 0.5 = 0.1
        # Trade: 10000/10000 * 0.3 = 0.3
        # Stock: 50/100 * 0.2 = 0.1
        # Total: 0.1 + 0.3 + 0.1 = 0.5
        assert score == 0.5
    
    def test_profitability_score_no_growth(self):
        """Test profitability score with no price growth."""
        market_data = [
            {
                'last_sold_price': 10000,
                'current_stock': 10,
                'total_trades': 1000
            },
            {
                'last_sold_price': 10000,  # No change
                'current_stock': 10,
                'total_trades': 1000
            }
        ]
        
        score = compute_profitability_score(market_data)
        
        # No price increase + low volume + low stock
        # Price: 0
        # Trade: 2000/10000 * 0.3 = 0.06
        # Stock: 10/100 * 0.2 = 0.02
        # Total: 0 + 0.06 + 0.02 = 0.08
        assert score == 0.08
    
    def test_profitability_score_empty_data(self):
        """Test profitability score with empty data."""
        market_data = []
        
        score = compute_profitability_score(market_data)
        
        assert score == 0.0
    
    def test_profitability_score_single_record(self):
        """Test profitability score with single record."""
        market_data = [
            {
                'last_sold_price': 15000,
                'current_stock': 50,
                'total_trades': 5000
            }
        ]
        
        score = compute_profitability_score(market_data)
        
        # No price change (min == max)
        # Price: 0
        # Trade: 5000/10000 * 0.3 = 0.15
        # Stock: 50/100 * 0.2 = 0.1
        # Total: 0 + 0.15 + 0.1 = 0.25
        assert score == 0.25
    
    def test_profitability_score_zero_min_price(self):
        """Test profitability score with zero minimum price."""
        market_data = [
            {
                'last_sold_price': 0,
                'current_stock': 50,
                'total_trades': 5000
            },
            {
                'last_sold_price': 10000,
                'current_stock': 50,
                'total_trades': 5000
            }
        ]
        
        score = compute_profitability_score(market_data)
        
        # Price score should be 0 (division by zero avoided)
        # Trade: 10000/10000 * 0.3 = 0.3
        # Stock: 50/100 * 0.2 = 0.1
        # Total: 0 + 0.3 + 0.1 = 0.4
        assert score == 0.4


class TestDeterminePriceTrend:
    """Test price trend detection."""
    
    def test_price_trend_increasing(self):
        """Test price trend detection for increasing prices."""
        market_data = [
            {
                'last_sold_price': 10000,
                'last_sold_time': datetime(2024, 1, 1, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 11000,
                'last_sold_time': datetime(2024, 1, 2, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 12000,
                'last_sold_time': datetime(2024, 1, 3, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 13000,
                'last_sold_time': datetime(2024, 1, 4, tzinfo=timezone.utc)
            }
        ]
        
        trend = determine_price_trend(market_data)
        
        # Second half avg (12500) > first half avg (10500) by ~19%
        assert trend == 'increasing'
    
    def test_price_trend_decreasing(self):
        """Test price trend detection for decreasing prices."""
        market_data = [
            {
                'last_sold_price': 13000,
                'last_sold_time': datetime(2024, 1, 1, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 12000,
                'last_sold_time': datetime(2024, 1, 2, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 11000,
                'last_sold_time': datetime(2024, 1, 3, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 10000,
                'last_sold_time': datetime(2024, 1, 4, tzinfo=timezone.utc)
            }
        ]
        
        trend = determine_price_trend(market_data)
        
        # Second half avg (10500) < first half avg (12500) by ~16%
        assert trend == 'decreasing'
    
    def test_price_trend_stable(self):
        """Test price trend detection for stable prices."""
        market_data = [
            {
                'last_sold_price': 10000,
                'last_sold_time': datetime(2024, 1, 1, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 10100,
                'last_sold_time': datetime(2024, 1, 2, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 10200,
                'last_sold_time': datetime(2024, 1, 3, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 10300,
                'last_sold_time': datetime(2024, 1, 4, tzinfo=timezone.utc)
            }
        ]
        
        trend = determine_price_trend(market_data)
        
        # Second half avg (10250) vs first half avg (10050) = ~2% change
        assert trend == 'stable'
    
    def test_price_trend_empty_data(self):
        """Test price trend detection with empty data."""
        market_data = []
        
        trend = determine_price_trend(market_data)
        
        assert trend == 'stable'
    
    def test_price_trend_single_record(self):
        """Test price trend detection with single record."""
        market_data = [
            {
                'last_sold_price': 10000,
                'last_sold_time': datetime(2024, 1, 1, tzinfo=timezone.utc)
            }
        ]
        
        trend = determine_price_trend(market_data)
        
        assert trend == 'stable'
    
    def test_price_trend_unsorted_data(self):
        """Test price trend detection with unsorted data."""
        market_data = [
            {
                'last_sold_price': 12000,
                'last_sold_time': datetime(2024, 1, 3, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 10000,
                'last_sold_time': datetime(2024, 1, 1, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 13000,
                'last_sold_time': datetime(2024, 1, 4, tzinfo=timezone.utc)
            },
            {
                'last_sold_price': 11000,
                'last_sold_time': datetime(2024, 1, 2, tzinfo=timezone.utc)
            }
        ]
        
        trend = determine_price_trend(market_data)
        
        # Should sort by time first: 10000, 11000, 12000, 13000
        # Second half avg (12500) > first half avg (10500) by ~19%
        assert trend == 'increasing'


class TestAnalyzeDataHandler:
    """Test analyzeData Lambda handler."""
    
    def test_handler_success_with_data(self):
        """Test successful handler execution with market data."""
        event = {
            'market_data': [
                {
                    'item_id': 1001,
                    'item_name': 'Black Stone (Weapon)',
                    'last_sold_price': 15000,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                },
                {
                    'item_id': 1001,
                    'item_name': 'Black Stone (Weapon)',
                    'last_sold_price': 16000,
                    'current_stock': 45,
                    'total_trades': 1567,
                    'last_sold_time': datetime(2024, 1, 16, 10, 30, 0, tzinfo=timezone.utc)
                }
            ],
            'query_params': {
                'item_id': 1001,
                'start_date': '2024-01-01T00:00:00+00:00',
                'end_date': '2024-01-31T23:59:59+00:00'
            }
        }
        
        context = Mock()
        context.function_name = 'analyzeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert result['statusCode'] == 200
        assert 'body' in result
        
        # Parse body
        import json
        body = json.loads(result['body'])
        
        # Verify response structure
        assert 'item_id' in body
        assert 'item_name' in body
        assert 'date_range' in body
        assert 'statistics' in body
        assert 'profitability_score' in body
        assert 'price_trend' in body
        assert 'record_count' in body
        
        # Verify values
        assert body['item_id'] == 1001
        assert body['item_name'] == 'Black Stone (Weapon)'
        assert body['record_count'] == 2
        assert body['statistics']['avg_last_sold_price'] == 15500.0
        assert body['statistics']['min_last_sold_price'] == 15000
        assert body['statistics']['max_last_sold_price'] == 16000
        assert body['price_trend'] == 'increasing'
        assert body['profitability_score'] > 0
    
    def test_handler_empty_market_data(self):
        """Test handler with empty market data."""
        event = {
            'market_data': [],
            'query_params': {
                'item_id': 1001,
                'start_date': '2024-01-01T00:00:00+00:00',
                'end_date': '2024-01-31T23:59:59+00:00'
            }
        }
        
        context = Mock()
        context.function_name = 'analyzeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert result['statusCode'] == 200
        
        # Parse body
        import json
        body = json.loads(result['body'])
        
        # Verify response structure
        assert body['item_id'] == 1001
        assert body['item_name'] is None
        assert body['record_count'] == 0
        assert body['profitability_score'] == 0.0
        assert body['price_trend'] == 'stable'
        assert body['statistics']['avg_last_sold_price'] == 0
    
    def test_handler_single_record(self):
        """Test handler with single market data record."""
        event = {
            'market_data': [
                {
                    'item_id': 1001,
                    'item_name': 'Black Stone (Weapon)',
                    'last_sold_price': 15000,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                }
            ],
            'query_params': {
                'item_id': 1001,
                'start_date': '2024-01-01T00:00:00+00:00',
                'end_date': '2024-01-31T23:59:59+00:00'
            }
        }
        
        context = Mock()
        context.function_name = 'analyzeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert result['statusCode'] == 200
        
        # Parse body
        import json
        body = json.loads(result['body'])
        
        # Verify response
        assert body['item_id'] == 1001
        assert body['record_count'] == 1
        assert body['statistics']['avg_last_sold_price'] == 15000
        assert body['statistics']['min_last_sold_price'] == 15000
        assert body['statistics']['max_last_sold_price'] == 15000
        assert body['price_trend'] == 'stable'
    
    def test_handler_preserves_query_context(self):
        """Test handler preserves query context in response."""
        event = {
            'market_data': [
                {
                    'item_id': 1001,
                    'item_name': 'Black Stone (Weapon)',
                    'last_sold_price': 15000,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                }
            ],
            'query_params': {
                'item_id': 1001,
                'name': 'Black Stone',
                'category': 'Buff',
                'start_date': '2024-01-01T00:00:00+00:00',
                'end_date': '2024-01-31T23:59:59+00:00',
                'limit': 100
            }
        }
        
        context = Mock()
        context.function_name = 'analyzeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert result['statusCode'] == 200
        
        # Parse body
        import json
        body = json.loads(result['body'])
        
        # Verify date range is preserved
        assert body['date_range']['start'] == '2024-01-01T00:00:00+00:00'
        assert body['date_range']['end'] == '2024-01-31T23:59:59+00:00'


class TestLambdaHandler:
    """Test Lambda handler with router integration."""
    
    def test_lambda_handler_success(self):
        """Test successful Lambda handler execution."""
        event = {
            'market_data': [
                {
                    'item_id': 1001,
                    'item_name': 'Black Stone (Weapon)',
                    'last_sold_price': 15000,
                    'current_stock': 50,
                    'total_trades': 1234,
                    'last_sold_time': datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
                }
            ],
            'query_params': {
                'item_id': 1001,
                'start_date': '2024-01-01T00:00:00+00:00',
                'end_date': '2024-01-31T23:59:59+00:00'
            }
        }
        
        context = Mock()
        context.function_name = 'analyzeData'
        context.aws_request_id = 'test-request-id'
        
        result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert result['statusCode'] == 200
        assert 'body' in result
        assert 'headers' in result
        
        # Verify correlation ID in headers
        assert 'X-Correlation-ID' in result['headers']
