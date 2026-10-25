from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any

from services.api.model_gateway import GenerationRequest


PROTECTED_PLACEHOLDERS = (
    "{{SHIPPING_TIME}}",
    "{{PACKAGE_CONTENTS}}",
    "{{AFTER_SALES}}",
)
ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "ul", "ol", "li"}

# Default fire_leaf 分区模板 (emoji + 纯文本). Fixed copy below is placeholder
# scaffolding — the store owner edits the 文案 later. Field/AI split follows the
# 混合 decision: Material/Target Users are store-fixed; Product Name / Theme /
# store_intro / Highlights are AI slots; Category / Color / Size map from data.
DEFAULT_SECTIONED_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "shipping",
        "type": "fixed",
        "emoji": "🚀",
        "title": "SHIPPING NOTICE",
        "body": (
            "🚚 100% Local Stock: Ready to ship straight to your doorstep!\n"
            "⚡ Fast Dispatch: Ships from the Philippines within 24 hours for quick delivery."
        ),
    },
    {
        "id": "welcome",
        "type": "ai",
        "slot": "store_intro",
        "emoji": "🌿",
        "title": "WELCOME TO FIRE_LEAF",
        "hint": "One warm sentence inviting shoppers, mention fire_leaf and the item vibe. No shipping/returns.",
    },
    {
        "id": "product_info",
        "type": "fields",
        "emoji": "👕",
        "title": "PRODUCT INFORMATION",
        "rows": [
            {"emoji": "🏷️", "label": "Product Name", "source": "ai", "slot": "product_name",
             "hint": "A catchy English product name for this item."},
            {"emoji": "📁", "label": "Category", "source": "field", "field": "category"},
            {"emoji": "🎬", "label": "Character/Theme", "source": "ai", "slot": "theme",
             "hint": "The print/design theme in a few words (e.g. Classic Anime & Pop Culture Print)."},
            {"emoji": "👥", "label": "Target Users", "source": "fixed",
             "value": "Women / Kids (Middle Age / Youth Fashion)"},
            # 必加行（Material / Size Guide / Size Tolerance / Age Range / Size /
            # Stock Availability）：这几行必须出现在每条描述里。固定值行不会因为
            # 商品缺数据被跳过（字段行会），所以常量的尺寸说明/公差/库存用固定值；
            # Age Range 随商品变，用 AI 槽位——槽位默认必填，模型没返回就重试并在
            # 耗尽后报「AI 未按格式返回必填槽位」，不会安静地少一行。
            {"emoji": "🧵", "label": "Material", "source": "fixed",
             "value": "100% High-Quality Cotton (Knit Fabric)"},
            {"emoji": "📐", "label": "Size Guide", "source": "fixed",
             "value": "Please refer to the size chart in the product images."},
            {"emoji": "📏", "label": "Size Tolerance", "source": "fixed",
             "value": "1-3 cm difference due to manual measurement."},
            {"emoji": "🧒", "label": "Age Range", "source": "ai", "slot": "age_range",
             "hint": "Who this item fits, taken from the product facts (e.g. Kids 2-8 years / Teens / Adults). One short line."},
            {"emoji": "📏", "label": "Size", "source": "field", "field": "size"},
            {"emoji": "📦", "label": "Stock Availability", "source": "fixed",
             "value": "Ready stock."},
            {"emoji": "🎨", "label": "Color", "source": "field", "field": "color"},
        ],
    },
    {
        "id": "highlights",
        "type": "ai",
        "slot": "highlights",
        "emoji": "✨",
        "title": "HIGHLIGHTS",
        "hint": "3-4 short lines on comfort, durable print, vivid colors, easy styling. One per line. No shipping/returns.",
    },
    {
        "id": "package",
        "type": "fixed",
        "emoji": "📦",
        "title": "PACKAGE CONTENTS",
        "body": "1 x Item as shown in the listing.",
    },
    {
        "id": "after_sales",
        "type": "fixed",
        "emoji": "💌",
        "title": "AFTER-SALES",
        "body": "Any issue after your order? Message us first and we'll gladly help you sort it out.",
    },
    {
        "id": "reminder",
        "type": "fixed",
        "emoji": "📌",
        "title": "FRIENDLY REMINDER",
        "body": (
            "Manual measurement — a 1-3cm difference is normal.\n"
            "Colors may vary slightly due to different monitors.\n"
            "Please check the size chart before ordering."
        ),
    },
]


