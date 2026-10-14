"""Strict pre-publish validation of trends.json against docs/trends.schema.json (spec §7).

A dependency-free interpreter for the JSON Schema (draft 2020-12) subset that the contract uses. Any keyword
it does not implement raises, so the schema cannot silently outgrow the checker. Tests cross-check verdicts
with the `jsonschema` package (a dev-only dependency).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PACKAGED_SCHEMA = Path(__file__).with_name("trends.schema.json")
_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_SUPPORTED = _ANNOTATIONS | {
    "type", "const", "enum", "$ref", "anyOf", "$defs",
    "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems",
    "pattern", "minLength", "maxLength",
    "minimum", "maximum",
}


class SchemaError(ValueError):
    """The schema uses a keyword this validator does not implement."""


def load_schema(docs_dir: Path | None = None) -> dict[str, Any]:
    """Prefer the normative docs/trends.schema.json; fall back to the packaged copy (kept identical by a test)."""
    candidates = ([docs_dir / "trends.schema.json"] if docs_dir else []) + [PACKAGED_SCHEMA]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("trends.schema.json not found")


@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _is_type(value: Any, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    raise SchemaError(f"unsupported type {t!r}")


def _same(a: Any, b: Any) -> bool:
    """JSON equality: booleans never equal numbers."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


class _Validator:
    def __init__(self, root: dict[str, Any]):
        self.root = root

    def resolve(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise SchemaError(f"unsupported $ref {ref!r}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def errors(self, value: Any, schema: dict[str, Any], path: str) -> list[str]:
        unknown = set(schema) - _SUPPORTED
        if unknown:
            raise SchemaError(f"unsupported schema keyword(s) {sorted(unknown)} at {path}")
        errs: list[str] = []
        if "$ref" in schema:
            errs += self.errors(value, self.resolve(schema["$ref"]), path)
        if "anyOf" in schema and not any(not self.errors(value, s, path) for s in schema["anyOf"]):
            errs.append(f"{path}: matches none of anyOf")
        if "const" in schema and not _same(value, schema["const"]):
            errs.append(f"{path}: must equal {schema['const']!r}")
        if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
            errs.append(f"{path}: {value!r} not in {schema['enum']!r}")
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(value, t) for t in types):
                return errs + [f"{path}: expected {schema['type']}, got {type(value).__name__}"]
        if isinstance(value, str):
            if "pattern" in schema and not _regex(schema["pattern"]).search(value):
                errs.append(f"{path}: {value[:60]!r} does not match {schema['pattern']}")
            if "minLength" in schema and len(value) < schema["minLength"]:
                errs.append(f"{path}: shorter than {schema['minLength']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errs.append(f"{path}: longer than {schema['maxLength']}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errs.append(f"{path}: {value} < minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errs.append(f"{path}: {value} > maximum {schema['maximum']}")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errs.append(f"{path}: fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errs.append(f"{path}: more than {schema['maxItems']} items")
            if "items" in schema:
                for i, v in enumerate(value):
                    errs += self.errors(v, schema["items"], f"{path}[{i}]")
        if isinstance(value, dict):
            props = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    errs.append(f"{path}: missing required {key!r}")
            for key, v in value.items():
                if key in props:
                    errs += self.errors(v, props[key], f"{path}.{key}")
                else:
                    extra = schema.get("additionalProperties", True)
                    if extra is False:
                        errs.append(f"{path}: unexpected property {key!r}")
                    elif isinstance(extra, dict):
                        errs += self.errors(v, extra, f"{path}.{key}")
        return errs


def validate_payload(payload: Any, schema: dict[str, Any]) -> list[str]:
    """Return human-readable contract violations (empty list = valid). Also checks contiguous ranks."""
    errs = _Validator(schema).errors(payload, schema, "$")
    if not errs and isinstance(payload, dict):
        ranks = [it.get("rank") for it in payload.get("items", [])]
        if ranks != list(range(1, len(ranks) + 1)):
            errs.append(f"$.items: ranks must be contiguous from 1, got {ranks}")
    return errs
