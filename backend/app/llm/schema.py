"""The JSON schema the gateway sends to a provider for structured output (2026-10-18).

Found by a failed probe (docs/evaluation-plan.md, V2): LiteLLM passed Pydantic's JSON schema to
Gemini unchanged — `$defs`/`$ref`, `additionalProperties`, a nullable `anyOf`, `default` — and the
calls came back `400 INVALID_ARGUMENT`. A flat schema had worked. So the gateway never sends a raw
Pydantic schema: `provider_schema` turns it into a plain one, and `check_provider_schema` refuses
anything outside a small allow-list before a request is built.

- `$ref` is inlined from `$defs`; no `$defs` is sent.
- A nullable property (`anyOf: [X, {"type": "null"}]`) is sent as plain `X` and taken out of its
  object's `required`: the model omits it when it has no value, and the Pydantic model's default
  (None) fills it. Neither `anyOf` nor a `["X", "null"]` type list is sent.
- `const` becomes a one-value `enum`.
- `additionalProperties`, `default`, `examples`, `$schema`, `minItems`, `maxItems` are dropped.

Checked against Gemini on 2026-10-18 with approved cheap-tier calls
(backend/scripts/schema_confirm.py): the first form (type lists for nullables, `maxItems` kept)
still returned 400; yesterday's flat smoke request through the same code path succeeded, so the
environment was fine and the schema's shape was the cause. This form (no type lists, no item
counts) then **succeeded** (11:06 UTC: a valid reply on the classifier's own schema and prompt).
Which of the two removed constructs Gemini rejected was not isolated; both are kept out.

Nothing is lost in strictness: the gateway still validates every reply against the full Pydantic
model (extra fields refused, bounds checked), and repairs once if it does not validate. What the
provider receives is guidance; the check that counts is ours.

Every structured schema the code sends is checked offline by tests/test_llm_request_body.py, which
captures the request body LiteLLM actually builds.
"""

from typing import Any

from pydantic import BaseModel

# The only keywords a provider schema may contain.
ALLOWED_KEYWORDS = frozenset(
    {
        "type", "properties", "required", "items", "enum", "title", "description",
        "minimum", "maximum", "minLength", "maxLength",
    }
)  # fmt: skip
_DROPPED = frozenset(
    {"additionalProperties", "default", "examples", "$schema", "minItems", "maxItems"}
)
_NULLABLE = "__nullable__"  # internal marker: never sent


class SchemaNotSupported(ValueError):
    """The schema uses a construct the provider form cannot express; fix the model."""


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_resolve(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        if name not in defs:
            raise SchemaNotSupported(f"unresolvable $ref {node['$ref']!r}")
        merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve(merged, defs)
    if "anyOf" in node:
        options = [_resolve(o, defs) for o in node["anyOf"]]
        non_null = [o for o in options if o != {"type": "null"}]
        if len(non_null) != 1 or len(options) != 2 or not isinstance(non_null[0].get("type"), str):
            raise SchemaNotSupported("only a nullable single-type anyOf is supported")
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return {**_resolve({**non_null[0], **rest}, defs), _NULLABLE: True}
    out: dict[str, Any] = {}
    nullable: set[str] = set()
    for key, value in node.items():
        if key in _DROPPED or key == "$defs":
            continue
        if key == "const":
            out["enum"] = [value]
        elif key == "properties":
            props = {}
            for name, sub in value.items():
                resolved = _resolve(sub, defs)
                if resolved.pop(_NULLABLE, False):
                    nullable.add(name)
                props[name] = resolved
            out[key] = props
        else:
            resolved_value = _resolve(value, defs)
            if isinstance(resolved_value, dict) and resolved_value.get(_NULLABLE):
                raise SchemaNotSupported(
                    f"a nullable {key!r} cannot be expressed; make it a property"
                )
            out[key] = resolved_value
    if nullable and "required" in out:
        out["required"] = [r for r in out["required"] if r not in nullable]
        if not out["required"]:
            del out["required"]
    return out


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The plain schema for `model`, checked against the allow-list."""
    raw = model.model_json_schema()
    schema: dict[str, Any] = _resolve(raw, raw.get("$defs", {}))
    check_provider_schema(schema)
    return schema


def check_provider_schema(node: Any, path: str = "$") -> None:
    """Raise SchemaNotSupported if any keyword outside ALLOWED_KEYWORDS remains."""
    if isinstance(node, list):
        for n, item in enumerate(node):
            check_provider_schema(item, f"{path}[{n}]")
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key not in ALLOWED_KEYWORDS:
            raise SchemaNotSupported(f"{path}: keyword {key!r} is not sent to providers")
        if key == "properties":
            for name, sub in value.items():
                check_provider_schema(sub, f"{path}.properties.{name}")
        elif key in ("items",):
            check_provider_schema(value, f"{path}.{key}")
