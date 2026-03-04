"""
Pydantic validation schemas for BDO Market Insights Lambda functions.

This module defines all input and output schemas for validation across
the ETL and Query pipelines.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# Valid category choices matching Django model
VALID_CATEGORIES = ['Uncategorized', 'Accessory', 'Buff', 'Costume']


class QueryRequest(BaseModel):
    """Validates API Gateway GET query parameters."""
    
    item_id: Optional[int] = Field(None, gt=0, description="Specific item ID to query")
    name: Optional[str] = Field(None, max_length=100, description="Item name to search")
    category: Optional[str] = Field(None, description="Category filter")
    start_date: datetime = Field(description="Start of date range for last_sold_time")
    end_date: datetime = Field(description="End of date range for last_sold_time")
    limit: int = Field(default=100, ge=1, le=1000, description="Max results")
    
    @field_validator('end_date')
    @classmethod
    def end_after_start(cls, v, info):
        """Ensure end_date is after start_date."""
        if 'start_date' in info.data and v < info.data['start_date']:
            raise ValueError('end_date must be after start_date')
        return v
    
    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        """Ensure category is valid if provided."""
        if v is not None and v not in VALID_CATEGORIES:
            raise ValueError(f'Category must be one of {VALID_CATEGORIES}')
        return v
    
    @model_validator(mode='after')
    def require_item_or_name(self):
        """Require either item_id or name to be specified."""
        if not self.item_id and not self.name:
            raise ValueError('Either item_id or name must be specified')
        return self


class MarketDataRecord(BaseModel):
    """Validates market data from External API."""
    
    item_id: int = Field(description="Item ID from BDO API")
    sid: int = Field(description="Enhancement level")
    current_stock: int = Field(ge=0, description="Current market stock")
    total_trades: int = Field(ge=0, description="Total number of trades")
    last_sold_price: int = Field(ge=0, description="Last sold price in silver")
    last_sold_time: datetime = Field(description="Timestamp of last sale")
    
    model_config = {
        'json_encoders': {
            datetime: lambda v: v.isoformat()
        }
    }


class ItemRecord(BaseModel):
    """Validates item information."""
    
    name: str = Field(max_length=100)
    item_id: int
    sid: int  # Enhancement level
    category: str = Field(description="Category name from ItemCategory choices")
    
    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        """Ensure category is one of the valid choices."""
        if v not in VALID_CATEGORIES:
            raise ValueError(f'Category must be one of {VALID_CATEGORIES}')
        return v


class ItemIdList(BaseModel):
    """Output from retrieveIdList Lambda."""
    
    item_ids: List[int] = Field(min_length=1)
    correlation_id: str


class FetchDataInput(BaseModel):
    """Input to fetchData Lambda."""
    
    item_ids: List[int]
    batch_size: int = Field(default=50, ge=1, le=100)
    correlation_id: str


class CleanDataInput(BaseModel):
    """Input to cleanData Lambda."""
    
    raw_data: List[dict]
    correlation_id: str


class StoreDataInput(BaseModel):
    """Input to storeData Lambda."""
    
    cleaned_data: List[MarketDataRecord]
    scrape_endpoint: str = Field(description="API endpoint used for this scrape")
    scrape_time: datetime = Field(description="Timestamp of this scrape session")
    correlation_id: str
