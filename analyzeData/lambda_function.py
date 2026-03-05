"""
analyzeData Lambda function.

Analyzes market data and computes statistics, profitability scores, and price trends.
Integrates with LambdaRouter for consistent request/response handling and structured logging.
"""

import sys
from typing import Any, Dict, List, Optional
from datetime import datetime

# Add lambda_layer to path
sys.path.insert(0, '/opt/python')

from common.router import LambdaRouter
from common.logging import StructuredLogger
from common.metrics import MetricsClient


# Initialize router
router = LambdaRouter()


def calculate_statistics(market_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate statistics from market data.
    
    Args:
        market_data: List of market data records
        
    Returns:
        dict: Statistics including avg/min/max prices, avg stock, total trades
    """
    if not market_data:
        return {
            'avg_last_sold_price': 0,
            'min_last_sold_price': 0,
            'max_last_sold_price': 0,
            'avg_current_stock': 0,
            'total_trades_sum': 0
        }
    
    prices = [record['last_sold_price'] for record in market_data]
    stocks = [record['current_stock'] for record in market_data]
    trades = [record['total_trades'] for record in market_data]
    
    return {
        'avg_last_sold_price': sum(prices) / len(prices),
        'min_last_sold_price': min(prices),
        'max_last_sold_price': max(prices),
        'avg_current_stock': sum(stocks) / len(stocks),
        'total_trades_sum': sum(trades)
    }


def compute_profitability_score(market_data: List[Dict[str, Any]]) -> float:
    """
    Compute profitability score based on price trends.
    
    Score is calculated as:
    - Price increase rate (0-0.5): (max_price - min_price) / min_price * 0.5
    - Trade volume factor (0-0.3): min(total_trades / 10000, 0.3)
    - Stock availability factor (0-0.2): min(avg_stock / 100, 0.2)
    
    Args:
        market_data: List of market data records
        
    Returns:
        float: Profitability score between 0 and 1
    """
    if not market_data:
        return 0.0
    
    stats = calculate_statistics(market_data)
    
    # Price increase rate (0-0.5)
    if stats['min_last_sold_price'] > 0:
        price_increase = (stats['max_last_sold_price'] - stats['min_last_sold_price']) / stats['min_last_sold_price']
        price_score = min(price_increase * 0.5, 0.5)
    else:
        price_score = 0.0
    
    # Trade volume factor (0-0.3)
    trade_score = min(stats['total_trades_sum'] / 10000 * 0.3, 0.3)
    
    # Stock availability factor (0-0.2)
    stock_score = min(stats['avg_current_stock'] / 100 * 0.2, 0.2)
    
    return round(price_score + trade_score + stock_score, 2)


def determine_price_trend(market_data: List[Dict[str, Any]]) -> str:
    """
    Determine price trend (increasing, decreasing, stable).
    
    Trend is determined by comparing first half vs second half of data:
    - Increasing: second half avg > first half avg by more than 5%
    - Decreasing: second half avg < first half avg by more than 5%
    - Stable: difference is within 5%
    
    Args:
        market_data: List of market data records (sorted by time)
        
    Returns:
        str: 'increasing', 'decreasing', or 'stable'
    """
    if not market_data or len(market_data) < 2:
        return 'stable'
    
    # Sort by last_sold_time to ensure chronological order
    sorted_data = sorted(market_data, key=lambda x: x['last_sold_time'])
    
    # Split into first and second half
    mid_point = len(sorted_data) // 2
    first_half = sorted_data[:mid_point]
    second_half = sorted_data[mid_point:]
    
    # Calculate average prices
    first_half_avg = sum(r['last_sold_price'] for r in first_half) / len(first_half)
    second_half_avg = sum(r['last_sold_price'] for r in second_half) / len(second_half)
    
    # Calculate percentage change
    if first_half_avg > 0:
        change_percent = (second_half_avg - first_half_avg) / first_half_avg
        
        if change_percent > 0.05:
            return 'increasing'
        elif change_percent < -0.05:
            return 'decreasing'
    
    return 'stable'


@router.route()
def handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    Process market data and compute analysis.
    
    Expected event structure:
    {
        "market_data": [...],  # List of market data records from queryData
        "query_params": {...},  # Original query parameters
        "correlation_id": "..."
    }
    
    Args:
        event: Lambda event containing market data and query params
        context: Lambda context
        logger: Structured logger instance
        
    Returns:
        dict: Analysis results with statistics, profitability score, and trend
    """
    logger.info("Starting market data analysis", event_keys=list(event.keys()))
    
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/Query", logger=logger)
    
    try:
        # Extract data from event
        market_data = event.get('market_data', [])
        query_params = event.get('query_params', {})
        
        logger.info("Processing market data", record_count=len(market_data))
        
        # Track analysis latency
        with metrics.track_latency("analyzeData", record_count=len(market_data)) as tracker:
            # Handle empty data
            if not market_data:
                logger.warning("No market data to analyze")
                return {
                    'item_id': query_params.get('item_id'),
                    'item_name': None,
                    'date_range': {
                        'start': query_params.get('start_date'),
                        'end': query_params.get('end_date')
                    },
                    'statistics': calculate_statistics([]),
                    'profitability_score': 0.0,
                    'price_trend': 'stable',
                    'record_count': 0
                }
            
            # Extract item information from first record
            first_record = market_data[0]
            item_id = first_record.get('item_id')
            item_name = first_record.get('item_name')
            
            logger.info("Analyzing item", item_id=item_id, item_name=item_name)
            
            # Calculate statistics
            statistics = calculate_statistics(market_data)
            logger.info("Statistics calculated", statistics=statistics)
            
            # Compute profitability score
            profitability_score = compute_profitability_score(market_data)
            logger.info("Profitability score computed", score=profitability_score)
            
            # Determine price trend
            price_trend = determine_price_trend(market_data)
            logger.info("Price trend determined", trend=price_trend)
            
            # Build response
            result = {
                'item_id': item_id,
                'item_name': item_name,
                'date_range': {
                    'start': query_params.get('start_date'),
                    'end': query_params.get('end_date')
                },
                'statistics': statistics,
                'profitability_score': profitability_score,
                'price_trend': price_trend,
                'record_count': len(market_data)
            }
            
            logger.info("Analysis completed successfully", result_summary={
                'item_id': item_id,
                'record_count': len(market_data),
                'profitability_score': profitability_score,
                'price_trend': price_trend
            })
            
            # Emit query latency metric
            metrics.emit_query_latency(
                function_name="analyzeData",
                latency_ms=tracker.elapsed_ms,
                record_count=len(market_data)
            )
            
            return result
    
    except Exception as e:
        logger.error("Analysis failed", error=e)
        metrics.emit_etl_failure(
            function_name="analyzeData",
            error_type=type(e).__name__
        )
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler function.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        dict: Formatted response with analysis results
    """
    return handler(event, context)
