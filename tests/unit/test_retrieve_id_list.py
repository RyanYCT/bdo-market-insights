"""
Unit tests for retrieveIdList Lambda function.

Tests DynamoDB query logic, error handling, and correlation ID generation.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
from botocore.exceptions import ClientError

# Import from retrieveIdList Lambda function
from retrieveIdList.lambda_function import DynamoDBService, handler, lambda_handler


class TestDynamoDBService:
    """Test DynamoDBService class."""
    
    def test_get_item_ids_success(self):
        """Test successful retrieval of item IDs from DynamoDB."""
        # Create mock logger
        mock_logger = Mock()
        
        # Create service
        service = DynamoDBService(mock_logger)
        
        # Mock DynamoDB table
        mock_table = Mock()
        mock_table.scan.return_value = {
            "Items": [
                {"id": Decimal("1001")},
                {"id": Decimal("1002")},
                {"id": Decimal("1003")}
            ]
        }
        
        # Mock boto3 resource
        with patch.object(service.dynamodb, 'Table', return_value=mock_table):
            result = service.get_item_ids("test-table")
        
        # Verify results
        assert result == [1001, 1002, 1003]
        assert len(result) == 3
        
        # Verify logging
        mock_logger.info.assert_called()
    
    def test_get_item_ids_with_pagination(self):
        """Test retrieval with DynamoDB pagination."""
        mock_logger = Mock()
        service = DynamoDBService(mock_logger)
        
        # Mock paginated responses
        mock_table = Mock()
        mock_table.scan.side_effect = [
            {
                "Items": [{"id": Decimal("1001")}, {"id": Decimal("1002")}],
                "LastEvaluatedKey": {"id": Decimal("1002")}
            },
            {
                "Items": [{"id": Decimal("1003")}, {"id": Decimal("1004")}]
            }
        ]
        
        with patch.object(service.dynamodb, 'Table', return_value=mock_table):
            result = service.get_item_ids("test-table")
        
        # Verify all items retrieved
        assert result == [1001, 1002, 1003, 1004]
        assert mock_table.scan.call_count == 2
    
    def test_get_item_ids_empty_table(self):
        """Test error handling when table is empty."""
        mock_logger = Mock()
        service = DynamoDBService(mock_logger)
        
        # Mock empty table
        mock_table = Mock()
        mock_table.scan.return_value = {"Items": []}
        
        with patch.object(service.dynamodb, 'Table', return_value=mock_table):
            with pytest.raises(ValueError, match="No item IDs found"):
                service.get_item_ids("test-table")
    
    def test_get_item_ids_missing_id_field(self):
        """Test handling of items missing 'id' field."""
        mock_logger = Mock()
        service = DynamoDBService(mock_logger)
        
        # Mock table with some items missing 'id'
        mock_table = Mock()
        mock_table.scan.return_value = {
            "Items": [
                {"id": Decimal("1001")},
                {"name": "invalid"},  # Missing 'id'
                {"id": Decimal("1002")}
            ]
        }
        
        with patch.object(service.dynamodb, 'Table', return_value=mock_table):
            result = service.get_item_ids("test-table")
        
        # Should skip invalid item and log warning
        assert result == [1001, 1002]
        mock_logger.warning.assert_called()
    
    def test_get_item_ids_client_error(self):
        """Test handling of DynamoDB ClientError."""
        mock_logger = Mock()
        service = DynamoDBService(mock_logger)
        
        # Mock ClientError
        mock_table = Mock()
        error_response = {'Error': {'Code': 'ResourceNotFoundException'}}
        mock_table.scan.side_effect = ClientError(error_response, 'Scan')
        
        with patch.object(service.dynamodb, 'Table', return_value=mock_table):
            with pytest.raises(ClientError):
                service.get_item_ids("non-existent-table")
        
        # Verify error logging
        mock_logger.error.assert_called()


class TestHandler:
    """Test Lambda handler function."""
    
    def test_handler_success(self):
        """Test successful handler execution."""
        # Mock event and context
        event = {"table_name": "test-table"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        # Mock DynamoDB service
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = [1001, 1002, 1003]
            
            result = lambda_handler(event, context)
        
        # Verify result structure
        import json
        body = json.loads(result["body"])
        assert "item_ids" in body
        assert "correlation_id" in body
        assert body["item_ids"] == [1001, 1002, 1003]
    
    def test_handler_with_env_variable(self):
        """Test handler using environment variable for table name."""
        event = {}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch.dict('os.environ', {'DYNAMODB_TABLE_NAME': 'env-table'}):
            with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
                mock_service = MockService.return_value
                mock_service.get_item_ids.return_value = [1001]
                
                result = lambda_handler(event, context)
        
        # Verify table name from environment was used
        mock_service.get_item_ids.assert_called_with('env-table')
        
        import json
        body = json.loads(result["body"])
        assert body["item_ids"] == [1001]
    
    def test_handler_missing_table_name(self):
        """Test error when table_name is not provided."""
        event = {}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch.dict('os.environ', {}, clear=True):
            result = lambda_handler(event, context)
        
        # Should return error response
        assert result["statusCode"] == 500
        import json
        body = json.loads(result["body"])
        assert "error" in body
    
    def test_handler_validation_error(self):
        """Test handling of output validation errors."""
        event = {"table_name": "test-table"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        # Mock service to return empty list (invalid)
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = []
            
            # Should return error response due to validation failure
            result = lambda_handler(event, context)
            # Validation errors return 400 status code
            assert result["statusCode"] == 400


class TestLambdaHandler:
    """Test Lambda entry point."""
    
    def test_lambda_handler_integration(self):
        """Test lambda_handler entry point with router integration."""
        event = {"table_name": "test-table"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        # Mock DynamoDB service
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = [1001, 1002]
            
            result = lambda_handler(event, context)
        
        # Verify response structure from router
        assert "statusCode" in result
        assert result["statusCode"] == 200
        assert "body" in result
        
        # Parse body
        import json
        body = json.loads(result["body"])
        assert "item_ids" in body
        assert "correlation_id" in body
        assert body["item_ids"] == [1001, 1002]
    
    def test_lambda_handler_error_handling(self):
        """Test lambda_handler error handling through router."""
        event = {}  # Missing table_name
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch.dict('os.environ', {}, clear=True):
            result = lambda_handler(event, context)
        
        # Should return error response
        assert "statusCode" in result
        assert result["statusCode"] == 500
        assert "body" in result
        
        import json
        body = json.loads(result["body"])
        assert "error" in body


class TestMultipleTables:
    """Test multiple table names functionality."""
    
    def test_handler_multiple_tables_success(self):
        """Test successful handler execution with multiple table names."""
        event = {"table_name": "bdo.accessory,bdo.buff"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        # Mock DynamoDB service
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            # Return different IDs for each table
            mock_service.get_item_ids.side_effect = [
                [1001, 1002, 1003],  # bdo.accessory
                [2001, 2002, 2003]   # bdo.buff
            ]
            
            result = lambda_handler(event, context)
        
        # Verify result structure (router wraps in HTTP response)
        assert result["statusCode"] == 200
        import json
        body = json.loads(result["body"])
        assert "item_ids" in body
        assert "correlation_id" in body
        # Should contain all IDs from both tables
        assert len(body["item_ids"]) == 6
        assert set(body["item_ids"]) == {1001, 1002, 1003, 2001, 2002, 2003}
        
        # Verify both tables were queried
        assert mock_service.get_item_ids.call_count == 2
    
    def test_handler_multiple_tables_with_duplicates(self):
        """Test that duplicate IDs are removed when querying multiple tables."""
        event = {"table_name": "table1,table2,table3"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            # Return overlapping IDs
            mock_service.get_item_ids.side_effect = [
                [1001, 1002, 1003],  # table1
                [1002, 1003, 1004],  # table2 (duplicates 1002, 1003)
                [1003, 1004, 1005]   # table3 (duplicates 1003, 1004)
            ]
            
            result = lambda_handler(event, context)
        
        import json
        body = json.loads(result["body"])
        # Should have unique IDs only
        assert len(body["item_ids"]) == 5
        assert set(body["item_ids"]) == {1001, 1002, 1003, 1004, 1005}
    
    def test_handler_multiple_tables_with_spaces(self):
        """Test parsing table names with spaces around commas."""
        event = {"table_name": "table1 , table2 ,  table3"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.side_effect = [
                [1001],
                [1002],
                [1003]
            ]
            
            result = lambda_handler(event, context)
        
        # Verify all three tables were queried (spaces trimmed)
        assert mock_service.get_item_ids.call_count == 3
        mock_service.get_item_ids.assert_any_call("table1")
        mock_service.get_item_ids.assert_any_call("table2")
        mock_service.get_item_ids.assert_any_call("table3")
    
    def test_handler_single_table_still_works(self):
        """Test backward compatibility with single table name."""
        event = {"table_name": "single-table"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = [1001, 1002]
            
            result = lambda_handler(event, context)
        
        import json
        body = json.loads(result["body"])
        assert body["item_ids"] == [1001, 1002]
        assert mock_service.get_item_ids.call_count == 1
    
    def test_handler_multiple_tables_one_fails(self):
        """Test error handling when one table fails."""
        event = {"table_name": "table1,table2"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            # First table succeeds, second fails
            mock_service.get_item_ids.side_effect = [
                [1001, 1002],
                ClientError({'Error': {'Code': 'ResourceNotFoundException'}}, 'Scan')
            ]
            
            result = lambda_handler(event, context)
        
        # Should return error response (router catches exception)
        assert result["statusCode"] == 500
    
    def test_handler_empty_table_names(self):
        """Test error handling with empty table names."""
        event = {"table_name": ",,"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        result = lambda_handler(event, context)
        
        # Should return error response (router catches exception)
        assert result["statusCode"] == 500
        import json
        body = json.loads(result["body"])
        assert "error" in body


class TestCorrelationID:
    """Test correlation ID generation and propagation."""
    
    def test_correlation_id_generated(self):
        """Test that correlation ID is generated when not provided."""
        event = {"table_name": "test-table"}
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = [1001]
            
            result = lambda_handler(event, context)
        
        import json
        body = json.loads(result["body"])
        
        # Verify correlation_id exists and is a valid UUID format
        assert "correlation_id" in body
        assert len(body["correlation_id"]) == 36  # UUID v4 length
        assert body["correlation_id"].count('-') == 4  # UUID format
    
    def test_correlation_id_propagated(self):
        """Test that existing correlation ID is propagated."""
        existing_corr_id = "existing-correlation-id-123"
        event = {
            "table_name": "test-table",
            "correlation_id": existing_corr_id
        }
        context = Mock()
        context.function_name = "retrieveIdList"
        context.aws_request_id = "test-request-id"
        
        with patch('retrieveIdList.lambda_function.DynamoDBService') as MockService:
            mock_service = MockService.return_value
            mock_service.get_item_ids.return_value = [1001]
            
            result = lambda_handler(event, context)
        
        import json
        body = json.loads(result["body"])
        
        # Verify existing correlation_id is used
        assert body["correlation_id"] == existing_corr_id
