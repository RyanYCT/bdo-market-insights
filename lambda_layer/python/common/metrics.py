"""
CloudWatch custom metrics emission for BDO Market Insights.

Provides MetricsClient class for emitting custom CloudWatch metrics
for ETL pipeline operations, query latency, External API calls, and
database connection pool utilization.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from contextlib import contextmanager
import boto3
from botocore.exceptions import ClientError


class MetricsClient:
    """
    Client for emitting custom CloudWatch metrics.
    
    Provides methods for tracking:
    - ETL pipeline success/failure
    - Query latency
    - External API response times
    - Database connection pool utilization
    
    Example:
        >>> metrics = MetricsClient(namespace="BDOMarketInsights/ETL")
        >>> metrics.emit_etl_success("retrieveIdList", item_count=100)
        >>> with metrics.track_latency("queryData") as tracker:
        ...     # Perform query operation
        ...     pass
    """
    
    def __init__(
        self,
        namespace: str = "BDOMarketInsights",
        logger: Optional[Any] = None
    ):
        """
        Initialize metrics client.
        
        Args:
            namespace: CloudWatch namespace for metrics
            logger: Optional StructuredLogger instance for error logging
        """
        self.namespace = namespace
        self.logger = logger
        self.cloudwatch = boto3.client('cloudwatch')
    
    def _emit_metric(
        self,
        metric_name: str,
        value: float,
        unit: str = 'None',
        dimensions: Optional[List[Dict[str, str]]] = None
    ) -> None:
        """
        Emit a single metric to CloudWatch.
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: CloudWatch unit (Count, Milliseconds, etc.)
            dimensions: Optional list of dimension dicts
        """
        try:
            metric_data = {
                'MetricName': metric_name,
                'Value': value,
                'Unit': unit,
                'Timestamp': datetime.now(timezone.utc)
            }
            
            if dimensions:
                metric_data['Dimensions'] = dimensions
            
            self.cloudwatch.put_metric_data(
                Namespace=self.namespace,
                MetricData=[metric_data]
            )
            
            if self.logger:
                self.logger.debug(
                    f"Emitted metric: {metric_name}",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    dimensions=dimensions
                )
                
        except ClientError as e:
            # Don't fail the operation if metrics emission fails
            if self.logger:
                self.logger.warning(
                    f"Failed to emit metric: {metric_name}",
                    error=e,
                    metric_name=metric_name
                )
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Unexpected error emitting metric: {metric_name}",
                    error=e,
                    metric_name=metric_name
                )
    
    def emit_etl_success(
        self,
        function_name: str,
        **dimensions_kwargs
    ) -> None:
        """
        Emit ETL pipeline success metric.
        
        Args:
            function_name: Name of the Lambda function
            **dimensions_kwargs: Additional dimensions (e.g., item_count=100)
        """
        dimensions = [
            {'Name': 'FunctionName', 'Value': function_name},
            {'Name': 'Status', 'Value': 'Success'}
        ]
        
        # Add custom dimensions
        for key, value in dimensions_kwargs.items():
            dimensions.append({'Name': key, 'Value': str(value)})
        
        self._emit_metric(
            metric_name='ETLPipelineExecution',
            value=1,
            unit='Count',
            dimensions=dimensions
        )
    
    def emit_etl_failure(
        self,
        function_name: str,
        error_type: str,
        **dimensions_kwargs
    ) -> None:
        """
        Emit ETL pipeline failure metric.
        
        Args:
            function_name: Name of the Lambda function
            error_type: Type of error that occurred
            **dimensions_kwargs: Additional dimensions
        """
        dimensions = [
            {'Name': 'FunctionName', 'Value': function_name},
            {'Name': 'Status', 'Value': 'Failure'},
            {'Name': 'ErrorType', 'Value': error_type}
        ]
        
        # Add custom dimensions
        for key, value in dimensions_kwargs.items():
            dimensions.append({'Name': key, 'Value': str(value)})
        
        self._emit_metric(
            metric_name='ETLPipelineExecution',
            value=1,
            unit='Count',
            dimensions=dimensions
        )
    
    def emit_query_latency(
        self,
        function_name: str,
        latency_ms: float,
        **dimensions_kwargs
    ) -> None:
        """
        Emit query latency metric.
        
        Args:
            function_name: Name of the Lambda function
            latency_ms: Latency in milliseconds
            **dimensions_kwargs: Additional dimensions
        """
        dimensions = [
            {'Name': 'FunctionName', 'Value': function_name}
        ]
        
        # Add custom dimensions
        for key, value in dimensions_kwargs.items():
            dimensions.append({'Name': key, 'Value': str(value)})
        
        self._emit_metric(
            metric_name='QueryLatency',
            value=latency_ms,
            unit='Milliseconds',
            dimensions=dimensions
        )
    
    def emit_api_call(
        self,
        success: bool,
        response_time_ms: float,
        status_code: int = 0,
        **dimensions_kwargs
    ) -> None:
        """
        Emit External API call metrics.
        
        Args:
            success: Whether the call was successful
            response_time_ms: Response time in milliseconds
            status_code: HTTP status code
            **dimensions_kwargs: Additional dimensions
        """
        dimensions = [
            {'Name': 'Success', 'Value': str(success)},
            {'Name': 'StatusCode', 'Value': str(status_code)}
        ]
        
        # Add custom dimensions
        for key, value in dimensions_kwargs.items():
            dimensions.append({'Name': key, 'Value': str(value)})
        
        # Emit call count
        self._emit_metric(
            metric_name='ExternalAPICallCount',
            value=1,
            unit='Count',
            dimensions=dimensions
        )
        
        # Emit response time
        self._emit_metric(
            metric_name='ExternalAPIResponseTime',
            value=response_time_ms,
            unit='Milliseconds',
            dimensions=[{'Name': 'Success', 'Value': str(success)}]
        )
    
    def emit_db_pool_utilization(
        self,
        pool_size: int,
        active_connections: int,
        idle_connections: int
    ) -> None:
        """
        Emit database connection pool utilization metrics.
        
        Args:
            pool_size: Total pool size
            active_connections: Number of active connections
            idle_connections: Number of idle connections
        """
        utilization_percent = (active_connections / pool_size * 100) if pool_size > 0 else 0
        
        self._emit_metric(
            metric_name='DBPoolUtilization',
            value=utilization_percent,
            unit='Percent'
        )
        
        self._emit_metric(
            metric_name='DBActiveConnections',
            value=active_connections,
            unit='Count'
        )
        
        self._emit_metric(
            metric_name='DBIdleConnections',
            value=idle_connections,
            unit='Count'
        )
    
    @contextmanager
    def track_latency(self, operation_name: str, **dimensions_kwargs):
        """
        Context manager for tracking operation latency.
        
        Automatically measures elapsed time and emits latency metric.
        
        Args:
            operation_name: Name of the operation being tracked
            **dimensions_kwargs: Additional dimensions
            
        Yields:
            LatencyTracker: Tracker object with elapsed_ms property
            
        Example:
            >>> with metrics.track_latency("queryData", item_id=1001) as tracker:
            ...     # Perform query
            ...     pass
            >>> print(f"Query took {tracker.elapsed_ms}ms")
        """
        tracker = LatencyTracker()
        tracker.start()
        
        try:
            yield tracker
        finally:
            tracker.stop()
            
            # Emit latency metric
            dimensions = [
                {'Name': 'Operation', 'Value': operation_name}
            ]
            
            for key, value in dimensions_kwargs.items():
                dimensions.append({'Name': key, 'Value': str(value)})
            
            self._emit_metric(
                metric_name='OperationLatency',
                value=tracker.elapsed_ms,
                unit='Milliseconds',
                dimensions=dimensions
            )


class LatencyTracker:
    """
    Helper class for tracking operation latency.
    
    Used by MetricsClient.track_latency() context manager.
    """
    
    def __init__(self):
        """Initialize latency tracker."""
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.elapsed_ms: float = 0.0
    
    def start(self) -> None:
        """Start timing."""
        self.start_time = time.time()
    
    def stop(self) -> None:
        """Stop timing and calculate elapsed time."""
        self.end_time = time.time()
        if self.start_time:
            self.elapsed_ms = (self.end_time - self.start_time) * 1000
