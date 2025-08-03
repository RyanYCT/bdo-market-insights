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

    def serialize_result(self, cursor, result):
        """Convert result to list of dicts with column names as keys"""
        if not result:
            return []

        colnames = [desc[0] for desc in cursor.description]
        serialized_result = []
        for row in result:
            row_dict = {}
            for col, value in zip(colnames, row):
                if isinstance(value, datetime.datetime):
                    row_dict[col] = value.isoformat()
                else:
                    row_dict[col] = value
            serialized_result.append(row_dict)
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
                    serialized_result = self.serialize_result(cursor, result)
                    return serialized_result

        except Exception as e:
            logger.error(f"Error querying data: {e}")
            return {"error": f"Failed to query data: {str(e)}"}


class ConstructQueryService:
    """Service class for constructing SQL statement"""

    # Missing parameters should be catched in upstream

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
        # column_map = {key: ("table_name_abbr", "column_name", "column_alias"),}
        columns = []
        for key in column_map:
            table_abbr, column, _ = column_map[key]
            col_expr = sql.SQL(f"{table_abbr}.") + sql.Identifier(column) + sql.SQL(" AS ") + sql.Identifier(key)
            columns.append(col_expr)
        return sql.SQL(", ").join(columns)

    def construct_query(self, item_category, item_id, item_sid, interval_day) -> Dict:
        tables = self.tables
        columns = self.columns

        # Clause fragments
        select_from_join = sql.SQL(
            """
            SELECT {columns}
            FROM {item_table} i
            JOIN {item_category_table} ic ON i.category_id = ic.id
            JOIN {market_data_table} md ON i.id = md.item_id
            """
        ).format(
            columns=columns,
            item_table=tables["item_table"],
            item_category_table=tables["item_category_table"],
            market_data_table=tables["market_data_table"],
        )

        with_latest = sql.SQL(
            """
            WITH latest_scrape AS (
                SELECT id AS scrape_id
                FROM {market_scrape_table}
                ORDER BY scrape_time DESC
                LIMIT 1
            )
            """
        ).format(market_scrape_table=tables["market_scrape_table"])

        join_latest = sql.SQL(
            """
            JOIN latest_scrape ls ON md.scrape_id = ls.scrape_id
            JOIN {market_scrape_table} ms ON ls.scrape_id = ms.id
            """
        ).format(market_scrape_table=tables["market_scrape_table"])

        with_ranked = sql.SQL(
            """
            WITH ranked_scrapes AS (
                SELECT
                    ms.id AS scrape_id,
                    ms.scrape_time::date AS scrape_date,
                    ms.scrape_time,
                    ROW_NUMBER() OVER (
                        PARTITION BY ms.scrape_time::date
                        ORDER BY ms.scrape_time DESC
                    ) AS rn
                FROM {market_scrape_table} ms
                WHERE ms.scrape_time::date >= (CURRENT_DATE - INTERVAL %s)
            ),
            last_n_days AS (
                SELECT scrape_id, scrape_date
                FROM ranked_scrapes
                WHERE rn = 1
                ORDER BY scrape_date DESC
                LIMIT %s
            )
            """
        ).format(market_scrape_table=tables["market_scrape_table"])

        join_last_n = sql.SQL(
            """
            JOIN last_n_days lnd ON md.scrape_id = lnd.scrape_id
            JOIN {market_scrape_table} ms ON lnd.scrape_id = ms.id
            """
        ).format(market_scrape_table=tables["market_scrape_table"])

        # Compose by case
        # In simple terms, cases depending on item_id and interval_day is provided or not.

        # Case 1: Both item_id and interval_day are not provided -> All level of all items in current hour
        if item_category and not item_id and not interval_day:
            where = sql.SQL(
                """
                WHERE ic.name = %s
                {item_sid_filter}
                """
            ).format(item_sid_filter=sql.SQL("AND i.sid = %s") if item_sid else sql.SQL(""))
            order = sql.SQL("ORDER BY i.item_id, i.sid;")
            query = with_latest + select_from_join + join_latest + where + order
            params = [item_category]
            if item_sid:
                params.append(item_sid)
            return {"query": query, "params": params}

        # Case 2: item_id is provided but interval_day is not -> All level of an item in current hour
        elif item_category and item_id and not interval_day:
            where = sql.SQL(
                """
                WHERE i.item_id = %s
                {item_sid_filter}
                """
            ).format(item_sid_filter=sql.SQL("AND i.sid = %s") if item_sid else sql.SQL(""))
            order = sql.SQL("ORDER BY i.sid;")
            query = with_latest + select_from_join + join_latest + where + order
            params = [item_id]
            if item_sid:
                params.append(item_sid)
            return {"query": query, "params": params}

        # Case 3: Both item_id and interval_day are provided -> All level of an item in n days at closing
        elif item_category and item_id and interval_day:
            where = sql.SQL(
                """
                WHERE i.item_id = %s AND ic.name = %s
                {item_sid_filter}
                """
            ).format(item_sid_filter=sql.SQL("AND i.sid = %s") if item_sid else sql.SQL(""))
            order = sql.SQL("ORDER BY lnd.scrape_date DESC, i.sid;")
            query = with_ranked + select_from_join + join_last_n + where + order
            params = [f"{interval_day} DAY", interval_day, item_id, item_category]
            if item_sid:
                params.append(item_sid)
            return {"query": query, "params": params}

        # Case 4: item_id is not provided but interval_day is -> All level of all items in n days at closing
        elif item_category and not item_id and interval_day:
            where = sql.SQL(
                """
                WHERE ic.name = %s
                {item_sid_filter}
                """
            ).format(item_sid_filter=sql.SQL("AND i.sid = %s") if item_sid else sql.SQL(""))
            order = sql.SQL("ORDER BY lnd.scrape_date DESC, i.sid;")
            query = with_ranked + select_from_join + join_last_n + where + order
            params = [f"{interval_day} DAY", interval_day, item_category]
            if item_sid:
                params.append(item_sid)
            return {"query": query, "params": params}

        logger.error("Invalid parameter combination.")
        return {"query": None, "params": []}


