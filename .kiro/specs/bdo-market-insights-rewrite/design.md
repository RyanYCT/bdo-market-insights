# Design Document: BDO Market Insights Rewrite

## Overview

The BDO Market Insights system is a serverless ETL pipeline that collects, processes, and serves Black Desert Online market data. The rewrite maintains the existing serverless architecture while addressing code quality, reliability, security, and maintainability concerns.

The system consists of two primary workflows:

1. **ETL Pipeline (Scheduled)**: Automated data collection running on EventBridge Scheduler
   - retrieveIdList → fetchData → cleanData → storeData

2. **Query Pipeline (On-demand)**: API-driven data retrieval orchestrated by Step Functions
   - API Gateway (GET) → Step Functions → queryData → analyzeData → Response

Key improvements include: shared Lambda Layer for common code, Pydantic input validation, Secrets Manager for credentials, structured JSON logging with correlation IDs, database connection pooling, comprehensive error handling, API authentication, automated data retention, and extensive testing.

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         ETL Pipeline (Scheduled)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  EventBridge Scheduler                                          │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │retrieveIdList│───▶│  fetchData   │───▶│  cleanData  │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                    │                    │             │
│         │                    │                    ▼             │
│         │                    │            ┌──────────────┐      │
│         │                    │            │  storeData   │      │
│         │                    │            └──────────────┘      │
│         │                    │                    │             │
│         ▼                    ▼                    ▼             │
│    DynamoDB            External API          PostgreSQL         │
│   (Item IDs)          (Market Data)          (RDS + Proxy)      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Query Pipeline (On-demand)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  API Gateway (GET /query?item_id=X&start_date=Y)                │
│         │                                                       │
│         │ (API Key Auth + Rate Limiting)                        │
│         ▼                                                       │
│  Step Functions State Machine                                   │
│         │                                                       │
│         ├──▶ queryData Lambda ──┐                              │
│         │                         │                             │
│         │                         ▼                             │
│         └──▶ (merge results) ──▶ analyzeData Lambda            │
│                                   │                             │
│                                   ▼                             │
│                              API Response                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      Shared Infrastructure                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Lambda Layer (Common Code)                                     │
│  ├─ LambdaRouter                                                │
│  ├─ Logging utilities (JSON + Correlation IDs)                  │
│  ├─ Database connection pool manager                            │
│  ├─ Error handling & retry logic                                │
│  └─ Pydantic schemas                                            │
│                                                                 │
│  AWS Secrets Manager                                            │
│  ├─ PostgreSQL credentials                                      │
│  └─ External API tokens                                         │
│                                                                 │
│  CloudWatch + X-Ray                                             │
│  ├─ Structured JSON logs                                        │
│  ├─ Custom metrics                                              │
│  └─ Distributed tracing                                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

**Lambda Layer (Shared Code)**:
- Provides LambdaRouter class for consistent request/response handling
- Implements structured logging with correlation ID propagation
- Manages database connection pooling and lifecycle
- Provides retry logic with exponential backoff
- Defines Pydantic schemas for validation

**ETL Pipeline Lambdas**:
- `retrieveIdList`: Fetches item IDs from DynamoDB, validates output
- `fetchData`: Calls External API with rate limiting and batching, handles retries
- `cleanData`: Transforms and filters raw data, validates against schemas
- `storeData`: Persists to PostgreSQL using connection pool, supports configurable categories

**Query Pipeline Lambdas**:
- `queryData`: Retrieves MarketData from PostgreSQL based on query parameters
- `analyzeData`: Computes profitability metrics and aggregations

**Step Functions State Machine**:
- Orchestrates Query Pipeline with input/output transformations
- Transforms API Gateway GET parameters into Lambda input format
- Merges queryData results with original query context for analyzeData
- Handles error states and retries

**API Gateway**:
- Exposes RESTful GET endpoints for queries
- Enforces API key authentication
- Implements rate limiting per API key
- Configures CORS for web clients
- Serves OpenAPI documentation

**RDS Proxy**:
- Manages PostgreSQL connection pooling
- Handles connection lifecycle and failover
- Reduces Lambda cold start connection overhead

**Data Retention Lambda**:
- Scheduled monthly execution
- Aggregates old detailed records into daily summaries
- Archives ancient data to S3 Glacier
- Configurable retention periods

## Components and Interfaces

### Lambda Layer Structure

```python
# lambda_layer/
# ├── python/
# │   ├── common/
# │   │   ├── __init__.py
# │   │   ├── router.py          # LambdaRouter class
# │   │   ├── logging.py         # Structured logging utilities
# │   │   ├── database.py        # Connection pool manager
# │   │   ├── errors.py          # Custom exceptions and error handling
# │   │   ├── retry.py           # Retry logic with backoff
# │   │   ├── secrets.py         # Secrets Manager client
# │   │   └── schemas.py         # Pydantic validation schemas
```

**LambdaRouter Interface**:
```python
class LambdaRouter:
    """Handles Lambda request routing and response formatting."""
    
    def __init__(self, correlation_id: Optional[str] = None):
        """Initialize router with optional correlation ID."""
        
    def route(self, event: dict, context: Any) -> dict:
        """
        Route Lambda event to appropriate handler.
        
        Returns standardized response with status code and body.
        Handles exceptions and formats error responses.
        """
        
    def add_route(self, path: str, handler: Callable) -> None:
        """Register a handler function for a specific route."""
```

