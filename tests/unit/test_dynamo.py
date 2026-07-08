"""Tests for bdo_common.dynamo using moto to mock DynamoDB."""

from __future__ import annotations

from typing import Any

import boto3
import moto
import pytest

from bdo_common.models import Item


@pytest.fixture()
def dynamodb_table(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create a mock DynamoDB table matching the bdo-<stage>-items schema."""
    monkeypatch.setenv("DYNAMODB_TABLE", "bdo-dev-items")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")

    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="bdo-dev-items",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "N"},
                {"AttributeName": "category", "AttributeType": "S"},
                {"AttributeName": "tracked", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "category-tracked-index",
                    "KeySchema": [
                        {"AttributeName": "category", "KeyType": "HASH"},
                        {"AttributeName": "tracked", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Need to reimport dynamo module so _TABLE_NAME picks up env
        import importlib

        import bdo_common.dynamo

        importlib.reload(bdo_common.dynamo)
        yield


class TestGetItem:
    """Test get_item wrapper."""

    def test_get_existing_item(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import get_item, put_item

        item = Item(id=11608, name="Deboreka Ring", category="accessories")
        put_item(item)

        result = get_item(11608)
        assert result is not None
        assert result.id == 11608
        assert result.name == "Deboreka Ring"
        assert result.category == "accessories"

    def test_get_missing_item_returns_none(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import get_item

        result = get_item(99999)
        assert result is None


class TestPutAndListItems:
    """Test put_item and list_items."""

    def test_put_and_list(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import list_items, put_item

        put_item(Item(id=1, name="Item A", category="weapons", tracked=True))
        put_item(Item(id=2, name="Item B", category="weapons", tracked=False))
        put_item(Item(id=3, name="Item C", category="armor", tracked=True))

        all_items = list_items()
        assert len(all_items) == 3

    def test_list_items_with_category_filter(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import list_items, put_item

        put_item(Item(id=1, name="Item A", category="weapons", tracked=True))
        put_item(Item(id=2, name="Item B", category="weapons", tracked=False))
        put_item(Item(id=3, name="Item C", category="armor", tracked=True))

        weapons = list_items(category="weapons")
        assert len(weapons) == 2
        assert all(i.category == "weapons" for i in weapons)


class TestScanTrackedItems:
    """Test scan_tracked_items returns only tracked=true."""

    def test_only_tracked_returned(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import put_item, scan_tracked_items

        put_item(Item(id=1, name="Tracked A", category="accessories", tracked=True))
        put_item(Item(id=2, name="Untracked B", category="accessories", tracked=False))
        put_item(Item(id=3, name="Tracked C", category="weapons", tracked=True))

        tracked = scan_tracked_items()
        assert len(tracked) == 2
        assert all(i.tracked is True for i in tracked)
        ids = {i.id for i in tracked}
        assert ids == {1, 3}


class TestUpsertCatalogItem:
    """Test upsert_catalog_item partial-upsert semantics."""

    def test_creates_new_returns_true(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import get_item, upsert_catalog_item

        is_new = upsert_catalog_item(item_id=37364, name="Wild Herb", grade=4)
        assert is_new is True

        item = get_item(37364)
        assert item is not None
        assert item.name == "Wild Herb"
        assert item.grade == 4

    def test_existing_returns_false(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import upsert_catalog_item

        upsert_catalog_item(item_id=1, name="First")
        assert upsert_catalog_item(item_id=1, name="Second") is False

    def test_preserves_etl_owned_fields(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import get_item, put_item, upsert_catalog_item

        put_item(
            Item(
                id=11608,
                name="Old Name",
                tracked=True,
                model_id="accessory_cron_v1",
                cron_table="b",
                icon_status="stored",
            )
        )
        upsert_catalog_item(item_id=11608, name="Deboreka Ring", grade=4, names={"tw": "戒指"})

        item = get_item(11608)
        assert item is not None
        # catalog-owned fields updated
        assert item.name == "Deboreka Ring"
        assert item.grade == 4
        assert item.names == {"tw": "戒指"}
        # ETL-owned fields untouched
        assert item.tracked is True
        assert item.model_id == "accessory_cron_v1"
        assert item.cron_table == "b"
        assert item.icon_status == "stored"

    def test_created_at_set_once(self, dynamodb_table: Any) -> None:
        from bdo_common.dynamo import upsert_catalog_item

        table = boto3.resource("dynamodb", region_name="us-east-1").Table("bdo-dev-items")

        upsert_catalog_item(item_id=1, name="First")
        created_first = table.get_item(Key={"id": 1})["Item"]["created_at"]

        upsert_catalog_item(item_id=1, name="Second")
        row = table.get_item(Key={"id": 1})["Item"]
        assert row["created_at"] == created_first  # stamped once, never overwritten
        assert row["name"] == "Second"  # other catalog fields still refresh
