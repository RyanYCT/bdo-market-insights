"""
fetchData Lambda function - Fetches market data from External API.

This Lambda function:
- Receives item IDs from retrieveIdList
- Processes them in configurable batches
- Calls External API with rate limiting
- Implements retry logic with circuit breaker
- Handles rate limit headers from External API
- Emits CloudWatch metrics for API calls
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import boto3

# Import from Lambda Layer
from common.router import LambdaRouter
from common.logging import StructuredLogger
from common.retry import retry, RateLimitError, NetworkError, TemporaryUnavailableError
from common.circuit_breaker import CircuitBreaker, CircuitBreakerError
from common.schemas import FetchDataInput
from common.metrics import MetricsClient
from pydantic import ValidationError


# Configuration from environment variables
BASE_URL = os.getenv("BASE_URL")
REGION = os.getenv("REGION")
API_ENDPOINT = os.getenv("API_ENDPOINT")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "100"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
CIRCUIT_BREAKER_TIMEOUT = int(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "60"))

# Validate required configuration
if not BASE_URL:
    raise ValueError("BASE_URL environment variable is required")
if not REGION:
    raise ValueError("REGION environment variable is required")
if not API_ENDPOINT:
    raise ValueError("API_ENDPOINT environment variable is required")

# Initialize circuit breaker (shared across invocations in same container)
circuit_breaker = None


def get_circuit_breaker(logger: StructuredLogger) -> CircuitBreaker:
    """
    Get or create circuit breaker instance.
    
    Circuit breaker is shared across invocations in the same container
    to maintain state.
    
    Args:
        logger: StructuredLogger instance
        
    Returns:
        CircuitBreaker: Shared circuit breaker instance
    """
    global circuit_breaker
    if circuit_breaker is None:
        circuit_breaker = CircuitBreaker(
            failure_threshold=CIRCUIT_BREAKER_THRESHOLD,
            timeout=CIRCUIT_BREAKER_TIMEOUT,
            logger=logger
        )
    return circuit_breaker


class RateLimiter:
    """
    Rate limiter for External API calls.
    
    Tracks API calls per minute and enforces rate limits.
    """
    
    def __init__(self, calls_per_minute: int, logger: StructuredLogger):
        """
        Initialize rate limiter.
        
        Args:
            calls_per_minute: Maximum calls allowed per minute
            logger: StructuredLogger instance
        """
        self.calls_per_minute = calls_per_minute
        self.logger = logger
        self.call_times: List[float] = []
    
    def wait_if_needed(self) -> None:
        """
        Wait if rate limit would be exceeded.
        
        Removes calls older than 1 minute from tracking and waits
        if necessary to stay within rate limit.
        """
        current_time = time.time()
        
        # Remove calls older than 1 minute
        self.call_times = [t for t in self.call_times if current_time - t < 60]
        
        # Check if we're at the limit
        if len(self.call_times) >= self.calls_per_minute:
            # Calculate wait time until oldest call expires
            oldest_call = self.call_times[0]
            wait_time = 60 - (current_time - oldest_call)
            
            if wait_time > 0:
                self.logger.info(
                    f"Rate limit reached, waiting {wait_time:.2f} seconds",
                    calls_in_window=len(self.call_times),
                    rate_limit=self.calls_per_minute
                )
                time.sleep(wait_time)
                
                # Remove expired calls after waiting
                current_time = time.time()
                self.call_times = [t for t in self.call_times if current_time - t < 60]
    
    def record_call(self) -> None:
        """Record a new API call."""
        self.call_times.append(time.time())


class ExternalAPIClient:
    """
    Client for calling External BDO Market API.
    
    Handles HTTP requests with retry logic, rate limiting, and circuit breaker.
    """
    
    def __init__(
        self,
        base_url: str,
        region: str,
        api_endpoint: str,
        rate_limiter: RateLimiter,
        circuit_breaker: CircuitBreaker,
        metrics_client: MetricsClient,
        logger: StructuredLogger
    ):
        """
        Initialize API client.
        
        Args:
            base_url: Base URL for API
            region: Region code (e.g., 'na', 'eu')
            api_endpoint: API endpoint path (e.g., 'GetWorldMarketSearchList')
            rate_limiter: RateLimiter instance
            circuit_breaker: CircuitBreaker instance
            metrics_client: MetricsClient instance
            logger: StructuredLogger instance
        """
        self.base_url = base_url
        self.region = region
        self.api_endpoint = api_endpoint
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker
        self.metrics_client = metrics_client
        self.logger = logger
    
    @retry(
        max_attempts=3,
        backoff_base=2.0,
        retriable_exceptions=(NetworkError, RateLimitError, TemporaryUnavailableError)
    )
    def _make_request(self, url: str) -> Dict[str, Any]:
        """
        Make HTTP request with retry logic.
        
        Args:
            url: Full URL to request
            
        Returns:
            dict: Parsed JSON response
            
        Raises:
            NetworkError: On network/connection errors
            RateLimitError: On rate limit errors (429)
            TemporaryUnavailableError: On temporary server errors (503)
        """
        start_time = time.time()
        
        try:
            headers = {
                "User-Agent": "BDO-Market-Insights/2.0",
                "Accept": "application/json"
            }
            
            req = urllib.request.Request(url, headers=headers)
            
            with urllib.request.urlopen(req, timeout=30) as response:
                response_time = time.time() - start_time
                
                # Emit metrics using MetricsClient
                self.metrics_client.emit_api_call(
                    success=True,
                    response_time_ms=response_time * 1000,
                    status_code=response.getcode()
                )
                
                if response.getcode() == 200:
                    data = response.read().decode("utf-8")
                    return json.loads(data)
                else:
                    self.logger.warning(
                        f"Unexpected status code: {response.getcode()}",
                        url=url,
                        status_code=response.getcode()
                    )
                    return {}
                    
        except urllib.error.HTTPError as e:
            response_time = time.time() - start_time
            
            # Emit metrics using MetricsClient
            self.metrics_client.emit_api_call(
                success=False,
                response_time_ms=response_time * 1000,
                status_code=e.code
            )
            
            if e.code == 429:
                # Rate limit error - check for retry-after header
                retry_after = e.headers.get('Retry-After')
                retry_after_seconds = int(retry_after) if retry_after else 60
                
                self.logger.warning(
                    "Rate limit error from API",
                    status_code=e.code,
                    retry_after=retry_after_seconds,
                    url=url
                )
                
                raise RateLimitError(
                    f"API rate limit exceeded: {e.reason}",
                    retry_after=retry_after_seconds
                )
            
            elif e.code == 503:
                # Service unavailable - temporary error
                self.logger.warning(
                    "Service temporarily unavailable",
                    status_code=e.code,
                    url=url
                )
                raise TemporaryUnavailableError(f"Service unavailable: {e.reason}")
            
            else:
                # Other HTTP errors
                self.logger.error(
                    "HTTP error from API",
                    error=e,
                    status_code=e.code,
                    url=url
                )
                raise NetworkError(f"HTTP error {e.code}: {e.reason}")
                
        except urllib.error.URLError as e:
            response_time = time.time() - start_time
            
            # Emit metrics using MetricsClient
            self.metrics_client.emit_api_call(
                success=False,
                response_time_ms=response_time * 1000,
                status_code=0
            )
            
            self.logger.error(
                "URL error from API",
                error=e,
                url=url
            )
            raise NetworkError(f"URL error: {e.reason}")
            
        except Exception as e:
            response_time = time.time() - start_time
            
            # Emit metrics using MetricsClient
            self.metrics_client.emit_api_call(
                success=False,
                response_time_ms=response_time * 1000,
                status_code=0
            )
            
            self.logger.error(
                "Unexpected error calling API",
                error=e,
                url=url
            )
            raise NetworkError(f"Unexpected error: {str(e)}")
    
    def fetch_market_data(self, item_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Fetch market data for a list of item IDs.
        
        Args:
            item_ids: List of item IDs to fetch
            
        Returns:
            list: List of market data records
            
        Raises:
            CircuitBreakerError: If circuit breaker is open
            NetworkError: On network errors after retries exhausted
        """
        if not item_ids:
            self.logger.warning("No item IDs provided to fetch")
            return []
        
        # Wait if rate limit would be exceeded
        self.rate_limiter.wait_if_needed()
        
        # Build URL with item IDs as comma-separated list
        # Note: API uses 'id' parameter (singular) not 'ids'
        item_ids_str = ",".join(str(id) for id in item_ids)
        url = f"{self.base_url}/{self.region}/{self.api_endpoint}?id={item_ids_str}"
        
        self.logger.info(
            "Fetching market data from External API",
            item_count=len(item_ids),
            url=url
        )
        
        # Call API with circuit breaker protection
        try:
            data = self.circuit_breaker.call(self._make_request, url)
            
            # Record successful call for rate limiting
            self.rate_limiter.record_call()
            
            # Extract items from response
            # The API returns a nested array structure: [[item1_data...], [item2_data...]]
            # Each inner array contains market data for one item ID
            items = []
            
            if isinstance(data, list):
                # Flatten the nested array structure
                for item_group in data:
                    if isinstance(item_group, list):
                        items.extend(item_group)
                    else:
                        # Single item not in array
                        items.append(item_group)
            elif isinstance(data, dict):
                # Try common response keys if wrapped in dict
                nested_data = data.get('items', data.get('data', data.get('results', [])))
                if isinstance(nested_data, list):
                    for item_group in nested_data:
                        if isinstance(item_group, list):
                            items.extend(item_group)
                        else:
                            items.append(item_group)
            
            self.logger.info(
                "Successfully fetched market data",
                item_count=len(item_ids),
                records_returned=len(items)
            )
            
            return items
            
        except CircuitBreakerError as e:
            self.logger.error(
                "Circuit breaker is open, failing fast",
                error=e,
                circuit_state=self.circuit_breaker.state.value
            )
            raise
        
        except Exception as e:
            self.logger.error(
                "Failed to fetch market data",
                error=e,
                item_count=len(item_ids)
            )
            raise


