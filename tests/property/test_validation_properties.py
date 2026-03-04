"""
Property-based tests for Pydantic schema validation.

Feature: bdo-market-insights-rewrite
Tests Properties 1 and 2 from the design document.
"""

import pytest
from datetime import datetime, timedelta
from hypothesis import given, strategies as st, settings
from pydantic import ValidationError

# Import schemas from lambda layer
import sys
sys.path.insert(0, 'lambda_layer/python')
from common.schemas import (
    QueryRequest,
    MarketDataRecord,
    ItemRecord,
    ItemIdList,
    FetchDataInput,
    CleanDataInput,
    StoreDataInput,
    VALID_CATEGORIES,
)


# Hypothesis strategies for generating test data
@st.composite
def valid_datetime_range(draw):
    """Generate valid start and end datetime pairs."""
    start = draw(st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31)
    ))
    # Ensure end is after start
    end = draw(st.datetimes(
        min_value=start + timedelta(seconds=1),
        max_value=datetime(2030, 12, 31)
    ))
    return start, end


@st.composite
def invalid_datetime_range(draw):
    """Generate invalid start and end datetime pairs (end before start)."""
    end = draw(st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31)
    ))
    # Ensure start is after end
    start = draw(st.datetimes(
        min_value=end + timedelta(seconds=1),
        max_value=datetime(2030, 12, 31)
    ))
    return start, end


# Property 1: Schema validation rejects invalid inputs
# **Validates: Requirements 2.2, 2.3**

@given(item_id=st.integers(max_value=0))
@settings(max_examples=100)
def test_query_request_rejects_non_positive_item_id(item_id):
    """Property 1: QueryRequest should reject non-positive item_id."""
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 1, 31)
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(
            item_id=item_id,
            start_date=start_date,
            end_date=end_date
        )
    
    # Verify error mentions item_id
    assert "item_id" in str(exc_info.value).lower()


@given(start_date=st.datetimes(), end_date=st.datetimes())
@settings(max_examples=100)
def test_query_request_rejects_end_before_start(start_date, end_date):
    """Property 1: QueryRequest should reject end_date before start_date."""
    # Only test when end is actually before start
    if end_date >= start_date:
        return
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(
            item_id=1,
            start_date=start_date,
            end_date=end_date
        )
    
    # Verify error mentions end_date
    assert "end_date" in str(exc_info.value).lower()


@given(category=st.text(min_size=1).filter(lambda x: x not in VALID_CATEGORIES))
@settings(max_examples=100)
def test_query_request_rejects_invalid_category(category):
    """Property 1: QueryRequest should reject invalid category values."""
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 1, 31)
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(
            item_id=1,
            category=category,
            start_date=start_date,
            end_date=end_date
        )
    
    # Verify error mentions category
    assert "category" in str(exc_info.value).lower()


@given(limit=st.integers().filter(lambda x: x < 1 or x > 1000))
@settings(max_examples=100)
def test_query_request_rejects_invalid_limit(limit):
    """Property 1: QueryRequest should reject limit outside valid range."""
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 1, 31)
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(
            item_id=1,
            start_date=start_date,
            end_date=end_date,
            limit=limit
        )
    
    # Verify error mentions limit
    assert "limit" in str(exc_info.value).lower()


def test_query_request_rejects_missing_item_and_name():
    """Property 1: QueryRequest should reject when both item_id and name are missing."""
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 1, 31)
    
    with pytest.raises(ValidationError) as exc_info:
        QueryRequest(
            start_date=start_date,
            end_date=end_date
        )
    
    # Verify error mentions requirement
    error_str = str(exc_info.value).lower()
    assert "item_id" in error_str or "name" in error_str


