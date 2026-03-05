"""
cleanData Lambda function - Transform and validate raw API data.

This Lambda function:
- Receives raw market data from fetchData Lambda
- Transforms API response format to MarketDataRecord format
- Validates all records against Pydantic schema
- Filters out invalid records with logging
- Returns cleaned and validated data for storeData Lambda

Requirements: 2.2, 2.3
"""

import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

# Add lambda layer to path
sys.path.insert(0, '/opt/python')

from common.router import LambdaRouter, ValidationError as RouterValidationError
from common.schemas import CleanDataInput, MarketDataRecord
from common.metrics import MetricsClient
from pydantic import ValidationError as PydanticValidationError


# Initialize router
router = LambdaRouter()


def transform_raw_record(raw_record: dict) -> dict:
    """
    Transform raw API record to MarketDataRecord format.
    
    Converts field names from API format (camelCase) to schema format (snake_case).
    Handles datetime parsing for last_sold_time.
    
    Args:
        raw_record: Raw record from External API
        
    Returns:
        dict: Transformed record ready for validation
    """
    # Map API fields to schema fields
    transformed = {
        'item_id': raw_record.get('itemId'),
        'sid': raw_record.get('sid', 0),
        'current_stock': raw_record.get('currentStock'),
        'total_trades': raw_record.get('totalTrades'),
        'last_sold_price': raw_record.get('lastSoldPrice'),
        'last_sold_time': raw_record.get('lastSoldTime')
    }
    
    # Parse datetime if it's a string
    if isinstance(transformed['last_sold_time'], str):
        try:
            # Try ISO format first
            transformed['last_sold_time'] = datetime.fromisoformat(
                transformed['last_sold_time'].replace('Z', '+00:00')
            )
        except (ValueError, AttributeError):
            # If parsing fails, set to None (will be caught by validation)
            transformed['last_sold_time'] = None
    
    return transformed


@router.route(function_name='cleanData')
def handler(event: Dict[str, Any], context: Any, logger) -> Dict[str, Any]:
    """
    Clean and validate raw market data.
    
    Args:
        event: Lambda event containing raw_data and correlation_id
        context: Lambda context
        logger: StructuredLogger instance
        
    Returns:
        dict: Cleaned and validated data with metadata
    """
    logger.info("Starting data cleaning and validation")
    
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/ETL", logger=logger)
    
    try:
        # Validate input
        try:
            input_data = CleanDataInput(**event)
            logger.info(
                "Input validated",
                raw_record_count=len(input_data.raw_data)
            )
        except PydanticValidationError as e:
            logger.error("Input validation failed", error=e)
            metrics.emit_etl_failure(
                function_name="cleanData",
                error_type="ValidationError"
            )
            # Re-raise as RouterValidationError so router catches it properly
            raise RouterValidationError(str(e))
        
        # Track execution latency
        with metrics.track_latency("cleanData"):
            # Transform and validate each record
            cleaned_records = []
            invalid_records = []
            
            for idx, raw_record in enumerate(input_data.raw_data):
                try:
                    # Transform to schema format
                    transformed = transform_raw_record(raw_record)
                    
                    # Validate against Pydantic schema
                    validated_record = MarketDataRecord(**transformed)
                    
                    # Add to cleaned list (serialize datetime to ISO format)
                    cleaned_records.append(validated_record.model_dump(mode='json'))
                    
                except PydanticValidationError as e:
                    # Log validation failure and skip record
                    logger.warning(
                        "Record validation failed, filtering out",
                        record_index=idx,
                        item_id=raw_record.get('itemId'),
                        validation_errors=e.errors()
                    )
                    invalid_records.append({
                        'index': idx,
                        'item_id': raw_record.get('itemId'),
                        'errors': e.errors()
                    })
                except Exception as e:
                    # Log unexpected transformation errors
                    logger.warning(
                        "Record transformation failed, filtering out",
                        record_index=idx,
                        item_id=raw_record.get('itemId'),
                        error_type=type(e).__name__,
                        error_message=str(e)
                    )
                    invalid_records.append({
                        'index': idx,
                        'item_id': raw_record.get('itemId'),
                        'error': str(e)
                    })
            
            # Log summary
            logger.info(
                "Data cleaning completed",
                total_records=len(input_data.raw_data),
                valid_records=len(cleaned_records),
                invalid_records=len(invalid_records)
            )
            
            # Emit success metric
            metrics.emit_etl_success(
                function_name="cleanData",
                valid_records=len(cleaned_records),
                invalid_records=len(invalid_records)
            )
            
            # Return cleaned data
            return {
                'cleaned_data': cleaned_records,
                'correlation_id': input_data.correlation_id,
                'metadata': {
                    'total_records': len(input_data.raw_data),
                    'valid_records': len(cleaned_records),
                    'invalid_records': len(invalid_records),
                    'filtered_items': [r['item_id'] for r in invalid_records if 'item_id' in r]
                }
            }
    
    except RouterValidationError:
        # Re-raise validation errors
        raise
    except Exception as e:
        logger.error("Unexpected error in cleanData", error=e)
        metrics.emit_etl_failure(
            function_name="cleanData",
            error_type=type(e).__name__
        )
        raise


# Lambda handler function
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function."""
    return handler(event, context)