# Initialize router
router = LambdaRouter()

# Initialize service
query_data_service = QueryDataService()

TABLE_MAP = {
    "item_table": os.getenv("ITEM_TABLE"),
    "item_category_table": os.getenv("ITEM_CATEGORY_TABLE"),
    "market_data_table": os.getenv("MARKET_DATA_TABLE"),
    "market_scrape_table": os.getenv("MARKET_SCRAPE_TABLE")
}

COLUMN_MAP = {
    # key: ("table_name_abbr", "column_name", "column_alias"),
    "item_name": ("i", os.getenv("ITEM_NAME"), "item_name"),
    "item_id": ("i", os.getenv("ITEM_ID"), None),
    "sid": ("i", os.getenv("SID"), None),
    "category": ("ic", os.getenv("CATEGORY"), "category"),
    "last_sold_price": ("md", os.getenv("LAST_SOLD_PRICE"), None),
    "current_stock": ("md", os.getenv("CURRENT_STOCK"), None),
    "total_trades": ("md", os.getenv("TOTAL_TRADES"), None),
    "scrape_time": ("ms", os.getenv("SCRAPE_TIME"), None),
}

# Step Functions handler
@router.step_route("default")
def retrieve_step(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process Step Functions"""
    try:
        # Get parameters from Step Functions input event object
        item_category = event.get("itemCategory")  # required
        item_id = event.get("itemID")  # optional
        item_sid = event.get("itemSID")  # optional
        interval_day = event.get("intervalDay")  # optional

        # Validate required parameters based on report type
        missing_params = []
        if not item_category:
            missing_params.append("item_category")
        if missing_params:
            return {"error": f"Missing parameters: {', '.join(missing_params)}"}

        # Initialize construct query service
        construct_query_service = ConstructQueryService(TABLE_MAP, COLUMN_MAP)

        statement = construct_query_service.construct_query(item_category, item_id, item_sid, interval_day)
        query = statement["query"]
        params = statement["params"]
        result = query_data_service.query_data(query, params)

        return {
            "itemCategory": item_category,
            "itemID": item_id,
            "itemSID": item_sid,
            "intervalDay": interval_day,
            "columns": list(COLUMN_MAP.keys()),
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
