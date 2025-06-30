import json
import logging
from typing import Any, Dict

import pandas as pd

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


class AnalysisService:
    """Service class for data analysis"""

    def __init__(self):
        pass

    def profit_analysis(self, result_set, expected_columns):
        if not result_set:
            return []

        df = pd.DataFrame(result_set)
        if df.empty:
            return []

        # Ensure columns are named correctly
        if len(df.columns) != len(expected_columns):
            return {"error": "Mismatch between result set and expected columns."}
        if list(df.columns) != expected_columns:
            df.columns = expected_columns

        analyzed_rows = []
        # Group by item id with its variants
        for item_id, group in df.groupby("item_id"):
            group = group.sort_values("sid")
            # Get clean price (sid == 0) for this item
            clean_row = group[group["sid"] == 0]
            clean_price = clean_row["last_sold_price"].iloc[0] if not clean_row.empty else None

            previous_price = None
            for idx, row in group.iterrows():
                sid = row["sid"]
                current_price = row["last_sold_price"]

                # For sid >= 1, calculate profit and rate
                if sid == 0 or clean_price is None:
                    profit = 0
                    rate = 0
                else:
                    # Previous level price (sid - 1)
                    prev_row = group[group["sid"] == sid - 1]
                    previous_price = prev_row["last_sold_price"].iloc[0] if not prev_row.empty else None

                    if previous_price is not None and clean_price is not None:
                        cost = previous_price + clean_price
                        profit = current_price - cost
                        rate = 1 + (profit / cost) if cost > 0 else None
                    else:
                        profit = 0
                        rate = 0

                analyzed_rows.append(
                    {
                        "item_name": row["item_name"],
                        "item_id": row["item_id"],
                        "sid": sid,
                        "category": row["category"],
                        "price": current_price,
                        "profit": profit,
                        "rate": rate,
                        "stock": row["current_stock"],
                        "total_trades": row["total_trades"],
                        "scrape_time": row["scrape_time"],
                    }
                )

        analyzed_df = pd.DataFrame(analyzed_rows)
        analyzed_df = analyzed_df.sort_values(
            ["scrape_time", "item_id", "sid", "rate"], ascending=True, na_position="last"
        )
        return json.loads(analyzed_df.to_json(orient="records"))


# Initialize router
router = LambdaRouter()

# Initialize service
analysis_service = AnalysisService()


# Step Functions handler
@router.step_route("default")
def retrieve_step(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process Step Functions"""
    try:
        # Get parameters from Step Functions input event object
        item_category = event.get("itemCategory", "")
        item_id = event.get("itemID", "")
        columns = event.get("columns", "")
        result_set = event.get("resultSet", [])

        # Validate required parameters
        missing_params = []
        if not item_category:
            missing_params.append("item_category")
        if missing_params:
            return {"error": f"Missing parameters: {', '.join(missing_params)}"}

        result = analysis_service.profit_analysis(result_set, columns)

        # return to API Gateway
        return {
            "statusCode": 200,
            "body": json.dumps({"result": result}),
            "headers": router.default_headers,
        }

    except Exception as e:
        logger.error(f"Unexpected errors: {e}")
        return {"error": str(e)}


# Lambda handler function
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler function"""
    return router.handle(event, context)