**Logging Interface**:
```python
class StructuredLogger:
    """Provides structured JSON logging with correlation IDs."""
    
    def __init__(self, function_name: str, correlation_id: str):
        """Initialize logger for specific Lambda function."""
        
    def info(self, message: str, **kwargs) -> None:
        """Log info level with structured fields."""
        
    def error(self, message: str, error: Exception, **kwargs) -> None:
        """Log error with exception details and stack trace."""
        
    def with_context(self, **kwargs) -> 'StructuredLogger':
        """Create child logger with additional context fields."""
```

**Database Connection Pool Interface**:
```python
class DatabasePool:
    """Manages PostgreSQL connection pooling for Lambda."""
    
    def __init__(self, secret_name: str, pool_size: int = 5):
        """Initialize pool with credentials from Secrets Manager."""
        
    def get_connection(self) -> Connection:
        """
        Get connection from pool (reuse if available).
        
        Implements retry logic for connection failures.
        Returns connection with automatic context management.
        """
        
    def execute_query(self, query: str, params: tuple) -> List[dict]:
        """Execute parameterized query and return results."""
        
    def execute_many(self, query: str, params_list: List[tuple]) -> int:
        """Execute batch insert/update and return affected rows."""
```

**Retry Logic Interface**:
```python
@retry(
    max_attempts=3,
    backoff_base=2,
    retriable_exceptions=[NetworkError, RateLimitError]
)
def call_external_api(url: str, params: dict) -> dict:
    """
    Call external API with automatic retry on transient failures.
    
    Respects rate limit headers and implements exponential backoff.
    """
```

### Pydantic Schemas

**Query Request Schema**:
```python
class QueryRequest(BaseModel):
    """Validates API Gateway GET query parameters."""
    
    item_id: Optional[int] = Field(None, gt=0, description="Specific item ID to query")
    name: Optional[str] = Field(None, max_length=100, description="Item name to search")
    category: Optional[str] = Field(None, description="Category filter")
    start_date: datetime = Field(description="Start of date range for last_sold_time")
    end_date: datetime = Field(description="End of date range for last_sold_time")
    limit: int = Field(default=100, ge=1, le=1000, description="Max results")
    
    @validator('end_date')
    def end_after_start(cls, v, values):
        """Ensure end_date is after start_date."""
        if 'start_date' in values and v < values['start_date']:
            raise ValueError('end_date must be after start_date')
        return v
    
    @validator('category')
    def validate_category(cls, v):
        """Ensure category is valid if provided."""
        if v is not None:
            valid_categories = ['Uncategorized', 'Accessory', 'Buff', 'Costume']
            if v not in valid_categories:
                raise ValueError(f'Category must be one of {valid_categories}')
        return v
    
    @root_validator
    def require_item_or_name(cls, values):
        """Require either item_id or name to be specified."""
        if not values.get('item_id') and not values.get('name'):
            raise ValueError('Either item_id or name must be specified')
        return values
```

**Market Data Schema**:
```python
class MarketDataRecord(BaseModel):
    """Validates market data from External API."""
    
    item_id: int = Field(description="Item ID from BDO API")
    sid: int = Field(description="Enhancement level")
    current_stock: int = Field(ge=0, description="Current market stock")
    total_trades: int = Field(ge=0, description="Total number of trades")
    last_sold_price: int = Field(ge=0, description="Last sold price in silver")
    last_sold_time: datetime = Field(description="Timestamp of last sale")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class ItemRecord(BaseModel):
    """Validates item information."""
    
    name: str = Field(max_length=100)
    item_id: int
    sid: int  # Enhancement level
    category: str = Field(description="Category name from ItemCategory choices")
    
    @validator('category')
    def validate_category(cls, v):
        """Ensure category is one of the valid choices."""
        valid_categories = ['Uncategorized', 'Accessory', 'Buff', 'Costume']
        if v not in valid_categories:
            raise ValueError(f'Category must be one of {valid_categories}')
        return v
```

**ETL Pipeline Schemas**:
```python
class ItemIdList(BaseModel):
    """Output from retrieveIdList Lambda."""
    item_ids: List[int] = Field(min_items=1)
    correlation_id: str

class FetchDataInput(BaseModel):
    """Input to fetchData Lambda."""
    item_ids: List[int]
    batch_size: int = Field(default=50, ge=1, le=100)
    correlation_id: str

class CleanDataInput(BaseModel):
    """Input to cleanData Lambda."""
    raw_data: List[dict]
    correlation_id: str

class StoreDataInput(BaseModel):
    """Input to storeData Lambda."""
    cleaned_data: List[MarketDataRecord]
    scrape_endpoint: str = Field(description="API endpoint used for this scrape")
    scrape_time: datetime = Field(description="Timestamp of this scrape session")
    correlation_id: str
```

### Step Functions State Machine

