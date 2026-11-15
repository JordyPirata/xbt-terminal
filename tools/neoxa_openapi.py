#!/usr/bin/env python3
"""Build docs/api/neoxa/openapi.json from the Neoxa API docs (D-84, T-100).

Neoxa publishes no OpenAPI file. Its docs page, https://neoxa.exchange/api-docs,
is a single-page app whose content ships inside the JavaScript bundle as literal
data. This tool reads that data as text and never runs the bundle: literals are
parsed, constants are found by their declaration, and nothing is evaluated.

    uv run python tools/neoxa_openapi.py                  # fetch the live bundle
    uv run python tools/neoxa_openapi.py --bundle FILE --url URL  # a saved copy
    uv run python tools/neoxa_openapi.py --out FILE       # write elsewhere
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import httpx

DOCS_URL = "https://neoxa.exchange/api-docs"
SERVER_URL = "https://neoxa.exchange"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "api" / "neoxa" / "openapi.json"

# The docs page loads its bundle from /static/index-<hash>.js.
BUNDLE_RE = re.compile(r'src="(/static/index-[\w-]+\.js)"')

# Parameter builders: `$e(name, location, required, description)` in the bundle. The
# bundle has several names bound to this same function; each one is found by its declaration.
BUILDER_DECLARATION_RE = r"[,;\s ]([\w$]+)=\(t,n,s,i\)=>\(\{name:t,loc:n,req:s,desc:i\}\)"

# Money and quantity fields are decimal strings in v2 (D-84, "Numbers" in the migration table).
# Integers named here are typed as integer; everything else in a parameter is a string.
INTEGER_PARAMS = {"limit", "depth", "id", "seconds", "recvWindow"}
BOOLEAN_PARAMS = {"enabled", "signedOnly", "postOnly", "tiersPaused"}

# v1 operations that have a v2 equivalent, as the "Moving from v1" guide table says.
# Anything not listed here stays v1 (wallet, deposits, withdrawals, pools, public market data).
V1_TO_V2 = {
    ("get", "/api/exchange/balances"): ("get", "/api/v2/balances"),
    ("get", "/api/exchange/orders"): ("get", "/api/v2/orders/open"),
    ("get", "/api/exchange/orders/history"): ("get", "/api/v2/orders/history"),
    ("get", "/api/exchange/trades/my"): ("get", "/api/v2/trades"),
    ("post", "/api/exchange/order"): ("post", "/api/v2/order"),
    ("delete", "/api/exchange/order/{orderId}"): ("delete", "/api/v2/order"),
    ("get", "/api/exchange/orderbook/{pair}"): ("get", "/api/v2/orderbook/{pair}"),
}

V2_AUTH_NOTE = (
    "Private v2 calls take either signed headers (X-NX-Key, X-NX-Timestamp, "
    "X-NX-Signature, optional X-NX-Recv-Window) or the plain X-API-Key and "
    "X-API-Secret pair. v1 takes the plain pair only."
)


class DocsError(RuntimeError):
    """The bundle does not have the shape this tool expects."""


class Literal:
    """Parses one JavaScript literal from the bundle text, without evaluating it.

    Covers what the docs data uses: strings, template strings with `${...}`,
    arrays, objects, `!0`/`!1`, numbers, declared constants, the parameter builder,
    `Ka(text, n)` (indent) and `.replace(a, b)`.
    """

    def __init__(self, bundle: Bundle, pos: int):
        self.bundle = bundle
        self.s = bundle.text
        self.i = pos

    def ws(self) -> None:
        while self.i < len(self.s):
            if self.s[self.i] in " \t\r\n":
                self.i += 1
            elif self.s.startswith("//", self.i):
                self.i = self.s.index("\n", self.i)
            elif self.s.startswith("/*", self.i):
                self.i = self.s.index("*/", self.i) + 2
            else:
                break

    def peek(self) -> str:
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise DocsError(f"expected {ch!r} at {self.i}: {self.s[self.i:self.i + 60]!r}")
        self.i += 1

    def value(self):
        char = self.peek()
        if char in "\"'":
            return self.quoted()
        if char == "`":
            return self.template()
        if char == "[":
            return self.array()
        if char == "{":
            return self.obj()
        if char == "!":
            digit = self.s[self.i + 1]
            self.i += 2
            return digit == "0"
        number = re.compile(r"-?\d+(\.\d+)?").match(self.s, self.i)
        if number:
            self.i = number.end()
            return json.loads(number.group(0))
        return self.name_or_call()

    def name_or_call(self):
        name_match = re.compile(r"[A-Za-z_$][\w$]*").match(self.s, self.i)
        if not name_match:
            raise DocsError(f"unexpected token at {self.i}: {self.s[self.i:self.i + 60]!r}")
        self.i = name_match.end()
        name = name_match.group(0)
        if self.s[self.i:self.i + 1] == "(":
            self.i += 1
            args = []
            while self.peek() != ")":
                args.append(self.value())
                if self.peek() == ",":
                    self.i += 1
            self.i += 1
            value = self.bundle.call(name, args)
        else:
            value = self.bundle.constant(name)
        while self.s.startswith(".replace(", self.i):
            self.i += len(".replace(")
            old = self.value()
            self.expect(",")
            new = self.value()
            self.expect(")")
            value = value.replace(old, new, 1)
        return value

    def quoted(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.s[self.i] != quote:
            if self.s[self.i] == "\\":
                escaped = self.s[self.i + 1]
                out.append({"n": "\n", "t": "\t"}.get(escaped, escaped))
                self.i += 2
            else:
                out.append(self.s[self.i])
                self.i += 1
        self.i += 1
        return "".join(out)

    def template(self) -> str:
        self.i += 1
        out = []
        while self.s[self.i] != "`":
            if self.s.startswith("${", self.i):
                self.i += 2
                out.append(str(self.value()))
                self.expect("}")
            elif self.s[self.i] == "\\":
                escaped = self.s[self.i + 1]
                out.append({"n": "\n"}.get(escaped, escaped))
                self.i += 2
            else:
                out.append(self.s[self.i])
                self.i += 1
        self.i += 1
        return "".join(out)

    def array(self) -> list:
        self.expect("[")
        items = []
        while self.peek() != "]":
            items.append(self.value())
            if self.peek() == ",":
                self.i += 1
        self.i += 1
        return items

    def obj(self) -> dict:
        self.expect("{")
        out = {}
        while self.peek() != "}":
            if self.peek() in "\"'":
                key = self.quoted()
            else:
                key_match = re.compile(r"[A-Za-z_$][\w$]*").match(self.s, self.i)
                key = key_match.group(0)
                self.i = key_match.end()
            self.expect(":")
            out[key] = self.value()
            if self.peek() == ",":
                self.i += 1
        self.i += 1
        return out


class Bundle:
    """The bundle text plus the lookups the literals need."""

    def __init__(self, text: str, builders: set[str]):
        self.text = text
        self.builders = builders
        self._constants: dict[str, object] = {}

    def at_after(self, pattern: str) -> int:
        """Position just after the single match of `pattern` (a declaration)."""
        matches = list(re.finditer(pattern, self.text))
        if len(matches) != 1:
            raise DocsError(f"expected one match for {pattern!r}, found {len(matches)}")
        return matches[0].end()

    def constant(self, name: str):
        if name not in self._constants:
            # Declarations in the bundle follow a comma, a semicolon or a space.
            pattern = r"[,;\s]" + re.escape(name) + r"=(?!=)"
            self._constants[name] = Literal(self, self.at_after(pattern)).value()
        return self._constants[name]

    def call(self, name: str, args: list):
        if name in self.builders:
            return {"name": args[0], "loc": args[1], "req": args[2], "desc": args[3]}
        if name == "Ka":
            pad = args[1]
            lines = args[0].split("\n")
            return "\n".join(lines[:1] + [" " * pad + line for line in lines[1:]])
        raise DocsError(f"unknown function {name} in the docs data")

    def literal_after(self, pattern: str):
        return Literal(self, self.at_after(pattern)).value()


def sections_of(bundle: Bundle, declaration: str) -> list[dict]:
    """Sections (`{id|title, items}`) of one array declared in the bundle."""
    return Literal(bundle, bundle.at_after(declaration) - 1).value()


def parse_bundle(text: str) -> dict:
    builders = set(re.findall(BUILDER_DECLARATION_RE, text))
    if not builders:
        raise DocsError("no parameter builder found in the bundle")
    bundle = Bundle(text, builders)
    # The wallet, deposit and withdrawal operations are a flat list, without sections.
    wallet_items = Literal(bundle, bundle.at_after(r"[,;\s ]dD=\[") - 1).value()
    return {
        "v2": sections_of(bundle, r",Gx=\["),
        "v1": [
            *sections_of(bundle, r",oD=\["),
            {"title": "Wallet and withdrawals", "items": wallet_items},
        ],
        "guide": Literal(bundle, bundle.at_after(r",TD=\{") - 1).value(),
    }


def bundle_url_from_page(page: str) -> str:
    match = BUNDLE_RE.search(page)
    if not match:
        raise DocsError("the docs page does not reference a bundle")
    return SERVER_URL + match.group(1)


def fetch_bundle() -> tuple[str, str]:
    """Returns the bundle text and its URL. Only GET requests to the public docs."""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        page = client.get(DOCS_URL)
        page.raise_for_status()
        url = bundle_url_from_page(page.text)
        script = client.get(url)
        script.raise_for_status()
    return script.text, url


# ---------------------------------------------------------------- guide -> markdown


def render_table(table: dict) -> str:
    cols = table["cols"]
    rows = table["rows"]
    flat = rows and not isinstance(rows[0], list)
    chunks = [rows[i:i + len(cols)] for i in range(0, len(rows), len(cols))] if flat else rows
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in chunks]
    return "\n".join(lines)


def render_blocks(blocks: list) -> str:
    out = []
    for block in blocks:
        if "p" in block:
            out.append(block["p"])
        elif "h" in block:
            out.append("#### " + block["h"])
        elif "list" in block:
            out.append("\n".join("- " + item for item in block["list"]))
        elif "steps" in block:
            out.append("\n".join(f"{number}. {step}"
                                 for number, step in enumerate(block["steps"], 1)))
        elif "table" in block:
            out.append(render_table(block["table"]))
        elif "code" in block or "example" in block:
            figure = block.get("code") or block.get("example")
            out.append(f"{figure['label']}:\n\n```\n{figure['text']}\n```")
        elif "tabs" in block:
            for tab in block["tabs"]:
                out.append(f"Example ({tab['label']}):\n\n```{tab['k']}\n{tab['code']}\n```")
        else:
            raise DocsError(f"unknown guide block {sorted(block)}")
    return "\n\n".join(out)


def render_guide(guide: dict, keys: list[str]) -> str:
    parts = []
    for key in keys:
        section = guide[key]
        parts.append(f"## {section['title']}\n\n" + render_blocks(section["blocks"]))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- example -> schema


DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
# A whole number in an example is a number unless its name says it is a count or an id.
# One example cannot show that a price is fractional, so JSON numbers stay `number`.
INTEGER_KEY_RE = re.compile(r"(?i)(^id$|time|count|precision|offset|depth|limit)")


def infer_schema(value, key: str = "") -> dict:
    """Schema from a documented example. Decimal strings keep their string type."""
    if value is None:
        return {"nullable": True}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer" if INTEGER_KEY_RE.search(key) else "number"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        schema = {"type": "string"}
        if DECIMAL_RE.match(value):
            schema["pattern"] = r"^-?\d+(\.\d+)?$"
        elif ISO_RE.match(value):
            schema["format"] = "date-time"
        return schema
    if isinstance(value, list):
        return {"type": "array", "items": infer_schema(value[0], key) if value else {}}
    # An example shows fields, not which of them are optional, so nothing is required.
    return {
        "type": "object",
        "properties": {name: infer_schema(item, name) for name, item in value.items()},
    }


# ---------------------------------------------------------------- operations


def oas_path(path: str) -> tuple[str, list[str]]:
    """`/api/v2/orderbook/:pair` -> `/api/v2/orderbook/{pair}`; also the query string."""
    base, _, query = path.partition("?")
    return re.sub(r":(\w+)", r"{\1}", base), ([query] if query else [])


def param_schema(name: str, description: str) -> dict:
    if "decimal string or number" in description:
        return {"oneOf": [{"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}, {"type": "number"}]}
    if "A digit string is accepted" in description:
        return {"oneOf": [{"type": "integer"}, {"type": "string", "pattern": r"^\d+$"}]}
    if description.startswith("Array"):
        item = {"type": "object"} if "objects" in description else {"type": "string"}
        return {"type": "array", "items": {**item, "description": description}}
    if name in INTEGER_PARAMS:
        return {"type": "integer"}
    if name in BOOLEAN_PARAMS:
        return {"type": "boolean"}
    return {"type": "string"}


def convert_params(params: list[dict]) -> tuple[list[dict], dict | None]:
    parameters = []
    body_props, body_required = {}, []
    for param in params:
        if param["loc"] == "body" and param["name"] == "(body)":
            # The docs give the body as a reference to another operation, not as fields.
            return parameters, {"required": True, "x-body-same-as-description": param["desc"],
                                "content": {"application/json": {"schema": {"type": "object",
                                            "description": param["desc"]}}}}
        if param["loc"] == "body":
            body_props[param["name"]] = {
                "description": param["desc"],
                **param_schema(param["name"], param["desc"]),
            }
            if param["req"]:
                body_required.append(param["name"])
            continue
        parameters.append({
            "name": param["name"],
            "in": "path" if param["loc"] == "path" else "query",
            "required": bool(param["req"]) or param["loc"] == "path",
            "description": param["desc"],
            "schema": param_schema(param["name"], param["desc"]),
        })
    request_body = None
    if body_props:
        schema = {"type": "object", "properties": body_props}
        if body_required:
            schema["required"] = body_required
        request_body = {
            "required": bool(body_required),
            "content": {"application/json": {"schema": schema}},
        }
    return parameters, request_body


def query_parameters_from_path(query: list[str]) -> list[dict]:
    """Query string written into the docs path, e.g. `?status=active`."""
    result = []
    for pair in query[0].split("&") if query else []:
        name, _, value = pair.partition("=")
        result.append({
            "name": name,
            "in": "query",
            "required": False,
            "description": f"The docs example uses {name}={value}.",
            "schema": {"type": "string", "enum": [value]},
        })
    return result


def security_for(auth: str | None, version: str) -> dict:
    """Security requirements of one operation, from its `auth` value in the docs."""
    if version == "v2" and auth == "session":
        return {"security": [], "x-website-session": True}
    if auth not in ("read", "trade", "withdraw"):
        return {"security": []}
    requirements = [{"NeoxexKey": [], "NeoxexSecret": []}]
    if version == "v2":
        # v2 also takes signed headers; either set is enough.
        requirements.insert(0, {
            "NeoxexSignedKey": [], "NeoxexSignedTimestamp": [], "NeoxexSignature": [],
        })
    return {"security": requirements, "x-permission": auth}


def operation(item: dict, section: dict, version: str) -> tuple[str, str, dict]:
    method = item["m"].lower()
    _path, query = oas_path(item["p"])
    params, request_body = convert_params(item.get("params", []))
    parameters = query_parameters_from_path(query) + params
    tag = f"{version} · {section.get('title', section.get('id'))}"

    responses = {}
    example = item.get("ex")
    ok = {"description": "OK"}
    if example:
        try:
            payload = json.loads(example)
        except json.JSONDecodeError:
            # Some docs examples carry // comments or trailing commas. They are kept
            # verbatim: no schema is invented from text that is not JSON.
            payload = None
        if payload is None:
            ok["description"] = "OK. The docs example is not valid JSON; see x-example-text."
            ok["x-example-text"] = example
        else:
            schema = infer_schema(payload)
            schema["x-source"] = "docs-example"
            ok["content"] = {"application/json": {"schema": schema, "example": payload}}
    responses["200"] = ok
    error_schema = f"#/components/schemas/Error{version.upper()}"
    responses["default"] = {
        "description": "Error",
        "content": {"application/json": {"schema": {"$ref": error_schema}}},
    }

    op = {
        "tags": [tag],
        "summary": item["t"],
        "description": item.get("d", ""),
        "operationId": re.sub(r"[^A-Za-z0-9]+", "_", f"{method}_{item['p']}").strip("_"),
        "parameters": parameters,
        "responses": responses,
        "x-section": section.get("id", section.get("title")),
    }
    if item.get("body") and request_body is None:
        request_body = {
            "required": False,
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    if request_body:
        op["requestBody"] = request_body
    if item.get("body"):
        op["requestBody"]["content"]["application/json"]["example"] = item["body"]
    if item.get("try"):
        op["x-try"] = item["try"]
    if item.get("notes"):
        op["x-notes"] = item["notes"]
    if item.get("errors"):
        op["x-error-codes"] = item["errors"]

    op.update(security_for(item.get("auth"), version))

    return oas_path(item["p"])[0], method, op


def build_spec(docs: dict, bundle_sha: str, bundle_url: str) -> dict:
    guide_keys = [
        "v2-authentication", "v2-rate-limits", "v2-errors", "v2-websocket", "v2-moving-from-v1",
    ]
    description = "\n\n".join([
        V2_AUTH_NOTE,
        "Generated by `tools/neoxa_openapi.py` from the Neoxa docs page: Neoxa publishes "
        "no OpenAPI file. The guide text below is copied from the same page.",
        render_guide(docs["guide"], guide_keys),
    ])
    spec = {
        "openapi": "3.0.1",
        "info": {
            "title": "Neoxa Exchange REST API",
            "version": "bundle-" + bundle_sha[:12],
            "description": description,
            "x-source": DOCS_URL,
            "x-bundle-url": bundle_url,
            "x-bundle-sha256": bundle_sha,
        },
        "servers": [{"url": SERVER_URL}],
        "tags": [],
        "paths": {},
        "components": {
            "securitySchemes": {
                "NeoxexKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "NeoxexSecret": {"type": "apiKey", "in": "header", "name": "X-API-Secret"},
                "NeoxexSignedKey": {"type": "apiKey", "in": "header", "name": "X-NX-Key"},
                "NeoxexSignedTimestamp": {
                    "type": "apiKey", "in": "header", "name": "X-NX-Timestamp",
                },
                "NeoxexSignature": {"type": "apiKey", "in": "header", "name": "X-NX-Signature"},
            },
            "schemas": {
                "ErrorV2": {
                    "type": "object",
                    "description": "v2 error: a stable code and a message.",
                    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
                    "required": ["code", "message"],
                },
                "ErrorV1": {
                    "type": "object",
                    "description": "v1 error: free text under `error`. Order calls answer "
                                   "400 for nearly everything.",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                },
            },
        },
    }

    seen = set()
    for version, sections in (("v2", docs["v2"]), ("v1", docs["v1"])):
        for section in sections:
            spec["tags"].append({"name": f"{version} · {section.get('title', section.get('id'))}"})
            for item in section["items"]:
                path, method, op = operation(item, section, version)
                if (method, path) in seen:
                    raise DocsError(f"duplicate operation {method} {path}")
                seen.add((method, path))
                spec["paths"].setdefault(path, {})[method] = op

    for (method, path), (v2_method, v2_path) in V1_TO_V2.items():
        if (method, path) in seen and (v2_method, v2_path) in seen:
            spec["paths"][path][method]["x-v2-equivalent"] = f"{v2_method.upper()} {v2_path}"
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--bundle", type=Path, help="read a saved bundle instead of fetching it")
    parser.add_argument("--url", help="where a saved bundle was downloaded from")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if args.bundle:
        text = args.bundle.read_text(encoding="utf-8", errors="replace")
        url = args.url or f"file:{args.bundle.name}"
    else:
        text, url = fetch_bundle()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    spec = build_spec(parse_bundle(text), sha, url)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    operations = sum(len(methods) for methods in spec["paths"].values())
    print(f"{operations} operations, {len(spec['paths'])} paths -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
