# Requirements Document: BDO Market Insights Rewrite

## Introduction

This document specifies requirements for rewriting the BDO Market Insights serverless ETL pipeline. The system collects Black Desert Online market data through scheduled Lambda functions, stores it in PostgreSQL, and provides query capabilities via API Gateway. The rewrite focuses on improving code quality, reliability, security, and maintainability while preserving the existing serverless architecture and functionality.

## Glossary

- **ETL_Pipeline**: The scheduled data collection workflow consisting of retrieveIdList, fetchData, cleanData, and storeData Lambda functions
- **Query_Pipeline**: The on-demand data retrieval workflow consisting of queryData and analyzeData Lambda functions orchestrated by Step Functions
- **Lambda_Layer**: A shared deployment package containing common code and dependencies used across multiple Lambda functions
- **RDS_Proxy**: AWS service that manages database connection pooling for Lambda functions
- **Secrets_Manager**: AWS service for secure storage and rotation of database credentials and API keys
- **API_Gateway**: AWS service that provides HTTP endpoints for the Query_Pipeline
- **Step_Functions**: AWS service that orchestrates Lambda function execution with state management
- **CloudWatch**: AWS monitoring service for logs, metrics, and alarms
- **X-Ray**: AWS distributed tracing service for request flow visualization
- **Pydantic**: Python library for data validation using type annotations
- **Correlation_ID**: Unique identifier that tracks a request across multiple Lambda invocations
- **MarketData**: Time-series records of item prices, quantities, and market activity
- **External_API**: Third-party Black Desert Online market data source

## Requirements

### Requirement 1: Shared Code Infrastructure

**User Story:** As a developer, I want common code consolidated into a Lambda Layer, so that I can maintain consistent functionality across all Lambda functions without duplication.

#### Acceptance Criteria

1. THE Lambda_Layer SHALL contain the LambdaRouter class used by all six Lambda functions
2. THE Lambda_Layer SHALL contain shared utility functions for logging, error handling, and database operations
3. WHEN any Lambda function is deployed, THE System SHALL include the Lambda_Layer as a dependency
4. THE Lambda_Layer SHALL be versioned independently from individual Lambda functions
5. WHEN the Lambda_Layer is updated, THE System SHALL allow selective redeployment of dependent functions

### Requirement 2: Input Validation

**User Story:** As a developer, I want all Lambda function inputs validated using schemas, so that invalid data is rejected early with clear error messages.

#### Acceptance Criteria

1. THE System SHALL define Pydantic schemas for all Lambda function input payloads
2. WHEN a Lambda function receives input, THE System SHALL validate it against the corresponding Pydantic schema
3. IF input validation fails, THEN THE System SHALL return a 400 error with specific validation failure details
4. THE System SHALL define Pydantic schemas for API Gateway request parameters
5. THE System SHALL define Pydantic schemas for Step Functions state transitions

### Requirement 3: Secure Credential Management

**User Story:** As a security engineer, I want database credentials stored in Secrets Manager, so that sensitive information is not exposed in environment variables or code.

#### Acceptance Criteria

1. THE System SHALL store PostgreSQL connection credentials in Secrets_Manager
2. THE System SHALL store External_API authentication tokens in Secrets_Manager
3. WHEN a Lambda function needs credentials, THE System SHALL retrieve them from Secrets_Manager at runtime
4. THE System SHALL cache retrieved secrets for the duration of a Lambda execution context
5. THE System SHALL support automatic credential rotation without code changes

### Requirement 4: Structured Logging

**User Story:** As a DevOps engineer, I want structured JSON logs with correlation IDs, so that I can trace requests across Lambda functions and query logs efficiently.

#### Acceptance Criteria

1. THE System SHALL emit all logs in JSON format to CloudWatch
2. WHEN a request enters the system, THE System SHALL generate a unique Correlation_ID
3. THE System SHALL include the Correlation_ID in all log entries for that request
4. WHEN a Lambda function invokes another Lambda function, THE System SHALL propagate the Correlation_ID
5. THE System SHALL include standard fields in every log entry: timestamp, level, function_name, correlation_id, message
6. THE System SHALL include request_id and execution context metadata in log entries

### Requirement 5: Database Connection Management

**User Story:** As a system architect, I want efficient database connection pooling, so that Lambda functions do not exhaust database connections or experience cold start delays.

#### Acceptance Criteria

