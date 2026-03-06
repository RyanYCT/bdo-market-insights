"""
storeData Lambda function - Stores cleaned market data to PostgreSQL.

Integrates with:
- LambdaRouter for consistent request/response handling
- StructuredLogger for JSON logging with correlation IDs
- DatabasePool for PostgreSQL connection pooling
- Pydantic schemas for input validation

Implements:
- Create or get MarketScrape record
- Create or get Item records with category lookup
- Bulk insert MarketData records
- Parameterized queries for SQL injection prevention
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add lambda_layer to path
sys.path.insert(0, '/opt/python')

from common.router import LambdaRouter
from common.database import DatabasePool
from common.schemas import StoreDataInput, MarketDataRecord
from common.logging import StructuredLogger
from common.metrics import MetricsClient
from pydantic import ValidationError


# Initialize router
router = LambdaRouter()

# Initialize database pool (lazy initialization on first use)
db_pool: Optional[DatabasePool] = None


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


def get_or_create_market_scrape(
    pool: DatabasePool,
    endpoint: str,
    scrape_time: datetime,
    logger: StructuredLogger
) -> int:
    """
    Create or get MarketScrape record.
    
    Uses parameterized query with ON CONFLICT to handle duplicates.
    
    Args:
        pool: Database connection pool
        endpoint: API endpoint used for scraping
        scrape_time: Timestamp of scrape session
        logger: Structured logger
        
    Returns:
        int: MarketScrape ID
    """
    logger.debug(
        "Creating or getting MarketScrape record",
        endpoint=endpoint,
        scrape_time=scrape_time.isoformat()
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # Parameterized query prevents SQL injection
            cursor.execute(
                """
                INSERT INTO bdo_marketscrape (endpoint, scrape_time)
                VALUES (%s, %s)
                ON CONFLICT (scrape_time) DO UPDATE SET endpoint=EXCLUDED.endpoint
                RETURNING id
                """,
                (endpoint, scrape_time)
            )
            
            scrape_id = cursor.fetchone()[0]
            conn.commit()
            
            logger.debug("MarketScrape record created/retrieved", scrape_id=scrape_id)
            
            return scrape_id


def get_or_create_items(
    pool: DatabasePool,
    records: List[MarketDataRecord],
    category_id: int,
    logger: StructuredLogger
) -> Dict[tuple, int]:
    """
    Create or get Item records with category lookup.
    
    Uses bulk upsert with parameterized queries for efficiency and security.
    
    Args:
        pool: Database connection pool
        records: List of market data records
        category_id: Category ID for items (default: 5 for Uncategorized)
        logger: Structured logger
        
    Returns:
        dict: Mapping of (item_id, sid) -> database item ID
    """
    logger.debug(
        "Creating or getting Item records",
        num_items=len(records),
        category_id=category_id
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            # Prepare item data for bulk upsert
            # Use parameterized query with execute_values for security
            from psycopg2.extras import execute_values
            
            item_rows = [
                (record.item_id, record.sid, f"Item_{record.item_id}", category_id)
                for record in records
            ]
            
            # Bulk upsert items (parameterized)
            execute_values(
                cursor,
                """
                INSERT INTO bdo_item (item_id, sid, name, category_id)
                VALUES %s
                ON CONFLICT (item_id, sid) DO UPDATE SET name=EXCLUDED.name
                """,
                item_rows
            )
            
            # Fetch item IDs (parameterized query)
            item_keys = [(record.item_id, record.sid) for record in records]
            cursor.execute(
                """
                SELECT item_id, sid, id FROM bdo_item
                WHERE (item_id, sid) IN %s
                """,
                (tuple(item_keys),)
            )
            
            # Build mapping
            item_map = {}
            for row in cursor.fetchall():
                key = (row[0], row[1])
                item_map[key] = row[2]
            
            conn.commit()
            
            logger.debug(
                "Item records created/retrieved",
                num_items=len(item_map)
            )
            
            return item_map


def bulk_insert_market_data(
    pool: DatabasePool,
    records: List[MarketDataRecord],
    item_map: Dict[tuple, int],
    scrape_id: int,
    logger: StructuredLogger
) -> int:
    """
    Bulk insert MarketData records.
    
    Uses parameterized batch insert with ON CONFLICT for upsert behavior.
    
    Args:
        pool: Database connection pool
        records: List of market data records
        item_map: Mapping of (item_id, sid) -> database item ID
        scrape_id: MarketScrape ID
        logger: Structured logger
        
    Returns:
        int: Number of records inserted
    """
    logger.debug(
        "Bulk inserting MarketData records",
        num_records=len(records),
        scrape_id=scrape_id
    )
    
    with pool.get_connection() as conn:
        with conn.cursor() as cursor:
            from psycopg2.extras import execute_values
            
            # Prepare market data rows (parameterized)
            marketdata_rows = []
            for record in records:
                item_fk = item_map.get((record.item_id, record.sid))
                if item_fk is None:
                    logger.warning(
                        "Item not found in map, skipping record",
                        item_id=record.item_id,
                        sid=record.sid
                    )
                    continue
                
                marketdata_rows.append((
                    record.current_stock,
                    record.total_trades,
                    record.last_sold_price,
                    record.last_sold_time,
                    item_fk,
                    scrape_id
                ))
            
            if not marketdata_rows:
                logger.warning("No market data rows to insert")
                return 0
            
            # Bulk insert with upsert (parameterized)
            execute_values(
                cursor,
                """
                INSERT INTO bdo_marketdata
                (current_stock, total_trades, last_sold_price, last_sold_time, item_id, scrape_id)
                VALUES %s
                ON CONFLICT (item_id, scrape_id) DO UPDATE SET
                    current_stock=EXCLUDED.current_stock,
                    total_trades=EXCLUDED.total_trades,
                    last_sold_price=EXCLUDED.last_sold_price,
                    last_sold_time=EXCLUDED.last_sold_time
                """,
                marketdata_rows
            )
            
            affected_rows = cursor.rowcount
            conn.commit()
            
            logger.info(
                "MarketData records inserted",
                records_inserted=affected_rows
            )
            
            return affected_rows


@router.route()
def handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    Main handler for storeData Lambda function.
    
    Validates input, stores data to PostgreSQL using connection pooling,
    and returns result with correlation ID.
    
    Args:
        event: Lambda event containing cleaned_data, scrape_endpoint, scrape_time
        context: Lambda context
        logger: Structured logger with correlation ID
        
    Returns:
        dict: Response with records_inserted, scrape_id, correlation_id
    """
    from pydantic import ValidationError as PydanticValidationError
    
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/ETL", logger=logger)
    
    try:
        # Validate input using Pydantic schema
        input_data = StoreDataInput(**event)
        
        logger.info(
            "Processing storeData request",
            num_records=len(input_data.cleaned_data),
            scrape_endpoint=input_data.scrape_endpoint
        )
        
        # Handle empty data
        if not input_data.cleaned_data:
            logger.info("No data to store")
            metrics.emit_etl_success(
                function_name="storeData",
                records_inserted=0
            )
            return {
                'message': 'No data to store',
                'records_inserted': 0,
                'correlation_id': input_data.correlation_id
            }
        
        # Track execution latency
        with metrics.track_latency("storeData"):
            # Get database pool
            pool = get_database_pool(logger)
            
            # Emit database pool utilization metrics
            if hasattr(pool, 'get_pool_stats'):
                stats = pool.get_pool_stats()
                metrics.emit_db_pool_utilization(
                    pool_size=stats.get('pool_size', 0),
                    active_connections=stats.get('active', 0),
                    idle_connections=stats.get('idle', 0)
                )
            
            # Get configurable category_id (default: 5 for Uncategorized)
            category_id = int(os.getenv('CATEGORY_ID', '5'))
            
            # Create or get MarketScrape record
            scrape_id = get_or_create_market_scrape(
                pool,
                input_data.scrape_endpoint,
                input_data.scrape_time,
                logger
            )
            
            # Create or get Item records
            item_map = get_or_create_items(
                pool,
                input_data.cleaned_data,
                category_id,
                logger
            )
            
            # Bulk insert MarketData records
            records_inserted = bulk_insert_market_data(
                pool,
                input_data.cleaned_data,
                item_map,
                scrape_id,
                logger
            )
            
            logger.info(
                "storeData completed successfully",
                records_inserted=records_inserted,
                scrape_id=scrape_id
            )
            
            # Emit success metric
            metrics.emit_etl_success(
                function_name="storeData",
                records_inserted=records_inserted
            )
            
            return {
                'message': 'Data stored successfully',
                'records_inserted': records_inserted,
                'scrape_id': scrape_id,
                'correlation_id': input_data.correlation_id
            }
        
    except PydanticValidationError as e:
        logger.error("Input validation failed", error=e)
        metrics.emit_etl_failure(
            function_name="storeData",
            error_type="ValidationError"
        )
        # Re-raise as router's ValidationError for proper 400 response
        from common.router import ValidationError
        raise ValidationError(str(e))
    except Exception as e:
        logger.error("Error storing data", error=e)
        metrics.emit_etl_failure(
            function_name="storeData",
            error_type=type(e).__name__
        )
        raise


# Lambda entry point
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function."""
    return handler(event, context)
