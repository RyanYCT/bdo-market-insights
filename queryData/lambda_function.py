import datetime
import json
import logging
import os
from typing import Any, Dict

import psycopg2
from psycopg2 import sql

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


class QueryDataService:
    """Service class for querying data from PostgreSQL"""

    def __init__(self):
        self.db_name = os.getenv("DB_NAME")
        self.db_user = os.getenv("DB_USER")
        self.db_password = os.getenv("DB_PASSWORD")
        self.db_host = os.getenv("DB_HOST")
        self.db_port = os.getenv("DB_PORT")

    def serialize_result(self, result):
        """Convert datetime object in result to string"""
        if not result:
            return []

        serialized_result = []
        for row in result:
            new_row = []
            for value in row:
                if value is None:
                    new_row.append(None)
                elif isinstance(value, datetime.datetime):
                    new_row.append(value.isoformat())
                else:
                    new_row.append(value)
            serialized_result.append(new_row)
        return serialized_result

    def query_data(self, query: sql.SQL, params):
        try:
            with psycopg2.connect(
                dbname=self.db_name,
                user=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    result = cursor.fetchall()
                    serialized_result = self.serialize_result(result)
                    return serialized_result

        except Exception as e:
            logger.error(f"Error querying data: {e}")
            return {"error": f"Failed to query data: {str(e)}"}


class ConstructQueryService:
    """Service class for constructing SQL statement"""

    def __init__(self, table_map: Dict[str, str], column_map: Dict[str, tuple]):
        # SQL identifiers
        self.table_map = table_map
        self.column_map = column_map
        self.tables = self._generate_tables(table_map)
        self.columns = self._generate_columns(column_map)

    def _generate_tables(self, table_map: Dict[str, str]) -> Dict[str, sql.Identifier]:
        """Generate SQL identifiers for tables"""
        # table_map = {key: table_name,}
        tables = {}
        for key in table_map:
            value = table_map[key]
            tables[key] = sql.Identifier(value)
        return tables

    def _generate_columns(self, column_map: Dict[str, tuple]) -> sql.Composed:
        """Generate comma separated SQL column expressions"""
        # column_map = {key:  ("table_name_abbr", "column_name", "alias"),}
        columns = []
        for key in column_map:
            table_abbr, column, alias = column_map[key]
            column = sql.SQL(f"{table_abbr}.") + sql.Identifier(column)
            if alias:
                column += sql.SQL(f" AS {alias}")
            columns.append(column)
        return sql.SQL(", ").join(columns)

    def construct_query(self, report_type, item_category, item_id) -> Dict:
        item_table = self.tables["item_table"]
        item_category_table = self.tables["item_category_table"]
        market_data_table = self.tables["market_data_table"]
        market_scrape_table = self.tables["market_scrape_table"]
        columns = self.columns

        # Construct query
        query = sql.SQL("")
        params = []

        # Missing parameters should be catched in upstream
        # Case 1: report type is not provided, missing parameter
        if not report_type:
            logger.error("Report type is required.")
        # Case 2: Both item category and item id are not provided, missing parameter
        if not item_category and not item_id:
            logger.error("At least one of item_category or item_id must be provided.")
        # Case 3: Only category is provided, default use case
        if item_category and not item_id:
            query = sql.SQL(
                """
                WITH latestscrape AS (
                    SELECT id AS scrape_id
                    FROM {market_scrape_table}
                    ORDER BY scrape_time DESC
                    LIMIT 1
                )
                SELECT {columns}
                FROM {item_table} i
                JOIN {item_category_table} ic ON i.category_id = ic.id    -- for category name
                JOIN {market_data_table} md ON i.id = md.item_id          -- for latest market numbers
                JOIN latestscrape ls ON md.scrape_id = ls.scrape_id       -- for latest scrape id
                JOIN {market_scrape_table} ms ON ls.scrape_id = ms.id     -- for scrape timestamp
                WHERE ic.name = %s
                ORDER BY i.item_id, i.sid;
                """
            ).format(
                item_table=item_table,
                item_category_table=item_category_table,
                market_data_table=market_data_table,
                market_scrape_table=market_scrape_table,
                columns=self.columns,
            )
            params = [item_category]

        # Case 4: Both category and item_id are provided
        elif item_category and item_id:
            query = sql.SQL(
                """
                WITH latestscrape AS (
                    SELECT id AS scrape_id
                    FROM {market_scrape_table}
                    ORDER BY scrape_time DESC
                    LIMIT 1
                )
                SELECT {columns}
                FROM {item_table} i
                JOIN {item_category_table} ic ON i.category_id = ic.id    -- for category name
                JOIN {market_data_table} md ON i.id = md.item_id          -- for latest market numbers
                JOIN latestscrape ls ON md.scrape_id = ls.scrape_id       -- for latest scrape id
                JOIN {market_scrape_table} ms ON ls.scrape_id = ms.id     -- for scrape timestamp
                WHERE i.item_id = %s
                ORDER BY i.sid;
                """
            ).format(
                item_table=item_table,
                item_category_table=item_category_table,
                market_data_table=market_data_table,
                market_scrape_table=market_scrape_table,
                columns=columns,
            )
            params = [item_id]

        return {
            "query": query,
            "params": params,
        }


# Initialize router
router = LambdaRouter()

# Initialize service
query_data_service = QueryDataService()


# Step Functions handler
@router.step_route("default")
def retrieve_step(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process Step Functions"""
    try:
        # Get parameters from Step Functions input event object
        report_type = event.get("reportType")
        item_category = event.get("itemCategory")
        item_id = event.get("itemID")
        # interval_day = report_details.get("intervalDay")
        table_map = event.get("tableMap", {})
        column_map_raw = event.get("columnMap", {})

        # Normalize parameter, remove null value
        # key: [table, column, alias],
        # key: [table, column, null]
        column_map = {}
        for key in column_map_raw:
            value = column_map_raw[key]
            column_map[key] = tuple(value)

        # Validate required parameters based on report type
        missing_params = []
        if not report_type:
            missing_params.append("report_type")
        if report_type and not item_category:
            missing_params.append("item_category")
        if missing_params:
            return {"error": f"Missing parameters: {', '.join(missing_params)}"}

        # Initialize construct query service
        construct_query_service = ConstructQueryService(table_map, column_map)

        statement = construct_query_service.construct_query(report_type, item_category, item_id)
        query = statement["query"]
        params = statement["params"]
        result = query_data_service.query_data(query, params)

        return {
            "reportType": report_type,
            "itemCategory": item_category,
            "itemID": item_id,
            "columns": list(column_map.keys()),
            "resultSet": result,
        }

    except ValueError as e:
        logger.error(f"Invalid parameters: {e}")
        return {"error": f"Invalid parameters: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected errors: {e}")
        return {"error": str(e)}


# Lambda handler function
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function"""
    return router.handle(event, context)
