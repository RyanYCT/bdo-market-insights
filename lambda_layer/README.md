# BDO Market Insights Lambda Layer

This Lambda Layer provides shared utilities for all Lambda functions in the BDO Market Insights system.

## Structure

```
lambda_layer/
├── python/
│   └── common/
│       ├── __init__.py          # Package exports
│       ├── correlation.py       # Correlation ID utilities
│       ├── logging.py           # Structured JSON logging
│       └── router.py            # Request routing and response formatting
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

## Components

### LambdaRouter

Provides consistent request/response handling across all Lambda functions.

**Features:**
- Automatic correlation ID extraction/generation
- Structured logging integration
- Standardized response format
- Exception handling and error responses

**Usage:**
```python
from common import LambdaRouter

router = LambdaRouter()

@router.route()
def lambda_handler(event, context, logger):
    logger.info("Processing request", item_count=5)
    return {"result": "success"}
```

### StructuredLogger

Provides JSON-formatted logging with correlation IDs for CloudWatch.

**Features:**
- JSON log format for efficient querying
- Automatic inclusion of standard fields (timestamp, level, correlation_id, etc.)
- Context-aware child loggers
- Exception details with stack traces

**Usage:**
```python
from common import StructuredLogger

logger = StructuredLogger("myFunction", "correlation-id-123")
logger.info("Processing started", item_count=5)
logger.error("Failed to process", error=exception_obj)

# Create child logger with additional context
child_logger = logger.with_context(user_id="user-456")
child_logger.info("User action")  # Includes user_id in log
```

### Correlation ID Utilities

Functions for generating and extracting correlation IDs from Lambda events.

**Usage:**
```python
from common import generate_correlation_id, extract_correlation_id

# Generate new correlation ID
corr_id = generate_correlation_id()

# Extract from event (or generate if not present)
corr_id = extract_correlation_id(event)
```

## Deployment

To deploy this Lambda Layer:

1. Install dependencies:
```bash
cd lambda_layer
pip install -r requirements.txt -t python/
```

2. Create deployment package:
```bash
zip -r lambda_layer.zip python/
```

3. Upload to AWS Lambda:
```bash
aws lambda publish-layer-version \
    --layer-name bdo-market-insights-common \
    --description "Common utilities for BDO Market Insights ETL pipeline - Python 3.14" \
    --zip-file fileb://lambda_layer.zip \
    --compatible-runtimes python3.14
```

4. Attach to Lambda functions:
```bash
aws lambda update-function-configuration \
    --function-name myFunction \
    --layers arn:aws:lambda:region:account:layer:bdo-market-insights-common:1
```

## Requirements

- Python 3.14+
- python-json-logger 2.0.7+

## Testing

See the `tests/` directory in the project root for unit tests and property-based tests for these utilities.