def default_sectioned_profile(name: str = "fire_leaf 分区模板") -> dict[str, Any]:
    """Return a ready-to-save sectioned profile payload (deep-copied sections)."""
    from copy import deepcopy

    return {
        "name": name,
        "mode": "sectioned",
        "enabled": True,
        "max_length": 6000,
        # Reasoning models spend thinking tokens out of this budget before any
        # text — 700/900 left replies at stop_reason=max_tokens with no content.
        "max_tokens": 4096,
        "instructions": "",
        "sections": deepcopy(DEFAULT_SECTIONED_SECTIONS),
    }


@dataclass(frozen=True)
class DescriptionGenerationResult:
    status: str
    description: str
    error: str
    model_id: str = ""
    connection_id: str = ""
    profile_version_id: str = ""
    usage: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "description": self.description,
            "error": self.error,
            "model_id": self.model_id,
            "connection_id": self.connection_id,
            "profile_version_id": self.profile_version_id,
            "usage": dict(self.usage or {}),
        }


class DescriptionGenerationService:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def generate(
        self,
        *,
        profile: dict[str, Any],
        product: dict[str, Any],
        connection: Any,
    ) -> DescriptionGenerationResult:
        mode = str(profile.get("mode") or "combined").strip().lower()
        if mode == "sectioned":
            return self._generate_sectioned(
                profile=profile, product=product, connection=connection
            )
        request = GenerationRequest(
            system=_system_prompt(mode, profile),
            user=_product_prompt(product, mode=mode),
            max_tokens=int(profile.get("max_tokens") or 4096),
            temperature=float(profile.get("temperature") or 0.4),
        )
        try:
            generated = self.gateway.generate(connection, request)
            if mode == "combined":
                if _contains_protected_claims(generated.text):
                    raise ValueError("AI 商品段包含运输、包装或售后受保护事实")
                description = _combined_description(profile, generated.text)
            elif mode == "full":
                description = _full_description(profile, product, generated.text)
            else:
                raise ValueError("不支持的描述生成模式")
            description = _sanitize_html(description)
            max_length = int(profile.get("max_length") or 6000)
            if not description.strip():
                raise ValueError("描述不能为空")
            if len(description) > max_length:
                raise ValueError("描述超过允许长度")
        except ValueError as exc:
            return DescriptionGenerationResult(
                status="pending",
                description="",
                error=str(exc),
                connection_id=_connection_value(connection, "id"),
                profile_version_id=_profile_version_id(profile),
            )
        except Exception:
            return DescriptionGenerationResult(
                status="pending",
                description="",
                error="AI 描述生成失败",
                connection_id=_connection_value(connection, "id"),
                profile_version_id=_profile_version_id(profile),
            )
        return DescriptionGenerationResult(
            status="ready",
            description=description,
            error="",
            model_id=generated.model_id,
            connection_id=_connection_value(connection, "id"),
            profile_version_id=_profile_version_id(profile),
            usage=generated.usage,
        )

    def _generate_sectioned(
        self,
        *,
        profile: dict[str, Any],
        product: dict[str, Any],
        connection: Any,
    ) -> DescriptionGenerationResult:
        model_id = ""
        usage: dict[str, int] | None = None
        try:
            sections = profile.get("sections")
            if not isinstance(sections, list) or not sections:
                raise ValueError("分区方案缺少 sections 配置")
            slots = _collect_ai_slots(sections)
            required = _required_slot_names(sections)
            slot_values: dict[str, str] = {}
            if slots:
                request = GenerationRequest(
                    system=_sectioned_system_prompt(profile, slots),
                    user=_product_prompt(product, mode="sectioned"),
                    max_tokens=int(profile.get("max_tokens") or 4096),
                    temperature=float(profile.get("temperature") or 0.5),
                )
                # The model is usually reliable but occasionally returns empty
                # content or drops the [[SLOT]] markers. Retry a few times so a
                # transient miss doesn't ship a half-empty description.
                attempts = max(1, int(profile.get("max_attempts") or 3))
                last_error: Exception | None = None
                for _ in range(attempts):
                    try:
                        generated = self.gateway.generate(connection, request)
                    except Exception as exc:  # transient API error — retry
                        last_error = exc
                        continue
                    model_id = generated.model_id
                    usage = generated.usage
                    parsed = _parse_ai_slots(generated.text, slots)
                    if _contains_protected_claims("\n".join(parsed.values())):
                        raise ValueError("AI 槽位包含运输、包装或售后受保护事实")
                    missing_required = [name for name in required if not parsed.get(name)]
                    if not missing_required:
                        slot_values = parsed
                        break
                    slot_values = parsed  # keep best effort in case we exhaust retries
                    last_error = ValueError(
                        "AI 未按格式返回必填槽位：" + ", ".join(missing_required)
                    )
                else:
                    if isinstance(last_error, ValueError):
                        raise last_error
                    raise ValueError("AI 服务多次未返回有效内容，请稍后重试")
            description = _sectioned_description(sections, product, slot_values)
            max_length = int(profile.get("max_length") or 6000)
            if not description.strip():
                raise ValueError("描述不能为空")
            if len(description) > max_length:
                raise ValueError("描述超过允许长度")
        except ValueError as exc:
            return DescriptionGenerationResult(
                status="pending",
                description="",
                error=str(exc),
                connection_id=_connection_value(connection, "id"),
                profile_version_id=_profile_version_id(profile),
            )
        except Exception:
            return DescriptionGenerationResult(
                status="pending",
                description="",
                error="AI 描述生成失败",
                connection_id=_connection_value(connection, "id"),
                profile_version_id=_profile_version_id(profile),
            )
        return DescriptionGenerationResult(
            status="ready",
            description=description,
            error="",
            model_id=model_id,
            connection_id=_connection_value(connection, "id"),
            profile_version_id=_profile_version_id(profile),
            usage=usage,
        )