**State Machine Definition** (Amazon States Language):
```json
{
  "Comment": "Query Pipeline - GET request handling",
  "StartAt": "TransformInput",
  "States": {
    "TransformInput": {
      "Type": "Pass",
      "Parameters": {
        "queryParams.$": "$.queryStringParameters",
        "correlationId.$": "$.requestContext.requestId"
      },
      "Next": "QueryData"
    },
    "QueryData": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:queryData",
      "Parameters": {
        "item_id.$": "$.queryParams.item_id",
        "start_date.$": "$.queryParams.start_date",
        "end_date.$": "$.queryParams.end_date",
        "limit.$": "$.queryParams.limit",
        "correlation_id.$": "$.correlationId"
      },
      "ResultPath": "$.queryResult",
      "Next": "MergeForAnalysis",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "HandleError"
        }
      ]
    },
    "MergeForAnalysis": {
      "Type": "Pass",
      "Parameters": {
        "market_data.$": "$.queryResult.data",
        "query_params.$": "$.queryParams",
        "correlation_id.$": "$.correlationId"
      },
      "Next": "AnalyzeData"
    },
    "AnalyzeData": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:REGION:ACCOUNT:function:analyzeData",
      "Parameters": {
        "market_data.$": "$.market_data",
        "query_params.$": "$.query_params",
        "correlation_id.$": "$.correlation_id"
      },
      "ResultPath": "$.analysisResult",
      "Next": "FormatResponse",
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "HandleError"
        }
      ]
    },
    "FormatResponse": {
      "Type": "Pass",
      "Parameters": {
        "statusCode": 200,
        "headers": {
          "Content-Type": "application/json",
          "Cache-Control": "public, max-age=300"
        },
        "body.$": "States.JsonToString($.analysisResult)"
      },
      "End": true
    },
    "HandleError": {
      "Type": "Pass",
      "Parameters": {
        "statusCode": 500,
        "headers": {
          "Content-Type": "application/json"
        },
        "body": {
          "error": "Internal server error",
          "correlation_id.$": "$.correlationId"
        }
      },
      "End": true
    }
  }
}
```

**Key Transformations**:
1. `TransformInput`: Extracts query parameters from API Gateway GET request
2. `QueryData`: Passes validated parameters to queryData Lambda
3. `MergeForAnalysis`: Combines query results with original parameters
4. `AnalyzeData`: Receives both data and context for analysis
5. `FormatResponse`: Structures response with appropriate headers

### API Gateway Configuration

**Endpoint Definition**:
```
GET /query
  Query Parameters:
    - item_id (required): integer
    - start_date (required): ISO 8601 datetime
    - end_date (required): ISO 8601 datetime
    - limit (optional): integer (default: 100, max: 1000)
  
  Headers:
    - x-api-key (required): API key for authentication
  
  Responses:
    200: Success with market data and analysis
    400: Invalid query parameters
    401: Missing or invalid API key
    429: Rate limit exceeded
    500: Internal server error
```

**API Key Configuration**:
- Usage plans with rate limiting (e.g., 100 requests/minute per key)
- Burst limits (e.g., 20 concurrent requests)
- Quota limits (e.g., 10,000 requests/day)

**CORS Configuration**:
```python
cors_config = {
    "AllowOrigins": ["https://example.com"],  # Configurable
    "AllowMethods": ["GET", "OPTIONS"],
    "AllowHeaders": ["Content-Type", "X-Api-Key"],
    "MaxAge": 3600
}
```

## Data Models

### PostgreSQL Schema

The system uses Django ORM models that map to the following PostgreSQL schema:

**ItemCategory Table**:
```sql
CREATE TABLE item_category (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) NOT NULL UNIQUE,
    -- Valid values: 'Uncategorized', 'Accessory', 'Buff', 'Costume'
    
    CHECK (name IN ('Uncategorized', 'Accessory', 'Buff', 'Costume'))
);
```

**Item Table**:
```sql
CREATE TABLE item (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    item_id INTEGER NOT NULL,
    sid INTEGER NOT NULL,  -- Enhancement level
    category_id INTEGER NOT NULL REFERENCES item_category(id) ON DELETE SET DEFAULT,
    
    UNIQUE (item_id, sid),
    INDEX idx_item_name (name),
    INDEX idx_item_id_sid (item_id, sid)
);
```

**MarketScrape Table**:
```sql
CREATE TABLE market_scrape (
    id SERIAL PRIMARY KEY,
    endpoint VARCHAR(100) NOT NULL,
    scrape_time TIMESTAMP WITH TIME ZONE NOT NULL UNIQUE,
    
    INDEX idx_scrape_time (scrape_time)
);
```

**MarketData Table**:
```sql
CREATE TABLE market_data (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    current_stock BIGINT NOT NULL,
    total_trades BIGINT NOT NULL,
    last_sold_price BIGINT NOT NULL,
    last_sold_time TIMESTAMP WITH TIME ZONE NOT NULL,
    scrape_id INTEGER NOT NULL REFERENCES market_scrape(id) ON DELETE CASCADE,
    
    UNIQUE (item_id, scrape_id),
    INDEX idx_market_data_scrape (scrape_id),
    INDEX idx_market_data_last_sold_time (last_sold_time)
);
```

**MarketDataSummary Table** (new - for aggregated data retention):
```sql
CREATE TABLE market_data_summary (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    avg_last_sold_price DECIMAL(15, 2) NOT NULL,
    min_last_sold_price BIGINT NOT NULL,
    max_last_sold_price BIGINT NOT NULL,
    avg_current_stock DECIMAL(15, 2) NOT NULL,
    total_trades_sum BIGINT NOT NULL,
    record_count INTEGER NOT NULL,
    
    UNIQUE (item_id, date),
    INDEX idx_summary_item_date (item_id, date DESC)
);
```

**Configuration Table** (new - for system configuration):
```sql
CREATE TABLE system_config (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Example configuration values
INSERT INTO system_config (key, value, description) VALUES
    ('retention_detailed_days', '90', 'Days to keep detailed market data'),
    ('retention_summary_days', '730', 'Days to keep summary data (2 years)');
```

