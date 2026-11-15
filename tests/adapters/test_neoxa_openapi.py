"""The Neoxa OpenAPI file, docs/api/neoxa/openapi.json (D-84, T-100).

Recorded v1 responses are checked against their operation. There are no v2
recordings: the live host answers 404 on every /api/v2 route, so the v2 docs
examples are checked against the schema inferred from them. That catches a broken
file, not a wrong one, and says so in the test names.
"""

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "api" / "neoxa" / "openapi.json"
FIXTURES = ROOT / "tests" / "fixtures" / "neoxa"
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))

# Routes the adapter calls (src/xbt_terminal/adapters/neoxa.py, base https://neoxa.exchange/api).
ADAPTER_ROUTES = [
    ("get", "/api/exchange/pairs"),
    ("get", "/api/exchange/orderbook/{pair}"),
    ("get", "/api/exchange/trades/{pair}"),
    ("get", "/api/exchange/candles/{pair}"),
    ("get", "/api/exchange/balances"),
    ("get", "/api/exchange/orders"),
    ("get", "/api/exchange/orders/history"),
    ("get", "/api/exchange/trades/my"),
    ("post", "/api/exchange/order"),
    ("delete", "/api/exchange/order/{orderId}"),
]

# Fields that hold money or quantities: decimal strings in v2 (its "Numbers" row).
V2_MONEY_FIELDS = {
    "price", "quantity", "total", "fee", "available", "inOrders", "withdrawing",
    "filledQuantity", "remainingQuantity", "averagePrice", "reserveBase", "reserveQuote",
    "feeRate", "spot", "buyEdge", "sellEdge", "makerFeeRate", "takerFeeRate",
    "neoxexDiscount",
}

# Recorded v1 responses and the operation each one answers. Fixtures that are not
# REST responses (WebSocket frames, the withdrawal fee table) are left out on purpose.
FIXTURE_OPERATIONS = [
    ("balances.json", "get", "/api/exchange/balances"),
    ("pairs.json", "get", "/api/exchange/pairs"),
    ("ticker.json", "get", "/api/exchange/ticker/{pair}"),
    ("orderbook.json", "get", "/api/exchange/orderbook/{pair}"),
    ("trades.json", "get", "/api/exchange/trades/{pair}"),
    ("candles_1m.json", "get", "/api/exchange/candles/{pair}"),
    ("candles_5m.json", "get", "/api/exchange/candles/{pair}"),
    ("candles_1h.json", "get", "/api/exchange/candles/{pair}"),
    ("candles_4h.json", "get", "/api/exchange/candles/{pair}"),
    ("candles_1d.json", "get", "/api/exchange/candles/{pair}"),
    ("orders_active.json", "get", "/api/exchange/orders"),
    ("orders_history.json", "get", "/api/exchange/orders/history"),
    ("trades_my.json", "get", "/api/exchange/trades/my"),
    ("order_placed.json", "post", "/api/exchange/order"),
    ("order_cancelled.json", "delete", "/api/exchange/order/{orderId}"),
    ("deposit_addresses.json", "get", "/api/wallet/deposit-addresses"),
]


def operations():
    for path, methods in SPEC["paths"].items():
        for method, operation in methods.items():
            yield method, path, operation


def resolve(ref: str):
    assert ref.startswith("#/"), f"external reference {ref}"
    node = SPEC
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def inline_refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            return inline_refs(resolve(node["$ref"]))
        return {key: inline_refs(value) for key, value in node.items()}
    if isinstance(node, list):
        return [inline_refs(item) for item in node]
    return node


