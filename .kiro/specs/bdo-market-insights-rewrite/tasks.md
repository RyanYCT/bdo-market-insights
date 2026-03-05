# Implementation Plan: BDO Market Insights Rewrite

## Overview

This implementation plan follows a phased approach to rewrite the BDO Market Insights serverless ETL pipeline. The plan prioritizes foundation work (shared code, validation, security) before rebuilding the ETL and Query pipelines, then adds monitoring and automation. Each task builds incrementally with testing integrated throughout.

## Tasks

### Phase 1: Foundation and Shared Infrastructure

- [x] 1. Set up Lambda Layer structure and shared utilities
  - Create `lambda_layer/python/common/` directory structure
  - Implement `LambdaRouter` class for consistent request/response handling
  - Implement `StructuredLogger` class for JSON logging with correlation IDs
  - Implement correlation ID generation and propagation utilities
  - _Requirements: 1.1, 1.2, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 2. Implement Pydantic validation schemas
  - [x] 2.1 Create base schemas for all Lambda inputs and outputs
    - Define `QueryRequest` schema with item_id, name, category, date range validation
    - Define `MarketDataRecord` schema matching Django model fields
    - Define `ItemRecord` schema with category validation
    - Define ETL pipeline schemas: `ItemIdList`, `FetchDataInput`, `CleanDataInput`, `StoreDataInput`
    - _Requirements: 2.1, 2.2, 2.3_
  
  - [x] 2.2 Write property test for schema validation
    - **Property 1: Schema validation rejects invalid inputs**
    - **Property 2: Schema validation accepts valid inputs**
    - **Validates: Requirements 2.2, 2.3**

- [x] 3. Implement Secrets Manager integration
  - [x] 3.1 Create `SecretsManagerClient` class
    - Implement credential retrieval from Secrets Manager
    - Implement caching for execution context duration
    - Add error handling for missing or invalid secrets
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  
  - [x] 3.2 Write property test for secret caching
    - **Property 3: Secret caching within execution context**
    - **Validates: Requirements 3.4**

- [x] 4. Implement database connection pooling
  - [x] 4.1 Create `DatabasePool` class
    - Implement connection pool with configurable size limits
    - Implement connection reuse logic
    - Add retry logic for connection failures with exponential backoff
    - Implement idle connection cleanup
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
  
  - [x] 4.2 Write property test for connection reuse
    - **Property 7: Connection reuse from pool**
    - **Validates: Requirements 5.2**

- [x] 5. Implement error handling and retry logic
  - [x] 5.1 Create retry decorator with exponential backoff
    - Implement error classification (retriable vs non-retriable)
    - Implement exponential backoff with jitter
    - Add comprehensive error logging with context
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  
  - [x] 5.2 Implement circuit breaker pattern
    - Create `CircuitBreaker` class for External API calls
    - Implement state management (CLOSED, OPEN, HALF_OPEN)
    - Add failure threshold and timeout configuration
    - _Requirements: 6.6_
  
  - [x] 5.3 Write property tests for retry and circuit breaker
    - **Property 8: Retry behavior with exponential backoff**
    - **Property 9: Circuit breaker prevents cascading failures**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.6**

- [x] 6. Checkpoint - Verify shared infrastructure
  - Ensure all tests pass, ask the user if questions arise.

### Phase 2: ETL Pipeline Rewrite

- [x] 7. Rewrite retrieveIdList Lambda function
  - [x] 7.1 Implement retrieveIdList with new architecture
    - Integrate LambdaRouter for request handling
    - Add structured logging with correlation ID
    - Implement Pydantic schema validation for output
    - Query DynamoDB for item IDs
    - _Requirements: 1.3, 2.2, 4.1, 4.3_
  
  - [x] 7.2 Write unit tests for retrieveIdList
    - Test DynamoDB query logic
    - Test error handling for missing data
    - Test correlation ID generation

- [x] 8. Rewrite fetchData Lambda function
  - [x] 8.1 Implement fetchData with batching and rate limiting
    - Integrate LambdaRouter and structured logging
    - Implement batch processing with configurable size limits
    - Add rate limiting for External API calls
    - Implement retry logic with circuit breaker
    - Handle rate limit headers from External API
    - _Requirements: 6.5, 7.1, 7.2, 7.3, 7.4, 7.5_
  
  - [x] 8.2 Write property tests for batch processing
    - **Property 10: Batch size enforcement**
    - **Property 11: API rate limiting**
    - **Property 12: Metrics emission for API calls**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**
  
  - [x] 8.3 Write unit tests for fetchData
    - Test External API error handling
    - Test rate limit header parsing
    - Test circuit breaker integration

- [x] 9. Rewrite cleanData Lambda function
  - [x] 9.1 Implement cleanData with schema validation
    - Integrate LambdaRouter and structured logging
    - Transform raw API data to MarketDataRecord format
    - Validate all records against Pydantic schema
    - Filter out invalid records with logging
    - _Requirements: 2.2, 2.3_
  
  - [x] 9.2 Write unit tests for cleanData
    - Test data transformation logic
    - Test validation error handling
    - Test filtering of invalid records