**Schema Notes**:
- The existing Django models remain unchanged for backward compatibility
- New `market_data_summary` table added for data retention aggregation
- New `system_config` table added for runtime configuration
- All existing indexes are preserved
- Foreign key relationships maintained as defined in Django models

### DynamoDB Schema

**ItemIds Table**:
```python
{
    "TableName": "bdo-item-ids",
    "KeySchema": [
        {"AttributeName": "category_id", "KeyType": "HASH"}
    ],
    "AttributeDefinitions": [
        {"AttributeName": "category_id", "AttributeType": "N"}
    ],
    "Attributes": {
        "category_id": "Number",
        "item_ids": "List[Number]",
        "last_updated": "String (ISO 8601)"
    }
}
```

### Data Flow Models

**ETL Pipeline Data Flow**:
```
retrieveIdList Output:
{
    "item_ids": [1001, 1002, 1003, ...],
    "correlation_id": "uuid-v4"
}

fetchData Output:
{
    "raw_data": [
        {
            "itemId": 1001,
            "sid": 0,
            "currentStock": 50,
            "totalTrades": 1234,
            "lastSoldPrice": 15000,
            "lastSoldTime": "2024-01-15T10:30:00Z"
        },
        ...
    ],
    "correlation_id": "uuid-v4"
}

cleanData Output:
{
    "cleaned_data": [
        {
            "item_id": 1001,
            "sid": 0,
            "current_stock": 50,
            "total_trades": 1234,
            "last_sold_price": 15000,
            "last_sold_time": "2024-01-15T10:30:00+00:00"
        },
        ...
    ],
    "correlation_id": "uuid-v4"
}

storeData Output:
{
    "records_inserted": 150,
    "scrape_id": 42,
    "correlation_id": "uuid-v4"
}
```

**Query Pipeline Data Flow**:
```
API Gateway GET Request:
GET /query?item_id=1001&start_date=2024-01-01T00:00:00Z&end_date=2024-01-15T23:59:59Z&limit=100

Step Functions Input (after TransformInput):
{
    "queryParams": {
        "item_id": "1001",
        "start_date": "2024-01-01T00:00:00Z",
        "end_date": "2024-01-15T23:59:59Z",
        "limit": "100"
    },
    "correlationId": "request-id-from-api-gateway"
}

queryData Output:
{
    "data": [
        {
            "item_id": 1001,
            "item_name": "Black Stone (Weapon)",
            "sid": 0,
            "current_stock": 50,
            "total_trades": 1234,
            "last_sold_price": 15000,
            "last_sold_time": "2024-01-15T10:30:00+00:00",
            "scrape_time": "2024-01-15T10:35:00+00:00"
        },
        ...
    ],
    "count": 100,
    "correlation_id": "request-id-from-api-gateway"
}

analyzeData Input (after MergeForAnalysis):
{
    "market_data": [...],  // from queryData
    "query_params": {...},  // original parameters
    "correlation_id": "request-id-from-api-gateway"
}

analyzeData Output:
{
    "item_id": 1001,
    "item_name": "Black Stone (Weapon)",
    "date_range": {
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-15T23:59:59Z"
    },
    "statistics": {
        "avg_last_sold_price": 14850.50,
        "min_last_sold_price": 14000,
        "max_last_sold_price": 16000,
        "avg_current_stock": 48.5,
        "total_trades_sum": 123400
    },
    "profitability_score": 0.85,
    "price_trend": "increasing",
    "correlation_id": "request-id-from-api-gateway"
}
```

## Error Handling

### Error Classification

**Retriable Errors**:
- Network timeouts (External API, database)
- Rate limit errors (429 from External API)
- Temporary database unavailability
- Lambda throttling (429 from AWS)

**Non-Retriable Errors**:
- Validation failures (400)
- Authentication failures (401)
- Authorization failures (403)
- Resource not found (404)
- Invalid SQL syntax
- Schema validation errors

### Retry Strategy

**Exponential Backoff Configuration**:
```python
retry_config = {
    "max_attempts": 3,
    "backoff_base": 2,  # seconds
    "backoff_multiplier": 2,
    "max_backoff": 60,  # seconds
    "jitter": True  # Add randomness to prevent thundering herd
}

# Retry delays: 2s, 4s, 8s (with jitter)
```

**Circuit Breaker Pattern**:
```python
class CircuitBreaker:
    """Prevents cascading failures to External API."""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        """
        failure_threshold: Number of failures before opening circuit
        timeout: Seconds to wait before attempting to close circuit
        """
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        
    def call(self, func: Callable) -> Any:
        """
        Execute function with circuit breaker protection.
        
        States:
        - CLOSED: Normal operation, track failures
        - OPEN: Fail fast without calling function
        - HALF_OPEN: Allow one test call to check recovery
        """
```

### Error Response Format

**Standardized Error Response**:
```python
{
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "Invalid query parameters",
        "details": {
            "item_id": "must be a positive integer",
            "end_date": "must be after start_date"
        },
        "correlation_id": "uuid-v4",
        "timestamp": "2024-01-15T10:30:00Z"
    }
}
```

