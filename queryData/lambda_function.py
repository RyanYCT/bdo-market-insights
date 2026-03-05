"""
queryData Lambda function for BDO Market Insights.

Retrieves market data from PostgreSQL based on query parameters with:
- LambdaRouter integration for consistent request handling
- Structured logging with correlation IDs
- Pydantic schema validation for inputs
- DatabasePool for connection pooling
- Dynamic SQL query building with parameterization
"""

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Add lambda_layer to path
sys.path.insert(0, '/opt/python')

from common.router import LambdaRouter
from common.schemas import QueryRequest
from common.database import DatabasePool
from common.logging import StructuredLogger

# Environment variables
DB_SECRET_NAME = os.getenv('DB_SECRET_NAME', 'bdo-db-credentials')
DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '5'))

# Global database pool (reused across Lambda invocations)
_db_pool: Optional[DatabasePool] = None


def get_database_pool(logger: Optional[StructuredLogger] = None) -> DatabasePool:
    """
    Get or create the global database pool.
    
    Reuses pool across Lambda invocations for connection efficiency.
    
    Args:
        logger: Optional StructuredLogger instance
        
    Returns:
        DatabasePool: Configured database pool
    """
    global _db_pool
    
    if _db_pool is None:
        _db_pool = DatabasePool(
            secret_name=DB_SECRET_NAME,
            pool_size=DB_POOL_SIZE,
            logger=logger
        )
    
    return _db_pool


def build_query(
    item_id: Optional[int] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100
) -> Tuple[str, List[Any]]:
    """
    Build dynamic SQL query based on parameters.
    
    Constructs parameterized query with:
    - JOIN between MarketData, Item, MarketScrape, and ItemCategory tables
    - WHERE clauses based on provided filters
    - Date range filtering on last_sold_time
    - LIMIT and ORDER BY clauses
    
    Args:
        item_id: Specific item ID to query (optional)
        name: Item name to search (optional, uses ILIKE for partial match)
        category: Category filter (optional)
        start_date: Start of date range for last_sold_time
        end_date: End of date range for last_sold_time
        limit: Maximum number of results (default: 100)
        
    Returns:
        tuple: (query_string, parameters_list)
    """
    # Base SELECT with JOINs
    query = """
        SELECT 
            i.item_id,
            i.name AS item_name,
            i.sid,
            ic.name AS category,
            md.current_stock,
            md.total_trades,
            md.last_sold_price,
            md.last_sold_time,
            ms.scrape_time
        FROM bdo_marketdata md
        JOIN bdo_item i ON md.item_id = i.id
        JOIN bdo_marketscrape ms ON md.scrape_id = ms.id
        JOIN bdo_itemcategory ic ON i.category_id = ic.id
    """
    
    # Build WHERE clauses
    where_clauses = []
    params = []
    
    if item_id is not None:
        where_clauses.append("i.item_id = %s")
        params.append(item_id)
    
    if name is not None:
        where_clauses.append("i.name ILIKE %s")
        params.append(f"%{name}%")
    
    if category is not None:
        where_clauses.append("ic.name = %s")
        params.append(category)
    
    if start_date is not None:
        where_clauses.append("md.last_sold_time >= %s")
        params.append(start_date)
    
    if end_date is not None:
        where_clauses.append("md.last_sold_time <= %s")
        params.append(end_date)
    
    # Add WHERE clause if any filters
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    
    # Add ORDER BY and LIMIT
    query += " ORDER BY md.last_sold_time DESC, i.item_id, i.sid LIMIT %s"
    params.append(limit)
    
    return query, params


def handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    Query market data from PostgreSQL.
    
    Validates input parameters, builds dynamic SQL query, executes query
    using connection pool, and returns results.
    
    Args:
        event: Lambda event containing query parameters
        context: Lambda context
        logger: StructuredLogger instance
        
    Returns:
        dict: Query results with data and metadata
        
    Raises:
        ValidationError: If input validation fails
        DatabasePoolError: If database query fails
    """
    logger.info("Starting queryData handler", event_keys=list(event.keys()))
    
    # Validate input with Pydantic schema
    try:
        query_request = QueryRequest(**event)
        logger.info(
            "Query parameters validated",
            item_id=query_request.item_id,
            item_name=query_request.name,
            category=query_request.category,
            start_date=query_request.start_date.isoformat(),
            end_date=query_request.end_date.isoformat(),
            limit=query_request.limit
        )
    except Exception as e:
        logger.error("Query parameter validation failed", error=e)
        raise
    
    # Get database pool
    pool = get_database_pool(logger)
    
    # Build query
    query, params = build_query(
        item_id=query_request.item_id,
        name=query_request.name,
        category=query_request.category,
        start_date=query_request.start_date,
        end_date=query_request.end_date,
        limit=query_request.limit
    )
    
    logger.info(
        "Executing database query",
        query_preview=query[:200],
        param_count=len(params)
    )
    
    # Execute query
    try:
        results = pool.execute_query(query, tuple(params))
        
        logger.info(
            "Query executed successfully",
            result_count=len(results)
        )
        
        # Convert datetime objects to ISO format strings
        for row in results:
            if 'last_sold_time' in row and row['last_sold_time']:
                row['last_sold_time'] = row['last_sold_time'].isoformat()
            if 'scrape_time' in row and row['scrape_time']:
                row['scrape_time'] = row['scrape_time'].isoformat()
        
        return {
            'data': results,
            'count': len(results),
            'query_params': {
                'item_id': query_request.item_id,
                'name': query_request.name,
                'category': query_request.category,
                'start_date': query_request.start_date.isoformat(),
                'end_date': query_request.end_date.isoformat(),
                'limit': query_request.limit
            }
        }
        
    except Exception as e:
        logger.error("Database query failed", error=e)
        raise


# Initialize router
router = LambdaRouter()


@router.route()
def lambda_handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    AWS Lambda handler function.
    
    Decorated with LambdaRouter for consistent request/response handling,
    structured logging, and error handling.
    
    Args:
        event: Lambda event
        context: Lambda context
        logger: StructuredLogger instance (injected by router)
        
    Returns:
        dict: Formatted Lambda response
    """
    return handler(event, context, logger)