- [x] 10. Rewrite storeData Lambda function
  - [x] 10.1 Implement storeData with connection pooling
    - Integrate LambdaRouter and structured logging
    - Use DatabasePool for PostgreSQL connections
    - Create or get MarketScrape record
    - Create or get Item records with category lookup
    - Bulk insert MarketData records
    - Use parameterized queries for SQL injection prevention
    - _Requirements: 5.2, 15.1_
  
  - [x] 10.2 Write property tests for database operations
    - **Property 22: SQL parameterization prevents injection**
    - **Property 23: Slow query logging**
    - **Validates: Requirements 15.1, 15.5**
  
  - [x] 10.3 Write unit tests for storeData
    - Test MarketScrape creation
    - Test Item creation with category handling
    - Test bulk insert logic
    - Test transaction rollback on errors

- [ ] 11. Checkpoint - Verify ETL pipeline
  - Ensure all tests pass, ask the user if questions arise.

### Phase 3: Query Pipeline Rewrite

- [ ] 12. Update API Gateway configuration
  - [ ] 12.1 Configure API Gateway for GET method
    - Change query endpoint from POST to GET
    - Configure query parameter extraction
    - Set up API key authentication
    - Configure rate limiting per API key
    - Set up CORS headers for approved origins
    - _Requirements: 8.1, 8.2, 9.1, 9.2, 9.3, 9.4_
  
  - [ ] 12.2 Write property tests for API authentication
    - **Property 15: API key rate limiting**
    - **Property 16: API access audit logging**
    - **Validates: Requirements 9.3, 9.5**

- [ ] 13. Rewrite Step Functions state machine
  - [ ] 13.1 Update state machine definition for GET requests
    - Implement TransformInput state to extract query parameters
    - Update QueryData state with parameter passing
    - Implement MergeForAnalysis state to combine results
    - Update AnalyzeData state with merged input
    - Add FormatResponse state with cache-control headers
    - Add HandleError state for error responses
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6, 18.1, 18.2, 18.3, 18.4_
  
  - [ ] 13.2 Write property tests for Step Functions data flow
    - **Property 13: Step Functions data flow integrity**
    - **Property 14: Cache-control headers on GET responses**
    - **Property 26: GET parameter to JSON transformation**
    - **Property 27: Query context preservation**
    - **Validates: Requirements 8.3, 8.4, 8.6, 18.1, 18.3**

- [ ] 14. Rewrite queryData Lambda function
  - [ ] 14.1 Implement queryData with connection pooling
    - Integrate LambdaRouter and structured logging
    - Validate query parameters with Pydantic schema
    - Use DatabasePool for PostgreSQL connections
    - Build dynamic SQL query based on parameters (item_id, name, category, date range)
    - Join MarketData with Item and MarketScrape tables
    - Apply limit and ordering
    - _Requirements: 5.2, 8.5, 15.1_
  
  - [ ] 14.2 Write unit tests for queryData
    - Test query building for different parameter combinations
    - Test date range filtering
    - Test limit enforcement
    - Test error handling for invalid parameters

- [ ] 15. Rewrite analyzeData Lambda function
  - [ ] 15.1 Implement analyzeData with enhanced metrics
    - Integrate LambdaRouter and structured logging
    - Calculate statistics: avg/min/max last_sold_price, avg current_stock, total_trades_sum
    - Compute profitability score based on price trends
    - Determine price trend (increasing, decreasing, stable)
    - Include item name and query context in response
    - _Requirements: 4.1, 4.3_
  
  - [ ] 15.2 Write unit tests for analyzeData
    - Test statistics calculation
    - Test profitability score computation
    - Test price trend detection
    - Test edge cases (empty data, single record)

- [ ] 16. Checkpoint - Verify Query pipeline
  - Ensure all tests pass, ask the user if questions arise.

### Phase 4: Monitoring and Observability

- [ ] 17. Implement CloudWatch custom metrics
  - [ ] 17.1 Add metrics emission to all Lambda functions
    - Emit ETL pipeline success/failure metrics
    - Emit query latency metrics
    - Emit External API response time metrics
    - Emit database connection pool utilization metrics
    - _Requirements: 11.1_
  
  - [ ] 17.2 Write property test for metrics emission
    - **Property 19: Custom metrics emission**
    - **Validates: Requirements 11.1**

- [ ] 18. Enable X-Ray tracing
  - [ ] 18.1 Configure X-Ray for all Lambda functions
    - Enable X-Ray tracing in Lambda configuration
    - Add X-Ray SDK instrumentation to code
    - Include X-Ray trace IDs in structured logs
    - _Requirements: 11.2, 11.5_
  
  - [ ] 18.2 Write property test for X-Ray integration
    - **Property 20: X-Ray trace ID in logs**
    - **Validates: Requirements 11.5**

- [ ] 19. Create CloudWatch alarms
  - Configure alarms for Lambda error rates > 5%
  - Configure alarms for database connection pool exhaustion
  - Configure alarms for External API failures > 10%
  - Set up SNS topic for alarm notifications
  - _Requirements: 11.4_