@given(current_stock=st.integers(max_value=-1))
@settings(max_examples=100)
def test_market_data_record_rejects_negative_stock(current_stock):
    """Property 1: MarketDataRecord should reject negative current_stock."""
    with pytest.raises(ValidationError) as exc_info:
        MarketDataRecord(
            item_id=1,
            sid=0,
            current_stock=current_stock,
            total_trades=100,
            last_sold_price=1000,
            last_sold_time=datetime.now()
        )
    
    assert "current_stock" in str(exc_info.value).lower()


@given(total_trades=st.integers(max_value=-1))
@settings(max_examples=100)
def test_market_data_record_rejects_negative_trades(total_trades):
    """Property 1: MarketDataRecord should reject negative total_trades."""
    with pytest.raises(ValidationError) as exc_info:
        MarketDataRecord(
            item_id=1,
            sid=0,
            current_stock=50,
            total_trades=total_trades,
            last_sold_price=1000,
            last_sold_time=datetime.now()
        )
    
    assert "total_trades" in str(exc_info.value).lower()


@given(last_sold_price=st.integers(max_value=-1))
@settings(max_examples=100)
def test_market_data_record_rejects_negative_price(last_sold_price):
    """Property 1: MarketDataRecord should reject negative last_sold_price."""
    with pytest.raises(ValidationError) as exc_info:
        MarketDataRecord(
            item_id=1,
            sid=0,
            current_stock=50,
            total_trades=100,
            last_sold_price=last_sold_price,
            last_sold_time=datetime.now()
        )
    
    assert "last_sold_price" in str(exc_info.value).lower()


@given(category=st.text(min_size=1).filter(lambda x: x not in VALID_CATEGORIES))
@settings(max_examples=100)
def test_item_record_rejects_invalid_category(category):
    """Property 1: ItemRecord should reject invalid category values."""
    with pytest.raises(ValidationError) as exc_info:
        ItemRecord(
            name="Test Item",
            item_id=1,
            sid=0,
            category=category
        )
    
    assert "category" in str(exc_info.value).lower()


def test_item_id_list_rejects_empty_list():
    """Property 1: ItemIdList should reject empty item_ids list."""
    with pytest.raises(ValidationError) as exc_info:
        ItemIdList(
            item_ids=[],
            correlation_id="test-123"
        )
    
    assert "item_ids" in str(exc_info.value).lower()


@given(batch_size=st.integers().filter(lambda x: x < 1 or x > 100))
@settings(max_examples=100)
def test_fetch_data_input_rejects_invalid_batch_size(batch_size):
    """Property 1: FetchDataInput should reject batch_size outside valid range."""
    with pytest.raises(ValidationError) as exc_info:
        FetchDataInput(
            item_ids=[1, 2, 3],
            batch_size=batch_size,
            correlation_id="test-123"
        )
    
    assert "batch_size" in str(exc_info.value).lower()


# Property 2: Schema validation accepts valid inputs
# **Validates: Requirements 2.2**

@given(
    item_id=st.integers(min_value=1, max_value=100000),
    limit=st.integers(min_value=1, max_value=1000),
    category=st.sampled_from(VALID_CATEGORIES + [None])
)
@settings(max_examples=100)
def test_query_request_accepts_valid_inputs(item_id, limit, category):
    """Property 2: QueryRequest should accept all valid inputs."""
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 1, 31)
    
    # Should not raise any exception
    request = QueryRequest(
        item_id=item_id,
        category=category,
        start_date=start_date,
        end_date=end_date,
        limit=limit
    )
    
    assert request.item_id == item_id
    assert request.limit == limit
    assert request.category == category