**Error Codes**:
- `VALIDATION_ERROR`: Input validation failed
- `AUTHENTICATION_ERROR`: Missing or invalid API key
- `RATE_LIMIT_EXCEEDED`: Too many requests
- `EXTERNAL_API_ERROR`: External API call failed
- `DATABASE_ERROR`: Database operation failed
- `INTERNAL_ERROR`: Unexpected system error

### Logging Error Context

**Error Log Format**:
```json
{
    "timestamp": "2024-01-15T10:30:00Z",
    "level": "ERROR",
    "function_name": "fetchData",
    "correlation_id": "uuid-v4",
    "request_id": "lambda-request-id",
    "message": "External API call failed",
    "error": {
        "type": "NetworkError",
        "message": "Connection timeout after 30s",
        "stack_trace": "...",
        "retry_attempt": 2,
        "max_attempts": 3
    },
    "context": {
        "item_ids": [1001, 1002],
        "batch_size": 50,
        "api_endpoint": "https://api.example.com/market"
    }
}
```

## Testing Strategy

### Testing Approach

The system requires a dual testing approach combining unit tests for specific scenarios and property-based tests for universal correctness properties. This ensures both concrete bug detection and general correctness verification.

**Unit Testing**:
- Specific examples demonstrating correct behavior
- Edge cases (empty inputs, boundary values, special characters)
- Error conditions (invalid inputs, service failures)
- Integration points between components
- Focus on concrete scenarios rather than exhaustive input coverage

**Property-Based Testing**:
- Universal properties that hold for all valid inputs
- Comprehensive input coverage through randomization
- Minimum 100 iterations per property test
- Each test references its design document property
- Tag format: `# Feature: bdo-market-insights-rewrite, Property {N}: {property_text}`

**Testing Framework**:
- Unit tests: `pytest` with `pytest-cov` for coverage
- Property-based tests: `hypothesis` for Python
- Mocking: `pytest-mock` and `moto` for AWS services
- Integration tests: `boto3` with test AWS resources

**Test Organization**:
```
tests/
├── unit/
│   ├── test_lambda_router.py
│   ├── test_logging.py
│   ├── test_database_pool.py
│   ├── test_validation_schemas.py
│   └── test_lambdas/
│       ├── test_retrieve_id_list.py
│       ├── test_fetch_data.py
│       ├── test_clean_data.py
│       ├── test_store_data.py
│       ├── test_query_data.py
│       └── test_analyze_data.py
├── integration/
│   ├── test_etl_pipeline.py
│   ├── test_query_pipeline.py
│   └── test_step_functions.py
├── property/
│   ├── test_validation_properties.py
│   ├── test_transformation_properties.py
│   ├── test_database_properties.py
│   └── test_api_properties.py
└── conftest.py  # Shared fixtures
```

### Test Coverage Requirements

- Minimum 80% code coverage for Lambda functions
- 100% coverage for validation schemas
- 100% coverage for error handling paths
- All correctness properties implemented as property-based tests


## Correctness Properties

A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.

### Property Reflection

After analyzing all acceptance criteria, I identified the following redundancies and consolidations:

**Logging Properties Consolidation**:
- Properties 4.1, 4.3, 4.5, 4.6 all relate to log structure and content
- These can be consolidated into a single comprehensive property about log entry completeness
- Property 4.2 (correlation ID uniqueness) is distinct and should remain separate
- Property 4.4 (correlation ID propagation) is distinct and should remain separate

**Retry and Error Handling Consolidation**:
- Properties 6.1, 6.2, 6.3 all relate to retry behavior
- These can be consolidated into a single comprehensive property about retry logic
- Property 6.6 (circuit breaker) is distinct and should remain separate

**Batch Processing Consolidation**:
- Properties 7.1, 7.2, 7.4 all relate to batch size management
- These can be consolidated into a single property about batch processing
- Property 7.3 (rate limiting) is distinct
- Property 7.5 (metrics) is distinct

**Step Functions Data Flow Consolidation**:
- Properties 8.3 and 8.4 both relate to data transformation and preservation in Step Functions
- These can be consolidated into a single property about data flow integrity

**Configuration Properties Consolidation**:
- Properties 14.3 and 14.4 both relate to configuration loading behavior
- These can be consolidated into a single property about configuration validation and defaults

After reflection, the following properties provide unique validation value:

### Input Validation Properties

Property 1: Schema validation rejects invalid inputs
*For any* Lambda function input that violates its Pydantic schema, the system should reject it with a 400 error containing specific validation failure details
**Validates: Requirements 2.2, 2.3**

Property 2: Schema validation accepts valid inputs
*For any* Lambda function input that conforms to its Pydantic schema, the system should accept it and process it successfully
**Validates: Requirements 2.2**

### Security and Credential Management Properties

Property 3: Secret caching within execution context
*For any* Lambda execution context, when secrets are retrieved multiple times, the system should return cached values after the first retrieval without making additional Secrets Manager calls
**Validates: Requirements 3.4**

### Logging Properties

Property 4: Log entry completeness
*For any* log entry emitted by the system, it should be valid JSON and contain all required fields: timestamp, level, function_name, correlation_id, message, request_id, and execution context metadata
**Validates: Requirements 4.1, 4.3, 4.5, 4.6**

Property 5: Correlation ID uniqueness
*For any* set of concurrent requests entering the system, all generated correlation IDs should be unique
**Validates: Requirements 4.2**

Property 6: Correlation ID propagation
*For any* request that flows through multiple Lambda functions, all log entries across all functions should contain the same correlation_id
**Validates: Requirements 4.4**

