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

__all__ = [
    "LambdaRouter",
    "StructuredLogger",
    "generate_correlation_id",
    "extract_correlation_id",
    "QueryRequest",
    "MarketDataRecord",
    "ItemRecord",
    "ItemIdList",
    "FetchDataInput",
    "CleanDataInput",
    "StoreDataInput",
    "VALID_CATEGORIES",
]
