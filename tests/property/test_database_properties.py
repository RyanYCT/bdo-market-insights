"""
Property-based tests for database connection pooling.

Feature: bdo-market-insights-rewrite
Tests Property 7 from the design document.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock, call
from hypothesis import given, strategies as st, settings

# Mock psycopg2 before importing DatabasePool
import sys
sys.path.insert(0, 'lambda_layer/python')

# Create mock psycopg2 module
mock_psycopg2 = MagicMock()
mock_psycopg2.pool = MagicMock()
mock_psycopg2.OperationalError = type('OperationalError', (Exception,), {})
mock_psycopg2.DatabaseError = type('DatabaseError', (Exception,), {})
sys.modules['psycopg2'] = mock_psycopg2
sys.modules['psycopg2.pool'] = mock_psycopg2.pool

from common.database import DatabasePool, DatabasePoolError


# Property 7: Connection reuse from pool
# **Validates: Requirements 5.2**

@given(
    num_operations=st.integers(min_value=2, max_value=20)
)
@settings(max_examples=100)
def test_connection_reuse_from_pool(num_operations):
    """
    Property 7: For any sequence of database operations within the same Lambda
    execution context, the system should reuse connections from the pool rather
    than creating new connections for each operation.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    # Mock database credentials
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    # Mock connection object
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1  # Simulate connection is alive
    
    # Mock cursor
    mock_cursor = MagicMock()
    mock_cursor.description = [('id',), ('name',)]
    mock_cursor.fetchall.return_value = [(1, 'test')]
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    # Mock connection pool
    mock_pool = MagicMock()
    # Always return the same connection object (simulating reuse)
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        # Mock Secrets Manager
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            # Create DatabasePool instance
            pool = DatabasePool("test-secret", pool_size=5)
            
            # Perform multiple database operations
            for i in range(num_operations):
                with pool.get_connection() as conn:
                    # Simulate using the connection
                    assert conn is not None
                    assert conn == mock_connection
            
            # CRITICAL: Verify connection pool was initialized only ONCE
            assert mock_pool_class.call_count == 1
            
            # CRITICAL: Verify getconn was called for each operation
            # This proves we're getting connections from the pool
            assert mock_pool.getconn.call_count == num_operations
            
            # CRITICAL: Verify putconn was called for each operation
            # This proves connections are being returned to the pool for reuse
            assert mock_pool.putconn.call_count == num_operations
            
            # Verify the same connection object was reused
            for call_args in mock_pool.putconn.call_args_list:
                assert call_args[0][0] == mock_connection


@given(
    num_queries=st.integers(min_value=2, max_value=15)
)
@settings(max_examples=100)
def test_execute_query_reuses_connections(num_queries):
    """
    Property 7 (variant): Multiple execute_query calls should reuse
    connections from the pool.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1
    
    mock_cursor = MagicMock()
    mock_cursor.description = [('count',)]
    mock_cursor.fetchall.return_value = [(42,)]
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            pool = DatabasePool("test-secret", pool_size=5)
            
            # Execute multiple queries
            for i in range(num_queries):
                results = pool.execute_query("SELECT COUNT(*) FROM items")
                assert len(results) == 1
                assert results[0]['count'] == 42
            
            # Verify pool was initialized once
            assert mock_pool_class.call_count == 1
            
            # Verify connections were acquired and returned for each query
            assert mock_pool.getconn.call_count == num_queries
            assert mock_pool.putconn.call_count == num_queries


@given(
    num_batches=st.integers(min_value=2, max_value=10)
)
@settings(max_examples=100)
def test_execute_many_reuses_connections(num_batches):
    """
    Property 7 (variant): Multiple execute_many calls should reuse
    connections from the pool.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1
    
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 5
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            pool = DatabasePool("test-secret", pool_size=5)
            
            # Execute multiple batch operations
            for i in range(num_batches):
                affected = pool.execute_many(
                    "INSERT INTO items (id, name) VALUES (%s, %s)",
                    [(1, "Item1"), (2, "Item2")]
                )
                assert affected == 5
            
            # Verify pool was initialized once
            assert mock_pool_class.call_count == 1
            
            # Verify connections were acquired and returned for each batch
            assert mock_pool.getconn.call_count == num_batches
            assert mock_pool.putconn.call_count == num_batches


