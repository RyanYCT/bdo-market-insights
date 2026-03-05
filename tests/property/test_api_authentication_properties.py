"""
Property-based tests for API Gateway authentication and authorization.

Feature: bdo-market-insights-rewrite
Tests Properties 15 and 16 from the design document.
"""

import pytest
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings, assume
from collections import defaultdict

# Import schemas from lambda layer
import sys
sys.path.insert(0, 'lambda_layer/python')


# Mock API Gateway request/response structures
class MockAPIGatewayContext:
    """Mock API Gateway context for testing."""
    
    def __init__(self, request_id=None, api_key_id=None):
        self.request_id = request_id or "test-request-id"
        self.api_key_id = api_key_id or "test-api-key-id"
        self.identity = Mock()
        self.identity.api_key = self.api_key_id


class APIGatewayRateLimiter:
    """
    Simulates API Gateway rate limiting behavior for testing.
    
    This class mimics how API Gateway enforces rate limits per API key
    based on usage plans.
    """
    
    def __init__(self, rate_limit=100, burst_limit=20, window_seconds=60):
        """
        Initialize rate limiter.
        
        Args:
            rate_limit: Maximum requests per window (e.g., 100 per minute)
            burst_limit: Maximum concurrent requests
            window_seconds: Time window in seconds (default: 60 for per-minute)
        """
        self.rate_limit = rate_limit
        self.burst_limit = burst_limit
        self.window_seconds = window_seconds
        
        # Track requests per API key
        self.request_counts = defaultdict(list)  # api_key -> [timestamps]
        self.concurrent_requests = defaultdict(int)  # api_key -> count
    
    def check_rate_limit(self, api_key, current_time=None):
        """
        Check if request should be allowed based on rate limits.
        
        Returns:
            tuple: (allowed: bool, reason: str)
        """
        if current_time is None:
            current_time = time.time()
        
        # Check burst limit (concurrent requests)
        if self.concurrent_requests[api_key] >= self.burst_limit:
            return False, "Burst limit exceeded"
        
        # Clean up old timestamps outside the window
        window_start = current_time - self.window_seconds
        self.request_counts[api_key] = [
            ts for ts in self.request_counts[api_key]
            if ts > window_start
        ]
        
        # Check rate limit
        if len(self.request_counts[api_key]) >= self.rate_limit:
            return False, "Rate limit exceeded"
        
        return True, "OK"
    
    def record_request(self, api_key, current_time=None):
        """Record a request for rate limiting."""
        if current_time is None:
            current_time = time.time()
        
        self.request_counts[api_key].append(current_time)
        self.concurrent_requests[api_key] += 1
    
    def complete_request(self, api_key):
        """Mark a request as complete (for burst limit tracking)."""
        if self.concurrent_requests[api_key] > 0:
            self.concurrent_requests[api_key] -= 1
    
    def reset(self):
        """Reset all rate limit counters."""
        self.request_counts.clear()
        self.concurrent_requests.clear()


class APIAccessAuditor:
    """
    Simulates API access audit logging for testing.
    
    This class mimics how API Gateway logs access attempts for audit purposes.
    """
    
    def __init__(self):
        self.audit_logs = []
    
    def log_access(self, api_key_id, endpoint, method, status_code, 
                   timestamp=None, error_message=None, request_id=None):
        """
        Log an API access attempt.
        
        Args:
            api_key_id: API key identifier
            endpoint: API endpoint path
            method: HTTP method
            status_code: Response status code
            timestamp: Request timestamp
            error_message: Error message if request failed
            request_id: Unique request identifier
        """
        if timestamp is None:
            timestamp = datetime.utcnow().isoformat()
        
        log_entry = {
            "timestamp": timestamp,
            "api_key_id": api_key_id,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "request_id": request_id or f"req-{len(self.audit_logs)}",
            "result": "success" if 200 <= status_code < 300 else "failure"
        }
        
        if error_message:
            log_entry["error_message"] = error_message
        
        self.audit_logs.append(log_entry)
    
    def get_logs_for_key(self, api_key_id):
        """Get all audit logs for a specific API key."""
        return [log for log in self.audit_logs if log["api_key_id"] == api_key_id]
    
    def get_logs_by_status(self, status_code):
        """Get all audit logs with a specific status code."""
        return [log for log in self.audit_logs if log["status_code"] == status_code]
    
    def clear(self):
        """Clear all audit logs."""
        self.audit_logs.clear()


# Property 15: API key rate limiting
# **Validates: Requirements 9.3**

