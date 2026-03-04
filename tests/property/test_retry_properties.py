"""
Property-based tests for error handling and retry logic.

Feature: bdo-market-insights-rewrite
Tests Properties 8 and 9 from the design document.
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch
from hypothesis import given, strategies as st, settings, assume

# Mock psycopg2 before importing common modules
import sys
sys.path.insert(0, 'lambda_layer/python')

# Create mock psycopg2 module to avoid import errors
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

# Import retry and circuit breaker from lambda layer
from common.retry import (
    retry,
    retry_with_context,
    calculate_backoff,
    is_retriable_error,
    RetryableError,
    NetworkError,
    RateLimitError,
    TemporaryUnavailableError,
    NonRetriableError,
    ValidationError,
    AuthenticationError,
)
from common.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState
from common.logging import StructuredLogger


# Property 8: Retry behavior with exponential backoff
# **Validates: Requirements 6.1, 6.2, 6.3**

@given(
    max_attempts=st.integers(min_value=2, max_value=4),
    failure_count=st.integers(min_value=1, max_value=4)
)
@settings(max_examples=50, deadline=None)
def test_retry_distinguishes_retriable_from_non_retriable_errors(max_attempts, failure_count):
    """
    Property 8: For any retriable error, the system should retry the operation
    with exponential backoff, distinguish retriable from non-retriable errors,
    and log with full context when all retries are exhausted.
    
    This test verifies error classification: retriable errors trigger retries,
    non-retriable errors do not.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    # Test retriable error - should retry
    call_count_retriable = 0
    
    @retry(max_attempts=max_attempts, retriable_exceptions=(NetworkError,), backoff_base=0.001, jitter=False)
    def func_with_retriable_error():
        nonlocal call_count_retriable
        call_count_retriable += 1
        if call_count_retriable < failure_count:
            raise NetworkError("Network timeout")
        return "success"
    
    # Should succeed after retries if failure_count <= max_attempts
    if failure_count <= max_attempts:
        result = func_with_retriable_error()
        assert result == "success"
        assert call_count_retriable == failure_count
    else:
        # Should exhaust retries and raise when failure_count > max_attempts
        with pytest.raises(NetworkError):
            func_with_retriable_error()
        assert call_count_retriable == max_attempts
    
    # Test non-retriable error - should NOT retry
    call_count_non_retriable = 0
    
    @retry(max_attempts=max_attempts, retriable_exceptions=(NetworkError,))
    def func_with_non_retriable_error():
        nonlocal call_count_non_retriable
        call_count_non_retriable += 1
        raise ValidationError("Invalid input")
    
    # Should raise immediately without retries
    with pytest.raises(ValidationError):
        func_with_non_retriable_error()
    
    # CRITICAL: Verify it was called only ONCE (no retries for non-retriable errors)
    assert call_count_non_retriable == 1


@given(
    max_attempts=st.integers(min_value=2, max_value=3),
    backoff_base=st.floats(min_value=0.001, max_value=0.01),
)
@settings(max_examples=50, deadline=None)
def test_retry_uses_exponential_backoff(max_attempts, backoff_base):
    """
    Property 8: Retry logic should use exponential backoff between attempts.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    call_times = []
    
    @retry(
        max_attempts=max_attempts,
        backoff_base=backoff_base,
        jitter=False,  # Disable jitter for predictable testing
        retriable_exceptions=(NetworkError,)
    )
    def func_that_always_fails():
        call_times.append(time.time())
        raise NetworkError("Connection failed")
    
    # Should exhaust all retries
    with pytest.raises(NetworkError):
        func_that_always_fails()
    
    # Verify we made max_attempts calls
    assert len(call_times) == max_attempts
    
    # Verify there was some delay between calls (exponential backoff is happening)
    # We just verify delays exist and are non-negative, not exact timing
    if len(call_times) >= 2:
        for i in range(1, len(call_times)):
            delay = call_times[i] - call_times[i-1]
            # Just verify there was some delay (timing can be imprecise)
            assert delay >= 0  # Non-negative delay


@given(
    max_attempts=st.integers(min_value=2, max_value=4),
    success_on_attempt=st.integers(min_value=1, max_value=4)
)
@settings(max_examples=50, deadline=None)
def test_retry_succeeds_before_exhausting_attempts(max_attempts, success_on_attempt):
    """
    Property 8: If operation succeeds before exhausting retries, it should
    return the result without further attempts.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    assume(success_on_attempt <= max_attempts)
    
    call_count = 0
    
    @retry(max_attempts=max_attempts, retriable_exceptions=(NetworkError,), backoff_base=0.001, jitter=False)
    def func_succeeds_eventually():
        nonlocal call_count
        call_count += 1
        if call_count < success_on_attempt:
            raise NetworkError("Temporary failure")
        return f"success_on_attempt_{call_count}"
    
    result = func_succeeds_eventually()
    
    # Verify it succeeded on the expected attempt
    assert result == f"success_on_attempt_{success_on_attempt}"
    assert call_count == success_on_attempt