- [ ] 20. Implement API documentation
  - [ ] 20.1 Create OpenAPI 3.0 specification
    - Define all query endpoints with GET method
    - Include request parameter schemas
    - Include response schemas for success and errors
    - Document authentication requirements (API keys)
    - Add example requests and responses
    - _Requirements: 12.1, 12.2, 12.5_
  
  - [ ] 20.2 Set up Swagger UI endpoint
    - Configure API Gateway to serve OpenAPI spec
    - Deploy Swagger UI for interactive documentation
    - _Requirements: 12.3, 12.4_

### Phase 5: Data Retention and Configuration

- [ ] 21. Implement data retention Lambda
  - [ ] 21.1 Create retention Lambda function
    - Integrate LambdaRouter and structured logging
    - Load retention periods from configuration
    - Aggregate MarketData older than 90 days into daily summaries
    - Calculate avg/min/max prices, avg stock, total trades
    - Archive summaries older than 2 years to S3 Glacier
    - Delete processed records from source tables
    - Log all operations with record counts and date ranges
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.7_
  
  - [ ] 21.2 Write property tests for data retention
    - **Property 17: Data aggregation for old records**
    - **Property 18: Retention operation logging**
    - **Validates: Requirements 10.2, 10.7**
  
  - [ ] 21.3 Write unit tests for retention Lambda
    - Test aggregation calculation accuracy
    - Test date range filtering
    - Test S3 archival
    - Test deletion logic

- [ ] 22. Implement configuration management
  - [ ] 22.1 Create central configuration module
    - Define all configurable parameters with defaults
    - Load configuration from environment variables
    - Validate configuration values against constraints
    - Support environment-specific configuration (dev, staging, prod)
    - Document all parameters with descriptions and valid ranges
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_
  
  - [ ] 22.2 Write property tests for configuration
    - **Property 21: Configuration validation and defaults**
    - **Validates: Requirements 14.3, 14.4**

- [ ] 23. Schedule retention Lambda
  - Configure EventBridge rule for monthly execution
  - Set up IAM permissions for S3 Glacier access
  - _Requirements: 10.5_

### Phase 6: Integration Testing and Deployment

- [ ] 24. Write integration tests
  - [ ] 24.1 Write ETL pipeline integration test
    - Test complete flow: retrieveIdList → fetchData → cleanData → storeData
    - Use mocked DynamoDB, External API, and RDS
    - Verify data flows correctly through all stages
    - Verify correlation ID propagation
    - _Requirements: 13.2, 13.5_
  
  - [ ] 24.2 Write Query pipeline integration test
    - Test complete flow: API Gateway → Step Functions → queryData → analyzeData
    - Use mocked API Gateway, Step Functions, and RDS
    - Verify GET parameter transformation
    - Verify query context preservation
    - Verify response format and headers
    - _Requirements: 13.2, 13.5_
  
  - [ ] 24.3 Write end-to-end test
    - Test full ETL pipeline followed by query
    - Verify data inserted by ETL can be queried
    - Verify analysis results are accurate
    - _Requirements: 13.5_

- [ ] 25. Set up CI/CD pipeline
  - [ ] 25.1 Create GitHub Actions workflow
    - Configure linting (flake8, black, mypy)
    - Configure unit test execution with coverage
    - Configure property test execution (100+ iterations)
    - Configure integration test execution
    - Require 80% code coverage
    - Run security scan (bandit)
    - _Requirements: 13.1, 13.4, 13.6, 17.1_
  
  - [ ] 25.2 Configure deployment automation
    - Deploy Lambda Layer on successful tests
    - Deploy Lambda functions with blue-green deployment
    - Update Step Functions state machine
    - Update API Gateway configuration
    - Send deployment notifications
    - Implement automatic rollback on failure
    - _Requirements: 17.2, 17.3, 17.4, 17.5, 17.6_

- [ ] 26. Deploy to staging environment
  - Create AWS Secrets Manager secrets with credentials
  - Deploy Lambda Layer
  - Deploy all Lambda functions
  - Update Step Functions state machine
  - Configure API Gateway with API keys
  - Enable X-Ray tracing
  - Create CloudWatch alarms
  - Configure EventBridge Scheduler for ETL and retention

- [ ] 27. Run staging validation
  - Execute ETL pipeline manually and verify success
  - Test query API with various parameters
  - Verify API key authentication works
  - Verify rate limiting works
  - Check CloudWatch logs for proper JSON format and correlation IDs
  - Verify X-Ray traces are complete
  - Check custom metrics are being emitted
  - Test error scenarios and verify error handling

- [ ] 28. Deploy to production
  - Deploy using blue-green deployment strategy
  - Monitor CloudWatch metrics for 24 hours
  - Verify ETL pipeline runs on schedule
  - Verify query API performance (< 2s p95 latency)
  - Check for any errors or anomalies
  - Keep old version available for rollback if needed

- [ ] 29. Final checkpoint - Production validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- All tasks are required for comprehensive implementation
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties with 100+ iterations
- Unit tests validate specific examples and edge cases
- Integration tests validate end-to-end flows
- The phased approach allows for early validation of foundation components
- Blue-green deployment enables safe rollback if issues are discovered
