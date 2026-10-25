from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


FIELD_TYPES = {"text", "number", "boolean", "single_select", "multi_select"}
CONDITION_OPERATORS = {"eq", "neq", "in", "not_in", "present"}


@dataclass(frozen=True)
class ValidationError:
    code: str
    field_id: str = ""
    label: str = ""
    message: str = ""


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    required_field_ids: list[str] = field(default_factory=list)

    @property
    def missing_field_ids(self) -> list[str]:
        return [error.field_id for error in self.errors if error.code == "required"]

    @property
    def blocking_errors(self) -> list[ValidationError]:
        return self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [_error_dict(error) for error in self.errors],
            "warnings": [_error_dict(warning) for warning in self.warnings],
            "required_field_ids": list(self.required_field_ids),
        }


def validate_profile_version(version: dict[str, Any]) -> ValidationResult:
    errors: list[ValidationError] = []
    seen: set[str] = set()
    for raw_field in version.get("fields") or []:
        definition = normalize_field_definition(raw_field)
        field_id = definition["field_id"]
        label = definition["label"]
        if not field_id:
            errors.append(ValidationError("missing_field_id", label=label, message="Field id is required"))
            continue
        if field_id in seen:
            errors.append(ValidationError("duplicate_field_id", field_id, label, "Field id must be unique"))
        seen.add(field_id)
        if definition["type"] not in FIELD_TYPES:
            errors.append(ValidationError("unsupported_field_type", field_id, label, "Field type is not supported"))
        condition_error = _validate_condition(definition.get("required_when"))
        if condition_error:
            errors.append(ValidationError(condition_error, field_id, label, "Required condition is not supported"))
        if definition["required"] and not _has_automation_mapping(definition.get("automation")):
            errors.append(
                ValidationError(
                    "missing_automation_mapping",
                    field_id,
                    label,
                    "Required field has no automation mapping",
                )
            )
    return ValidationResult(valid=not errors, errors=errors)


def validate_attribute_values(version: dict[str, Any], values: dict[str, Any] | None) -> ValidationResult:
    values = values if isinstance(values, dict) else {}
    definitions = [normalize_field_definition(item) for item in version.get("fields") or []]
    required = resolve_required_fields(version, values)
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
    for definition in definitions:
        field_id = definition["field_id"]
        label = definition["label"]
        value = values.get(field_id)
        if field_id in required and _is_empty(value):
            errors.append(ValidationError("required", field_id, label, f"{label} is required"))
            continue
        if _is_empty(value):
            continue
        if not _matches_type(definition["type"], value):
            errors.append(ValidationError("invalid_type", field_id, label, f"{label} has an invalid value type"))
            continue
        allowed = {str(item.get("value")) for item in definition.get("options") or [] if isinstance(item, dict)}
        unknown = _unknown_option_text(definition["type"], value, allowed) if allowed else ""
        if unknown:
            warnings.append(
                ValidationError(
                    "custom_option",
                    field_id,
                    label,
                    # 枚举外的值是马帮支持的「自填选项」（原产地=Philippines 这类自选值）：
                    # 采集端会把页面上的自填值并入 options，执行器会走 select_custom
                    # 写进下拉，故不阻塞，只提醒操作员该值不在枚举里。
                    f"{label} 使用枚举外的自选值「{unknown}」，自动化将按马帮「自填选项」写入",
                )
            )
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings, required_field_ids=required)


def resolve_required_fields(version: dict[str, Any], values: dict[str, Any] | None) -> list[str]:
    values = values if isinstance(values, dict) else {}
    required: list[str] = []
    for raw_field in version.get("fields") or []:
        definition = normalize_field_definition(raw_field)
        field_id = definition["field_id"]
        if not field_id:
            continue
        condition = definition.get("required_when")
        if definition["required"] or (condition and _condition_matches(condition, values)):
            required.append(field_id)
    return sorted(set(required))


