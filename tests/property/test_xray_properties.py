"""
Property-based tests for X-Ray tracing integration.

Tests Property 20: X-Ray trace ID in logs
Validates: Requirements 11.5
"""

import json
import os
import sys
from unittest.mock import Mock, patch, MagicMock, mock_open
from hypothesis import given, strategies as st, settings
import pytest

# Mock psycopg2 before importing common modules
sys.modules['psycopg2'] = MagicMock()
sys.modules['psycopg2.pool'] = MagicMock()
sys.modules['psycopg2.extras'] = MagicMock()

# Import from common
from common.logging import StructuredLogger

# Import X-Ray utilities
try:
    from common.xray import (
        is_xray_enabled,
        get_trace_id,
        create_subsegment,
        add_annotation,
        add_metadata,
    )
except ImportError:
    # If xray module doesn't exist yet, skip tests
    pytest.skip("X-Ray module not available", allow_module_level=True)


# Strategies for generating test data
@st.composite
def log_message_strategy(draw):
    """Generate random log messages."""
    return draw(st.text(min_size=1, max_size=200))


@st.composite
def function_name_strategy(draw):
    """Generate random function names."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'),
        min_size=3,
        max_size=50
    ))


@st.composite
def correlation_id_strategy(draw):
    """Generate random correlation IDs."""
    return draw(st.uuids()).hex


@st.composite
def trace_id_strategy(draw):
    """Generate X-Ray trace IDs in the correct format."""
    # X-Ray trace ID format: 1-{hex-timestamp}-{hex-unique-id}
    timestamp = draw(st.integers(min_value=0x5e000000, max_value=0x6fffffff))
    unique_id = draw(st.binary(min_size=12, max_size=12))
    return f"1-{timestamp:08x}-{unique_id.hex()}"


class TestXRayTraceIDInLogs:
    """
    Property 20: X-Ray trace ID in logs
    
    For any log entry when X-Ray tracing is enabled, the log should include
    the X-Ray trace ID for correlation.
    
    Validates: Requirements 11.5
    """
    
    @given(
        function_name=function_name_strategy(),
        correlation_id=correlation_id_strategy(),
        message=log_message_strategy(),
        trace_id=trace_id_strategy()
    )
    @settings(max_examples=100, deadline=None)
    def test_xray_trace_id_included_in_logs_when_enabled(
        self,
        function_name,
        correlation_id,
        message,
        trace_id
    ):
        """
        Property: When X-Ray is enabled, all log entries should include xray_trace_id.
        
        Feature: bdo-market-insights-rewrite, Property 20: X-Ray trace ID in logs
        """
        # Mock X-Ray to be enabled and return a trace ID
        with patch('common.logging.is_xray_enabled', return_value=True), \
             patch('common.logging.get_trace_id', return_value=trace_id):
            
            # Create logger
            logger = StructuredLogger(
                function_name=function_name,
                correlation_id=correlation_id,
                request_id='test-request-id'
            )
            
            # Capture log output
            with patch.object(logger._logger, 'info') as mock_log:
                logger.info(message)
                
                # Verify log was called
                assert mock_log.called, "Logger should emit log entry"
                
                # Get the extra fields passed to the logger
                call_args = mock_log.call_args
                extra_fields = call_args[1].get('extra', {})
                
                # Property: xray_trace_id must be present
                assert 'xray_trace_id' in extra_fields, \
                    "Log entry must include xray_trace_id when X-Ray is enabled"
                
                # Property: xray_trace_id must match the current trace
                assert extra_fields['xray_trace_id'] == trace_id, \
                    f"xray_trace_id must match current trace ID: expected {trace_id}, got {extra_fields.get('xray_trace_id')}"
                
                # Property: Standard fields must still be present
                assert 'timestamp' in extra_fields, "timestamp must be present"
                assert 'level' in extra_fields, "level must be present"
                assert 'function_name' in extra_fields, "function_name must be present"
                assert 'correlation_id' in extra_fields, "correlation_id must be present"
                assert extra_fields['function_name'] == function_name
                assert extra_fields['correlation_id'] == correlation_id
    
    @given(
        function_name=function_name_strategy(),
        correlation_id=correlation_id_strategy(),
        message=log_message_strategy()
    )
    @settings(max_examples=100, deadline=None)
    def test_logs_work_when_xray_disabled(
        self,
        function_name,
        correlation_id,
        message
    ):
        """
        Property: When X-Ray is disabled, logs should still work without xray_trace_id.
        
        Feature: bdo-market-insights-rewrite, Property 20: X-Ray trace ID in logs
        """
        # Mock X-Ray to be disabled
        with patch('common.logging.is_xray_enabled', return_value=False):
            
            # Create logger
            logger = StructuredLogger(
                function_name=function_name,
                correlation_id=correlation_id,
                request_id='test-request-id'
            )
            
            # Capture log output
            with patch.object(logger._logger, 'info') as mock_log:
                logger.info(message)
                
                # Verify log was called
                assert mock_log.called, "Logger should emit log entry"
                
                # Get the extra fields passed to the logger
                call_args = mock_log.call_args
                extra_fields = call_args[1].get('extra', {})
                
                # Property: xray_trace_id should NOT be present when disabled
                assert 'xray_trace_id' not in extra_fields, \
                    "Log entry should not include xray_trace_id when X-Ray is disabled"
                
                # Property: Standard fields must still be present
                assert 'timestamp' in extra_fields, "timestamp must be present"
                assert 'level' in extra_fields, "level must be present"
                assert 'function_name' in extra_fields, "function_name must be present"
                assert 'correlation_id' in extra_fields, "correlation_id must be present"
    
    @given(
        function_name=function_name_strategy(),
        correlation_id=correlation_id_strategy(),
        trace_id=trace_id_strategy()
    )
    @settings(max_examples=100, deadline=None)
    def test_trace_id_consistent_across_multiple_logs(
        self,
        function_name,
        correlation_id,
        trace_id
    ):
        """
        Property: All logs within the same trace should have the same xray_trace_id.
        
        Feature: bdo-market-insights-rewrite, Property 20: X-Ray trace ID in logs
        """
        # Mock X-Ray to be enabled and return consistent trace ID
        with patch('common.logging.is_xray_enabled', return_value=True), \
             patch('common.logging.get_trace_id', return_value=trace_id):
            
            # Create logger
            logger = StructuredLogger(
                function_name=function_name,
                correlation_id=correlation_id
            )
            
            # Emit multiple log entries
            trace_ids_seen = []
            
            with patch.object(logger._logger, 'info') as mock_log:
                for i in range(5):
                    logger.info(f"Log message {i}")
                
                # Check all log calls
                for call in mock_log.call_args_list:
                    extra_fields = call[1].get('extra', {})
                    if 'xray_trace_id' in extra_fields:
                        trace_ids_seen.append(extra_fields['xray_trace_id'])
                
                # Property: All trace IDs should be the same
                assert len(trace_ids_seen) == 5, "Should have 5 log entries with trace IDs"
                assert all(tid == trace_id for tid in trace_ids_seen), \
                    f"All logs should have the same trace ID: {trace_id}, but got {trace_ids_seen}"
    
    @given(
        function_name=function_name_strategy(),
        correlation_id=correlation_id_strategy(),
        message=log_message_strategy(),
        trace_id=trace_id_strategy()
    )
    @settings(max_examples=100, deadline=None)
    def test_trace_id_in_all_log_levels(
        self,
        function_name,
        correlation_id,
        message,
        trace_id
    ):
        """
        Property: X-Ray trace ID should be included in logs at all levels (info, warning, error, debug).
        
        Feature: bdo-market-insights-rewrite, Property 20: X-Ray trace ID in logs
        """
        # Mock X-Ray to be enabled
        with patch('common.logging.is_xray_enabled', return_value=True), \
             patch('common.logging.get_trace_id', return_value=trace_id):
            
            # Create logger
            logger = StructuredLogger(
                function_name=function_name,
                correlation_id=correlation_id
            )
            
            # Test each log level
            log_methods = [
                ('info', logger.info),
                ('warning', logger.warning),
                ('error', logger.error),
                ('debug', logger.debug),
            ]
            
            for level_name, log_method in log_methods:
                with patch.object(logger._logger, level_name) as mock_log:
                    # Call the log method
                    if level_name == 'error':
                        log_method(message, error=Exception("test"))
                    else:
                        log_method(message)
                    
                    # Verify trace ID is present
                    assert mock_log.called, f"{level_name} should be called"
                    call_args = mock_log.call_args
                    extra_fields = call_args[1].get('extra', {})
                    
                    assert 'xray_trace_id' in extra_fields, \
                        f"xray_trace_id must be present in {level_name} logs"
                    assert extra_fields['xray_trace_id'] == trace_id, \
                        f"xray_trace_id must match in {level_name} logs"
    
    @given(
        function_name=function_name_strategy(),
        correlation_id=correlation_id_strategy(),
        message=log_message_strategy(),
        trace_id=trace_id_strategy()
    )
    @settings(max_examples=100, deadline=None)
    def test_trace_id_in_child_logger(
        self,
        function_name,
        correlation_id,
        message,
        trace_id
    ):
        """
        Property: Child loggers created with with_context() should also include xray_trace_id.
        
        Feature: bdo-market-insights-rewrite, Property 20: X-Ray trace ID in logs
        """
        # Mock X-Ray to be enabled
        with patch('common.logging.is_xray_enabled', return_value=True), \
             patch('common.logging.get_trace_id', return_value=trace_id):
            
            # Create parent logger
            parent_logger = StructuredLogger(
                function_name=function_name,
                correlation_id=correlation_id
            )
            
            # Create child logger with additional context
            child_logger = parent_logger.with_context(user_id='user-123')
            
            # Emit log from child
            with patch.object(child_logger._logger, 'info') as mock_log:
                child_logger.info(message)
                
                # Verify trace ID is present in child logger
                assert mock_log.called
                call_args = mock_log.call_args
                extra_fields = call_args[1].get('extra', {})
                
                assert 'xray_trace_id' in extra_fields, \
                    "Child logger must include xray_trace_id"
                assert extra_fields['xray_trace_id'] == trace_id, \
                    "Child logger xray_trace_id must match parent"
                
                # Verify child context is also present
                assert 'user_id' in extra_fields, \
                    "Child logger must include additional context"
                assert extra_fields['user_id'] == 'user-123'


class TestXRayUtilities:
    """Test X-Ray utility functions for correctness."""
    
    def test_is_xray_enabled_returns_false_when_sdk_not_available(self):
        """X-Ray should be disabled if SDK is not available."""
        with patch('common.xray.XRAY_AVAILABLE', False):
            assert is_xray_enabled() is False
    
    def test_is_xray_enabled_returns_false_when_explicitly_disabled(self):
        """X-Ray should be disabled if AWS_XRAY_TRACING_ENABLED=false."""
        with patch('common.xray.XRAY_AVAILABLE', True), \
             patch.dict(os.environ, {'AWS_XRAY_TRACING_ENABLED': 'false'}):
            assert is_xray_enabled() is False
    
    def test_is_xray_enabled_returns_false_outside_lambda(self):
        """X-Ray should be disabled if not running in Lambda."""
        with patch('common.xray.XRAY_AVAILABLE', True), \
             patch.dict(os.environ, {}, clear=True):
            # No AWS_LAMBDA_FUNCTION_NAME set
            assert is_xray_enabled() is False
    
    def test_get_trace_id_returns_none_when_disabled(self):
        """get_trace_id should return None when X-Ray is disabled."""
        with patch('common.xray.is_xray_enabled', return_value=False):
            assert get_trace_id() is None
    
    def test_create_subsegment_returns_nullcontext_when_disabled(self):
        """create_subsegment should return no-op context when X-Ray is disabled."""
        with patch('common.xray.is_xray_enabled', return_value=False):
            # Should not raise an error
            with create_subsegment('test'):
                pass  # No-op
    
    def test_add_annotation_does_not_fail_when_disabled(self):
        """add_annotation should not fail when X-Ray is disabled."""
        with patch('common.xray.is_xray_enabled', return_value=False):
            # Should not raise an error
            add_annotation('key', 'value')
    
    def test_add_metadata_does_not_fail_when_disabled(self):
        """add_metadata should not fail when X-Ray is disabled."""
        with patch('common.xray.is_xray_enabled', return_value=False):
            # Should not raise an error
            add_metadata('key', {'data': 'value'})


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--hypothesis-show-statistics'])
