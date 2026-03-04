"""
BDO Market Insights - Common Lambda Layer

This package provides shared utilities for all Lambda functions:
- LambdaRouter: Consistent request/response handling
- StructuredLogger: JSON logging with correlation IDs
- Correlation ID utilities: Generation and propagation
"""

from .router import LambdaRouter
from .logging import StructuredLogger
from .correlation import generate_correlation_id, extract_correlation_id

__all__ = [
    "LambdaRouter",
    "StructuredLogger",
    "generate_correlation_id",
    "extract_correlation_id",
]