### Database Connection Management Properties

Property 7: Connection reuse from pool
*For any* sequence of database operations within the same Lambda execution context, the system should reuse connections from the pool rather than creating new connections for each operation
**Validates: Requirements 5.2**

### Error Handling and Retry Properties

Property 8: Retry behavior with exponential backoff
*For any* retriable error, the system should retry the operation with exponential backoff, distinguish retriable from non-retriable errors, and log with full context when all retries are exhausted
**Validates: Requirements 6.1, 6.2, 6.3**

Property 9: Circuit breaker prevents cascading failures
*For any* sequence of External API calls, when failures exceed the threshold, the circuit breaker should open and fail fast without making additional API calls until the timeout period expires
**Validates: Requirements 6.6**

### Batch Processing Properties

Property 10: Batch size enforcement
*For any* list of item IDs to process, the system should split them into batches that never exceed the configured maximum batch size
**Validates: Requirements 7.1, 7.2, 7.4**

Property 11: API rate limiting
*For any* sequence of External API calls, the system should enforce rate limits to ensure calls per minute do not exceed the configured quota
**Validates: Requirements 7.3**

Property 12: Metrics emission for API calls
*For any* External API call, the system should emit CloudWatch metrics including request count, latency, and error status
**Validates: Requirements 7.5**

### API and Step Functions Properties

Property 13: Step Functions data flow integrity
*For any* GET request with query parameters, the Step Functions should transform parameters correctly for queryData, and merge queryData output with original parameters before passing to analyzeData
**Validates: Requirements 8.3, 8.4**

Property 14: Cache-control headers on GET responses
*For any* successful GET request to query endpoints, the response should include appropriate cache-control headers
**Validates: Requirements 8.6**

Property 15: API key rate limiting
*For any* API key, when requests exceed the configured rate limit, subsequent requests should receive 429 errors until the rate limit window resets
**Validates: Requirements 9.3**

Property 16: API access audit logging
*For any* API request (successful or failed), the system should emit an audit log entry containing the API key identifier, endpoint, timestamp, and result
**Validates: Requirements 9.5**

### Data Retention Properties

Property 17: Data aggregation for old records
*For any* MarketData records older than the configured retention period (90 days), the retention process should aggregate them into daily summaries with correct statistics (avg, min, max, total quantity, count)
**Validates: Requirements 10.2**

Property 18: Retention operation logging
*For any* data retention operation, the system should emit logs containing the operation type, record counts affected, and date ranges processed
**Validates: Requirements 10.7**

### Monitoring Properties

Property 19: Custom metrics emission
*For any* significant system operation (ETL pipeline execution, query request, External API call, database operation), the system should emit corresponding custom CloudWatch metrics
**Validates: Requirements 11.1**

Property 20: X-Ray trace ID in logs
*For any* log entry when X-Ray tracing is enabled, the log should include the X-Ray trace ID for correlation
**Validates: Requirements 11.5**

### Configuration Properties

Property 21: Configuration validation and defaults
*For any* configuration parameter, the system should validate it against defined constraints when loading from environment variables, reject invalid values with clear errors, and use documented default values for missing optional parameters
**Validates: Requirements 14.3, 14.4**

### Database Query Properties

Property 22: SQL parameterization prevents injection
*For any* SQL query that includes user-provided input, the system should use parameterized queries rather than string concatenation
**Validates: Requirements 15.1**

Property 23: Slow query logging
*For any* database query that exceeds 1 second execution time, the system should emit a log entry containing the query, execution time, and execution plan
**Validates: Requirements 15.5**

### Category Management Properties

Property 24: Category ID validation
*For any* category_id provided to storeData, the system should validate it against the list of valid categories and reject invalid values with a clear error
**Validates: Requirements 16.4**

### Step Functions Error Handling Properties

Property 25: Step Functions error state handling
*For any* Lambda function error within the Step Functions execution (queryData or analyzeData), the Step Functions should transition to the error handling state and return an appropriate error response
**Validates: Requirements 18.4**

Property 26: GET parameter to JSON transformation
*For any* API Gateway GET request with query parameters, the Step Functions should receive them as a properly formatted JSON object with all parameter names and values preserved
**Validates: Requirements 18.1**

Property 27: Query context preservation
*For any* query request flowing through Step Functions, the original query parameters should be available to analyzeData Lambda along with the queryData results
**Validates: Requirements 18.3**


## Testing Strategy (Continued)

### Property-Based Test Configuration

Each property-based test must:
- Run minimum 100 iterations with randomized inputs
- Include a comment tag referencing the design property
- Use Hypothesis strategies appropriate for the data types
- Include shrinking to find minimal failing examples

**Example Property Test**:
```python
from hypothesis import given, strategies as st
import pytest

# Feature: bdo-market-insights-rewrite, Property 1: Schema validation rejects invalid inputs
@given(
    item_id=st.integers(max_value=0),  # Invalid: must be positive
    start_date=st.datetimes(),
    end_date=st.datetimes()
)
@pytest.mark.property_test
def test_invalid_query_request_rejected(item_id, start_date, end_date):
    """Property 1: Invalid inputs should be rejected with 400 error."""
    request_data = {
        "item_id": item_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(**request_data)
    
    assert "item_id" in str(exc_info.value)
    assert "positive" in str(exc_info.value).lower()
```

### Unit Test Examples

