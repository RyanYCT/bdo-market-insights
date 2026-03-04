"""
Correlation ID utilities for request tracking across Lambda functions.

Provides functions to generate unique correlation IDs and extract them from events.
"""

import uuid
from typing import Optional, Dict, Any


def generate_correlation_id() -> str:
    """
    Generate a unique correlation ID for tracking requests.
    
    Returns:
        str: A unique UUID v4 string
        
    Example:
        >>> corr_id = generate_correlation_id()
        >>> len(corr_id) == 36
        True
    """
    return str(uuid.uuid4())


def extract_correlation_id(event: Dict[str, Any]) -> str:
    """
    Extract correlation ID from Lambda event or generate a new one.
    
    Checks multiple locations in the event for an existing correlation ID:
    1. event['correlation_id'] - Direct field
    2. event['requestContext']['requestId'] - API Gateway
    3. event['Records'][0]['eventID'] - SQS/SNS/DynamoDB Streams
    
    If no correlation ID is found, generates a new one.
    
    Args:
        event: Lambda event dictionary
        
    Returns:
        str: Correlation ID (existing or newly generated)
        
    Example:
        >>> event = {'correlation_id': 'existing-id'}
        >>> extract_correlation_id(event)
        'existing-id'
        >>> extract_correlation_id({})  # doctest: +SKIP
        'newly-generated-uuid'
    """
    # Check direct correlation_id field
    if 'correlation_id' in event:
        return event['correlation_id']
    
    # Check API Gateway request context
    if 'requestContext' in event and 'requestId' in event['requestContext']:
        return event['requestContext']['requestId']
    
    # Check event records (SQS, SNS, DynamoDB Streams)
    if 'Records' in event and len(event['Records']) > 0:
        first_record = event['Records'][0]
        if 'eventID' in first_record:
            return first_record['eventID']
    
    # Generate new correlation ID if none found
    return generate_correlation_id()