def _system_prompt(mode: str, profile: dict[str, Any]) -> str:
    instructions = str(profile.get("instructions") or "").strip()
    if mode == "full":
        base = (
            "Write a marketplace product description. Include each protected placeholder exactly once: "
            + ", ".join(PROTECTED_PLACEHOLDERS)
            + ". Do not invent shipping times, package quantities, links, or after-sales promises."
        )
    else:
        base = (
            "Write only the product-specific middle section. Do not include greetings, shipping promises, "
            "package quantity, links, or after-sales terms."
        )
    return "\n".join(item for item in [base, instructions] if item)


def _product_prompt(product: dict[str, Any], *, mode: str) -> str:
    safe = {
        key: product.get(key)
        for key in ("product_name", "category", "attributes", "colors", "sizes", "package_item")
        if product.get(key) not in (None, "", [], {})
    }
    return f"Mode: {mode}\nProduct facts: {safe}"


def _combined_description(profile: dict[str, Any], product_section: str) -> str:
    parts = [
        str(profile.get("opening") or "").strip(),
        str(product_section or "").strip(),
        str(profile.get("after_sales") or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part)


def _contains_protected_claims(value: str) -> bool:
    text = str(value or "").lower()
    patterns = (
        r"\b\d+\s*(?:-|to)\s*\d+\s*days?\b",
        r"\b(?:ship|shipping|arrive|delivery)\b",
        r"what'?s in the package|\bpackage\s*:\s*\d+|\d+\s*\*\s*\w+",
        r"\b(?:refund|return|after[- ]sales|bad feedback|protection period)\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _full_description(
    profile: dict[str, Any],
    product: dict[str, Any],
    generated: str,
) -> str:
    text = str(generated or "").strip()
    for placeholder in PROTECTED_PLACEHOLDERS:
        if text.count(placeholder) != 1:
            raise ValueError(f"受保护事实占位符必须且只能出现一次：{placeholder}")
    protected = profile.get("protected_facts") if isinstance(profile.get("protected_facts"), dict) else {}
    shipping = str(protected.get("shipping_time") or "").strip()
    after_sales = str(protected.get("after_sales") or profile.get("after_sales") or "").strip()
    quantity = int(product.get("package_quantity") or 1)
    package_item = str(product.get("package_item") or product.get("product_name") or "Product").strip()
    replacements = {
        "{{SHIPPING_TIME}}": shipping,
        "{{PACKAGE_CONTENTS}}": f"What's in the package:{quantity}*{package_item}",
        "{{AFTER_SALES}}": after_sales,
    }
    if not all(replacements.values()):
        raise ValueError("受保护事实配置不完整")
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


# ---------------------------------------------------------------------------
# Sectioned mode: fixed / fields / ai section assembly (emoji + plain text)
# ---------------------------------------------------------------------------

_SLOT_OPEN = "[[SLOT:{name}]]"
_SLOT_CLOSE = "[[/SLOT]]"


def _iter_ai_slots(sections: list[Any]):
    """Yield (slot_name, hint, optional) for every ai-backed slot in the template."""
    for section in sections:
        if not isinstance(section, dict):
            continue
        stype = str(section.get("type") or "").strip().lower()
        if stype == "ai":
            name = str(section.get("slot") or section.get("id") or "").strip()
            if name:
                yield (
                    name,
                    str(section.get("hint") or section.get("title") or "").strip(),
                    bool(section.get("optional")),
                )
        elif stype == "fields":
            for row in section.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("source") or "").strip().lower() == "ai":
                    name = str(row.get("slot") or row.get("label") or "").strip()
                    if name:
                        yield (
                            name,
                            str(row.get("hint") or row.get("label") or "").strip(),
                            bool(row.get("optional")),
                        )


