from __future__ import annotations

import json
import re
from typing import Any

from services.api.model_gateway import GenerationRequest, ModelGateway

_MAX_TEXT_VALUE_LENGTH = 120
_SYSTEM_PROMPT = (
    "你是 Shopee 刊登类目属性助手。给你一个商品的上下文和一组马帮类目的必填/可选属性，"
    "每个属性带有选项枚举（value + label）。请为每个属性挑选最符合该商品的一个选项值，"
    "规则：\n"
    "1. 枚举属性只能从给定 options 里选，value 必须逐字使用选项的 value，禁止编造。\n"
    "2. 多选属性（multi_select）返回 value 数组，其余返回单个 value 字符串。\n"
    "3. 没有枚举的文本属性（品牌、型号等）用简短英文填写；确实不适用时用最接近的通用选项"
    "（如「其他 (Others)」），不要留空。\n"
    "4. 只输出 JSON，格式：{\"values\": {\"字段id\": \"value\" 或 [\"value\", ...]}}，不要多余文字。"
)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", str(text or "")).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 回复里没有 JSON")
    return json.loads(cleaned[start : end + 1])


def _sanitize_value(field: dict[str, Any], value: Any) -> Any | None:
    """枚举字段只接受选项里的 value（数组逐个过滤）；文本字段截断后放行。"""
    options = {str(option.get("value") or "") for option in field.get("options") or []}
    if field.get("type") == "multi_select":
        if not isinstance(value, list):
            value = [value]
        picked = [str(item).strip() for item in value if str(item).strip()]
        if options:
            picked = [item for item in picked if item in options]
        return picked or None
    text = str(value or "").strip()
    if not text:
        return None
    if options:
        return text if text in options else None
    return text[:_MAX_TEXT_VALUE_LENGTH]


def recommend_attribute_values(
    gateway: ModelGateway,
    connection: dict[str, Any],
    fields: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """AI 为类目属性推荐值：枚举属性只从选项里选，文本属性短文本。

    返回 {"values": {field_id: value}, "skipped": [field_id]}；values 只包含请求里
    存在的字段，枚举外的值一律丢弃（宁缺勿错，自选值由操作员手动加）。"""
    usable = [field for field in fields if str(field.get("field_id") or "").strip()]
    if not usable:
        return {"values": {}, "skipped": []}
    source_context = context or {}
    brief_context = {
        key: str(source_context.get(key) or "").strip()[:400]
        for key in ("product_name", "category_path", "store_name", "description", "product_line")
    }
    # 商品识别结果是补充事实，不是指令；保留短 JSON 让模型能据此选择材料、
    # 图案、袖长等枚举，同时避免把过大的基础商品对象塞进提示词。
    for key in ("recognized_attributes", "recognized_colors"):
        value = source_context.get(key)
        if isinstance(value, (dict, list)):
            brief_context[key] = json.dumps(value, ensure_ascii=False)[:800]
    user_payload = {
        "context": brief_context,
        "fields": [
            {
                "field_id": field.get("field_id"),
                "label": field.get("label"),
                "required": bool(field.get("required")),
                "type": field.get("type"),
                "options": [
                    {"value": option.get("value"), "label": option.get("label")}
                    for option in (field.get("options") or [])[:80]
                ],
            }
            for field in usable
        ],
    }
    generated = None
    last_error = ""
    for _attempt in range(2):  # 空回复偶发，重试一次
        try:
            generated = gateway.generate(
                connection,
                GenerationRequest(
                    system=_SYSTEM_PROMPT,
                    user=json.dumps(user_payload, ensure_ascii=False),
                    max_tokens=4096,
                ),
            )
            if str(generated.text or "").strip():
                break
        except Exception as exc:  # noqa: BLE001 - 推荐失败不阻塞编辑器
            last_error = str(exc)
    if generated is None or not str(generated.text or "").strip():
        raise ValueError(last_error or "AI 未返回内容")
    parsed = _extract_json(generated.text)
    raw_values = parsed.get("values") if isinstance(parsed, dict) else None
    if not isinstance(raw_values, dict):
        raise ValueError("AI 回复缺少 values 对象")
    by_id = {str(field.get("field_id")): field for field in usable}
    values: dict[str, Any] = {}
    skipped: list[str] = []
    for field_id, value in raw_values.items():
        field = by_id.get(str(field_id))
        if not field:
            continue
        sanitized = _sanitize_value(field, value)
        if sanitized is None:
            skipped.append(str(field_id))
        else:
            values[str(field_id)] = sanitized
    return {"values": values, "skipped": skipped}
