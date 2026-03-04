"""
Circuit breaker pattern for preventing cascading failures.

Provides CircuitBreaker class for protecting External API calls with:
- State management (CLOSED, OPEN, HALF_OPEN)
- Failure threshold and timeout configuration
- Automatic state transitions based on success/failure patterns
"""

import time
import functools
from enum import Enum
from typing import Any, Callable, Optional
from .logging import StructuredLogger


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"      # Normal operation, requests pass through
    OPEN = "OPEN"          # Circuit is open, requests fail fast
    HALF_OPEN = "HALF_OPEN"  # Testing if service has recovered


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    
    def __init__(self, message: str, retry_after: Optional[float] = None):
        """
        Initialize circuit breaker error.
        
        Args:
            message: Error message
            retry_after: Seconds until circuit may close (optional)
        """
        super().__init__(message)
        self.retry_after = retry_after


class CircuitBreaker:
    """
    Implements circuit breaker pattern for External API calls.
    
    The circuit breaker prevents cascading failures by:
    - Tracking failure rates
    - Opening circuit when failures exceed threshold
    - Failing fast when circuit is open
    - Testing recovery in half-open state
    
    States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Too many failures, fail fast without calling function
    - HALF_OPEN: Testing recovery, allow one request through
    
    Example:
        >>> breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        >>> @breaker.protect
        ... def call_external_api():
        ...     # API call that might fail
        ...     pass
        >>> result = call_external_api()
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,
        success_threshold: int = 2,
        logger: Optional[StructuredLogger] = None
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit (default: 5)
            timeout: Seconds to wait before attempting to close circuit (default: 60)
            success_threshold: Number of successes in HALF_OPEN to close circuit (default: 2)
            logger: Optional StructuredLogger for logging state changes
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.success_threshold = success_threshold
        self.logger = logger
        
        # State tracking
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._opened_at: Optional[float] = None
    
    @property
    def state(self) -> CircuitState:
        """
        Get current circuit breaker state.
        
        Automatically transitions from OPEN to HALF_OPEN if timeout has elapsed.
        
        Returns:
            CircuitState: Current state
        """
        # Check if we should transition from OPEN to HALF_OPEN
        if self._state == CircuitState.OPEN and self._opened_at:
            elapsed = time.time() - self._opened_at
            if elapsed >= self.timeout:
                self._transition_to_half_open()
        
        return self._state
    
    def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state."""
        if self.logger:
            self.logger.info(
                "Circuit breaker transitioning to CLOSED",
                previous_state=self._state.value,
                failure_count=self._failure_count
            )
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._opened_at = None
    
    def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state."""
        if self.logger:
            self.logger.warning(
                "Circuit breaker transitioning to OPEN",
                previous_state=self._state.value,
                failure_count=self._failure_count,
                failure_threshold=self.failure_threshold,
                timeout=self.timeout
            )
        
        self._state = CircuitState.OPEN
        self._opened_at = time.time()
    
    def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state."""
        if self.logger:
            self.logger.info(
                "Circuit breaker transitioning to HALF_OPEN",
                previous_state=self._state.value,
                timeout_elapsed=time.time() - self._opened_at if self._opened_at else 0
            )
        
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0
    
    def _record_success(self) -> None:
        """Record a successful call."""
        current_state = self.state
        
        if current_state == CircuitState.HALF_OPEN:
            self._success_count += 1
            
            if self.logger:
                self.logger.info(
                    "Successful call in HALF_OPEN state",
                    success_count=self._success_count,
                    success_threshold=self.success_threshold
                )
            
            # If we've had enough successes, close the circuit
            if self._success_count >= self.success_threshold:
                self._transition_to_closed()
        
        elif current_state == CircuitState.CLOSED:
            # Reset failure count on success
            if self._failure_count > 0:
                if self.logger:
                    self.logger.debug(
                        "Resetting failure count after success",
                        previous_failure_count=self._failure_count
                    )
                self._failure_count = 0
    
    def _record_failure(self) -> None:
        """Record a failed call."""
        current_state = self.state
        
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self.logger:
            self.logger.warning(
                "Failed call recorded",
                state=current_state.value,
                failure_count=self._failure_count,
                failure_threshold=self.failure_threshold
            )
        
        if current_state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN reopens the circuit
            if self.logger:
                self.logger.warning(
                    "Failure in HALF_OPEN state, reopening circuit"
                )
            self._transition_to_open()
        
        elif current_state == CircuitState.CLOSED:
            # Check if we've exceeded the failure threshold
            if self._failure_count >= self.failure_threshold:
                self._transition_to_open()
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function
            
        Returns:
            Any: Function result
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Any exception raised by the function
        """
        current_state = self.state
        
        # Fail fast if circuit is open
        if current_state == CircuitState.OPEN:
            time_until_retry = self.timeout - (time.time() - self._opened_at) if self._opened_at else 0
            
            if self.logger:
                self.logger.warning(
                    "Circuit breaker is OPEN, failing fast",
                    failure_count=self._failure_count,
                    time_until_retry=time_until_retry
                )
            
            raise CircuitBreakerError(
                f"Circuit breaker is OPEN. Service unavailable. Retry after {time_until_retry:.0f} seconds.",
                retry_after=time_until_retry
            )
        
        # Allow call in CLOSED or HALF_OPEN state
        try:
            if self.logger and current_state == CircuitState.HALF_OPEN:
                self.logger.info("Attempting call in HALF_OPEN state")
            
            result = func(*args, **kwargs)
            
            # Record success
            self._record_success()
            
            return result
            
        except Exception as e:
            # Record failure
            self._record_failure()
            
            # Re-raise the original exception
            raise
    
    def protect(self, func: Callable) -> Callable:
        """
        Decorator to protect a function with circuit breaker.
        
        Args:
            func: Function to protect
            
        Returns:
            Callable: Protected function
            
        Example:
            >>> breaker = CircuitBreaker(failure_threshold=5)
            >>> @breaker.protect
            ... def call_api():
            ...     # API call
            ...     pass
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            return self.call(func, *args, **kwargs)
        
        return wrapper
    
    def reset(self) -> None:
        """
        Manually reset circuit breaker to CLOSED state.
        
        Useful for testing or manual intervention.
        """
        if self.logger:
            self.logger.info(
                "Manually resetting circuit breaker",
                previous_state=self._state.value,
                failure_count=self._failure_count
            )
        
        self._transition_to_closed()
    
    def get_stats(self) -> dict:
        """
        Get current circuit breaker statistics.
        
        Returns:
            dict: Statistics including state, failure count, etc.
        """
        stats = {
            'state': self.state.value,
            'failure_count': self._failure_count,
            'success_count': self._success_count,
            'failure_threshold': self.failure_threshold,
            'timeout': self.timeout,
        }
        
        if self._opened_at:
            stats['opened_at'] = self._opened_at
            stats['time_until_retry'] = max(0, self.timeout - (time.time() - self._opened_at))
        
        if self._last_failure_time:
            stats['last_failure_time'] = self._last_failure_time
        
        return stats