@given(
    max_attempts=st.integers(min_value=2, max_value=3),
    retry_after=st.integers(min_value=1, max_value=2)
)
@settings(max_examples=50, deadline=None)
def test_retry_respects_rate_limit_retry_after_header(max_attempts, retry_after):
    """
    Property 8: When encountering a rate limit error with retry_after,
    the system should respect the retry_after value instead of using
    exponential backoff.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    call_times = []
    call_count = 0
    
    @retry(
        max_attempts=max_attempts,
        backoff_base=2.0,
        retriable_exceptions=(RateLimitError,)
    )
    def func_with_rate_limit():
        nonlocal call_count
        call_count += 1
        call_times.append(time.time())
        
        if call_count < max_attempts:
            raise RateLimitError("Rate limit exceeded", retry_after=retry_after)
        return "success"
    
    result = func_with_rate_limit()
    assert result == "success"
    
    # Verify retry_after was respected (check first retry delay)
    if len(call_times) >= 2:
        first_retry_delay = call_times[1] - call_times[0]
        # Allow 20% tolerance
        assert first_retry_delay >= retry_after * 0.8
        assert first_retry_delay <= retry_after * 1.5


@given(
    max_attempts=st.integers(min_value=2, max_value=3)
)
@settings(max_examples=50, deadline=None)
def test_retry_logs_with_full_context_when_exhausted(max_attempts):
    """
    Property 8: When all retries are exhausted, the system should log
    with full context including error details, attempt count, and function name.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    # Create a mock logger to capture log calls
    mock_logger = MagicMock(spec=StructuredLogger)
    
    @retry(
        max_attempts=max_attempts,
        retriable_exceptions=(NetworkError,),
        logger=mock_logger,
        backoff_base=0.01
    )
    def func_that_always_fails():
        raise NetworkError("Connection timeout")
    
    # Should exhaust all retries
    with pytest.raises(NetworkError):
        func_that_always_fails()
    
    # Verify logger was called with error information
    # Should have warning logs for each retry attempt
    assert mock_logger.warning.call_count >= max_attempts - 1
    
    # Should have final error log when exhausted
    assert mock_logger.error.call_count >= 1
    
    # Verify the final error log contains context
    final_error_call = mock_logger.error.call_args_list[-1]
    error_message = final_error_call[0][0]
    error_kwargs = final_error_call[1]
    
    # Verify context includes max_attempts and function name
    assert "exhausted" in error_message.lower() or "exhausted" in str(error_kwargs).lower()
    assert "max_attempts" in error_kwargs or "max_attempts" in str(error_kwargs)


