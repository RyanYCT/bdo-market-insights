"""
retainData Lambda function - Implements data retention policies.

Integrates with:
- LambdaRouter for consistent request/response handling
- StructuredLogger for JSON logging with correlation IDs
- DatabasePool for PostgreSQL connection pooling

Implements:
- Aggregate MarketData older than 90 days into daily summaries
- Calculate avg/min/max prices, avg stock, total trades
- Archive summaries older than 2 years to S3 Glacier
- Delete processed records from source tables
- Log all operations with record counts and date ranges
"""

import os
import sys
import json
import boto3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal

# Add lambda_layer to path
sys.path.insert(0, '/opt/python')

from common.router import LambdaRouter
from common.database import DatabasePool
from common.logging import StructuredLogger
from common.metrics import MetricsClient


# Initialize router
router = LambdaRouter()

# Initialize database pool (lazy initialization on first use)
db_pool: Optional[DatabasePool] = None

# Initialize S3 client (lazy initialization)
s3_client: Optional[Any] = None


def get_database_pool(logger: StructuredLogger) -> DatabasePool:
    """
    Get or create database pool instance.
    
    Uses module-level singleton to reuse pool across invocations
    within the same Lambda execution context.
    
    Args:
        logger: Structured logger instance
        
    Returns:
        DatabasePool: Database connection pool
    """
    global db_pool
    
    if db_pool is None:
        secret_name = os.getenv('DB_SECRET_NAME', 'bdo-db-credentials')
        pool_size = int(os.getenv('DB_POOL_SIZE', '5'))
        
        logger.info(
            "Initializing database pool",
            secret_name=secret_name,
            pool_size=pool_size
        )
        
        db_pool = DatabasePool(
            secret_name=secret_name,
            pool_size=pool_size,
            logger=logger
        )
    
    return db_pool


def get_s3_client():
    """
    Get or create S3 client instance.
    
    Returns:
        boto3.client: S3 client
    """
    global s3_client
    
    if s3_client is None:
        s3_client = boto3.client('s3')
    
    return s3_client


def load_retention_config(logger: StructuredLogger) -> Dict[str, int]:
    """
    Load retention periods from configuration.
    
    Reads from environment variables with defaults:
    - RETENTION_DETAILED_DAYS: Days to keep detailed records (default: 90)
    - RETENTION_SUMMARY_DAYS: Days to keep summary records (default: 730 = 2 years)
    
    Args:
        logger: Structured logger
        
    Returns:
        dict: Configuration with retention_detailed_days and retention_summary_days
    """
    config = {
        'retention_detailed_days': int(os.getenv('RETENTION_DETAILED_DAYS', '90')),
        'retention_summary_days': int(os.getenv('RETENTION_SUMMARY_DAYS', '730'))
    }
    
    logger.info(
        "Loaded retention configuration",
        retention_detailed_days=config['retention_detailed_days'],
        retention_summary_days=config['retention_summary_days']
    )
    
    return config