def split_into_batches(item_ids: List[int], batch_size: int, max_batch_size: int) -> List[List[int]]:
    """
    Split item IDs into batches of specified size.
    
    Args:
        item_ids: List of item IDs
        batch_size: Desired batch size
        max_batch_size: Maximum allowed batch size
        
    Returns:
        list: List of batches (each batch is a list of item IDs)
    """
    # Enforce maximum batch size
    effective_batch_size = min(batch_size, max_batch_size)
    
    batches = []
    for i in range(0, len(item_ids), effective_batch_size):
        batch = item_ids[i:i + effective_batch_size]
        batches.append(batch)
    
    return batches


# Initialize router
router = LambdaRouter()


@router.route(function_name="fetchData")
def handler(event: Dict[str, Any], context: Any, logger: StructuredLogger) -> Dict[str, Any]:
    """
    Lambda handler for fetchData function.
    
    Args:
        event: Lambda event containing item_ids and optional batch_size
        context: Lambda context
        logger: StructuredLogger instance
        
    Returns:
        dict: Response containing raw market data and metadata
    """
    logger.info("fetchData Lambda invocation started", event_keys=list(event.keys()))
    
    # Initialize metrics client
    metrics = MetricsClient(namespace="BDOMarketInsights/ETL", logger=logger)
    
    try:
        # Validate input
        try:
            input_data = FetchDataInput(**event)
            logger.info(
                "Input validated successfully",
                item_count=len(input_data.item_ids),
                batch_size=input_data.batch_size
            )
        except ValidationError as e:
            logger.error("Input validation failed", error=e)
            metrics.emit_etl_failure(
                function_name="fetchData",
                error_type="ValidationError"
            )
            raise
        
        # Track execution latency
        with metrics.track_latency("fetchData"):
            # Get configuration
            batch_size = input_data.batch_size
            item_ids = input_data.item_ids
            
            # Split into batches
            batches = split_into_batches(item_ids, batch_size, MAX_BATCH_SIZE)
            
            logger.info(
                "Split items into batches",
                total_items=len(item_ids),
                batch_count=len(batches),
                batch_size=batch_size,
                max_batch_size=MAX_BATCH_SIZE
            )
            
            # Initialize rate limiter and circuit breaker
            rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, logger)
            breaker = get_circuit_breaker(logger)
            
            # Initialize API client
            api_client = ExternalAPIClient(
                base_url=BASE_URL,
                region=REGION,
                api_endpoint=API_ENDPOINT,
                rate_limiter=rate_limiter,
                circuit_breaker=breaker,
                metrics_client=metrics,
                logger=logger
            )
            
            # Fetch data for all batches
            all_data = []
            successful_batches = 0
            failed_batches = 0
            
            for i, batch in enumerate(batches):
                try:
                    logger.info(
                        f"Processing batch {i+1}/{len(batches)}",
                        batch_number=i+1,
                        batch_size=len(batch)
                    )
                    
                    batch_data = api_client.fetch_market_data(batch)
                    all_data.extend(batch_data)
                    successful_batches += 1
                    
                except CircuitBreakerError as e:
                    # Circuit breaker is open - fail fast for remaining batches
                    logger.error(
                        "Circuit breaker open, stopping batch processing",
                        error=e,
                        processed_batches=i,
                        remaining_batches=len(batches) - i
                    )
                    failed_batches = len(batches) - i
                    break
                    
                except Exception as e:
                    # Log error but continue with next batch
                    logger.error(
                        f"Failed to fetch batch {i+1}",
                        error=e,
                        batch_number=i+1
                    )
                    failed_batches += 1
            
            # Prepare response
            scrape_time = datetime.now(timezone.utc)
            
            response = {
                "raw_data": all_data,
                "scrape_endpoint": f"{BASE_URL}/{REGION}/{API_ENDPOINT}",
                "scrape_time": scrape_time.isoformat(),
                "correlation_id": input_data.correlation_id,
                "metadata": {
                    "total_items_requested": len(item_ids),
                    "total_records_fetched": len(all_data),
                    "batches_processed": successful_batches,
                    "batches_failed": failed_batches,
                    "batch_size": batch_size
                }
            }
            
            logger.info(
                "fetchData completed successfully",
                records_fetched=len(all_data),
                batches_processed=successful_batches,
                batches_failed=failed_batches
            )
            
            # Emit success metric
            metrics.emit_etl_success(
                function_name="fetchData",
                records_fetched=len(all_data),
                batches_processed=successful_batches
            )
            
            return response
        
    except ValidationError as e:
        # Re-raise validation errors to be handled by router
        raise
        
    except Exception as e:
        logger.error("Unexpected error in fetchData handler", error=e)
        metrics.emit_etl_failure(
            function_name="fetchData",
            error_type=type(e).__name__
        )
        raise


# Lambda entry point
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        dict: Lambda response
    """
    return handler(event, context)