1. THE System SHALL implement connection pooling for PostgreSQL database access
2. WHEN a Lambda function needs a database connection, THE System SHALL reuse existing connections from the pool when available
3. THE System SHALL configure connection pool size limits to prevent database connection exhaustion
4. THE System SHALL handle connection failures with automatic retry logic
5. THE System SHALL close idle connections after a configurable timeout period

### Requirement 6: Error Handling and Retries

**User Story:** As a reliability engineer, I want consistent error handling with automatic retries, so that transient failures do not cause data loss or pipeline failures.

#### Acceptance Criteria

1. WHEN a Lambda function encounters a retriable error, THE System SHALL retry the operation with exponential backoff
2. THE System SHALL distinguish between retriable errors (network timeouts, rate limits) and non-retriable errors (validation failures, authentication errors)
3. IF all retry attempts fail, THEN THE System SHALL log the error with full context and raise an exception
4. THE System SHALL configure maximum retry attempts per operation type
5. WHEN the External_API returns a rate limit error, THE System SHALL respect the retry-after header
6. THE System SHALL implement circuit breaker pattern for External_API calls to prevent cascading failures

### Requirement 7: API Rate Limiting and Batch Processing

**User Story:** As a system architect, I want controlled batch sizes for External_API calls, so that Lambda functions do not timeout or overwhelm the external service.

#### Acceptance Criteria

1. THE fetchData Lambda SHALL process item IDs in configurable batch sizes
2. WHEN processing a batch, THE System SHALL enforce a maximum batch size to prevent Lambda timeout
3. THE System SHALL implement rate limiting for External_API calls to respect service quotas
4. IF a batch exceeds the maximum size, THEN THE System SHALL split it into multiple invocations
5. THE System SHALL track API call metrics (requests per minute, error rates) in CloudWatch

### Requirement 8: RESTful Query API

**User Story:** As an API consumer, I want to query market data using HTTP GET requests, so that I can follow RESTful conventions and enable browser caching.

#### Acceptance Criteria

1. THE API_Gateway SHALL expose query endpoints using the GET HTTP method
2. WHEN a GET request is received, THE API_Gateway SHALL extract query parameters and pass them to Step_Functions
3. THE Step_Functions SHALL transform query parameters into the format expected by queryData Lambda
4. THE Step_Functions SHALL pass queryData output to analyzeData Lambda while preserving query context
5. THE System SHALL support query parameters: item_id, start_date, end_date, limit
6. THE System SHALL return appropriate cache-control headers for GET responses

### Requirement 9: API Authentication and Authorization

**User Story:** As a security engineer, I want API access controlled through API keys, so that only authorized clients can query market data.

#### Acceptance Criteria

1. THE API_Gateway SHALL require API keys for all query endpoints
2. WHEN a request lacks a valid API key, THE System SHALL return a 401 Unauthorized error
3. THE System SHALL implement rate limiting per API key to prevent abuse
4. THE System SHALL configure CORS headers to allow web client access from approved origins
5. THE System SHALL log all API access attempts with API key identifier for audit purposes

### Requirement 10: Data Retention Automation

**User Story:** As a database administrator, I want automated data retention policies, so that storage costs are controlled and old data is archived appropriately.

#### Acceptance Criteria

1. THE System SHALL retain detailed MarketData records for 90 days
2. WHEN MarketData exceeds 90 days old, THE System SHALL aggregate it into daily summaries
3. THE System SHALL retain daily summary data for 2 years
4. WHEN daily summaries exceed 2 years old, THE System SHALL archive them to S3 Glacier
5. THE System SHALL execute retention policies through a scheduled Lambda function triggered monthly
6. THE System SHALL make retention periods configurable via environment variables
7. THE System SHALL log all data retention operations with record counts and date ranges

### Requirement 11: Monitoring and Observability

**User Story:** As a DevOps engineer, I want comprehensive monitoring and tracing, so that I can quickly identify and diagnose production issues.

#### Acceptance Criteria

1. THE System SHALL emit custom CloudWatch metrics for: ETL pipeline success/failure, query latency, External_API response times, database connection pool utilization
2. THE System SHALL enable X-Ray tracing for all Lambda functions
3. WHEN a request flows through multiple Lambda functions, THE X-Ray trace SHALL show the complete execution path
4. THE System SHALL create CloudWatch alarms for: Lambda error rates exceeding 5%, database connection pool exhaustion, External_API failures exceeding 10%
5. THE System SHALL include X-Ray trace IDs in structured logs for correlation