@given(
    item_id=st.integers(min_value=1, max_value=100000),
    sid=st.integers(min_value=0, max_value=20),
    current_stock=st.integers(min_value=0, max_value=1000000),
    total_trades=st.integers(min_value=0, max_value=10000000),
    last_sold_price=st.integers(min_value=0, max_value=1000000000),
    last_sold_time=st.datetimes(min_value=datetime(2020, 1, 1))
)
@settings(max_examples=100)
def test_market_data_record_accepts_valid_inputs(
    item_id, sid, current_stock, total_trades, last_sold_price, last_sold_time
):
    """Property 2: MarketDataRecord should accept all valid inputs."""
    # Should not raise any exception
    record = MarketDataRecord(
        item_id=item_id,
        sid=sid,
        current_stock=current_stock,
        total_trades=total_trades,
        last_sold_price=last_sold_price,
        last_sold_time=last_sold_time
    )
    
    assert record.item_id == item_id
    assert record.sid == sid
    assert record.current_stock == current_stock
    assert record.total_trades == total_trades
    assert record.last_sold_price == last_sold_price
    assert record.last_sold_time == last_sold_time


@given(
    name=st.text(min_size=1, max_size=100),
    item_id=st.integers(min_value=1, max_value=100000),
    sid=st.integers(min_value=0, max_value=20),
    category=st.sampled_from(VALID_CATEGORIES)
)
@settings(max_examples=100)
def test_item_record_accepts_valid_inputs(name, item_id, sid, category):
    """Property 2: ItemRecord should accept all valid inputs."""
    # Should not raise any exception
    record = ItemRecord(
        name=name,
        item_id=item_id,
        sid=sid,
        category=category
    )
    
    assert record.name == name
    assert record.item_id == item_id
    assert record.sid == sid
    assert record.category == category


@given(
    item_ids=st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=1000),
    correlation_id=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_item_id_list_accepts_valid_inputs(item_ids, correlation_id):
    """Property 2: ItemIdList should accept all valid inputs."""
    # Should not raise any exception
    item_list = ItemIdList(
        item_ids=item_ids,
        correlation_id=correlation_id
    )
    
    assert item_list.item_ids == item_ids
    assert item_list.correlation_id == correlation_id


@given(
    item_ids=st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=1000),
    batch_size=st.integers(min_value=1, max_value=100),
    correlation_id=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_fetch_data_input_accepts_valid_inputs(item_ids, batch_size, correlation_id):
    """Property 2: FetchDataInput should accept all valid inputs."""
    # Should not raise any exception
    fetch_input = FetchDataInput(
        item_ids=item_ids,
        batch_size=batch_size,
        correlation_id=correlation_id
    )
    
    assert fetch_input.item_ids == item_ids
    assert fetch_input.batch_size == batch_size
    assert fetch_input.correlation_id == correlation_id


@given(
    raw_data=st.lists(st.dictionaries(st.text(), st.text()), min_size=0, max_size=100),
    correlation_id=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_clean_data_input_accepts_valid_inputs(raw_data, correlation_id):
    """Property 2: CleanDataInput should accept all valid inputs."""
    # Should not raise any exception
    clean_input = CleanDataInput(
        raw_data=raw_data,
        correlation_id=correlation_id
    )
    
    assert clean_input.raw_data == raw_data
    assert clean_input.correlation_id == correlation_id


@given(
    scrape_endpoint=st.text(min_size=1, max_size=100),
    scrape_time=st.datetimes(min_value=datetime(2020, 1, 1)),
    correlation_id=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_store_data_input_accepts_valid_inputs(scrape_endpoint, scrape_time, correlation_id):
    """Property 2: StoreDataInput should accept all valid inputs."""
    # Create a valid MarketDataRecord
    market_data = MarketDataRecord(
        item_id=1,
        sid=0,
        current_stock=50,
        total_trades=100,
        last_sold_price=1000,
        last_sold_time=datetime.now()
    )
    
    # Should not raise any exception
    store_input = StoreDataInput(
        cleaned_data=[market_data],
        scrape_endpoint=scrape_endpoint,
        scrape_time=scrape_time,
        correlation_id=correlation_id
    )
    
    assert len(store_input.cleaned_data) == 1
    assert store_input.scrape_endpoint == scrape_endpoint
    assert store_input.scrape_time == scrape_time
    assert store_input.correlation_id == correlation_id
