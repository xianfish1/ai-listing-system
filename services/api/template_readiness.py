from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from services.api.category_rules import validate_attribute_values
from services.api.promotion_resolution import promotion_state


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    errors: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
            "missing_fields": [item.field for item in self.errors if item.field],
        }


def calculate_sale_price(value: Any, *, decimal_places: int = 2) -> Decimal:
    try:
        original = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("原价必须是有效数字") from exc
    if original <= 0:
        raise ValueError("原价必须大于 0")
    quantum = Decimal(1).scaleb(-max(0, int(decimal_places)))
    return (original * Decimal("0.60")).quantize(quantum, rounding=ROUND_HALF_UP)


def check_store_template(
    template: dict[str, Any],
    *,
    category_version: dict[str, Any] | None,
    promotion: dict[str, Any] | None,
) -> ReadinessResult:
    errors: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    is_cnsc = str(template.get("listing_mode") or "").strip().lower() == "cnsc_global"
    if not template.get("category_profile_version_id") or not category_version:
        errors.append(
            ReadinessIssue("category_rule_missing", "category_profile_version_id", "需要从马帮导入类目规则")
        )
    else:
        validation = validate_attribute_values(
            category_version,
            template.get("attribute_values") or template.get("category_attributes") or {},
        )
        for error in validation.errors:
            message = f"{error.label or error.field_id}为当前类目的必填项" if error.code == "required" else (
                error.message or f"{error.label or error.field_id}不符合类目规则"
            )
            errors.append(
                ReadinessIssue(
                    f"category_{error.code}",
                    f"attribute_values.{error.field_id}",
                    message,
                )
            )
        # 枚举外的自选值（原产地=Philippines）执行器会走马帮「自填选项」，不算错误。
        for warning in validation.warnings:
            warnings.append(
                ReadinessIssue(
                    f"category_{warning.code}",
                    f"attribute_values.{warning.field_id}",
                    warning.message or f"{warning.label or warning.field_id}不在类目枚举中",
                )
            )

    if not is_cnsc:
        promotion_id = str(
            template.get("promotion_activity_id")
            or template.get("mabang_discount_id")
            or template.get("discount_id")
            or ""
        ).strip()
        state = promotion_state(promotion_id, promotion, now=_readiness_now())
        if state == "unconfigured":
            warnings.append(ReadinessIssue("promotion_unconfigured", "promotion_activity_id", "活动未配置"))
        elif state == "expired":
            warnings.append(ReadinessIssue("promotion_expired", "promotion_activity_id", "促销活动已过期"))
        elif state == "unresolved":
            warnings.append(ReadinessIssue("promotion_unresolved", "promotion_activity_id", "促销活动未解析"))

    transaction = template.get("transaction_info") if isinstance(template.get("transaction_info"), dict) else {}
    _require_positive(errors, template.get("stock", transaction.get("stock")), "stock", "库存")
    _require_positive(errors, template.get("weight_kg", transaction.get("weight_kg")), "weight", "重量")
    _require_positive(
        errors,
        template.get("package_length_cm", transaction.get("package_length_cm")),
        "package_length",
        "包裹长度",
    )
    _require_positive(
        errors,
        template.get("package_width_cm", transaction.get("package_width_cm")),
        "package_width",
        "包裹宽度",
    )
    _require_positive(
        errors,
        template.get("package_height_cm", transaction.get("package_height_cm")),
        "package_height",
        "包裹高度",
    )

    sizes = template.get("sizes") or []
    if not isinstance(sizes, list) or not any(str(item or "").strip() for item in sizes):
        errors.append(ReadinessIssue("sizes_missing", "sizes", "至少选择一个尺码"))
    if not str(template.get("description_profile_version_id") or "").strip():
        errors.append(
            ReadinessIssue(
                "description_profile_missing",
                "description_profile_version_id",
                "请选择描述生成方案",
            )
        )
    if not is_cnsc and not str(
        template.get("size_chart_asset_id")
        or template.get("default_size_chart_path")
        or template.get("mabang_size_chart_template_id")
        or ""
    ).strip():
        errors.append(ReadinessIssue("size_chart_missing", "size_chart_asset_id", "请上传模板尺码图"))

    if any(item.code == "category_rule_missing" for item in errors):
        status = "needs_mabang_import"
    elif errors:
        status = "needs_supplement"
    else:
        status = "ready"
    return ReadinessResult(status=status, errors=tuple(errors), warnings=tuple(warnings))


def _readiness_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_positive(
    errors: list[ReadinessIssue],
    value: Any,
    code_prefix: str,
    label: str,
) -> None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        number = Decimal(0)
    if number <= 0:
        errors.append(ReadinessIssue(f"{code_prefix}_missing", code_prefix, f"{label}必须大于 0"))
