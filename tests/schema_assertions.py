"""Small Draft 2020-12 subset used to test the checked-in API contracts.

The project intentionally has no runtime dependencies. This validator covers
the keywords used by contracts/*.schema.json so contract tests stay portable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class SchemaAssertionError(AssertionError):
    pass


class SchemaStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._documents: dict[Path, dict] = {}

    def load(self, path: Path | str) -> dict:
        resolved = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise SchemaAssertionError(f"schema escapes contract root: {path}") from exc
        if resolved not in self._documents:
            self._documents[resolved] = json.loads(resolved.read_text(encoding="utf-8"))
        return self._documents[resolved]

    def resolve(self, reference: str, base_file: Path) -> tuple[Any, Path]:
        if reference.startswith(("http://", "https://")):
            raise SchemaAssertionError(f"remote $ref is not allowed: {reference}")
        file_part, separator, fragment = reference.partition("#")
        target_file = (base_file.parent / file_part).resolve() if file_part else base_file.resolve()
        document = self.load(target_file)
        target: Any = document
        if separator and fragment:
            if not fragment.startswith("/"):
                raise SchemaAssertionError(f"unsupported JSON pointer: {reference}")
            for raw_part in fragment[1:].split("/"):
                part = raw_part.replace("~1", "/").replace("~0", "~")
                try:
                    target = target[int(part)] if isinstance(target, list) else target[part]
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    raise SchemaAssertionError(f"dangling $ref: {base_file.name} -> {reference}") from exc
        return target, target_file

    def assert_all_local_refs_resolve(self, path: Path) -> None:
        document = self.load(path)

        def walk(value: Any, base_file: Path, visited: set[tuple[Path, str]]) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str):
                    key = (base_file, reference)
                    if key not in visited:
                        visited.add(key)
                        target, target_file = self.resolve(reference, base_file)
                        walk(target, target_file, visited)
                for child in value.values():
                    walk(child, base_file, visited)
            elif isinstance(value, list):
                for child in value:
                    walk(child, base_file, visited)

        walk(document, path.resolve(), set())

    def assert_valid(self, instance: Any, schema_file: Path | str) -> None:
        schema_path = (self.root / schema_file).resolve() if not Path(schema_file).is_absolute() else Path(schema_file).resolve()
        self._validate(instance, self.load(schema_path), schema_path, "$")

    def _matches(self, instance: Any, schema: Any, base_file: Path, path: str) -> bool:
        try:
            self._validate(instance, schema, base_file, path)
            return True
        except SchemaAssertionError:
            return False

    def _validate(self, instance: Any, schema: Any, base_file: Path, path: str) -> None:
        if schema is True:
            return
        if schema is False:
            raise SchemaAssertionError(f"{path}: value rejected by false schema")
        if not isinstance(schema, dict):
            raise SchemaAssertionError(f"{path}: schema must be an object or boolean")

        reference = schema.get("$ref")
        if reference is not None:
            target, target_file = self.resolve(reference, base_file)
            self._validate(instance, target, target_file, path)

        for subschema in schema.get("allOf", []):
            self._validate(instance, subschema, base_file, path)
        if "anyOf" in schema and not any(
            self._matches(instance, subschema, base_file, path) for subschema in schema["anyOf"]
        ):
            raise SchemaAssertionError(f"{path}: value does not match anyOf")
        if "oneOf" in schema:
            matches = sum(self._matches(instance, subschema, base_file, path) for subschema in schema["oneOf"])
            if matches != 1:
                raise SchemaAssertionError(f"{path}: value must match exactly one oneOf branch, got {matches}")
        if "not" in schema and self._matches(instance, schema["not"], base_file, path):
            raise SchemaAssertionError(f"{path}: value matches forbidden schema")
        if "if" in schema:
            branch = schema.get("then") if self._matches(instance, schema["if"], base_file, path) else schema.get("else")
            if branch is not None:
                self._validate(instance, branch, base_file, path)

        expected_type = schema.get("type")
        if expected_type is not None:
            allowed = expected_type if isinstance(expected_type, list) else [expected_type]
            if not any(self._is_type(instance, item) for item in allowed):
                raise SchemaAssertionError(f"{path}: expected {allowed}, got {type(instance).__name__}")
        if "const" in schema and instance != schema["const"]:
            raise SchemaAssertionError(f"{path}: expected const {schema['const']!r}, got {instance!r}")
        if "enum" in schema and instance not in schema["enum"]:
            raise SchemaAssertionError(f"{path}: {instance!r} is not in enum")

        if isinstance(instance, dict):
            required = schema.get("required", [])
            missing = [name for name in required if name not in instance]
            if missing:
                raise SchemaAssertionError(f"{path}: missing required properties {missing}")
            properties = schema.get("properties", {})
            additional = schema.get("additionalProperties", {})
            for name, value in instance.items():
                if name in properties:
                    self._validate(value, properties[name], base_file, f"{path}.{name}")
                elif additional is False:
                    raise SchemaAssertionError(f"{path}: unexpected property {name!r}")
                elif isinstance(additional, dict):
                    self._validate(value, additional, base_file, f"{path}.{name}")

        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                raise SchemaAssertionError(f"{path}: too few items")
            if "maxItems" in schema and len(instance) > schema["maxItems"]:
                raise SchemaAssertionError(f"{path}: too many items")
            if schema.get("uniqueItems"):
                serialized = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
                if len(serialized) != len(set(serialized)):
                    raise SchemaAssertionError(f"{path}: duplicate items")
            item_schema = schema.get("items")
            if item_schema is not None:
                for index, value in enumerate(instance):
                    self._validate(value, item_schema, base_file, f"{path}[{index}]")

        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                raise SchemaAssertionError(f"{path}: string is too short")
            if "maxLength" in schema and len(instance) > schema["maxLength"]:
                raise SchemaAssertionError(f"{path}: string is too long")
            if "pattern" in schema and re.search(schema["pattern"], instance) is None:
                raise SchemaAssertionError(f"{path}: string does not match {schema['pattern']!r}")

        if self._is_type(instance, "number"):
            if "minimum" in schema and instance < schema["minimum"]:
                raise SchemaAssertionError(f"{path}: number is below minimum")
            if "maximum" in schema and instance > schema["maximum"]:
                raise SchemaAssertionError(f"{path}: number is above maximum")

    @staticmethod
    def _is_type(value: Any, expected: str) -> bool:
        mapping = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if expected not in mapping:
            raise SchemaAssertionError(f"unsupported schema type in test validator: {expected}")
        return mapping[expected](value)
