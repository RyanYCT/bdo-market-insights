"""
Structured JSON logging with correlation IDs for CloudWatch.

Provides StructuredLogger class that emits JSON-formatted logs with
standard fields for efficient querying and tracing.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from pythonjsonlogger import jsonlogger as JsonFormatter
except ImportError:
    try:
        from pythonjsonlogger.json import JsonFormatter
    except ImportError:
        # Fallback if pythonjsonlogger is not available
        JsonFormatter = None

# Import X-Ray utilities
try:
    from common.xray import get_trace_id, is_xray_enabled
except ImportError:
    # Fallback if xray module not available
    def get_trace_id():
        return None
    def is_xray_enabled():
        return False


class StructuredLogger:
    """
    Provides structured JSON logging with correlation IDs.
    
    All log entries include standard fields:
    - timestamp: ISO 8601 format
    - level: Log level (INFO, ERROR, etc.)
    - function_name: Name of the Lambda function
    - correlation_id: Unique ID for request tracking
    - message: Log message
    - request_id: Lambda request ID (from context)
    - Additional context fields as needed
    
    Example:
        >>> logger = StructuredLogger("myFunction", "corr-123")
        >>> logger.info("Processing started", item_count=5)
        >>> logger.error("Failed to process", error=Exception("test"))
    """
    
    def __init__(
        self,
        function_name: str,
        correlation_id: str,
        request_id: Optional[str] = None,
        level: int = logging.INFO
    ):
        """
        Initialize structured logger for a Lambda function.
        
        Args:
            function_name: Name of the Lambda function
            correlation_id: Correlation ID for request tracking
            request_id: Lambda request ID from context (optional)
            level: Logging level (default: INFO)
        """
        self.function_name = function_name
        self.correlation_id = correlation_id
        self.request_id = request_id
        self._context_fields: Dict[str, Any] = {}
        
        # Create logger instance
        self._logger = logging.getLogger(function_name)
        self._logger.setLevel(level)
        
        # Remove existing handlers to avoid duplicates
        self._logger.handlers = []
        
        # Create JSON formatter
        handler = logging.StreamHandler(sys.stdout)
        
        if JsonFormatter:
            # Use JSON formatter if available
            if hasattr(JsonFormatter, 'JsonFormatter'):
                formatter = JsonFormatter.JsonFormatter(
                    '%(timestamp)s %(level)s %(function_name)s %(correlation_id)s %(message)s'
                )
            else:
                formatter = JsonFormatter(
                    '%(timestamp)s %(level)s %(function_name)s %(correlation_id)s %(message)s'
                )
            handler.setFormatter(formatter)
        else:
            # Fallback to standard formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
            )
            handler.setFormatter(formatter)
        
        self._logger.addHandler(handler)
    
    def _build_log_entry(self, message: str, level: str, **kwargs) -> Dict[str, Any]:
        """
        Build a complete log entry with all standard fields.
        
        Args:
            message: Log message
            level: Log level string
            **kwargs: Additional context fields
            
        Returns:
            dict: Complete log entry
        """
        entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': level,
            'function_name': self.function_name,
            'correlation_id': self.correlation_id,
            'log_message': message,  # Use 'log_message' to avoid conflict with reserved 'msg'
        }
        
        # Add request_id if available
        if self.request_id:
            entry['request_id'] = self.request_id
        
        # Add X-Ray trace ID if available
        if is_xray_enabled():
            trace_id = get_trace_id()
            if trace_id:
                entry['xray_trace_id'] = trace_id
        
        # Add context fields
        entry.update(self._context_fields)
        
        # Add additional kwargs
        entry.update(kwargs)
        
        return entry
    
    def info(self, message: str, **kwargs) -> None:
        """
        Log info level message with structured fields.
        
        Args:
            message: Log message
            **kwargs: Additional context fields
        """
        entry = self._build_log_entry(message, 'INFO', **kwargs)
        self._logger.info(message, extra=entry)
    
    def warning(self, message: str, **kwargs) -> None:
        """
        Log warning level message with structured fields.
        
        Args:
            message: Log message
            **kwargs: Additional context fields
        """
        entry = self._build_log_entry(message, 'WARNING', **kwargs)
        self._logger.warning(message, extra=entry)
    
    def error(self, message: str, error: Optional[Exception] = None, **kwargs) -> None:
        """
        Log error level message with exception details and stack trace.
        
        Args:
            message: Log message
            error: Exception object (optional)
            **kwargs: Additional context fields
        """
        entry = self._build_log_entry(message, 'ERROR', **kwargs)
        
        # Add error details if exception provided
        if error:
            entry['error'] = {
                'type': type(error).__name__,
                'message': str(error),
            }
            # Add stack trace if available
            if hasattr(error, '__traceback__'):
                import traceback
                entry['error']['stack_trace'] = ''.join(
                    traceback.format_tb(error.__traceback__)
                )
        
        self._logger.error(message, extra=entry, exc_info=error is not None)
    
    def debug(self, message: str, **kwargs) -> None:
        """
        Log debug level message with structured fields.
        
        Args:
            message: Log message
            **kwargs: Additional context fields
        """
        entry = self._build_log_entry(message, 'DEBUG', **kwargs)
        self._logger.debug(message, extra=entry)
    
    def with_context(self, **kwargs) -> 'StructuredLogger':
        """
        Create a child logger with additional context fields.
        
        The child logger inherits all settings and adds the specified
        context fields to all subsequent log entries.
        
        Args:
            **kwargs: Context fields to add
            
        Returns:
            StructuredLogger: New logger instance with added context
            
        Example:
            >>> logger = StructuredLogger("func", "corr-123")
            >>> child = logger.with_context(user_id="user-456")
            >>> child.info("User action")  # Includes user_id in log
        """
        child = StructuredLogger(
            self.function_name,
            self.correlation_id,
            self.request_id,
            self._logger.level
        )
        # Copy parent context and add new fields
        child._context_fields = {**self._context_fields, **kwargs}
        return child