**Edge Cases**:
```python
def test_empty_item_id_list():
    """Edge case: Empty item ID list should be handled gracefully."""
    result = process_item_ids([])
    assert result == {"processed": 0, "errors": []}

def test_special_characters_in_log_message():
    """Edge case: Special characters should be properly escaped in JSON logs."""
    logger = StructuredLogger("test", "corr-123")
    logger.info("Message with \"quotes\" and \n newlines")
    # Verify log is valid JSON
```

**Error Conditions**:
```python
def test_database_connection_failure_retries():
    """Error condition: Database connection failures should trigger retries."""
    with patch('psycopg2.connect', side_effect=OperationalError("Connection refused")):
        with pytest.raises(OperationalError):
            pool = DatabasePool("test-secret")
            pool.get_connection()
        
        # Verify retry attempts were made
        assert psycopg2.connect.call_count == 3

def test_invalid_api_key_returns_401():
    """Error condition: Invalid API key should return 401."""
    response = api_gateway_request(
        path="/query",
        method="GET",
        headers={"x-api-key": "invalid-key"}
    )
    assert response["statusCode"] == 401
```

**Integration Tests**:
```python
@pytest.mark.integration
def test_etl_pipeline_end_to_end(dynamodb_table, rds_instance, mock_external_api):
    """Integration: Complete ETL pipeline from DynamoDB to RDS."""
    # Setup: Populate DynamoDB with item IDs
    dynamodb_table.put_item(Item={"category_id": 5, "item_ids": [1001, 1002]})
    
    # Execute: Run ETL pipeline
    retrieve_result = retrieveIdList_handler({}, {})
    fetch_result = fetchData_handler(retrieve_result, {})
    clean_result = cleanData_handler(fetch_result, {})
    store_result = storeData_handler(clean_result, {})
    
    # Verify: Data in RDS
    with rds_instance.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM market_data WHERE category_id = 5")
        count = cursor.fetchone()[0]
        assert count > 0

@pytest.mark.integration
def test_query_pipeline_with_step_functions(api_gateway, step_functions, rds_instance):
    """Integration: Query pipeline through API Gateway and Step Functions."""
    # Setup: Populate RDS with test data
    insert_test_market_data(rds_instance, item_id=1001, days=30)
    
    # Execute: API Gateway GET request
    response = api_gateway.get(
        "/query",
        params={"item_id": 1001, "start_date": "2024-01-01T00:00:00Z", "end_date": "2024-01-30T23:59:59Z"},
        headers={"x-api-key": "test-key"}
    )
    
    # Verify: Response structure and data
    assert response.status_code == 200
    data = response.json()
    assert "statistics" in data
    assert data["item_id"] == 1001
```

### Test Fixtures

**Shared Fixtures** (conftest.py):
```python
import pytest
from moto import mock_dynamodb, mock_secretsmanager, mock_rds
import boto3

@pytest.fixture
def correlation_id():
    """Generate unique correlation ID for tests."""
    return f"test-{uuid.uuid4()}"

@pytest.fixture
def mock_secrets_manager():
    """Mock AWS Secrets Manager with test credentials."""
    with mock_secretsmanager():
        client = boto3.client("secretsmanager", region_name="us-east-1")
        client.create_secret(
            Name="bdo-db-credentials",
            SecretString='{"username":"test","password":"test","host":"localhost","port":5432,"database":"test"}'
        )
        yield client

@pytest.fixture
def mock_dynamodb_table():
    """Mock DynamoDB table for item IDs."""
    with mock_dynamodb():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="bdo-item-ids",
            KeySchema=[{"AttributeName": "category_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "category_id", "AttributeType": "N"}]
        )
        yield table

@pytest.fixture
def mock_external_api(requests_mock):
    """Mock external BDO market API."""
    requests_mock.get(
        "https://api.example.com/market",
        json={"items": [{"itemId": 1001, "price": 15000, "count": 50}]}
    )
    return requests_mock
```

### CI/CD Test Execution

**Test Pipeline Stages**:
1. **Lint and Format**: Run flake8, black, mypy
2. **Unit Tests**: Run all unit tests with coverage report
3. **Property Tests**: Run property-based tests (100+ iterations each)
4. **Integration Tests**: Run integration tests against mocked AWS services
5. **Coverage Check**: Ensure minimum 80% coverage
6. **Security Scan**: Run bandit for security vulnerabilities

**GitHub Actions Workflow** (example):
```yaml
name: Test and Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.14'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
      
      - name: Lint
        run: |
          flake8 src/ tests/
          black --check src/ tests/
          mypy src/
      
      - name: Run unit tests
        run: pytest tests/unit/ -v --cov=src --cov-report=xml
      
      - name: Run property tests
        run: pytest tests/property/ -v --hypothesis-show-statistics
      
      - name: Run integration tests
        run: pytest tests/integration/ -v
      
      - name: Check coverage
        run: |
          coverage report --fail-under=80
      
      - name: Security scan
        run: bandit -r src/
  
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to AWS
        run: |
          # Deploy Lambda functions, update Step Functions, etc.
```

## Migration Plan

### Migration Strategy: Big-Bang Rewrite

The migration will be a complete rewrite deployed in a single release. This approach is acceptable because:
- The system is relatively small (6 Lambda functions)
- Comprehensive testing will validate correctness before deployment
- Rollback capability through blue-green deployment minimizes risk

### Migration Phases

