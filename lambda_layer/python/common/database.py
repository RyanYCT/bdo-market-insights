"""
Database connection pooling for PostgreSQL with Lambda.

Provides DatabasePool class for managing PostgreSQL connections with:
- Connection pooling with configurable size limits
- Connection reuse logic
- Retry logic for connection failures with exponential backoff
- Idle connection cleanup
"""

import time
import psycopg2
from psycopg2 import pool, OperationalError, DatabaseError
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager
from .secrets import SecretsManagerClient
from .logging import StructuredLogger


class DatabasePool:
    """
    Manages PostgreSQL connection pooling for Lambda functions.
    
    Implements connection pooling with:
    - Configurable pool size limits
    - Connection reuse from pool
    - Automatic retry with exponential backoff for connection failures
    - Idle connection cleanup
    
    Example:
        >>> pool = DatabasePool("bdo-db-credentials", pool_size=5)
        >>> with pool.get_connection() as conn:
        ...     with conn.cursor() as cursor:
        ...         cursor.execute("SELECT * FROM items")
        ...         results = cursor.fetchall()
    """
    
    def __init__(
        self,
        secret_name: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
        logger: Optional[StructuredLogger] = None
    ):
        """
        Initialize database connection pool.
        
        Args:
            secret_name: Name of secret in Secrets Manager containing DB credentials
            pool_size: Minimum number of connections to maintain (default: 5)
            max_overflow: Maximum additional connections beyond pool_size (default: 10)
            timeout: Connection timeout in seconds (default: 30)
            max_retries: Maximum retry attempts for connection failures (default: 3)
            retry_backoff_base: Base for exponential backoff in seconds (default: 2.0)
            logger: Optional StructuredLogger instance for logging
        """
        self.secret_name = secret_name
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.logger = logger
        
        # Initialize Secrets Manager client
        self._secrets_client = SecretsManagerClient()
        
        # Connection pool (lazy initialization)
        self._pool: Optional[pool.SimpleConnectionPool] = None
        self._pool_initialized = False
    
    def _get_connection_params(self) -> Dict[str, Any]:
        """
        Retrieve database connection parameters from Secrets Manager.
        
        Returns:
            dict: Connection parameters for psycopg2
        """
        credentials = self._secrets_client.get_database_credentials(self.secret_name)
        
        return {
            'host': credentials['host'],
            'port': int(credentials['port']),
            'database': credentials['database'],
            'user': credentials['username'],
            'password': credentials['password'],
            'connect_timeout': self.timeout
        }
    
    def _initialize_pool(self) -> None:
        """
        Initialize the connection pool.
        
        Creates a SimpleConnectionPool with configured size limits.
        Uses retry logic with exponential backoff for initialization failures.
        
        Raises:
            DatabasePoolError: If pool initialization fails after all retries
        """
        if self._pool_initialized:
            return
        
        connection_params = self._get_connection_params()
        
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.logger:
                    self.logger.info(
                        "Initializing database connection pool",
                        attempt=attempt,
                        pool_size=self.pool_size,
                        max_overflow=self.max_overflow
                    )
                
                # Create connection pool
                self._pool = pool.SimpleConnectionPool(
                    minconn=1,  # Start with 1 connection
                    maxconn=self.pool_size + self.max_overflow,
                    **connection_params
                )
                
                self._pool_initialized = True
                
                if self.logger:
                    self.logger.info("Database connection pool initialized successfully")
                
                return
                
            except (OperationalError, DatabaseError) as e:
                if self.logger:
                    self.logger.warning(
                        f"Failed to initialize connection pool (attempt {attempt}/{self.max_retries})",
                        error=str(e)
                    )
                
                if attempt == self.max_retries:
                    error_msg = f"Failed to initialize connection pool after {self.max_retries} attempts"
                    if self.logger:
                        self.logger.error(error_msg, error=e)
                    raise DatabasePoolError(error_msg) from e
                
                # Exponential backoff with jitter
                backoff_time = self.retry_backoff_base ** attempt
                jitter = backoff_time * 0.1  # 10% jitter
                sleep_time = backoff_time + (jitter * (0.5 - time.time() % 1))
                
                if self.logger:
                    self.logger.info(f"Retrying in {sleep_time:.2f} seconds")
                
                time.sleep(sleep_time)
    
    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool with automatic context management.
        
        Implements connection reuse from pool. If pool is not initialized,
        initializes it first. Automatically returns connection to pool
        when context exits.
        
        Yields:
            psycopg2.connection: Database connection
            
        Raises:
            DatabasePoolError: If unable to get connection from pool
            
        Example:
            >>> pool = DatabasePool("db-secret")
            >>> with pool.get_connection() as conn:
            ...     cursor = conn.cursor()
            ...     cursor.execute("SELECT 1")
        """
        # Initialize pool if needed
        if not self._pool_initialized:
            self._initialize_pool()
        
        connection = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.logger:
                    self.logger.debug(
                        "Getting connection from pool",
                        attempt=attempt
                    )
                
                # Get connection from pool (reuses existing if available)
                connection = self._pool.getconn()
                
                if connection is None:
                    raise DatabasePoolError("Pool returned None connection")
                
                # Test connection is alive
                connection.isolation_level
                
                if self.logger:
                    self.logger.debug("Connection acquired from pool")
                
                yield connection
                
                # Connection used successfully, return to pool
                if connection and not connection.closed:
                    self._pool.putconn(connection)
                    if self.logger:
                        self.logger.debug("Connection returned to pool")
                
                return
                
            except (OperationalError, DatabaseError) as e:
                if self.logger:
                    self.logger.warning(
                        f"Connection error (attempt {attempt}/{self.max_retries})",
                        error=str(e)
                    )
                
                # Close bad connection
                if connection and not connection.closed:
                    try:
                        connection.close()
                    except Exception:
                        pass
                
                if attempt == self.max_retries:
                    error_msg = f"Failed to get working connection after {self.max_retries} attempts"
                    if self.logger:
                        self.logger.error(error_msg, error=e)
                    raise DatabasePoolError(error_msg) from e
                
                # Exponential backoff
                backoff_time = self.retry_backoff_base ** attempt
                jitter = backoff_time * 0.1
                sleep_time = backoff_time + (jitter * (0.5 - time.time() % 1))
                
                time.sleep(sleep_time)
            
            except Exception as e:
                # Return connection to pool on unexpected error
                if connection and not connection.closed:
                    self._pool.putconn(connection)
                raise
    
    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a parameterized SELECT query and return results.
        
        Uses connection from pool and automatically handles connection lifecycle.
        
        Args:
            query: SQL query with parameter placeholders (%s)
            params: Query parameters tuple (optional)
            
        Returns:
            list: List of result rows as dictionaries
            
        Raises:
            DatabasePoolError: If query execution fails
            
        Example:
            >>> pool = DatabasePool("db-secret")
            >>> results = pool.execute_query(
            ...     "SELECT * FROM items WHERE item_id = %s",
            ...     (1001,)
            ... )
        """
        with self.get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    if self.logger:
                        self.logger.debug(
                            "Executing query",
                            query=query[:100]  # Log first 100 chars
                        )
                    
                    cursor.execute(query, params or ())
                    
                    # Fetch column names
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    
                    # Fetch all rows
                    rows = cursor.fetchall()
                    
                    # Convert to list of dictionaries
                    results = [dict(zip(columns, row)) for row in rows]
                    
                    if self.logger:
                        self.logger.debug(f"Query returned {len(results)} rows")
                    
                    return results
                    
            except (OperationalError, DatabaseError) as e:
                error_msg = f"Query execution failed: {str(e)}"
                if self.logger:
                    self.logger.error(error_msg, error=e, query=query[:100])
                raise DatabasePoolError(error_msg) from e
    
    def execute_many(
        self,
        query: str,
        params_list: List[Tuple]
    ) -> int:
        """
        Execute batch INSERT/UPDATE with multiple parameter sets.
        
        Uses connection from pool and automatically handles connection lifecycle.
        Commits transaction on success, rolls back on error.
        
        Args:
            query: SQL query with parameter placeholders (%s)
            params_list: List of parameter tuples
            
        Returns:
            int: Number of rows affected
            
        Raises:
            DatabasePoolError: If batch execution fails
            
        Example:
            >>> pool = DatabasePool("db-secret")
            >>> affected = pool.execute_many(
            ...     "INSERT INTO items (item_id, name) VALUES (%s, %s)",
            ...     [(1, "Item 1"), (2, "Item 2")]
            ... )
        """
        with self.get_connection() as conn:
            try:
                with conn.cursor() as cursor:
                    if self.logger:
                        self.logger.debug(
                            "Executing batch operation",
                            query=query[:100],
                            batch_size=len(params_list)
                        )
                    
                    cursor.executemany(query, params_list)
                    
                    affected_rows = cursor.rowcount
                    
                    # Commit transaction
                    conn.commit()
                    
                    if self.logger:
                        self.logger.debug(f"Batch operation affected {affected_rows} rows")
                    
                    return affected_rows
                    
            except (OperationalError, DatabaseError) as e:
                # Rollback on error
                try:
                    conn.rollback()
                except Exception:
                    pass
                
                error_msg = f"Batch execution failed: {str(e)}"
                if self.logger:
                    self.logger.error(
                        error_msg,
                        error=e,
                        query=query[:100],
                        batch_size=len(params_list)
                    )
                raise DatabasePoolError(error_msg) from e
    
    def close_all(self) -> None:
        """
        Close all connections in the pool.
        
        Should be called when Lambda execution context is ending
        or when pool is no longer needed.
        """
        if self._pool:
            try:
                if self.logger:
                    self.logger.info("Closing all database connections")
                
                self._pool.closeall()
                self._pool_initialized = False
                
                if self.logger:
                    self.logger.info("All database connections closed")
                    
            except Exception as e:
                if self.logger:
                    self.logger.error("Error closing connections", error=e)
    
    def __del__(self):
        """Cleanup connections when pool is garbage collected."""
        self.close_all()


class DatabasePoolError(Exception):
    """Exception raised for database pool errors."""
    pass