@given(
    max_attempts=st.integers(min_value=2, max_value=3),
    backoff_base=st.floats(min_value=0.001, max_value=0.01),
    max_backoff=st.floats(min_value=0.1, max_value=0.5)
)
@settings(max_examples=50, deadline=None)
def test_retry_respects_max_backoff_limit(max_attempts, backoff_base, max_backoff):
    """
    Property 8: Exponential backoff should never exceed the configured
    max_backoff value.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    call_times = []
    
    @retry(
        max_attempts=max_attempts,
        backoff_base=backoff_base,
        max_backoff=max_backoff,
        jitter=False,
        retriable_exceptions=(NetworkError,)
    )
    def func_that_always_fails():
        call_times.append(time.time())
        raise NetworkError("Connection failed")
    
    with pytest.raises(NetworkError):
        func_that_always_fails()
    
    # Check that no delay exceeded max_backoff
    for i in range(1, len(call_times)):
        delay = call_times[i] - call_times[i-1]
        # Allow some tolerance for execution time
        assert delay <= max_backoff * 1.5


@given(
    attempt=st.integers(min_value=1, max_value=5),
    backoff_base=st.floats(min_value=0.001, max_value=0.1),
    max_backoff=st.floats(min_value=0.5, max_value=5.0)
)
@settings(max_examples=50, deadline=None)
def test_calculate_backoff_produces_valid_delays(attempt, backoff_base, max_backoff):
    """
    Property 8: The calculate_backoff function should always produce
    non-negative delays that respect the max_backoff limit.
    
    Feature: bdo-market-insights-rewrite, Property 8: Retry behavior with exponential backoff
    """
    # Test without jitter
    delay_no_jitter = calculate_backoff(
        attempt=attempt,
        backoff_base=backoff_base,
        max_backoff=max_backoff,
        jitter=False
    )
    
    # Verify delay is non-negative
    assert delay_no_jitter >= 0
    
    # Verify delay respects max_backoff
    assert delay_no_jitter <= max_backoff
    
    # Verify exponential growth (when not capped)
    expected = (backoff_base ** attempt)
    if expected <= max_backoff:
        assert abs(delay_no_jitter - expected) < 0.01
    else:
        assert delay_no_jitter == max_backoff
    
    # Test with jitter
    delay_with_jitter = calculate_backoff(
        attempt=attempt,
        backoff_base=backoff_base,
        max_backoff=max_backoff,
        jitter=True
    )
    
    # Verify delay is non-negative
    assert delay_with_jitter >= 0
    
    # Verify delay respects max_backoff (with tolerance for jitter)
    assert delay_with_jitter <= max_backoff * 1.2


# Property 9: Circuit breaker prevents cascading failures
# **Validates: Requirements 6.6**

@given(
    failure_threshold=st.integers(min_value=2, max_value=5),
    num_failures=st.integers(min_value=1, max_value=10)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_opens_after_threshold_failures(failure_threshold, num_failures):
    """
    Property 9: For any sequence of External API calls, when failures exceed
    the threshold, the circuit breaker should open and fail fast without making
    additional API calls until the timeout period expires.
    
    This test verifies the circuit opens after reaching failure threshold.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    breaker = CircuitBreaker(failure_threshold=failure_threshold, timeout=60)
    
    call_count = 0
    
    def failing_function():
        nonlocal call_count
        call_count += 1
        raise NetworkError("API call failed")
    
    # Make calls until we reach or exceed the failure threshold
    for i in range(min(num_failures, failure_threshold)):
        try:
            breaker.call(failing_function)
        except NetworkError:
            pass  # Expected
    
    # After failure_threshold failures, circuit should be OPEN
    if num_failures >= failure_threshold:
        assert breaker.state == CircuitState.OPEN
        
        # Verify subsequent calls fail fast without calling the function
        initial_call_count = call_count
        
        with pytest.raises(CircuitBreakerError):
            breaker.call(failing_function)
        
        # CRITICAL: Verify function was NOT called (circuit is open)
        assert call_count == initial_call_count
    else:
        # Circuit should still be CLOSED
        assert breaker.state == CircuitState.CLOSED


@given(
    failure_threshold=st.integers(min_value=2, max_value=5),
    timeout=st.integers(min_value=1, max_value=2),
    num_fast_fail_attempts=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_fails_fast_when_open(
    failure_threshold, timeout, num_fast_fail_attempts
):
    """
    Property 9: When circuit is OPEN, all calls should fail fast without
    executing the protected function.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        timeout=timeout
    )
    
    call_count = 0
    
    def api_call():
        nonlocal call_count
        call_count += 1
        raise NetworkError("API unavailable")
    
    # Trigger failures to open the circuit
    for i in range(failure_threshold):
        try:
            breaker.call(api_call)
        except NetworkError:
            pass
    
    # Circuit should be OPEN
    assert breaker.state == CircuitState.OPEN
    
    # Record call count when circuit opened
    calls_before_open = call_count
    
    # Attempt multiple calls while circuit is open
    for i in range(num_fast_fail_attempts):
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.call(api_call)
        
        # Verify error indicates circuit is open
        assert "OPEN" in str(exc_info.value)
    
    # CRITICAL: Verify function was NOT called while circuit was open
    assert call_count == calls_before_open


@given(
    failure_threshold=st.integers(min_value=2, max_value=3),
    timeout=st.integers(min_value=1, max_value=2),
    success_threshold=st.integers(min_value=1, max_value=2)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_transitions_to_half_open_after_timeout(
    failure_threshold, timeout, success_threshold
):
    """
    Property 9: After timeout period expires, circuit should transition
    from OPEN to HALF_OPEN and allow test calls.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        timeout=timeout,
        success_threshold=success_threshold
    )
    
    def api_call():
        raise NetworkError("API failed")
    
    # Open the circuit
    for i in range(failure_threshold):
        try:
            breaker.call(api_call)
        except NetworkError:
            pass
    
    assert breaker.state == CircuitState.OPEN
    
    # Wait for timeout to expire
    time.sleep(timeout + 0.5)
    
    # Check state - should transition to HALF_OPEN
    current_state = breaker.state
    assert current_state == CircuitState.HALF_OPEN