@given(
    num_mixed_operations=st.integers(min_value=3, max_value=15)
)
@settings(max_examples=100)
def test_mixed_operations_reuse_connections(num_mixed_operations):
    """
    Property 7 (variant): Mixed operations (get_connection, execute_query,
    execute_many) should all reuse connections from the same pool.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1
    
    mock_cursor = MagicMock()
    mock_cursor.description = [('id',)]
    mock_cursor.fetchall.return_value = [(1,)]
    mock_cursor.rowcount = 3
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            pool = DatabasePool("test-secret", pool_size=5)
            
            # Perform mixed operations
            for i in range(num_mixed_operations):
                operation_type = i % 3
                
                if operation_type == 0:
                    # Direct connection usage
                    with pool.get_connection() as conn:
                        assert conn is not None
                elif operation_type == 1:
                    # Execute query
                    results = pool.execute_query("SELECT id FROM items")
                    assert len(results) == 1
                else:
                    # Execute batch
                    affected = pool.execute_many(
                        "INSERT INTO items (id) VALUES (%s)",
                        [(1,), (2,), (3,)]
                    )
                    assert affected == 3
            
            # Verify pool was initialized once
            assert mock_pool_class.call_count == 1
            
            # Verify connections were acquired and returned for all operations
            assert mock_pool.getconn.call_count == num_mixed_operations
            assert mock_pool.putconn.call_count == num_mixed_operations


@given(
    pool_size=st.integers(min_value=1, max_value=10),
    num_operations=st.integers(min_value=2, max_value=20)
)
@settings(max_examples=100)
def test_pool_size_configuration_respected(pool_size, num_operations):
    """
    Property 7 (variant): The connection pool should be initialized with
    the configured pool size, and connections should be reused regardless
    of the pool size setting.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1
    
    mock_cursor = MagicMock()
    mock_cursor.description = [('result',)]
    mock_cursor.fetchall.return_value = [(1,)]
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            # Create pool with specific size
            pool = DatabasePool("test-secret", pool_size=pool_size)
            
            # Perform operations
            for i in range(num_operations):
                results = pool.execute_query("SELECT 1")
                assert len(results) == 1
            
            # Verify pool was initialized with correct parameters
            assert mock_pool_class.call_count == 1
            call_kwargs = mock_pool_class.call_args[1]
            # maxconn should be pool_size + max_overflow (default 10)
            assert call_kwargs['maxconn'] == pool_size + 10
            
            # Verify connections were reused
            assert mock_pool.getconn.call_count == num_operations
            assert mock_pool.putconn.call_count == num_operations


@given(
    num_operations_before_close=st.integers(min_value=2, max_value=10),
    num_operations_after_close=st.integers(min_value=2, max_value=10)
)
@settings(max_examples=100)
def test_connection_reuse_after_pool_reinitialization(
    num_operations_before_close, num_operations_after_close
):
    """
    Property 7 (variant): After closing all connections and reinitializing,
    the pool should still reuse connections properly.
    
    Feature: bdo-market-insights-rewrite, Property 7: Connection reuse from pool
    """
    db_credentials = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    mock_connection = MagicMock()
    mock_connection.closed = False
    mock_connection.isolation_level = 1
    
    mock_cursor = MagicMock()
    mock_cursor.description = [('value',)]
    mock_cursor.fetchall.return_value = [(100,)]
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connection.cursor.return_value.__exit__.return_value = None
    
    mock_pool = MagicMock()
    mock_pool.getconn.return_value = mock_connection
    
    with patch('boto3.client') as mock_boto_client:
        mock_secrets_client = MagicMock()
        mock_boto_client.return_value = mock_secrets_client
        mock_secrets_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        with patch('psycopg2.pool.SimpleConnectionPool') as mock_pool_class:
            mock_pool_class.return_value = mock_pool
            
            pool = DatabasePool("test-secret", pool_size=5)
            
            # Perform operations before close
            for i in range(num_operations_before_close):
                results = pool.execute_query("SELECT 100")
                assert len(results) == 1
            
            # Close all connections
            pool.close_all()
            
            # Perform operations after close (should reinitialize)
            for i in range(num_operations_after_close):
                results = pool.execute_query("SELECT 100")
                assert len(results) == 1
            
            # Verify pool was initialized twice (before and after close)
            assert mock_pool_class.call_count == 2
            
            # Verify total connection acquisitions
            total_operations = num_operations_before_close + num_operations_after_close
            assert mock_pool.getconn.call_count == total_operations
            assert mock_pool.putconn.call_count == total_operations

