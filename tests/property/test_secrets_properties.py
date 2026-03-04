"""
Property-based tests for Secrets Manager integration.

Feature: bdo-market-insights-rewrite
Tests Property 3 from the design document.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings

# Import SecretsManagerClient from lambda layer
import sys
sys.path.insert(0, 'lambda_layer/python')
from common.secrets import (
    SecretsManagerClient,
    SecretNotFoundError,
    SecretAccessDeniedError,
    InvalidSecretError,
)


# Property 3: Secret caching within execution context
# **Validates: Requirements 3.4**

@given(
    secret_name=st.text(min_size=1, max_size=100),
    num_retrievals=st.integers(min_value=2, max_value=10)
)
@settings(max_examples=100)
def test_secret_caching_within_execution_context(secret_name, num_retrievals):
    """
    Property 3: For any Lambda execution context, when secrets are retrieved
    multiple times, the system should return cached values after the first
    retrieval without making additional Secrets Manager calls.
    
    Feature: bdo-market-insights-rewrite, Property 3: Secret caching within execution context
    """
    # Create mock secret value
    secret_value = {
        "username": "test_user",
        "password": "test_pass",
        "host": "localhost",
        "port": "5432",
        "database": "testdb"
    }
    
    # Mock boto3 client
    with patch('boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        
        # Configure mock to return secret value
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps(secret_value)
        }
        
        # Create SecretsManagerClient instance
        client = SecretsManagerClient()
        
        # Retrieve secret multiple times
        results = []
        for _ in range(num_retrievals):
            result = client.get_secret(secret_name)
            results.append(result)
        
        # Verify all results are identical
        for result in results:
            assert result == secret_value
        
        # CRITICAL: Verify Secrets Manager was called only ONCE
        # This proves caching is working
        assert mock_client.get_secret_value.call_count == 1
        
        # Verify the call was made with correct secret name
        mock_client.get_secret_value.assert_called_once_with(SecretId=secret_name)


@given(
    secret_name=st.text(min_size=1, max_size=100),
    num_retrievals_before_refresh=st.integers(min_value=1, max_value=5),
    num_retrievals_after_refresh=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=100)
def test_force_refresh_bypasses_cache(
    secret_name, num_retrievals_before_refresh, num_retrievals_after_refresh
):
    """
    Property 3 (variant): When force_refresh=True, the system should bypass
    cache and make a new Secrets Manager call.
    
    Feature: bdo-market-insights-rewrite, Property 3: Secret caching within execution context
    """
    secret_value = {"token": "test_token_123"}
    
    with patch('boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps(secret_value)
        }
        
        client = SecretsManagerClient()
        
        # Retrieve secret multiple times (should use cache)
        for _ in range(num_retrievals_before_refresh):
            client.get_secret(secret_name)
        
        # Should have called Secrets Manager only once
        assert mock_client.get_secret_value.call_count == 1
        
        # Force refresh
        client.get_secret(secret_name, force_refresh=True)
        
        # Should have called Secrets Manager again
        assert mock_client.get_secret_value.call_count == 2
        
        # Retrieve again without force_refresh (should use cache)
        for _ in range(num_retrievals_after_refresh):
            client.get_secret(secret_name)
        
        # Should still be 2 calls (no additional calls after force_refresh)
        assert mock_client.get_secret_value.call_count == 2


@given(
    secret_names=st.lists(
        st.text(min_size=1, max_size=50),
        min_size=2,
        max_size=10,
        unique=True
    ),
    retrievals_per_secret=st.integers(min_value=2, max_value=5)
)
@settings(max_examples=100)
def test_cache_isolation_between_secrets(secret_names, retrievals_per_secret):
    """
    Property 3 (variant): Cache should be isolated per secret name.
    Each unique secret should be retrieved from Secrets Manager once,
    regardless of how many times it's accessed.
    
    Feature: bdo-market-insights-rewrite, Property 3: Secret caching within execution context
    """
    with patch('boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        
        # Configure mock to return different values for different secrets
        def get_secret_side_effect(SecretId):
            return {
                'SecretString': json.dumps({"secret_name": SecretId, "value": f"value_{SecretId}"})
            }
        
        mock_client.get_secret_value.side_effect = get_secret_side_effect
        
        client = SecretsManagerClient()
        
        # Retrieve each secret multiple times
        for secret_name in secret_names:
            for _ in range(retrievals_per_secret):
                result = client.get_secret(secret_name)
                # Verify we got the correct secret
                assert result["secret_name"] == secret_name
        
        # Verify Secrets Manager was called exactly once per unique secret
        assert mock_client.get_secret_value.call_count == len(secret_names)


@given(
    secret_name=st.text(min_size=1, max_size=100),
    num_retrievals=st.integers(min_value=2, max_value=10)
)
@settings(max_examples=100)
def test_cache_persists_across_multiple_method_calls(secret_name, num_retrievals):
    """
    Property 3 (variant): Cache should persist across different convenience
    methods (get_secret, get_database_credentials, get_api_token).
    
    Feature: bdo-market-insights-rewrite, Property 3: Secret caching within execution context
    """
    db_credentials = {
        "username": "user",
        "password": "pass",
        "host": "localhost",
        "port": "5432",
        "database": "db"
    }
    
    with patch('boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps(db_credentials)
        }
        
        client = SecretsManagerClient()
        
        # Mix calls between get_secret and get_database_credentials
        for i in range(num_retrievals):
            if i % 2 == 0:
                result = client.get_secret(secret_name)
            else:
                result = client.get_database_credentials(secret_name)
            
            # Verify we got the correct credentials
            assert result == db_credentials
        
        # Should have called Secrets Manager only once despite mixed method calls
        assert mock_client.get_secret_value.call_count == 1


@given(
    secret_name=st.text(min_size=1, max_size=100),
    num_retrievals_before_clear=st.integers(min_value=1, max_value=5),
    num_retrievals_after_clear=st.integers(min_value=1, max_value=5)
)
@settings(max_examples=100)
def test_clear_cache_forces_new_retrieval(
    secret_name, num_retrievals_before_clear, num_retrievals_after_clear
):
    """
    Property 3 (variant): Clearing cache should force a new retrieval
    from Secrets Manager on the next access.
    
    Feature: bdo-market-insights-rewrite, Property 3: Secret caching within execution context
    """
    secret_value = {"api_key": "test_key"}
    
    with patch('boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps(secret_value)
        }
        
        client = SecretsManagerClient()
        
        # Retrieve secret multiple times (should use cache)
        for _ in range(num_retrievals_before_clear):
            client.get_secret(secret_name)
        
        assert mock_client.get_secret_value.call_count == 1
        
        # Clear cache
        client.clear_cache(secret_name)
        
        # Retrieve again (should make new call)
        for _ in range(num_retrievals_after_clear):
            client.get_secret(secret_name)
        
        # Should have made exactly 2 calls total (before and after clear)
        assert mock_client.get_secret_value.call_count == 2