def to_draft4(schema):
    """An OpenAPI 3.0 schema as JSON Schema draft 4, which jsonschema checks."""
    if isinstance(schema, list):
        return [to_draft4(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: to_draft4(value) for key, value in schema.items()
           if key != "nullable" and not key.startswith("x-")}
    if schema.get("nullable") is True and "type" in out:
        out["type"] = [out["type"], "null"]
    return out


def response_schema(operation):
    content = operation["responses"]["200"].get("content")
    return content["application/json"]["schema"] if content else None


def v2_operations():
    for method, path, operation in operations():
        if "/api/v2/" in path:
            yield method, path, operation


def walk_properties(schema, trail=""):
    """Every (field name, schema) pair, through nested objects and arrays."""
    if not isinstance(schema, dict):
        return
    for name, sub in schema.get("properties", {}).items():
        yield f"{trail}.{name}", name, sub
        yield from walk_properties(sub, f"{trail}.{name}")
    if "items" in schema:
        yield from walk_properties(schema["items"], f"{trail}[]")


@pytest.mark.spec("RF-36", "RNF-09")
def test_spec_is_openapi_3_0_1_from_the_neoxa_docs():
    assert SPEC["openapi"] == "3.0.1"
    assert SPEC["info"]["x-source"] == "https://neoxa.exchange/api-docs"
    assert len(SPEC["info"]["x-bundle-sha256"]) == 64


@pytest.mark.spec("RF-36", "RNF-09")
def test_every_operation_has_a_200_and_an_error_response_and_a_unique_id():
    ids = []
    for method, path, operation in operations():
        assert "200" in operation["responses"], f"{method} {path}"
        assert "default" in operation["responses"], f"{method} {path}"
        ids.append(operation["operationId"])
    assert len(ids) == len(set(ids))


@pytest.mark.spec("RF-36", "RNF-09")
def test_every_reference_points_at_a_declared_schema():
    def check(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert node["$ref"].startswith("#/components/schemas/"), node["$ref"]
                resolve(node["$ref"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(SPEC)


@pytest.mark.spec("RF-36", "RNF-09")
def test_every_security_requirement_names_a_declared_scheme():
    declared = set(SPEC["components"]["securitySchemes"])
    for _, _, operation in operations():
        for requirement in operation["security"]:
            assert set(requirement) <= declared


@pytest.mark.spec("RF-36", "RNF-09")
@pytest.mark.parametrize(("method", "path"), ADAPTER_ROUTES)
def test_every_route_the_adapter_calls_is_documented(method, path):
    assert path in SPEC["paths"], path
    assert method in SPEC["paths"][path], f"{method} {path}"


@pytest.mark.spec("RF-36", "RNF-09")
def test_v2_money_fields_are_decimal_strings_in_responses():
    for method, path, operation in v2_operations():
        schema = response_schema(operation)
        if schema is None:
            continue
        for trail, name, sub in walk_properties(inline_refs(schema)):
            if name in V2_MONEY_FIELDS:
                assert sub.get("type") == "string", f"{method} {path} {trail}"


@pytest.mark.spec("RF-36", "RNF-09")
def test_v2_docs_response_examples_match_the_schema_inferred_from_them():
    checked = 0
    for _method, _path, operation in v2_operations():
        content = operation["responses"]["200"].get("content")
        if not content:
            continue
        media = content["application/json"]
        jsonschema.validate(media["example"], to_draft4(inline_refs(media["schema"])))
        checked += 1
    assert checked > 0


@pytest.mark.spec("RF-36", "RNF-09")
def test_v2_docs_request_examples_match_their_body_schema():
    checked = 0
    for _method, _path, operation in v2_operations():
        body = operation.get("requestBody", {}).get("content", {}).get("application/json")
        if not body or "example" not in body:
            continue
        jsonschema.validate(body["example"], to_draft4(inline_refs(body["schema"])))
        checked += 1
    assert checked > 0


def fixture_params():
    # Strict xfail: the test starts failing, and must be updated, when the docs change.
    known_differences = {
        "order_placed.json": "the docs example of POST /api/exchange/order is not JSON "
                             "(// comments), so there is no schema to check the recording against",
        "trades_my.json": "the recording has fee_token null on some trades; the docs example "
                          "shows a string. The recording wins (D-84); the gap is in "
                          "docs/api/README.md",
    }
    for name, method, path in FIXTURE_OPERATIONS:
        marks = []
        if name in known_differences:
            marks.append(pytest.mark.xfail(strict=True, reason=known_differences[name]))
        yield pytest.param(name, method, path, id=name, marks=marks)


@pytest.mark.spec("RF-36", "RNF-09")
@pytest.mark.parametrize(("name", "method", "path"), list(fixture_params()))
def test_recorded_v1_response_matches_its_operation(name, method, path):
    recorded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    schema = response_schema(SPEC["paths"][path][method])
    assert schema is not None, f"{method} {path} has no documented example"
    jsonschema.validate(recorded, to_draft4(inline_refs(schema)))