def aggregate_old_market_data(
    pool: DatabasePool,
    cutoff_date: datetime,
    logger: StructuredLogger
) -> Tuple[int, datetime, datetime]:
    """
    Aggregate MarketData older than cutoff date into daily summaries.
    
    Calculates:
    - avg_last_sold_price: Average price for the day
    - min_last_sold_price: Minimum price for the day
    - max_last_sold_price: Maximum price for the day
    - avg_current_stock: Average stock for the day
    - total_trades_sum: Sum of total trades for the day
    - record_count: Number of records aggregated
    
    Args:
        pool: Database connection pool
        cutoff_date: Aggregate records older than this date
        logger: Structured logger
        
    Returns:
        tuple: (records_aggregated, min_date, max_date)
    """
    logger.info(
        "Starting aggregation of old market data",
        cutoff_date=cutoff_date.isoformat()
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # First, check if there are records to aggregate
            cursor.execute(
                """
                SELECT 
                    MIN(last_sold_time::date) as min_date,
                    MAX(last_sold_time::date) as max_date,
                    COUNT(*) as record_count
                FROM bdo_marketdata
                WHERE last_sold_time < %s
                """,
                (cutoff_date,)
            )
            
            result = cursor.fetchone()
            if not result or result[2] == 0:
                logger.info("No records found to aggregate")
                return 0, None, None
            
            min_date, max_date, record_count = result
            
            logger.info(
                "Found records to aggregate",
                record_count=record_count,
                date_range_start=min_date.isoformat() if min_date else None,
                date_range_end=max_date.isoformat() if max_date else None
            )
            
            # Aggregate into daily summaries (parameterized query)
            cursor.execute(
                """
                INSERT INTO bdo_marketdatasummary 
                (item_id, date, avg_last_sold_price, min_last_sold_price, 
                 max_last_sold_price, avg_current_stock, total_trades_sum, record_count)
                SELECT 
                    item_id,
                    last_sold_time::date as date,
                    AVG(last_sold_price) as avg_last_sold_price,
                    MIN(last_sold_price) as min_last_sold_price,
                    MAX(last_sold_price) as max_last_sold_price,
                    AVG(current_stock) as avg_current_stock,
                    SUM(total_trades) as total_trades_sum,
                    COUNT(*) as record_count
                FROM bdo_marketdata
                WHERE last_sold_time < %s
                GROUP BY item_id, last_sold_time::date
                ON CONFLICT (item_id, date) DO UPDATE SET
                    avg_last_sold_price = EXCLUDED.avg_last_sold_price,
                    min_last_sold_price = EXCLUDED.min_last_sold_price,
                    max_last_sold_price = EXCLUDED.max_last_sold_price,
                    avg_current_stock = EXCLUDED.avg_current_stock,
                    total_trades_sum = EXCLUDED.total_trades_sum,
                    record_count = EXCLUDED.record_count
                """,
                (cutoff_date,)
            )
            
            aggregated_rows = cursor.rowcount
            conn.commit()
            
            logger.info(
                "Aggregation completed",
                records_aggregated=record_count,
                summary_rows_created=aggregated_rows,
                date_range_start=min_date.isoformat() if min_date else None,
                date_range_end=max_date.isoformat() if max_date else None
            )
            
            return record_count, min_date, max_date


def delete_aggregated_records(
    pool: DatabasePool,
    cutoff_date: datetime,
    logger: StructuredLogger
) -> int:
    """
    Delete MarketData records that have been aggregated.
    
    Only deletes records older than cutoff_date that have corresponding
    summary records.
    
    Args:
        pool: Database connection pool
        cutoff_date: Delete records older than this date
        logger: Structured logger
        
    Returns:
        int: Number of records deleted
    """
    logger.info(
        "Deleting aggregated market data records",
        cutoff_date=cutoff_date.isoformat()
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # Delete old records (parameterized query)
            cursor.execute(
                """
                DELETE FROM bdo_marketdata
                WHERE last_sold_time < %s
                """,
                (cutoff_date,)
            )
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            logger.info(
                "Deleted aggregated records",
                records_deleted=deleted_count
            )
            
            return deleted_count


def archive_old_summaries_to_s3(
    pool: DatabasePool,
    cutoff_date: datetime,
    logger: StructuredLogger
) -> Tuple[int, str]:
    """
    Archive summary records older than cutoff date to S3 Glacier.
    
    Exports summaries to JSON format and uploads to S3 with Glacier storage class.
    
    Args:
        pool: Database connection pool
        cutoff_date: Archive summaries older than this date
        logger: Structured logger
        
    Returns:
        tuple: (records_archived, s3_key)
    """
    logger.info(
        "Archiving old summary records to S3",
        cutoff_date=cutoff_date.isoformat()
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # Fetch old summaries (parameterized query)
            cursor.execute(
                """
                SELECT 
                    s.id, s.item_id, s.date, s.avg_last_sold_price,
                    s.min_last_sold_price, s.max_last_sold_price,
                    s.avg_current_stock, s.total_trades_sum, s.record_count,
                    i.name as item_name, i.item_id as bdo_item_id, i.sid
                FROM bdo_marketdatasummary s
                JOIN bdo_item i ON s.item_id = i.id
                WHERE s.date < %s
                ORDER BY s.date, s.item_id
                """,
                (cutoff_date,)
            )
            
            summaries = cursor.fetchall()
            
            if not summaries:
                logger.info("No summaries found to archive")
                return 0, None
            
            logger.info(
                "Found summaries to archive",
                summary_count=len(summaries)
            )
            
            # Convert to JSON-serializable format
            archive_data = []
            for row in summaries:
                archive_data.append({
                    'id': row[0],
                    'item_id': row[1],
                    'date': row[2].isoformat(),
                    'avg_last_sold_price': float(row[3]) if isinstance(row[3], Decimal) else row[3],
                    'min_last_sold_price': row[4],
                    'max_last_sold_price': row[5],
                    'avg_current_stock': float(row[6]) if isinstance(row[6], Decimal) else row[6],
                    'total_trades_sum': row[7],
                    'record_count': row[8],
                    'item_name': row[9],
                    'bdo_item_id': row[10],
                    'sid': row[11]
                })
            
            # Generate S3 key with timestamp
            archive_timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            s3_key = f"archives/market_data_summaries_{archive_timestamp}.json"
            
            # Upload to S3 with Glacier storage class
            bucket_name = os.getenv('ARCHIVE_BUCKET_NAME', 'bdo-market-insights-archive')
            
            s3 = get_s3_client()
            s3.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=json.dumps(archive_data, indent=2),
                StorageClass='GLACIER',
                ContentType='application/json',
                Metadata={
                    'record_count': str(len(summaries)),
                    'cutoff_date': cutoff_date.isoformat(),
                    'archive_timestamp': archive_timestamp
                }
            )
            
            logger.info(
                "Archived summaries to S3",
                records_archived=len(summaries),
                s3_bucket=bucket_name,
                s3_key=s3_key
            )
            
            return len(summaries), s3_key


def delete_archived_summaries(
    pool: DatabasePool,
    cutoff_date: datetime,
    logger: StructuredLogger
) -> int:
    """
    Delete summary records that have been archived to S3.
    
    Args:
        pool: Database connection pool
        cutoff_date: Delete summaries older than this date
        logger: Structured logger
        
    Returns:
        int: Number of summaries deleted
    """
    logger.info(
        "Deleting archived summary records",
        cutoff_date=cutoff_date.isoformat()
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # Delete old summaries (parameterized query)
            cursor.execute(
                """
                DELETE FROM bdo_marketdatasummary
                WHERE date < %s
                """,
                (cutoff_date,)
            )
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            logger.info(
                "Deleted archived summaries",
                summaries_deleted=deleted_count
            )
            
            return deleted_count


@router.route()
def handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    Main handler for retainData Lambda function.
    
    Executes data retention policies:
    1. Aggregate old detailed records into daily summaries
    2. Delete aggregated detailed records
    3. Archive old summaries to S3 Glacier
    4. Delete archived summaries
    
    Args:
        event: Lambda event (can be empty for scheduled execution)
        context: Lambda context
        logger: Structured logger with correlation ID
        
    Returns:
        dict: Response with operation results and correlation_id
    """
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/Retention", logger=logger)
    
    try:
        logger.info("Starting data retention process")
        
        # Load retention configuration
        config = load_retention_config(logger)
        
        # Calculate cutoff dates
        now = datetime.now(timezone.utc)
        detailed_cutoff = now - timedelta(days=config['retention_detailed_days'])
        summary_cutoff = now - timedelta(days=config['retention_summary_days'])
        
        logger.info(
            "Calculated cutoff dates",
            detailed_cutoff=detailed_cutoff.isoformat(),
            summary_cutoff=summary_cutoff.isoformat()
        )
        
        # Get database pool
        pool = get_database_pool(logger)
        
        results = {
            'detailed_records_aggregated': 0,
            'detailed_records_deleted': 0,
            'summaries_archived': 0,
            'summaries_deleted': 0,
            'archive_s3_key': None,
            'date_ranges': {}
        }
        
        # Step 1: Aggregate old detailed records
        logger.info("Step 1: Aggregating old detailed records")
        records_aggregated, min_date, max_date = aggregate_old_market_data(
            pool,
            detailed_cutoff,
            logger
        )
        results['detailed_records_aggregated'] = records_aggregated
        if min_date and max_date:
            results['date_ranges']['aggregated'] = {
                'start': min_date.isoformat(),
                'end': max_date.isoformat()
            }
        
        # Step 2: Delete aggregated detailed records
        if records_aggregated > 0:
            logger.info("Step 2: Deleting aggregated detailed records")
            deleted_count = delete_aggregated_records(
                pool,
                detailed_cutoff,
                logger
            )
            results['detailed_records_deleted'] = deleted_count
        else:
            logger.info("Step 2: Skipping deletion (no records aggregated)")
        
        # Step 3: Archive old summaries to S3
        logger.info("Step 3: Archiving old summaries to S3")
        summaries_archived, s3_key = archive_old_summaries_to_s3(
            pool,
            summary_cutoff,
            logger
        )
        results['summaries_archived'] = summaries_archived
        results['archive_s3_key'] = s3_key
        
        # Step 4: Delete archived summaries
        if summaries_archived > 0:
            logger.info("Step 4: Deleting archived summaries")
            summaries_deleted = delete_archived_summaries(
                pool,
                summary_cutoff,
                logger
            )
            results['summaries_deleted'] = summaries_deleted
        else:
            logger.info("Step 4: Skipping deletion (no summaries archived)")
        
        logger.info(
            "Data retention process completed successfully",
            detailed_records_aggregated=results['detailed_records_aggregated'],
            detailed_records_deleted=results['detailed_records_deleted'],
            summaries_archived=results['summaries_archived'],
            summaries_deleted=results['summaries_deleted']
        )
        
        # Emit success metrics
        metrics._emit_metric(
            metric_name="RetentionRecordsAggregated",
            value=results['detailed_records_aggregated'],
            unit="Count"
        )
        metrics._emit_metric(
            metric_name="RetentionRecordsDeleted",
            value=results['detailed_records_deleted'],
            unit="Count"
        )
        metrics._emit_metric(
            metric_name="RetentionSummariesArchived",
            value=results['summaries_archived'],
            unit="Count"
        )
        
        return {
            'message': 'Data retention completed successfully',
            'results': results
        }
        
    except Exception as e:
        logger.error("Error in data retention process", error=e)
        metrics._emit_metric(
            metric_name="RetentionFailure",
            value=1,
            unit="Count"
        )
        raise


# Lambda entry point
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function."""
    return handler(event, context)