### Requirement 12: API Documentation

**User Story:** As an API consumer, I want OpenAPI documentation for query endpoints, so that I can understand available operations and request/response formats.

#### Acceptance Criteria

1. THE System SHALL provide an OpenAPI 3.0 specification document for all API endpoints
2. THE OpenAPI specification SHALL include request parameter schemas, response schemas, error codes, and authentication requirements
3. THE API_Gateway SHALL serve the OpenAPI specification at a dedicated endpoint
4. THE System SHALL generate interactive API documentation using Swagger UI
5. THE OpenAPI specification SHALL include example requests and responses for each endpoint

### Requirement 13: Comprehensive Testing

**User Story:** As a developer, I want comprehensive automated tests, so that I can confidently deploy changes without introducing regressions.

#### Acceptance Criteria

1. THE System SHALL include unit tests for all Lambda function business logic with minimum 80% code coverage
2. THE System SHALL include integration tests that verify Lambda functions interact correctly with AWS services
3. THE System SHALL include property-based tests for data transformation and validation logic
4. WHEN property-based tests execute, THE System SHALL run minimum 100 iterations per property
5. THE System SHALL include end-to-end tests that verify complete ETL and Query pipeline flows
6. THE System SHALL execute all tests automatically before deployment

### Requirement 14: Configuration Management

**User Story:** As a DevOps engineer, I want centralized configuration management, so that I can adjust system behavior without code changes.

#### Acceptance Criteria

1. THE System SHALL define all configurable parameters in a central configuration module
2. THE System SHALL support environment-specific configuration (development, staging, production)
3. THE System SHALL load configuration from environment variables with validation
4. THE System SHALL provide default values for optional configuration parameters
5. THE System SHALL document all configuration parameters with descriptions and valid value ranges

### Requirement 15: Database Query Optimization

**User Story:** As a database administrator, I want maintainable and efficient SQL queries, so that query performance remains consistent as data volume grows.

#### Acceptance Criteria

1. THE System SHALL use parameterized SQL queries to prevent SQL injection
2. THE System SHALL define database queries in a dedicated repository module separate from business logic
3. THE System SHALL include database indexes for all frequently queried columns
4. THE System SHALL use query builders or ORM for complex dynamic queries
5. THE System SHALL log slow queries (exceeding 1 second) with execution plans for optimization

### Requirement 16: Category Flexibility

**User Story:** As a system administrator, I want configurable category filtering, so that the system can collect data for different item categories without code changes.

#### Acceptance Criteria

1. THE storeData Lambda SHALL accept category_id as a configurable parameter
2. THE System SHALL support multiple category IDs for parallel data collection
3. WHEN no category_id is specified, THE System SHALL process all categories
4. THE System SHALL validate category_id values against a defined list of valid categories
5. THE System SHALL make category configuration accessible via environment variables

### Requirement 17: Deployment Automation

**User Story:** As a DevOps engineer, I want automated CI/CD pipelines, so that tested code is deployed consistently without manual intervention.

#### Acceptance Criteria

1. WHEN code is pushed to the main branch, THE System SHALL automatically execute all tests
2. IF all tests pass, THEN THE System SHALL deploy Lambda functions to the production environment
3. THE System SHALL deploy infrastructure changes using Infrastructure as Code (CloudFormation or Terraform)
4. THE System SHALL perform blue-green deployments for Lambda functions to enable rollback
5. THE System SHALL send deployment notifications to a configured notification channel
6. IF deployment fails, THEN THE System SHALL automatically rollback to the previous version

### Requirement 18: Step Functions State Management

**User Story:** As a system architect, I want Step Functions to properly transform data between Lambda invocations, so that the Query_Pipeline handles GET request parameters correctly.

#### Acceptance Criteria

1. WHEN API_Gateway receives a GET request, THE Step_Functions SHALL receive query parameters as a JSON object
2. THE Step_Functions SHALL pass query parameters to the queryData Lambda function
3. THE Step_Functions SHALL combine queryData output with original query parameters before invoking analyzeData Lambda
4. THE Step_Functions SHALL handle errors from either Lambda function with appropriate error states
5. THE Step_Functions SHALL include input/output processing transformations in the state machine definition