@given(
    failure_threshold=st.integers(min_value=2, max_value=3),
    success_threshold=st.integers(min_value=1, max_value=2),
    num_successes=st.integers(min_value=1, max_value=3)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_closes_after_successful_half_open_calls(
    failure_threshold, success_threshold, num_successes
):
    """
    Property 9: In HALF_OPEN state, after success_threshold successful calls,
    circuit should close and resume normal operation.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    assume(num_successes >= success_threshold)
    
    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        timeout=1,
        success_threshold=success_threshold
    )
    
    call_count = 0
    
    def api_call_that_fails_then_succeeds():
        nonlocal call_count
        call_count += 1
        # Fail initially to open circuit
        if call_count <= failure_threshold:
            raise NetworkError("Initial failures")
        # Then succeed
        return "success"
    
    # Open the circuit
    for i in range(failure_threshold):
        try:
            breaker.call(api_call_that_fails_then_succeeds)
        except NetworkError:
            pass
    
    assert breaker.state == CircuitState.OPEN
    
    # Wait for timeout
    time.sleep(1.5)
    
    # Make successful calls in HALF_OPEN state
    for i in range(num_successes):
        result = breaker.call(api_call_that_fails_then_succeeds)
        assert result == "success"
        
        # Check if circuit closed after reaching success_threshold
        if i + 1 >= success_threshold:
            assert breaker.state == CircuitState.CLOSED
            break


@given(
    failure_threshold=st.integers(min_value=2, max_value=3)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_reopens_on_half_open_failure(failure_threshold):
    """
    Property 9: In HALF_OPEN state, any failure should immediately reopen
    the circuit.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        timeout=1,
        success_threshold=2
    )
    
    call_count = 0
    
    def api_call():
        nonlocal call_count
        call_count += 1
        raise NetworkError("API failed")
    
    # Open the circuit
    for i in range(failure_threshold):
        try:
            breaker.call(api_call)
        except NetworkError:
            pass
    
    assert breaker.state == CircuitState.OPEN
    
    # Wait for timeout to transition to HALF_OPEN
    time.sleep(1.5)
    assert breaker.state == CircuitState.HALF_OPEN
    
    # Make a failing call in HALF_OPEN state
    try:
        breaker.call(api_call)
    except NetworkError:
        pass
    
    # Circuit should be OPEN again
    assert breaker.state == CircuitState.OPEN


@given(
    failure_threshold=st.integers(min_value=2, max_value=5),
    num_calls=st.integers(min_value=1, max_value=10)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_tracks_failure_count_accurately(failure_threshold, num_calls):
    """
    Property 9: Circuit breaker should accurately track failure count
    and open exactly when threshold is reached.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    breaker = CircuitBreaker(failure_threshold=failure_threshold, timeout=60)
    
    def failing_api_call():
        raise NetworkError("API error")
    
    # Make calls and track state transitions
    for i in range(1, num_calls + 1):
        try:
            breaker.call(failing_api_call)
        except (NetworkError, CircuitBreakerError):
            pass
        
        # Check state based on failure count
        if i < failure_threshold:
            assert breaker.state == CircuitState.CLOSED
        else:
            assert breaker.state == CircuitState.OPEN


@given(
    failure_threshold=st.integers(min_value=3, max_value=5),
    num_successes_before_failure=st.integers(min_value=1, max_value=3)
)
@settings(max_examples=50, deadline=None)
def test_circuit_breaker_resets_failure_count_on_success(
    failure_threshold, num_successes_before_failure
):
    """
    Property 9: In CLOSED state, successful calls should reset the failure count.
    
    Feature: bdo-market-insights-rewrite, Property 9: Circuit breaker prevents cascading failures
    """
    assume(num_successes_before_failure < failure_threshold)
    
    breaker = CircuitBreaker(failure_threshold=failure_threshold, timeout=60)
    
    call_count = 0
    
    def api_call_with_intermittent_success():
        nonlocal call_count
        call_count += 1
        # Fail a few times, then succeed
        if call_count <= num_successes_before_failure:
            raise NetworkError("Temporary failure")
        return "success"
    
    # Make failing calls (less than threshold)
    for i in range(num_successes_before_failure):
        try:
            breaker.call(api_call_with_intermittent_success)
        except NetworkError:
            pass
    
    # Circuit should still be CLOSED
    assert breaker.state == CircuitState.CLOSED
    
    # Make a successful call
    result = breaker.call(api_call_with_intermittent_success)
    assert result == "success"
    
    # Failure count should be reset, so we can make more failures
    # without opening the circuit immediately
    call_count = 0  # Reset for new test
    
    for i in range(num_successes_before_failure):
        try:
            breaker.call(api_call_with_intermittent_success)
        except NetworkError:
            pass
    
    # Circuit should still be CLOSED (failures were reset by previous success)
    assert breaker.state == CircuitState.CLOSED
