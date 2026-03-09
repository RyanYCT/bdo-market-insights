"""
retrieveIdList Lambda Function

Retrieves item IDs from DynamoDB for the ETL pipeline.
Integrates with the new architecture using LambdaRouter, structured logging,
and Pydantic schema validation. Includes X-Ray tracing for observability.
"""

import os
from typing import Any, Dict, List
import boto3
from botocore.exceptions import ClientError
from common import (
    LambdaRouter,
    ItemIdList,
    MetricsClient,
    create_subsegment,
    add_annotation,
    add_metadata,
)
from pydantic import ValidationError


class DynamoDBService:
    """Service class for retrieving item IDs from DynamoDB."""
    
    def __init__(self, logger):
        """
        Initialize DynamoDB service.
        
        Args:
            logger: StructuredLogger instance for logging
        """
        self.dynamodb = boto3.resource("dynamodb")
        self.logger = logger
    
    def get_item_ids(self, table_name: str) -> List[int]:
        """
        Retrieve all item IDs from DynamoDB table.
        
        Args:
            table_name: Name of the DynamoDB table
            
        Returns:
            List[int]: List of item IDs
            
        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If table is empty or data is invalid
        """
        self.logger.info(f"Retrieving item IDs from DynamoDB table", table_name=table_name)
        
        # Create X-Ray subsegment for DynamoDB operation
        with create_subsegment('DynamoDB.Scan'):
            try:
                table = self.dynamodb.Table(table_name)
                
                # Add X-Ray annotations for filtering
                add_annotation('table_name', table_name)
                add_annotation('operation', 'scan')
                
                # Scan the entire table with projection
                scan_params = {"ProjectionExpression": "id"}
                response = table.scan(**scan_params)
                items = response.get("Items", [])
                
                # Handle pagination
                page_count = 1
                while "LastEvaluatedKey" in response:
                    self.logger.debug("Fetching next page of results", 
                                     last_key=str(response["LastEvaluatedKey"]))
                    scan_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = table.scan(**scan_params)
                    items.extend(response.get("Items", []))
                    page_count += 1
                
                # Add X-Ray metadata
                add_metadata('page_count', page_count, 'dynamodb')
                add_metadata('total_items', len(items), 'dynamodb')
                
                # Extract and convert item IDs
                item_ids = []
                for item in items:
                    if "id" not in item:
                        self.logger.warning("Item missing 'id' field", item=str(item))
                        continue
                    # Convert Decimal to int
                    item_ids.append(int(item["id"]))
                
                if not item_ids:
                    raise ValueError(f"No item IDs found in table {table_name}")
                
                self.logger.info(f"Successfully retrieved item IDs", 
                               item_count=len(item_ids),
                               table_name=table_name)
                
                return item_ids
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                self.logger.error(f"DynamoDB ClientError: {error_code}", 
                                error=e,
                                table_name=table_name)
                raise
            except Exception as e:
                self.logger.error("Unexpected error retrieving item IDs", 
                                error=e,
                                table_name=table_name)
                raise


# Initialize router
router = LambdaRouter()


@router.route(function_name="retrieveIdList")
def handler(event: Dict[str, Any], context: Any, logger) -> Dict[str, Any]:
    """
    Lambda handler for retrieveIdList.
    
    Retrieves item IDs from DynamoDB and returns them in a validated format.
    Supports multiple table names separated by commas.
    
    Args:
        event: Lambda event (can be from Step Functions or direct invocation)
        context: Lambda context
        logger: StructuredLogger instance
        
    Returns:
        dict: ItemIdList schema with item_ids and correlation_id
        
    Raises:
        ValidationError: If output validation fails
        ValueError: If required parameters are missing or table is empty
    """
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/ETL", logger=logger)
    
    # Add X-Ray annotations for the Lambda function
    add_annotation('function_name', 'retrieveIdList')
    add_annotation('correlation_id', logger.correlation_id)
    
    try:
        # Get table name(s) from event or environment variable
        table_name_input = event.get("table_name") or os.getenv("DYNAMODB_TABLE_NAME")
        
        if not table_name_input:
            raise ValueError("table_name must be provided in event or DYNAMODB_TABLE_NAME environment variable")
        
        # Parse table names (support comma-separated list)
        table_names = [name.strip() for name in table_name_input.split(",") if name.strip()]
        
        if not table_names:
            raise ValueError("No valid table names provided")
        
        logger.info("Starting retrieveIdList execution", 
                   table_names=table_names,
                   table_count=len(table_names))
        
        # Add X-Ray annotation for table count
        add_annotation('table_count', len(table_names))
        
        # Track execution latency
        with metrics.track_latency("retrieveIdList"):
            # Initialize DynamoDB service
            dynamodb_service = DynamoDBService(logger)
            
            # Retrieve item IDs from all tables
            all_item_ids = []
            table_stats = {}
            
            for table_name in table_names:
                logger.info(f"Processing table", table_name=table_name)
                
                try:
                    # Retrieve item IDs from current table
                    item_ids = dynamodb_service.get_item_ids(table_name)
                    all_item_ids.extend(item_ids)
                    table_stats[table_name] = len(item_ids)
                    
                    logger.info(f"Retrieved IDs from table", 
                               table_name=table_name,
                               item_count=len(item_ids))
                    
                except Exception as e:
                    logger.error(f"Failed to retrieve IDs from table",
                               table_name=table_name,
                               error=e)
                    # Re-raise to fail the entire operation
                    raise
            
            # Remove duplicates while preserving order
            unique_item_ids = list(dict.fromkeys(all_item_ids))
            
            logger.info("Combined item IDs from all tables",
                       total_items=len(all_item_ids),
                       unique_items=len(unique_item_ids),
                       duplicates_removed=len(all_item_ids) - len(unique_item_ids),
                       table_stats=table_stats)
            
            # Validate output using Pydantic schema
            try:
                output = ItemIdList(
                    item_ids=unique_item_ids,
                    correlation_id=logger.correlation_id
                )
                
                logger.info("Output validation successful", 
                           item_count=len(output.item_ids))
                
                # Add X-Ray metadata
                add_metadata('output', {
                    'item_count': len(output.item_ids),
                    'table_names': table_names,
                    'table_stats': table_stats,
                    'duplicates_removed': len(all_item_ids) - len(unique_item_ids)
                }, 'retrieveIdList')
                
                # Emit success metric
                metrics.emit_etl_success(
                    function_name="retrieveIdList",
                    item_count=len(output.item_ids)
                )
                
                # Return as dict for Lambda response
                return output.model_dump()
                
            except ValidationError as e:
                logger.error("Output validation failed", error=e)
                metrics.emit_etl_failure(
                    function_name="retrieveIdList",
                    error_type="ValidationError"
                )
                raise
    
    except Exception as e:
        # Emit failure metric
        error_type = type(e).__name__
        metrics.emit_etl_failure(
            function_name="retrieveIdList",
            error_type=error_type
        )
        raise


# Lambda entry point
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler function.
    
    Entry point for Lambda invocations. Routes to the main handler
    through LambdaRouter for consistent error handling and logging.
    """
    return handler(event, context)