@given(
    num_requests=st.integers(min_value=1, max_value=200),
    rate_limit=st.integers(min_value=10, max_value=100),
    burst_limit=st.integers(min_value=5, max_value=50)
)
@settings(max_examples=100)
def test_api_key_rate_limiting_enforces_limits(num_requests, rate_limit, burst_limit):
    """
    Property 15: When requests exceed the configured rate limit,
    subsequent requests should receive 429 errors until the rate limit window resets.
    """
    # Ensure burst limit doesn't exceed rate limit
    assume(burst_limit <= rate_limit)
    
    rate_limiter = APIGatewayRateLimiter(
        rate_limit=rate_limit,
        burst_limit=burst_limit,
        window_seconds=60
    )
    
    api_key = "test-api-key-123"
    current_time = time.time()
    
    allowed_count = 0
    denied_count = 0
    
    # Simulate sequential requests
    for i in range(num_requests):
        allowed, reason = rate_limiter.check_rate_limit(api_key, current_time)
        
        if allowed:
            rate_limiter.record_request(api_key, current_time)
            allowed_count += 1
            # Simulate request completion (instant for sequential)
            rate_limiter.complete_request(api_key)
        else:
            denied_count += 1
        
        # Advance time slightly for each request
        current_time += 0.1
    
    # Verify rate limiting behavior
    if num_requests <= rate_limit:
        # All requests should be allowed if under limit
        assert allowed_count == num_requests
        assert denied_count == 0
    else:
        # Some requests should be denied if over limit
        assert denied_count > 0
        # At most rate_limit requests should be allowed in the window
        assert allowed_count <= rate_limit


@given(
    burst_size=st.integers(min_value=1, max_value=100),
    burst_limit=st.integers(min_value=5, max_value=50)
)
@settings(max_examples=100)
def test_api_key_burst_limit_enforces_concurrent_limit(burst_size, burst_limit):
    """
    Property 15: When concurrent requests exceed the burst limit,
    additional concurrent requests should be denied with 429 errors.
    """
    rate_limiter = APIGatewayRateLimiter(
        rate_limit=1000,  # High rate limit to focus on burst
        burst_limit=burst_limit,
        window_seconds=60
    )
    
    api_key = "test-api-key-456"
    current_time = time.time()
    
    # Simulate concurrent requests (all at same time)
    concurrent_allowed = 0
    concurrent_denied = 0
    
    for i in range(burst_size):
        allowed, reason = rate_limiter.check_rate_limit(api_key, current_time)
        
        if allowed:
            rate_limiter.record_request(api_key, current_time)
            concurrent_allowed += 1
        else:
            concurrent_denied += 1
            assert reason == "Burst limit exceeded"
    
    # Verify burst limiting behavior
    if burst_size <= burst_limit:
        # All concurrent requests should be allowed if under burst limit
        assert concurrent_allowed == burst_size
        assert concurrent_denied == 0
    else:
        # Requests beyond burst limit should be denied
        assert concurrent_denied > 0
        assert concurrent_allowed <= burst_limit


@given(
    initial_requests=st.integers(min_value=50, max_value=100),
    wait_time=st.integers(min_value=61, max_value=120)
)
@settings(max_examples=100)
def test_api_key_rate_limit_window_resets(initial_requests, wait_time):
    """
    Property 15: After the rate limit window expires,
    the rate limit counter should reset and allow new requests.
    """
    rate_limit = 100
    rate_limiter = APIGatewayRateLimiter(
        rate_limit=rate_limit,
        burst_limit=20,
        window_seconds=60
    )
    
    api_key = "test-api-key-789"
    current_time = time.time()
    
    # Fill up the rate limit
    for i in range(rate_limit):
        allowed, _ = rate_limiter.check_rate_limit(api_key, current_time)
        if allowed:
            rate_limiter.record_request(api_key, current_time)
            rate_limiter.complete_request(api_key)
    
    # Next request should be denied (rate limit reached)
    allowed_before_reset, _ = rate_limiter.check_rate_limit(api_key, current_time)
    assert not allowed_before_reset, "Request should be denied when rate limit is reached"
    
    # Wait for window to expire
    current_time += wait_time
    
    # Request after window reset should be allowed
    allowed_after_reset, _ = rate_limiter.check_rate_limit(api_key, current_time)
    assert allowed_after_reset, "Request should be allowed after rate limit window resets"