def normalize_field_definition(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    field_id = str(value.get("field_id") or "").strip()
    label = str(value.get("label") or field_id).strip()
    options = []
    for option in value.get("options") or []:
        if not isinstance(option, dict) or option.get("value") is None:
            continue
        options.append({"value": str(option["value"]), "label": str(option.get("label") or option["value"])})
    return {
        "field_id": field_id,
        "label": label,
        "type": str(value.get("type") or "text").strip(),
        "required": bool(value.get("required")),
        "required_when": deepcopy(value.get("required_when")),
        "options": options,
        "default_value": deepcopy(value.get("default_value", "")),
        "automation": deepcopy(value.get("automation") if isinstance(value.get("automation"), dict) else {}),
        "identity_confidence": str(value.get("identity_confidence") or "high").strip(),
    }


def build_profile_snapshot(profile: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": str(profile.get("id") or version.get("profile_id") or ""),
        "platform": str(profile.get("platform") or ""),
        "country": str(profile.get("country") or ""),
        "marketplace_category_id": str(profile.get("marketplace_category_id") or ""),
        "marketplace_category_path": str(profile.get("marketplace_category_path") or ""),
        "version_id": str(version.get("id") or ""),
        "version": int(version.get("version") or 0),
        "fields": [normalize_field_definition(item) for item in version.get("fields") or []],
        "variation": deepcopy(version.get("variation") if isinstance(version.get("variation"), dict) else {}),
    }


def hash_profile_snapshot(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _validate_condition(condition: Any) -> str:
    if condition in (None, {}):
        return ""
    if not isinstance(condition, dict):
        return "unsupported_condition"
    group_keys = [key for key in ("all", "any") if key in condition]
    if group_keys:
        if len(group_keys) != 1 or len(condition) != 1:
            return "unsupported_condition"
        children = condition[group_keys[0]]
        if not isinstance(children, list) or not children:
            return "unsupported_condition"
        return "" if all(_validate_leaf_condition(child) == "" for child in children) else "unsupported_condition"
    return _validate_leaf_condition(condition)


def _validate_leaf_condition(condition: Any) -> str:
    if not isinstance(condition, dict):
        return "unsupported_condition"
    if set(condition) - {"field", "op", "value"}:
        return "unsupported_condition"
    if not str(condition.get("field") or "").strip():
        return "unsupported_condition"
    if condition.get("op") not in CONDITION_OPERATORS:
        return "unsupported_condition"
    return ""


def _condition_matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    if "all" in condition:
        return all(_leaf_matches(child, values) for child in condition["all"])
    if "any" in condition:
        return any(_leaf_matches(child, values) for child in condition["any"])
    return _leaf_matches(condition, values)


def _leaf_matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    actual = values.get(str(condition.get("field") or ""))
    expected = condition.get("value")
    operator = condition.get("op")
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    if operator == "in":
        return actual in (expected if isinstance(expected, list) else [expected])
    if operator == "not_in":
        return actual not in (expected if isinstance(expected, list) else [expected])
    if operator == "present":
        return not _is_empty(actual)
    return False


def _has_automation_mapping(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(str(value.get("page_field_id") or value.get("label") or value.get("strategy") or "").strip())


def _matches_type(field_type: str, value: Any) -> bool:
    if field_type == "number":
        if isinstance(value, bool):
            return False
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "multi_select":
        return isinstance(value, list)
    return isinstance(value, str)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _unknown_option_text(field_type: str, value: Any, allowed: set[str], *, limit: int = 40) -> str:
    """枚举之外的值合并成一条可读提示（多选只列真正的越界项）。"""

    items = value if field_type == "multi_select" and isinstance(value, list) else [value]
    text = "、".join(
        text for text in (str(item).strip() for item in items) if text and text not in allowed
    )
    if not text:
        return ""
    return text if len(text) <= limit else f"{text[:limit]}…"


def _error_dict(error: ValidationError) -> dict[str, str]:
    return {
        "code": error.code,
        "field_id": error.field_id,
        "label": error.label,
        "message": error.message,
    }
