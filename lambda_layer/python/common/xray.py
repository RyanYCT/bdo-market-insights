"""
X-Ray tracing utilities for Lambda functions.

Provides utilities for AWS X-Ray integration including:
- X-Ray SDK instrumentation
- Trace ID extraction
- Subsegment creation for custom operations
"""

import os
from typing import Optional, Any, Callable
from functools import wraps

# Try to import X-Ray SDK
try:
    from aws_xray_sdk.core import xray_recorder
    from aws_xray_sdk.core import patch_all
    XRAY_AVAILABLE = True
except ImportError:
    XRAY_AVAILABLE = False
    xray_recorder = None


def is_xray_enabled() -> bool:
    """
    Check if X-Ray tracing is enabled.
    
    X-Ray is considered enabled if:
    1. The SDK is available (installed)
    2. AWS_XRAY_TRACING_ENABLED environment variable is not set to 'false'
    3. Running in Lambda environment (AWS_LAMBDA_FUNCTION_NAME is set)
    
    Returns:
        bool: True if X-Ray tracing is enabled
    """
    if not XRAY_AVAILABLE:
        return False
    
    # Check if explicitly disabled
    if os.getenv('AWS_XRAY_TRACING_ENABLED', '').lower() == 'false':
        return False
    
    # Check if running in Lambda
    if not os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
        return False
    
    return True


def get_trace_id() -> Optional[str]:
    """
    Get the current X-Ray trace ID.
    
    Returns:
        str: X-Ray trace ID in format '1-5e645f3e-1234567890abcdef12345678'
        None: If X-Ray is not enabled or no active trace
    """
    if not is_xray_enabled():
        return None
    
    try:
        # Get the current segment/subsegment
        segment = xray_recorder.current_segment()
        if segment:
            # Extract trace ID from segment
            trace_id = segment.trace_id
            return trace_id
    except Exception:
        # If there's any error getting trace ID, return None
        pass
    
    return None


def get_trace_header() -> Optional[str]:
    """
    Get the X-Ray trace header for propagation.
    
    The trace header is used to propagate trace context across service boundaries.
    Format: Root=1-5e645f3e-...; Parent=...; Sampled=1
    
    Returns:
        str: X-Ray trace header
        None: If X-Ray is not enabled or no active trace
    """
    if not is_xray_enabled():
        return None
    
    try:
        segment = xray_recorder.current_segment()
        if segment:
            # Build trace header
            header_parts = [f"Root={segment.trace_id}"]
            
            if hasattr(segment, 'id'):
                header_parts.append(f"Parent={segment.id}")
            
            if hasattr(segment, 'sampled'):
                sampled = 1 if segment.sampled else 0
                header_parts.append(f"Sampled={sampled}")
            
            return "; ".join(header_parts)
    except Exception:
        pass
    
    return None


def create_subsegment(name: str):
    """
    Create a subsegment for custom operation tracing.
    
    Use as a context manager to trace custom operations:
    
    Example:
        >>> with create_subsegment('database_query'):
        ...     result = db.query(...)
    
    Args:
        name: Name of the subsegment
        
    Returns:
        Context manager for the subsegment (or no-op if X-Ray disabled)
    """
    if not is_xray_enabled():
        # Return a no-op context manager
        from contextlib import nullcontext
        return nullcontext()
    
    try:
        return xray_recorder.capture(name)
    except Exception:
        # If there's an error, return no-op context manager
        from contextlib import nullcontext
        return nullcontext()


def trace_function(name: Optional[str] = None):
    """
    Decorator to trace a function with X-Ray.
    
    Creates a subsegment for the function execution.
    
    Args:
        name: Optional name for the subsegment (defaults to function name)
        
    Example:
        >>> @trace_function()
        ... def process_data(data):
        ...     return transform(data)
    """
    def decorator(func: Callable) -> Callable:
        subsegment_name = name or func.__name__
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not is_xray_enabled():
                # X-Ray disabled, just call function
                return func(*args, **kwargs)
            
            try:
                with xray_recorder.capture(subsegment_name):
                    return func(*args, **kwargs)
            except Exception:
                # If X-Ray fails, still execute function
                return func(*args, **kwargs)
        
        return wrapper
    return decorator


def add_annotation(key: str, value: Any) -> None:
    """
    Add an annotation to the current X-Ray segment.
    
    Annotations are indexed and can be used for filtering traces.
    
    Args:
        key: Annotation key
        value: Annotation value (must be string, number, or boolean)
    """
    if not is_xray_enabled():
        return
    
    try:
        segment = xray_recorder.current_segment()
        if segment:
            segment.put_annotation(key, value)
    except Exception:
        pass


def add_metadata(key: str, value: Any, namespace: str = 'default') -> None:
    """
    Add metadata to the current X-Ray segment.
    
    Metadata is not indexed but can contain any JSON-serializable data.
    
    Args:
        key: Metadata key
        value: Metadata value (any JSON-serializable object)
        namespace: Namespace for the metadata (default: 'default')
    """
    if not is_xray_enabled():
        return
    
    try:
        segment = xray_recorder.current_segment()
        if segment:
            segment.put_metadata(key, value, namespace)
    except Exception:
        pass


def initialize_xray() -> None:
    """
    Initialize X-Ray SDK for Lambda function.
    
    This should be called once at module load time.
    Patches common libraries for automatic tracing:
    - boto3/botocore (AWS SDK)
    - requests/urllib3 (HTTP clients)
    - psycopg2 (PostgreSQL)
    """
    if not is_xray_enabled():
        return
    
    try:
        # Patch common libraries for automatic instrumentation
        # This will trace AWS SDK calls, HTTP requests, and database queries
        patch_all()
    except Exception:
        # If patching fails, continue without it
        # X-Ray will still work for manual instrumentation
        pass


# Initialize X-Ray when module is imported
initialize_xray()