@given(
    num_keys=st.integers(min_value=2, max_value=10),
    requests_per_key=st.integers(min_value=50, max_value=150)
)
@settings(max_examples=100)
def test_api_key_rate_limits_are_independent(num_keys, requests_per_key):
    """
    Property 15: Rate limits should be enforced independently per API key.
    One key reaching its limit should not affect other keys.
    """
    rate_limit = 100
    rate_limiter = APIGatewayRateLimiter(
        rate_limit=rate_limit,
        burst_limit=20,
        window_seconds=60
    )
    
    current_time = time.time()
    
    # Track results per key
    results_per_key = {}
    
    for key_num in range(num_keys):
        api_key = f"test-api-key-{key_num}"
        allowed_count = 0
        
        for i in range(requests_per_key):
            allowed, _ = rate_limiter.check_rate_limit(api_key, current_time)
            if allowed:
                rate_limiter.record_request(api_key, current_time)
                rate_limiter.complete_request(api_key)
                allowed_count += 1
            current_time += 0.01  # Slight time advance
        
        results_per_key[api_key] = allowed_count
    
    # Verify each key gets its own rate limit allowance
    for api_key, allowed_count in results_per_key.items():
        if requests_per_key <= rate_limit:
            assert allowed_count == requests_per_key, \
                f"Key {api_key} should allow all requests under limit"
        else:
            assert allowed_count <= rate_limit, \
                f"Key {api_key} should not exceed rate limit"


# Property 16: API access audit logging
# **Validates: Requirements 9.5**

@given(
    num_requests=st.integers(min_value=1, max_value=50),
    status_codes=st.lists(
        st.sampled_from([200, 400, 401, 429, 500]),
        min_size=1,
        max_size=50
    )
)
@settings(max_examples=100)
def test_api_access_audit_logging_records_all_requests(num_requests, status_codes):
    """
    Property 16: For any API request (successful or failed),
    the system should emit an audit log entry.
    """
    # Ensure we have enough status codes for all requests
    while len(status_codes) < num_requests:
        status_codes.append(200)
    
    auditor = APIAccessAuditor()
    api_key = "test-api-key-audit"
    
    # Simulate requests
    for i in range(num_requests):
        status_code = status_codes[i]
        error_msg = "Error occurred" if status_code >= 400 else None
        
        auditor.log_access(
            api_key_id=api_key,
            endpoint="/query",
            method="GET",
            status_code=status_code,
            error_message=error_msg,
            request_id=f"req-{i}"
        )
    
    # Verify all requests were logged
    logs = auditor.get_logs_for_key(api_key)
    assert len(logs) == num_requests, \
        "All API requests should be logged regardless of success or failure"
    
    # Verify each log has required fields
    for log in logs:
        assert "timestamp" in log, "Log must contain timestamp"
        assert "api_key_id" in log, "Log must contain API key identifier"
        assert "endpoint" in log, "Log must contain endpoint"
        assert "method" in log, "Log must contain HTTP method"
        assert "status_code" in log, "Log must contain status code"
        assert "request_id" in log, "Log must contain request ID"
        assert "result" in log, "Log must contain result (success/failure)"


@given(
    api_keys=st.lists(
        st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
        min_size=1,
        max_size=10,
        unique=True
    ),
    requests_per_key=st.integers(min_value=1, max_value=20)
)
@settings(max_examples=100)
def test_api_access_audit_logging_includes_api_key_identifier(api_keys, requests_per_key):
    """
    Property 16: For any API request, the audit log should include
    the API key identifier for tracking and accountability.
    """
    auditor = APIAccessAuditor()
    
    # Simulate requests from different API keys
    for api_key in api_keys:
        for i in range(requests_per_key):
            auditor.log_access(
                api_key_id=api_key,
                endpoint="/query",
                method="GET",
                status_code=200,
                request_id=f"{api_key}-req-{i}"
            )
    
    # Verify logs can be filtered by API key
    for api_key in api_keys:
        key_logs = auditor.get_logs_for_key(api_key)
        assert len(key_logs) == requests_per_key, \
            f"Should have {requests_per_key} logs for key {api_key}"
        
        # Verify all logs for this key have correct identifier
        for log in key_logs:
            assert log["api_key_id"] == api_key, \
                "Log should contain correct API key identifier"


