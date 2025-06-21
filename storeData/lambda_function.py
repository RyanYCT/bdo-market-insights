import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import execute_values

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


class LambdaRouter:
    """Router class to handle different types of Lambda events"""

    def __init__(self):
        self.api_routes = {}
        self.step_routes = {}
        self.default_headers = {"Content-Type": "application/json"}

    def api_route(self, method: str, path: str):
        """Decorator to register API Gateway routes"""
        route_key = f"{method}:{path}"

        def decorator(func):
            self.api_routes[route_key] = func
            return func

        return decorator

    def step_route(self, step_name: str):
        """Decorator to register Step Functions routes"""

        def decorator(func):
            self.step_routes[step_name] = func
            return func

        return decorator

    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Main handler that routes requests based on the event source"""
        logger.info(f"Event received: {json.dumps(event)}")

        # Determine the event source and route
        try:
            # API Gateway event
            if "httpMethod" in event or "requestContext" in event:
                return self._handle_api_gateway(event)

            # Step Functions event or default
            else:
                return self._handle_step_function(event)

        except Exception as e:
            logger.error(f"Error processing event: {e}")
            # For API Gateway, return HTTP error
            if "httpMethod" in event or "requestContext" in event:
                return {
                    "statusCode": 500,
                    "headers": self.default_headers,
                    "body": json.dumps({"error": str(e)}),
                }
            # For Step Functions, return plain error
            else:
                return {"error": str(e)}

    def _handle_api_gateway(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Handle API Gateway events by routing to the appropriate handler"""
        # Extract HTTP method and path from the event
        if "requestContext" in event and "http" in event["requestContext"]:
            # HTTP API format
            http_method = event["requestContext"]["http"]["method"]
            resource_path = event["requestContext"]["http"]["path"]
        else:
            # REST API format
            http_method = event.get("httpMethod")
            resource_path = event.get("resource", event.get("path", ""))

        # Create the route key
        route_key = f"{http_method}:{resource_path}"

        # Find the handler function
        handler_func = self.api_routes.get(route_key)

        if not handler_func:
            # Try to match parameterized routes
            for config_route, config_handler in self.api_routes.items():
                if self._is_route_match(config_route, route_key):
                    handler_func = config_handler
                    break

        if handler_func:
            return handler_func(event)
        else:
            logger.error(f"No handler found for route: {route_key}")
            return {
                "statusCode": 404,
                "headers": self.default_headers,
                "body": json.dumps({"error": f"Route not found: {route_key}"}),
            }

    def _handle_step_function(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Step Functions events by routing to the appropriate handler"""
        # Extract the step name from the event if available
        step_name = event.get("step", "default")

        # Find the handler function
        handler_func = self.step_routes.get(step_name)
        if handler_func:
            return handler_func(event)
        else:
            logger.error(f"Invalid step name: {step_name}")
            return {"error": f"Invalid step name: {step_name}"}

    def _is_route_match(self, config_route: str, actual_route: str) -> bool:
        """Check if a parameterized route matches the actual route"""
        # Split method and path
        config_method, config_path = config_route.split(":", 1)
        actual_method, actual_path = actual_route.split(":", 1)

        # Methods must match
        if config_method != actual_method:
            return False

        # Check path matching
        config_parts = config_path.split("/")
        actual_parts = actual_path.split("/")

        if len(config_parts) != len(actual_parts):
            return False

        for i, part in enumerate(config_parts):
            if "{" in part and "}" in part:
                # Skip parameter
                continue
            elif part != actual_parts[i]:
                return False

        return True


class StoreDataService:
    """Service class for storing data to PostgreSQL database"""

    def __init__(self):
        self.db_name = os.getenv("DB_NAME")
        self.db_user = os.getenv("DB_USER")
        self.db_password = os.getenv("DB_PASSWORD")
        self.db_host = os.getenv("DB_HOST")
        self.db_port = os.getenv("DB_PORT")

    def store_data(self, endpoint: str, scrape_time: Any, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not data:
            return {"message": "No data to store"}

        try:
            with psycopg2.connect(
                dbname=self.db_name,
                user=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
            ) as conn:
                with conn.cursor() as cursor:
                    # Insert MarketScrape
                    scrape_dt = datetime.fromtimestamp(scrape_time)
                    cursor.execute(
                        """
                        INSERT INTO bdo_marketscrape (endpoint, scrape_time)
                        VALUES (%s, %s)
                        ON CONFLICT (scrape_time) DO UPDATE SET endpoint=EXCLUDED.endpoint
                        RETURNING id
                        """,
                        (endpoint, scrape_dt),
                    )
                    scrape_id = cursor.fetchone()[0]

                    # Batch upsert items
                    item_rows = []
                    for item in data:
                        # Only set category_id = 5 for new item
                        item_rows.append((item["id"], item["sid"], item["name"], 5))
                    execute_values(
                        cursor,
                        """
                        INSERT INTO bdo_item (item_id, sid, name, category_id)
                        VALUES %s
                        ON CONFLICT (item_id, sid) DO UPDATE SET name=EXCLUDED.name
                        """,
                        item_rows,
                    )

                    # Fetch all item ids to build item_map
                    cursor.execute(
                        """
                        SELECT item_id, sid, id FROM bdo_item
                        WHERE (item_id, sid) IN %s
                        """,
                        (tuple((item["id"], item["sid"]) for item in data),),
                    )
                    # item_map = {(item_id, sid): id}
                    item_map = {}
                    rows = cursor.fetchall()
                    for row in rows:
                        key = (row[0], row[1])
                        value = row[2]
                        item_map[key] = value

                    # Prepare batch insert for MarketData
                    marketdata_rows = []
                    for item in data:
                        item_id = item["id"]
                        sid = item["sid"]
                        item_fk = item_map[(item_id, sid)]
                        current_stock = item["currentStock"]
                        total_trades = item["totalTrades"]
                        last_sold_price = item["lastSoldPrice"]
                        last_sold_time = datetime.fromtimestamp(item["lastSoldTime"])
                        marketdata_rows.append(
                            (
                                current_stock,
                                total_trades,
                                last_sold_price,
                                last_sold_time,
                                item_fk,
                                scrape_id,
                            )
                        )

                    # Batch insert MarketData with upsert
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
                        marketdata_rows,
                    )

                conn.commit()
                return {
                    "message": "Data stored successfully",
                    "insertedCount": len(marketdata_rows),
                    "scrape_id": scrape_id,
                }
        except Exception as e:
            logger.error(f"Error storing data: {e}")
            return {"error": f"Failed to store data: {str(e)}"}


# Initialize router
router = LambdaRouter()

# Initialize services
store_service = StoreDataService()


# Step Functions handler
@router.step_route("default")
def store_step(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process Step Functions"""
    try:
        # Get parameters from event
        endpoint = event.get("endpoint")
        scrape_time = event.get("scrapeTime")
        data = event.get("data", [])

        # Validate required parameters
        missing_params = []
        if not endpoint:
            missing_params.append("endpoint")
        if not scrape_time:
            missing_params.append("scrapeTime")
        if not data:
            missing_params.append("data")
        if missing_params:
            return {"error": f"Missing parameters: {', '.join(missing_params)}"}

        # Store data in PostgreSQL database
        result = store_service.store_data(endpoint, scrape_time, data)
        if "error" in result:
            return result
        return {
            "endpoint": endpoint,
            "scrapeTime": scrape_time,
            "status": "success",
            "insertedCount": result.get("insertedCount", 0),
            "scrape_id": result.get("scrape_id"),
        }
    except Exception as e:
        logger.error(f"Error processing Step Functions task: {e}")
        return {"error": str(e)}


# Lambda handler function
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function"""
    return router.handle(event, context)
