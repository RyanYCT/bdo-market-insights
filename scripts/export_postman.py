"""Generate ``postman/bdo-market-insights.postman_collection.json`` from the API spec.

Derives a Postman Collection (v2.1.0) from the same OpenAPI document that
``export_openapi.py`` builds from the Powertools resolvers, so the collection
stays in lock-step with the API contract. Regenerate with ``make postman``
after changing any route.

The collection authenticates with the ``x-api-key`` header bound to an
``{{apiKey}}`` variable and targets a ``{{baseUrl}}`` variable, both left blank
so a published Postman environment supplies them. No key value is written here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from export_openapi import build_openapi  # sibling script (scripts/ is on sys.path[0])

_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _ROOT / "postman" / "bdo-market-insights.postman_collection.json"
_NAME = "BDO Market Insights API"
_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

# Group operations into folders by the first path segment after /v1.
_FOLDERS = {"items": "Items", "market": "Market", "insights": "Insights"}
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# Per-folder overview text (Postman renders a folder's `description`).
_FOLDER_DESCRIPTIONS = {
    "Items": (
        "Item registry (DynamoDB-backed catalog). Reads are open to the demo key; "
        "writes (`POST`/`PATCH`/`DELETE`) are blocked for it and return `403`."
    ),
    "Market": (
        "Market data (RDS-backed): raw hourly snapshots, daily rollups, and a combined "
        "analysis (enhancement cost + volatility + liquidity + anomaly flag)."
    ),
    "Insights": (
        "Market-wide daily/weekly insights digests (top movers per category, with a "
        "narrative summary)."
    ),
}

# Collection overview (Postman renders the collection's `info.description`).
_COLLECTION_OVERVIEW = (
    "**BDO Market Insights API** — read-only market data for the *Black Desert Online* "
    "economy: hourly snapshots, daily rollups, enhancement-cost / volatility / liquidity "
    "analysis, and daily/weekly insights digests.\n\n"
    "**Setup**\n"
    "- `baseUrl` — API base, e.g. `https://api.example.com` (no trailing `/v1`).\n"
    "- `apiKey` — sent as the `x-api-key` header. This workspace's environment has the "
    "public **read-only demo key** preset; in your own copy, supply your own key.\n\n"
    "**Auth** — every request inherits the collection's API-key auth (`x-api-key: "
    "{{apiKey}}`); the per-request *“authorization helper from collection”* note just "
    "reflects that inheritance, nothing to configure.\n\n"
    "**Limits** — the demo key is read-only (writes to `/v1/items` return `403`) and "
    "lightly rate-limited (~2 req/s, 500/day).\n\n"
    "Generated from the OpenAPI spec (`scripts/export_postman.py`); the contract is also "
    "served as Swagger UI at `/v1/docs`."
)


def _resolve_ref(ref: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local ``#/components/...`` JSON reference to its schema dict."""
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _example_for(schema: dict[str, Any], spec: dict[str, Any]) -> Any:
    """Build a minimal example value from a (possibly $ref'd) JSON schema."""
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], spec)
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        return {
            name: _example_for(prop, spec) for name, prop in schema.get("properties", {}).items()
        }
    if schema_type == "array":
        return [_example_for(schema.get("items", {}), spec)]
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0
    if schema_type == "boolean":
        return True
    # string / anyOf / unknown -> empty string placeholder
    return ""


def _folder_key(path: str) -> str:
    """Map a path to its folder name (Items / Market / Insights / Other)."""
    segments = [s for s in path.split("/") if s and s != "v1"]
    return _FOLDERS.get(segments[0], "Other") if segments else "Other"


def _to_postman_url(path: str) -> dict[str, Any]:
    """Build a Postman URL object from an OpenAPI path (``{x}`` -> ``:x``)."""
    raw_segments = [s for s in path.split("/") if s]
    postman_path = [seg.replace("{", ":").replace("}", "") for seg in raw_segments]
    variables = [
        {
            "key": seg[1:-1],
            "value": "",
            "description": "Path parameter (e.g. a tracked item id; see GET /v1/items).",
        }
        for seg in raw_segments
        if seg.startswith("{") and seg.endswith("}")
    ]
    url: dict[str, Any] = {
        "raw": "{{baseUrl}}/" + "/".join(postman_path),
        "host": ["{{baseUrl}}"],
        "path": postman_path,
    }
    if variables:
        url["variable"] = variables
    return url


def _query_params(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map OpenAPI query parameters to Postman query entries (disabled by default).

    Pre-fills the value with the schema default (so the param is self-documenting)
    and carries the param description through verbatim from the spec.
    """
    query: list[dict[str, Any]] = []
    for param in parameters:
        if param.get("in") != "query":
            continue
        default = param.get("schema", {}).get("default")
        query.append(
            {
                "key": param["name"],
                "value": "" if default is None else str(default),
                "description": param.get("description", ""),
                "disabled": True,
            }
        )
    return query


def _request_body(operation: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Build a Postman raw JSON body from an operation's requestBody schema."""
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if not content:
        return None
    example = _example_for(content.get("schema", {}), spec)
    return {
        "mode": "raw",
        "raw": json.dumps(example, indent=2),
        "options": {"raw": {"language": "json"}},
    }


def _build_item(
    method: str, path: str, operation: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """Build a single Postman request item for one operation."""
    url = _to_postman_url(path)
    query = _query_params(operation.get("parameters", []))
    if query:
        url["query"] = query
        url["raw"] += "?" + "&".join(f"{q['key']}={q['value']}" for q in query)

    request: dict[str, Any] = {
        "method": method.upper(),
        "header": [{"key": "Accept", "value": "application/json"}],
        "url": url,
        "description": operation.get("description") or operation.get("summary", ""),
    }
    body = _request_body(operation, spec)
    if body:
        request["header"].append({"key": "Content-Type", "value": "application/json"})
        request["body"] = body

    name = operation.get("summary") or operation.get("operationId") or f"{method.upper()} {path}"
    return {"name": name, "request": request}


def build_collection(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAPI spec dict into a Postman v2.1 collection dict."""
    folders: dict[str, dict[str, Any]] = {}
    for path, path_item in sorted(spec.get("paths", {}).items()):
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not operation:
                continue
            folder_name = _folder_key(path)
            folder = folders.setdefault(
                folder_name,
                {
                    "name": folder_name,
                    "description": _FOLDER_DESCRIPTIONS.get(folder_name, ""),
                    "item": [],
                },
            )
            folder["item"].append(_build_item(method, path, operation, spec))

    info = spec.get("info", {})
    return {
        "info": {
            "name": _NAME,
            "description": _COLLECTION_OVERVIEW,
            "version": info.get("version", "1.0.0"),
            "schema": _SCHEMA,
        },
        "auth": {
            "type": "apikey",
            "apikey": [
                {"key": "key", "value": "x-api-key", "type": "string"},
                {"key": "value", "value": "{{apiKey}}", "type": "string"},
                {"key": "in", "value": "header", "type": "string"},
            ],
        },
        "variable": [
            {"key": "baseUrl", "value": "", "type": "string"},
            {"key": "apiKey", "value": "", "type": "string"},
        ],
        "item": [folders[name] for name in sorted(folders)],
    }


def main() -> None:
    spec = build_openapi()
    collection = build_collection(spec)
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
    request_count = sum(len(folder["item"]) for folder in collection["item"])
    print(f"wrote {_OUTPUT.relative_to(_ROOT)} ({request_count} requests)")


if __name__ == "__main__":
    main()