@given(
    successful_requests=st.integers(min_value=0, max_value=50),
    failed_requests=st.integers(min_value=0, max_value=50)
)
@settings(max_examples=100)
def test_api_access_audit_logging_distinguishes_success_failure(
    successful_requests, failed_requests
):
    """
    Property 16: For any API request, the audit log should clearly indicate
    whether the request was successful or failed.
    """
    assume(successful_requests + failed_requests > 0)
    
    auditor = APIAccessAuditor()
    api_key = "test-api-key-result"
    
    # Log successful requests
    for i in range(successful_requests):
        auditor.log_access(
            api_key_id=api_key,
            endpoint="/query",
            method="GET",
            status_code=200,
            request_id=f"success-{i}"
        )
    
    # Log failed requests
    for i in range(failed_requests):
        status_code = 400 if i % 2 == 0 else 500
        auditor.log_access(
            api_key_id=api_key,
            endpoint="/query",
            method="GET",
            status_code=status_code,
            error_message="Request failed",
            request_id=f"failure-{i}"
        )
    
    # Verify result field correctly indicates success/failure
    all_logs = auditor.get_logs_for_key(api_key)
    success_logs = [log for log in all_logs if log["result"] == "success"]
    failure_logs = [log for log in all_logs if log["result"] == "failure"]
    
    assert len(success_logs) == successful_requests, \
        "All successful requests should be marked as success"
    assert len(failure_logs) == failed_requests, \
        "All failed requests should be marked as failure"
    
    # Verify status codes align with results
    for log in success_logs:
        assert 200 <= log["status_code"] < 300, \
            "Success logs should have 2xx status codes"
    
    for log in failure_logs:
        assert log["status_code"] >= 400, \
            "Failure logs should have 4xx or 5xx status codes"


@given(
    num_requests=st.integers(min_value=1, max_value=100)
)
@settings(max_examples=100)
def test_api_access_audit_logging_includes_timestamp(num_requests):
    """
    Property 16: For any API request, the audit log should include
    a timestamp for temporal analysis and compliance.
    """
    auditor = APIAccessAuditor()
    api_key = "test-api-key-timestamp"
    
    start_time = datetime.utcnow()
    
    # Simulate requests
    for i in range(num_requests):
        auditor.log_access(
            api_key_id=api_key,
            endpoint="/query",
            method="GET",
            status_code=200,
            request_id=f"req-{i}"
        )
    
    end_time = datetime.utcnow()
    
    # Verify all logs have timestamps
    logs = auditor.get_logs_for_key(api_key)
    assert len(logs) == num_requests
    
    for log in logs:
        assert "timestamp" in log, "Log must contain timestamp"
        
        # Parse timestamp and verify it's within test execution window
        log_time = datetime.fromisoformat(log["timestamp"].replace('Z', '+00:00'))
        assert start_time <= log_time <= end_time, \
            "Log timestamp should be within test execution window"


@given(
    endpoints=st.lists(
        st.sampled_from(["/query", "/health", "/metrics"]),
        min_size=1,
        max_size=20
    ),
    methods=st.lists(
        st.sampled_from(["GET", "POST", "OPTIONS"]),
        min_size=1,
        max_size=20
    )
)
@settings(max_examples=100)
def test_api_access_audit_logging_includes_endpoint_and_method(endpoints, methods):
    """
    Property 16: For any API request, the audit log should include
    the endpoint and HTTP method for request tracking.
    """
    # Ensure equal length lists
    min_len = min(len(endpoints), len(methods))
    endpoints = endpoints[:min_len]
    methods = methods[:min_len]
    
    auditor = APIAccessAuditor()
    api_key = "test-api-key-endpoint"
    
    # Simulate requests to different endpoints with different methods
    for i, (endpoint, method) in enumerate(zip(endpoints, methods)):
        auditor.log_access(
            api_key_id=api_key,
            endpoint=endpoint,
            method=method,
            status_code=200,
            request_id=f"req-{i}"
        )
    
    # Verify all logs have endpoint and method
    logs = auditor.get_logs_for_key(api_key)
    assert len(logs) == len(endpoints)
    
    for i, log in enumerate(logs):
        assert log["endpoint"] == endpoints[i], \
            "Log should contain correct endpoint"
        assert log["method"] == methods[i], \
            "Log should contain correct HTTP method"


@given(
    num_requests=st.integers(min_value=1, max_value=50)
)
@settings(max_examples=100)
def test_api_access_audit_logging_includes_unique_request_id(num_requests):
    """
    Property 16: For any API request, the audit log should include
    a unique request ID for correlation with other logs.
    """
    auditor = APIAccessAuditor()
    api_key = "test-api-key-reqid"
    
    # Simulate requests with unique request IDs
    request_ids = [f"unique-req-{i}" for i in range(num_requests)]
    
    for req_id in request_ids:
        auditor.log_access(
            api_key_id=api_key,
            endpoint="/query",
            method="GET",
            status_code=200,
            request_id=req_id
        )
    
    # Verify all logs have unique request IDs
    logs = auditor.get_logs_for_key(api_key)
    assert len(logs) == num_requests
    
    logged_request_ids = [log["request_id"] for log in logs]
    
    # All request IDs should be present
    assert set(logged_request_ids) == set(request_ids), \
        "All request IDs should be logged"
    
    # All request IDs should be unique
    assert len(logged_request_ids) == len(set(logged_request_ids)), \
        "All request IDs should be unique"