def _collect_ai_slots(sections: list[Any]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for name, hint, _optional in _iter_ai_slots(sections):
        seen.setdefault(name, hint)
    return list(seen.items())


def _required_slot_names(sections: list[Any]) -> list[str]:
    required: list[str] = []
    seen: set[str] = set()
    for name, _hint, optional in _iter_ai_slots(sections):
        if name in seen:
            continue
        seen.add(name)
        if not optional:
            required.append(name)
    return required


def _sectioned_system_prompt(profile: dict[str, Any], slots: list[tuple[str, str]]) -> str:
    instructions = str(profile.get("instructions") or "").strip()
    lines = [
        "You fill short copy slots for a marketplace product description.",
        "Return ONLY the slots below, each wrapped exactly as:",
        f"{_SLOT_OPEN.format(name='SLOT_NAME')}your text{_SLOT_CLOSE}",
        "Keep each slot concise (one short line unless the hint says a few lines).",
        "Never mention shipping, delivery, days-to-arrive, package quantity, links,"
        " refunds, returns, or after-sales — those are handled elsewhere.",
        "Slots to fill:",
    ]
    for name, hint in slots:
        lines.append(f"- {name}: {hint}" if hint else f"- {name}")
    return "\n".join(item for item in ["\n".join(lines), instructions] if item)


def _parse_ai_slots(text: str, slots: list[tuple[str, str]]) -> dict[str, str]:
    body = str(text or "")
    values: dict[str, str] = {}
    for name, _hint in slots:
        # The model usually closes with [[/SLOT]], but sometimes omits it and
        # just opens the next [[SLOT:...]]. Capture up to the close tag, the next
        # open tag, or end of text — whichever comes first — so a missing close
        # tag doesn't swallow every following slot.
        pattern = (
            re.escape(_SLOT_OPEN.format(name=name))
            + r"(.*?)(?:"
            + re.escape(_SLOT_CLOSE)
            + r"|\[\[SLOT:|$)"
        )
        match = re.search(pattern, body, re.DOTALL)
        if match:
            values[name] = _clean_slot_text(match.group(1))
    return values


def _clean_slot_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    text = re.sub(r"\[\[/?SLOT[^\]]*\]\]", "", text)  # strip any stray markers
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _field_value(key: str, product: dict[str, Any]) -> str:
    key = str(key or "").strip().lower()
    if key == "category":
        return str(product.get("marketplace_category_path") or product.get("category") or "").strip()
    if key in {"color", "colors"}:
        colors = product.get("recognized_colors") or _axis_values(product, "color")
        return ", ".join(_title_words(c) for c in colors if c)
    if key in {"size", "sizes"}:
        sizes = _axis_values(product, "size") or product.get("sizes") or []
        return " / ".join(str(s).strip() for s in sizes if str(s).strip())
    if key in {"product_name", "name"}:
        return str(product.get("listing_title") or product.get("product_name") or "").strip()
    # 平台类目基础属性（基础商品 AI 识别表，如 Item Type / Material / Style）
    # 按属性名匹配，大小写/空格容错，找不到再落到 product 顶层字段。
    attributes = product.get("recognized_attributes")
    if isinstance(attributes, dict) and attributes:
        wanted = str(key).strip().lower()
        for attr_key, attr_value in attributes.items():
            attr_key_norm = str(attr_key or "").strip().lower()
            if attr_key_norm and attr_key_norm == wanted:
                return str(attr_value or "").strip()
    value = product.get(key)
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def _axis_values(product: dict[str, Any], axis_name: str) -> list[str]:
    variation = product.get("variation")
    if not isinstance(variation, dict):
        return []
    for raw in variation.get("axes") or []:
        axis = raw
        if isinstance(raw, str):
            try:
                import ast

                axis = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
        if isinstance(axis, dict) and str(axis.get("name") or "").strip().lower() == axis_name:
            return [str(v).strip() for v in (axis.get("values") or []) if str(v).strip()]
    return []


def _title_words(value: str) -> str:
    return " ".join(word.capitalize() for word in str(value or "").split())


def _sectioned_description(
    sections: list[Any],
    product: dict[str, Any],
    slot_values: dict[str, str],
) -> str:
    blocks: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        block = _render_section(section, product, slot_values)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def _render_section(
    section: dict[str, Any],
    product: dict[str, Any],
    slot_values: dict[str, str],
) -> str:
    stype = str(section.get("type") or "").strip().lower()
    header = _section_header(section)
    if stype == "fixed":
        body = str(section.get("body") or "").strip()
        return "\n".join(item for item in [header, body] if item)
    if stype == "ai":
        name = str(section.get("slot") or section.get("id") or "").strip()
        body = slot_values.get(name, "").strip()
        if not body:
            return ""
        return "\n".join(item for item in [header, body] if item)
    if stype == "fields":
        parts: list[str] = []
        line_buffer: list[str] = []

        def flush_lines() -> None:
            if line_buffer:
                parts.append("\n".join(line_buffer))
                line_buffer.clear()

        if header:
            parts.append(header)
        for row in section.get("rows") or []:
            if not isinstance(row, dict):
                continue
            source = str(row.get("source") or "").strip().lower()
            if source == "base_detail":
                # 基础商品详情：整组键值列表独占一段（多行内容不适合塞进单行前缀）
                detail = _row_value(row, product, slot_values)
                if detail:
                    flush_lines()
                    parts.append(str(detail).strip())
                continue
            value = _row_value(row, product, slot_values)
            if not value:
                continue
            emoji = str(row.get("emoji") or "").strip()
            label = str(row.get("label") or "").strip()
            prefix = " ".join(item for item in [emoji, f"{label}:" if label else ""] if item)
            line_buffer.append(f"{prefix} {value}".strip())
        flush_lines()
        # 只有标题没有任何实际内容时，整区不输出（与旧行为一致）
        if len(parts) == 1 and parts[0] == header:
            return ""
        return "\n\n".join(part for part in parts if part).strip()
    return ""


def _row_value(
    row: dict[str, Any],
    product: dict[str, Any],
    slot_values: dict[str, str],
) -> str:
    source = str(row.get("source") or "").strip().lower()
    if source == "fixed":
        return str(row.get("value") or "").strip()
    if source == "ai":
        name = str(row.get("slot") or row.get("label") or "").strip()
        return slot_values.get(name, "").strip()
    if source == "base_detail":
        # 基础商品详情：整份 AI 识别属性键值（Item Type : Hair Ornament ...）。
        # 保留商品里原始换行排版；无识别属性时回退到商品详情文本。
        attributes = product.get("recognized_attributes")
        if isinstance(attributes, dict) and attributes:
            lines = []
            for attr_key, attr_value in attributes.items():
                key = str(attr_key or "").strip()
                val = str(attr_value if attr_value is not None else "").strip()
                if key and val:
                    lines.append(f"{key} : {val}")
            if lines:
                return "\n".join(lines).strip()
        return str(product.get("description") or "").strip()
    return _field_value(row.get("field") or row.get("label") or "", product)


def _section_header(section: dict[str, Any]) -> str:
    emoji = str(section.get("emoji") or "").strip()
    title = str(section.get("title") or "").strip()
    return " ".join(item for item in [emoji, title] if item).strip()


def _sanitize_html(value: str) -> str:
    tokens = re.split(r"(<[^>]+>)", str(value or ""))
    output: list[str] = []
    for token in tokens:
        if not token:
            continue
        if token.startswith("<"):
            match = re.fullmatch(r"</?([a-zA-Z0-9]+)\s*/?>", token.strip())
            if match and match.group(1).lower() in ALLOWED_TAGS:
                output.append(token.strip())
            continue
        output.append(escape(token, quote=False))
    return "".join(output).strip()


def _connection_value(connection: Any, key: str) -> str:
    if isinstance(connection, dict):
        return str(connection.get(key) or "")
    return str(getattr(connection, key, "") or "")


def _profile_version_id(profile: dict[str, Any]) -> str:
    return str(profile.get("current_version_id") or profile.get("id") or "")
