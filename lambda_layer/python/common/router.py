"""
Lambda request routing and response formatting.

Provides LambdaRouter class for consistent request/response handling
across all Lambda functions.
"""

import json
import traceback
from typing import Any, Callable, Dict, Optional
from .logging import StructuredLogger
from .correlation import extract_correlation_id


class LambdaRouter:
    """
    Handles Lambda request routing and response formatting.
    
    Provides consistent request/response handling with:
    - Automatic correlation ID extraction/generation
    - Structured logging integration
    - Standardized response format
    - Exception handling and error responses
    
    Example:
        >>> router = LambdaRouter()
        >>> @router.route()
        ... def handler(event, context, logger):
        ...     logger.info("Processing request")
        ...     return {"result": "success"}
        >>> response = handler({"test": "data"}, None)
        >>> response['statusCode']
        200
    """
    
    def __init__(self, correlation_id: Optional[str] = None):
        """
        Initialize router with optional correlation ID.
        
        Args:
            correlation_id: Pre-existing correlation ID (optional)
        """
        self.correlation_id = correlation_id
        self._routes: Dict[str, Callable] = {}
    
    def route(
        self,
        function_name: Optional[str] = None
    ) -> Callable:
        """
        Decorator for Lambda handler functions.
        
        Wraps the handler to provide:
        - Correlation ID extraction/generation
        - Structured logger instance
        - Standardized response formatting
        - Exception handling
        
        Args:
            function_name: Name for logging (defaults to handler function name)
            
        Returns:
            Callable: Decorated handler function
            
        Example:
            >>> router = LambdaRouter()
            >>> @router.route()
            ... def my_handler(event, context, logger):
            ...     return {"data": "result"}
        """
        def decorator(handler: Callable) -> Callable:
            def wrapper(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
                # Extract or generate correlation ID
                correlation_id = self.correlation_id or extract_correlation_id(event)
                
                # Get function name from context or handler
                func_name = function_name or (
                    context.function_name if context and hasattr(context, 'function_name')
                    else handler.__name__
                )
                
                # Get request ID from context
                request_id = (
                    context.aws_request_id if context and hasattr(context, 'aws_request_id')
                    else None
                )
                
                # Create structured logger
                logger = StructuredLogger(func_name, correlation_id, request_id)
                
                try:
                    logger.info("Lambda invocation started", event_keys=list(event.keys()))
                    
                    # Call the actual handler
                    result = handler(event, context, logger)
                    
                    logger.info("Lambda invocation completed successfully")
                    
                    # Format successful response
                    return self._format_response(200, result, correlation_id)
                    
                except ValidationError as e:
                    # Handle validation errors (400)
                    logger.error("Validation error", error=e)
                    return self._format_error_response(
                        400,
                        "VALIDATION_ERROR",
                        str(e),
                        correlation_id
                    )
                    
                except Exception as e:
                    # Handle unexpected errors (500)
                    logger.error("Unexpected error in Lambda handler", error=e)
                    return self._format_error_response(
                        500,
                        "INTERNAL_ERROR",
                        "An unexpected error occurred",
                        correlation_id,
                        details={"error_type": type(e).__name__}
                    )
            
            return wrapper
        return decorator
    
    def add_route(self, path: str, handler: Callable) -> None:
        """
        Register a handler function for a specific route.
        
        This allows multiple routes within a single Lambda function.
        
        Args:
            path: Route path identifier
            handler: Handler function for this route
            
        Example:
            >>> router = LambdaRouter()
            >>> def get_handler(event, context, logger):
            ...     return {"method": "GET"}
            >>> router.add_route("/items", get_handler)
        """
        self._routes[path] = handler
    
    def dispatch(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """
        Dispatch request to appropriate handler based on path.
        
        Args:
            event: Lambda event
            context: Lambda context
            
        Returns:
            dict: Handler response
            
        Raises:
            ValueError: If no handler found for path
        """
        # Extract path from event
        path = event.get('path') or event.get('rawPath', '/')
        
        # Find matching handler
        handler = self._routes.get(path)
        if not handler:
            correlation_id = extract_correlation_id(event)
            return self._format_error_response(
                404,
                "NOT_FOUND",
                f"No handler found for path: {path}",
                correlation_id
            )
        
        # Execute handler with routing
        return self.route()(handler)(event, context)
    
    def _format_response(
        self,
        status_code: int,
        body: Any,
        correlation_id: str
    ) -> Dict[str, Any]:
        """
        Format standardized Lambda response.
        
        Args:
            status_code: HTTP status code
            body: Response body (will be JSON serialized)
            correlation_id: Correlation ID for tracking
            
        Returns:
            dict: Formatted Lambda response
        """
        # Ensure body includes correlation_id
        if isinstance(body, dict):
            body['correlation_id'] = correlation_id
        
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'X-Correlation-ID': correlation_id
            },
            'body': json.dumps(body) if not isinstance(body, str) else body
        }
    
    def _format_error_response(
        self,
        status_code: int,
        error_code: str,
        message: str,
        correlation_id: str,
        details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format standardized error response.
        
        Args:
            status_code: HTTP status code
            error_code: Application error code
            message: Error message
            correlation_id: Correlation ID for tracking
            details: Additional error details (optional)
            
        Returns:
            dict: Formatted error response
        """
        from datetime import datetime, timezone
        
        error_body = {
            'error': {
                'code': error_code,
                'message': message,
                'correlation_id': correlation_id,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        }
        
        if details:
            error_body['error']['details'] = details
        
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'X-Correlation-ID': correlation_id
            },
            'body': json.dumps(error_body)
        }


class ValidationError(Exception):
    """Exception raised for validation errors."""
    pass
