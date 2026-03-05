"""
BDO Market Insights - Common Lambda Layer

This package provides shared utilities for all Lambda functions:
- LambdaRouter: Consistent request/response handling
- StructuredLogger: JSON logging with correlation IDs
- Correlation ID utilities: Generation and propagation
- Pydantic schemas: Input/output validation
"""

from .router import LambdaRouter
from .logging import StructuredLogger
from .correlation import generate_correlation_id, extract_correlation_id
from .secrets import (
    SecretsManagerClient,
    SecretsManagerError,
    SecretNotFoundError,
    SecretAccessDeniedError,
    InvalidSecretError,
)
from .schemas import (
    QueryRequest,
    MarketDataRecord,
    ItemRecord,
    ItemIdList,
    FetchDataInput,
    CleanDataInput,
    StoreDataInput,
    VALID_CATEGORIES,
)
from .database import DatabasePool, DatabasePoolError
from .retry import (
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
    AuthorizationError,
    ResourceNotFoundError,
)
from .circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState
from .metrics import MetricsClient, LatencyTracker

__all__ = [
    "LambdaRouter",
    "StructuredLogger",
    "generate_correlation_id",
    "extract_correlation_id",
    "SecretsManagerClient",
    "SecretsManagerError",
    "SecretNotFoundError",
    "SecretAccessDeniedError",
    "InvalidSecretError",
    "QueryRequest",
    "MarketDataRecord",
    "ItemRecord",
    "ItemIdList",
    "FetchDataInput",
    "CleanDataInput",
    "StoreDataInput",
    "VALID_CATEGORIES",
    "DatabasePool",
    "DatabasePoolError",
    "retry",
    "retry_with_context",
    "calculate_backoff",
    "is_retriable_error",
    "RetryableError",
    "NetworkError",
    "RateLimitError",
    "TemporaryUnavailableError",
    "NonRetriableError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "ResourceNotFoundError",
    "CircuitBreaker",
    "CircuitBreakerError",
    "CircuitState",
    "MetricsClient",
    "LatencyTracker",
]