**Phase 1: Foundation (Week 1-2)**
- Create Lambda Layer with shared code
- Implement Pydantic schemas for all inputs
- Set up Secrets Manager with credentials
- Implement structured logging with correlation IDs
- Write unit tests for shared components

**Phase 2: ETL Pipeline Rewrite (Week 3-4)**
- Rewrite retrieveIdList with new architecture
- Rewrite fetchData with retry logic and batching
- Rewrite cleanData with schema validation
- Rewrite storeData with connection pooling and configurable categories
- Write property-based tests for ETL transformations
- Write integration tests for complete ETL flow

**Phase 3: Query Pipeline Rewrite (Week 5-6)**
- Update API Gateway to use GET method
- Rewrite Step Functions state machine with transformations
- Rewrite queryData with connection pooling
- Rewrite analyzeData with enhanced metrics
- Implement API key authentication and rate limiting
- Write property-based tests for query operations
- Write integration tests for complete query flow

**Phase 4: Infrastructure and Monitoring (Week 7)**
- Set up RDS Proxy for connection pooling
- Configure CloudWatch custom metrics and alarms
- Enable X-Ray tracing for all functions
- Create OpenAPI specification and Swagger UI
- Implement data retention Lambda
- Configure CORS for API Gateway

**Phase 5: Testing and Validation (Week 8)**
- Run complete test suite (unit, property, integration)
- Perform load testing on query endpoints
- Validate error handling and retry logic
- Test data retention automation
- Security review and penetration testing
- Performance benchmarking

**Phase 6: Deployment (Week 9)**
- Deploy to staging environment
- Run smoke tests in staging
- Deploy to production using blue-green deployment
- Monitor CloudWatch metrics and X-Ray traces
- Validate ETL pipeline execution
- Validate query API functionality
- Keep old version available for rollback

### Rollback Plan

If critical issues are discovered after deployment:
1. Use AWS Lambda version aliases to switch back to previous version
2. Revert API Gateway integration to previous Step Functions state machine
3. Monitor for 24 hours to ensure stability
4. Investigate and fix issues in development environment
5. Re-deploy with fixes after validation

### Data Migration

No data migration is required because:
- PostgreSQL schema remains compatible (only adding indexes and summary table)
- DynamoDB structure unchanged
- Historical data remains accessible

New tables to create:
- `market_data_summary` for aggregated data
- `system_config` for configuration management
- Partitions for `market_data` table (by month)

### Deployment Checklist

**Pre-Deployment**:
- [ ] All tests passing (unit, property, integration)
- [ ] Code coverage ≥ 80%
- [ ] Security scan passed
- [ ] OpenAPI documentation generated
- [ ] Secrets Manager populated with credentials
- [ ] RDS Proxy configured
- [ ] CloudWatch alarms created
- [ ] API keys generated for clients

**Deployment**:
- [ ] Deploy Lambda Layer
- [ ] Deploy Lambda functions with new code
- [ ] Update Step Functions state machine
- [ ] Update API Gateway configuration (GET method, API keys)
- [ ] Enable X-Ray tracing
- [ ] Configure EventBridge Scheduler for ETL pipeline
- [ ] Deploy data retention Lambda with monthly schedule

**Post-Deployment**:
- [ ] Verify ETL pipeline executes successfully
- [ ] Verify query API responds correctly
- [ ] Check CloudWatch logs for errors
- [ ] Verify X-Ray traces are complete
- [ ] Monitor custom metrics
- [ ] Test API key authentication
- [ ] Verify rate limiting works
- [ ] Check database connection pool metrics

**Monitoring (First 48 Hours)**:
- Monitor Lambda error rates (should be < 1%)
- Monitor API Gateway 4xx/5xx rates
- Monitor database connection pool utilization
- Monitor External API call success rates
- Check for any unexpected errors in CloudWatch logs
- Verify correlation IDs are propagating correctly
- Monitor query latency (should be < 2 seconds p95)

### Success Criteria

The migration is considered successful when:
- All Lambda functions executing without errors for 48 hours
- ETL pipeline completing successfully on schedule
- Query API responding with < 2 second p95 latency
- No database connection pool exhaustion
- All CloudWatch alarms in OK state
- API authentication and rate limiting working correctly
- X-Ray traces showing complete request flows
- Structured logs with correlation IDs present
- Zero data loss or corruption

## Appendix: Technology Stack

**AWS Services**:
- Lambda (Python 3.14)
- API Gateway (REST API)
- Step Functions (Standard workflows)
- RDS (PostgreSQL 15)
- RDS Proxy
- DynamoDB
- Secrets Manager
- CloudWatch (Logs, Metrics, Alarms)
- X-Ray
- EventBridge Scheduler
- S3 (for data archival)
- S3 Glacier (for long-term archival)

**Python Libraries**:
- `pydantic` (2.x) - Data validation
- `psycopg2-binary` - PostgreSQL driver
- `boto3` - AWS SDK
- `pytest` - Testing framework
- `hypothesis` - Property-based testing
- `pytest-cov` - Coverage reporting
- `moto` - AWS service mocking
- `requests` - HTTP client for External API
- `python-json-logger` - Structured logging

**Development Tools**:
- `black` - Code formatting
- `flake8` - Linting
- `mypy` - Type checking
- `bandit` - Security scanning
- `pre-commit` - Git hooks

**Infrastructure as Code**:
- AWS CloudFormation or Terraform (to be determined)

**CI/CD**:
- GitHub Actions (or AWS CodePipeline)
