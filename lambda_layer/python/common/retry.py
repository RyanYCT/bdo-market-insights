"""
Error handling and retry logic with exponential backoff.

Provides retry decorator for handling transient failures with:
- Error classification (retriable vs non-retriable)
- Exponential backoff with jitter
- Comprehensive error logging with context
"""

import time
import random
import functools
from typing import Any, Callable, Optional, Tuple, Type
from .logging import StructuredLogger


# Retriable error types (network, rate limits, temporary unavailability)
RETRIABLE_ERRORS = (
    # Network errors
    ConnectionError,
    TimeoutError,
    # Add more as needed based on specific libraries used
)


class RetryableError(Exception):
    """Base class for errors that should trigger retry logic."""
    pass


class NetworkError(RetryableError):
    """Network-related errors that are retriable."""
    pass


class RateLimitError(RetryableError):
    """Rate limit errors that are retriable."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None):
        """
        Initialize rate limit error.
        
        Args:
            message: Error message
            retry_after: Seconds to wait before retry (from API header)
        """
        super().__init__(message)
        self.retry_after = retry_after


class TemporaryUnavailableError(RetryableError):
    """Temporary service unavailability errors that are retriable."""
    pass


# Non-retriable error types (validation, authentication, authorization)
class NonRetriableError(Exception):
    """Base class for errors that should NOT trigger retry logic."""
    pass


class ValidationError(NonRetriableError):
    """Validation errors that are not retriable."""
    pass


class AuthenticationError(NonRetriableError):
    """Authentication errors that are not retriable."""
    pass


class AuthorizationError(NonRetriableError):
    """Authorization errors that are not retriable."""
    pass


class ResourceNotFoundError(NonRetriableError):
    """Resource not found errors that are not retriable."""
    pass


def is_retriable_error(error: Exception, retriable_exceptions: Tuple[Type[Exception], ...]) -> bool:
    """
    Determine if an error should trigger retry logic.
    
    Args:
        error: Exception to check
        retriable_exceptions: Tuple of exception types considered retriable
        
    Returns:
        bool: True if error is retriable, False otherwise
    """
    # Check if error is instance of explicitly non-retriable errors
    if isinstance(error, NonRetriableError):
        return False
    
    # Check if error is instance of retriable exceptions
    if isinstance(error, retriable_exceptions):
        return True
    
    # Check if error is instance of base retriable error
    if isinstance(error, RetryableError):
        return True
    
    return False


def calculate_backoff(
    attempt: int,
    backoff_base: float = 2.0,
    backoff_multiplier: float = 1.0,
    max_backoff: float = 60.0,
    jitter: bool = True
) -> float:
    """
    Calculate exponential backoff delay with optional jitter.
    
    Args:
        attempt: Current attempt number (1-indexed)
        backoff_base: Base for exponential calculation (default: 2.0)
        backoff_multiplier: Multiplier for backoff time (default: 1.0)
        max_backoff: Maximum backoff time in seconds (default: 60.0)
        jitter: Whether to add random jitter (default: True)
        
    Returns:
        float: Backoff delay in seconds
        
    Example:
        >>> calculate_backoff(1, jitter=False)
        2.0
        >>> calculate_backoff(2, jitter=False)
        4.0
        >>> calculate_backoff(3, jitter=False)
        8.0
    """
    # Calculate exponential backoff: base^attempt * multiplier
    backoff_time = (backoff_base ** attempt) * backoff_multiplier
    
    # Cap at maximum backoff
    backoff_time = min(backoff_time, max_backoff)
    
    # Add jitter to prevent thundering herd
    if jitter:
        # Add random jitter of ±10%
        jitter_amount = backoff_time * 0.1
        backoff_time += random.uniform(-jitter_amount, jitter_amount)
    
    return max(0, backoff_time)  # Ensure non-negative


def retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_multiplier: float = 1.0,
    max_backoff: float = 60.0,
    jitter: bool = True,
    retriable_exceptions: Tuple[Type[Exception], ...] = RETRIABLE_ERRORS,
    logger: Optional[StructuredLogger] = None
) -> Callable:
    """
    Decorator for retrying functions with exponential backoff.
    
    Implements retry logic with:
    - Error classification (retriable vs non-retriable)
    - Exponential backoff with jitter
    - Comprehensive error logging with context
    - Respect for rate limit headers (retry_after)
    
    Args:
        max_attempts: Maximum number of attempts (default: 3)
        backoff_base: Base for exponential backoff (default: 2.0)
        backoff_multiplier: Multiplier for backoff time (default: 1.0)
        max_backoff: Maximum backoff time in seconds (default: 60.0)
        jitter: Whether to add random jitter (default: True)
        retriable_exceptions: Tuple of exception types to retry (default: RETRIABLE_ERRORS)
        logger: Optional StructuredLogger for logging retry attempts
        
    Returns:
        Callable: Decorated function with retry logic
        
    Raises:
        Exception: Re-raises the last exception if all retries are exhausted
        
    Example:
        >>> @retry(max_attempts=3, retriable_exceptions=(NetworkError,))
        ... def call_api():
        ...     # API call that might fail
        ...     pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    if logger and attempt > 1:
                        logger.info(
                            f"Retry attempt {attempt}/{max_attempts}",
                            function=func.__name__,
                            attempt=attempt,
                            max_attempts=max_attempts
                        )
                    
                    # Execute the function
                    result = func(*args, **kwargs)
                    
                    # Success - log if this was a retry
                    if logger and attempt > 1:
                        logger.info(
                            f"Function succeeded on attempt {attempt}",
                            function=func.__name__,
                            attempt=attempt
                        )
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    # Check if error is retriable
                    if not is_retriable_error(e, retriable_exceptions):
                        if logger:
                            logger.error(
                                "Non-retriable error encountered",
                                error=e,
                                function=func.__name__,
                                error_type=type(e).__name__
                            )
                        raise
                    
                    # Log retriable error
                    if logger:
                        logger.warning(
                            f"Retriable error on attempt {attempt}/{max_attempts}",
                            error=e,
                            function=func.__name__,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            error_type=type(e).__name__
                        )
                    
                    # If this was the last attempt, raise the error
                    if attempt == max_attempts:
                        if logger:
                            logger.error(
                                f"All {max_attempts} retry attempts exhausted",
                                error=e,
                                function=func.__name__,
                                max_attempts=max_attempts,
                                error_type=type(e).__name__
                            )
                        raise
                    
                    # Calculate backoff delay
                    if isinstance(e, RateLimitError) and e.retry_after:
                        # Respect rate limit retry_after header
                        delay = e.retry_after
                        if logger:
                            logger.info(
                                f"Rate limit error, respecting retry_after header",
                                retry_after=delay,
                                function=func.__name__
                            )
                    else:
                        # Use exponential backoff
                        delay = calculate_backoff(
                            attempt,
                            backoff_base,
                            backoff_multiplier,
                            max_backoff,
                            jitter
                        )
                    
                    if logger:
                        logger.info(
                            f"Waiting {delay:.2f} seconds before retry",
                            delay=delay,
                            function=func.__name__,
                            attempt=attempt
                        )
                    
                    # Wait before retry
                    time.sleep(delay)
            
            # This should never be reached, but just in case
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def retry_with_context(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    retriable_exceptions: Tuple[Type[Exception], ...] = RETRIABLE_ERRORS
) -> Callable:
    """
    Simplified retry decorator that extracts logger from function arguments.
    
    This decorator looks for a 'logger' keyword argument in the function call
    and uses it for logging retry attempts.
    
    Args:
        max_attempts: Maximum number of attempts (default: 3)
        backoff_base: Base for exponential backoff (default: 2.0)
        retriable_exceptions: Tuple of exception types to retry
        
    Returns:
        Callable: Decorated function with retry logic
        
    Example:
        >>> @retry_with_context(max_attempts=3)
        ... def call_api(url, logger=None):
        ...     # API call that might fail
        ...     pass
        >>> logger = StructuredLogger("func", "corr-123")
        >>> call_api("https://api.example.com", logger=logger)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Extract logger from kwargs if available
            logger = kwargs.get('logger')
            
            # Use the main retry decorator with extracted logger
            retry_decorator = retry(
                max_attempts=max_attempts,
                backoff_base=backoff_base,
                retriable_exceptions=retriable_exceptions,
                logger=logger
            )
            
            return retry_decorator(func)(*args, **kwargs)
        
        return wrapper
    return decorator
