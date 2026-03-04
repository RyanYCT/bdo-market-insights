"""
Secrets Manager integration for secure credential management.

Provides SecretsManagerClient class for retrieving and caching credentials
from AWS Secrets Manager during Lambda execution context.
"""

import json
import boto3
from typing import Any, Dict, Optional
from botocore.exceptions import ClientError


class SecretsManagerClient:
    """
    Manages credential retrieval from AWS Secrets Manager with caching.
    
    Implements caching for the duration of a Lambda execution context to
    minimize API calls and improve performance. Handles errors for missing
    or invalid secrets.
    
    Example:
        >>> client = SecretsManagerClient()
        >>> db_creds = client.get_secret("bdo-db-credentials")
        >>> db_creds['username']
        'myuser'
        >>> # Second call uses cache
        >>> db_creds_cached = client.get_secret("bdo-db-credentials")
    """
    
    def __init__(self, region_name: str = "us-east-1"):
        """
        Initialize Secrets Manager client.
        
        Args:
            region_name: AWS region for Secrets Manager (default: us-east-1)
        """
        self._client = boto3.client('secretsmanager', region_name=region_name)
        self._cache: Dict[str, Any] = {}
    
    def get_secret(self, secret_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieve secret from Secrets Manager with caching.
        
        On first call, retrieves the secret from AWS Secrets Manager and caches it.
        Subsequent calls return the cached value unless force_refresh is True.
        
        Args:
            secret_name: Name or ARN of the secret in Secrets Manager
            force_refresh: If True, bypass cache and fetch fresh value
            
        Returns:
            dict: Parsed secret value (assumes JSON format)
            
        Raises:
            SecretNotFoundError: If secret does not exist
            SecretAccessDeniedError: If insufficient permissions to access secret
            InvalidSecretError: If secret value is not valid JSON
            SecretsManagerError: For other Secrets Manager errors
            
        Example:
            >>> client = SecretsManagerClient()
            >>> creds = client.get_secret("my-db-credentials")
            >>> print(creds['username'])
        """
        # Return cached value if available and not forcing refresh
        if not force_refresh and secret_name in self._cache:
            return self._cache[secret_name]
        
        try:
            # Retrieve secret from Secrets Manager
            response = self._client.get_secret_value(SecretId=secret_name)
            
            # Parse secret string (assumes JSON format)
            if 'SecretString' in response:
                secret_value = json.loads(response['SecretString'])
            else:
                # Handle binary secrets (decode if needed)
                raise InvalidSecretError(
                    f"Secret '{secret_name}' contains binary data, expected JSON string"
                )
            
            # Cache the secret value
            self._cache[secret_name] = secret_value
            
            return secret_value
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            
            if error_code == 'ResourceNotFoundException':
                raise SecretNotFoundError(
                    f"Secret '{secret_name}' not found in Secrets Manager"
                ) from e
            elif error_code == 'AccessDeniedException':
                raise SecretAccessDeniedError(
                    f"Access denied to secret '{secret_name}'. Check IAM permissions."
                ) from e
            elif error_code == 'InvalidRequestException':
                raise InvalidSecretError(
                    f"Invalid request for secret '{secret_name}': {str(e)}"
                ) from e
            else:
                raise SecretsManagerError(
                    f"Error retrieving secret '{secret_name}': {str(e)}"
                ) from e
                
        except json.JSONDecodeError as e:
            raise InvalidSecretError(
                f"Secret '{secret_name}' contains invalid JSON: {str(e)}"
            ) from e
    
    def get_database_credentials(self, secret_name: str) -> Dict[str, str]:
        """
        Retrieve database credentials from Secrets Manager.
        
        Convenience method that retrieves and validates database credentials.
        Expects secret to contain: username, password, host, port, database.
        
        Args:
            secret_name: Name of the secret containing database credentials
            
        Returns:
            dict: Database credentials with keys: username, password, host, port, database
            
        Raises:
            InvalidSecretError: If required credential fields are missing
            
        Example:
            >>> client = SecretsManagerClient()
            >>> db_creds = client.get_database_credentials("bdo-db-credentials")
            >>> connection_string = f"postgresql://{db_creds['username']}@{db_creds['host']}"
        """
        credentials = self.get_secret(secret_name)
        
        # Validate required fields
        required_fields = ['username', 'password', 'host', 'port', 'database']
        missing_fields = [field for field in required_fields if field not in credentials]
        
        if missing_fields:
            raise InvalidSecretError(
                f"Database credentials missing required fields: {', '.join(missing_fields)}"
            )
        
        return credentials
    
    def get_api_token(self, secret_name: str, token_key: str = 'token') -> str:
        """
        Retrieve API token from Secrets Manager.
        
        Convenience method for retrieving API authentication tokens.
        
        Args:
            secret_name: Name of the secret containing the API token
            token_key: Key name for the token in the secret (default: 'token')
            
        Returns:
            str: API token value
            
        Raises:
            InvalidSecretError: If token key is not found in secret
            
        Example:
            >>> client = SecretsManagerClient()
            >>> api_token = client.get_api_token("bdo-api-credentials")
            >>> headers = {"Authorization": f"Bearer {api_token}"}
        """
        secret = self.get_secret(secret_name)
        
        if token_key not in secret:
            raise InvalidSecretError(
                f"Token key '{token_key}' not found in secret '{secret_name}'"
            )
        
        return secret[token_key]
    
    def clear_cache(self, secret_name: Optional[str] = None) -> None:
        """
        Clear cached secrets.
        
        Args:
            secret_name: Specific secret to clear from cache (if None, clears all)
            
        Example:
            >>> client = SecretsManagerClient()
            >>> client.get_secret("my-secret")
            >>> client.clear_cache("my-secret")  # Clear specific secret
            >>> client.clear_cache()  # Clear all cached secrets
        """
        if secret_name:
            self._cache.pop(secret_name, None)
        else:
            self._cache.clear()


# Custom exceptions for better error handling

class SecretsManagerError(Exception):
    """Base exception for Secrets Manager errors."""
    pass


class SecretNotFoundError(SecretsManagerError):
    """Raised when a secret does not exist in Secrets Manager."""
    pass


class SecretAccessDeniedError(SecretsManagerError):
    """Raised when access to a secret is denied due to insufficient permissions."""
    pass


class InvalidSecretError(SecretsManagerError):
    """Raised when a secret value is invalid or missing required fields."""
    pass
