"""Unit tests for the itemRegistry API handler (FR-8..12).

Drives the Powertools resolver with synthetic API Gateway REST proxy events;
DynamoDB and arsha.io are mocked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

from bdo_common.models import Item, Record


def _event(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal API Gateway REST proxy event."""
    return {
        "resource": path,
        "path": path,
        "httpMethod": method.upper(),
        "headers": {"Content-Type": "application/json", "Accept": "application/json"},
        "queryStringParameters": query,
        "pathParameters": None,
        "requestContext": {"requestId": "req-1", "stage": "test", "httpMethod": method.upper()},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def _record(item_id: int, name: str) -> Record:
    return Record(
        item_id=item_id,
        sid=0,
        name=name,
        base_price=1,
        current_stock=1,
        total_trades=1,
        last_sold_price=1,
        last_sold_at=datetime(2026, 6, 1, tzinfo=UTC),
        max_enhance=5,
        price_min=1,
        price_max=2,
    )


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("item_registry")


def test_list_items_filters(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(**kwargs: Any) -> list[Item]:
        captured.update(kwargs)
        return [Item(id=12094, name="Deboreka Ring", category="ring", tracked=True)]

    monkeypatch.setattr(mod.dynamo, "list_items", fake_list)
    resp = mod.handler(
        _event("GET", "/v1/items", query={"category": "ring", "tracked": "true"}), lambda_context
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["count"] == 1
    assert body["items"][0]["id"] == 12094
    assert captured == {"category": "ring", "tracked": True}


def test_get_item_found(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod.dynamo, "get_item", lambda item_id: Item(id=item_id, name="Deboreka Ring")
    )
    resp = mod.handler(_event("GET", "/v1/items/12094"), lambda_context)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["id"] == 12094


def test_get_item_404(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.dynamo, "get_item", lambda item_id: None)
    resp = mod.handler(_event("GET", "/v1/items/999"), lambda_context)
    assert resp["statusCode"] == 404


def test_create_item_validates_via_arsha(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod.ArshaClient, "fetch_sub_list", lambda self, ids: [_record(ids[0], "Deboreka Ring")]
    )
    created: list[Item] = []
    monkeypatch.setattr(mod.dynamo, "put_item", lambda item: created.append(item))

    resp = mod.handler(
        _event("POST", "/v1/items", body={"id": 12094, "category": "ring", "cron_table": "b"}),
        lambda_context,
    )
    assert resp["statusCode"] == 201
    assert created[0].id == 12094
    assert created[0].name == "Deboreka Ring"  # taken from arsha
    assert created[0].cron_table == "b"


def test_create_item_rejects_unknown_id(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.ArshaClient, "fetch_sub_list", lambda self, ids: [])
    called: list[Item] = []
    monkeypatch.setattr(mod.dynamo, "put_item", lambda item: called.append(item))

    resp = mod.handler(_event("POST", "/v1/items", body={"id": 99999999}), lambda_context)
    assert resp["statusCode"] == 400
    assert not called  # never written


def test_patch_item_maps_tracked_to_string(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mod.dynamo, "get_item", lambda item_id: Item(id=item_id, name="x", tracked=True)
    )
    updates: dict[str, Any] = {}
    monkeypatch.setattr(mod.dynamo, "update_item", lambda item_id, u: updates.update(u))

    resp = mod.handler(
        _event("PATCH", "/v1/items/12094", body={"tracked": False, "category": "necklace"}),
        lambda_context,
    )
    assert resp["statusCode"] == 200
    assert updates == {"tracked": "false", "category": "necklace"}


def test_delete_item_soft_deletes(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.dynamo, "get_item", lambda item_id: Item(id=item_id, name="x"))
    updates: dict[str, Any] = {}
    monkeypatch.setattr(
        mod.dynamo, "update_item", lambda item_id, u: updates.update({"id": item_id, **u})
    )

    resp = mod.handler(_event("DELETE", "/v1/items/12094"), lambda_context)
    assert resp["statusCode"] == 200
    assert updates == {"id": 12094, "tracked": "false"}
    assert json.loads(resp["body"])["tracked"] is False


def _event_with_api_key(
    method: str,
    path: str,
    api_key_id: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """API Gateway event carrying a caller ``apiKeyId`` (for the read-only demo guard)."""
    event = _event(method, path, body=body)
    event["requestContext"]["identity"] = {"apiKeyId": api_key_id}
    return event


def test_demo_key_blocks_create(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_API_KEY_ID", "demo-key-123")
    monkeypatch.setattr(
        mod.ArshaClient, "fetch_sub_list", lambda self, ids: [_record(ids[0], "Deboreka Ring")]
    )
    created: list[Item] = []
    monkeypatch.setattr(mod.dynamo, "put_item", lambda item: created.append(item))

    resp = mod.handler(
        _event_with_api_key("POST", "/v1/items", "demo-key-123", body={"id": 12094}),
        lambda_context,
    )
    assert resp["statusCode"] == 403
    assert not created  # never written


def test_demo_key_blocks_update_and_delete(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_API_KEY_ID", "demo-key-123")
    monkeypatch.setattr(mod.dynamo, "get_item", lambda item_id: Item(id=item_id, name="x"))
    mutations: list[Any] = []
    monkeypatch.setattr(
        mod.dynamo, "update_item", lambda item_id, u: mutations.append((item_id, u))
    )

    patch = mod.handler(
        _event_with_api_key("PATCH", "/v1/items/12094", "demo-key-123", body={"tracked": False}),
        lambda_context,
    )
    delete = mod.handler(
        _event_with_api_key("DELETE", "/v1/items/12094", "demo-key-123"), lambda_context
    )
    assert patch["statusCode"] == 403
    assert delete["statusCode"] == 403
    assert not mutations  # nothing mutated


def test_demo_key_allows_reads(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_API_KEY_ID", "demo-key-123")
    monkeypatch.setattr(
        mod.dynamo, "get_item", lambda item_id: Item(id=item_id, name="Deboreka Ring")
    )
    resp = mod.handler(
        _event_with_api_key("GET", "/v1/items/12094", "demo-key-123"), lambda_context
    )
    assert resp["statusCode"] == 200  # reads are not guarded


def test_non_demo_key_allows_write(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_API_KEY_ID", "demo-key-123")
    monkeypatch.setattr(
        mod.ArshaClient, "fetch_sub_list", lambda self, ids: [_record(ids[0], "Deboreka Ring")]
    )
    created: list[Item] = []
    monkeypatch.setattr(mod.dynamo, "put_item", lambda item: created.append(item))

    resp = mod.handler(
        _event_with_api_key("POST", "/v1/items", "real-key-999", body={"id": 12094}),
        lambda_context,
    )
    assert resp["statusCode"] == 201
    assert created[0].id == 12094


def test_write_guard_noop_when_unset(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEMO_API_KEY_ID", raising=False)
    monkeypatch.setattr(
        mod.ArshaClient, "fetch_sub_list", lambda self, ids: [_record(ids[0], "Deboreka Ring")]
    )
    created: list[Item] = []
    monkeypatch.setattr(mod.dynamo, "put_item", lambda item: created.append(item))

    # Even a request that looks like the demo key is allowed when the guard is off.
    resp = mod.handler(
        _event_with_api_key("POST", "/v1/items", "demo-key-123", body={"id": 12094}),
        lambda_context,
    )
    assert resp["statusCode"] == 201
