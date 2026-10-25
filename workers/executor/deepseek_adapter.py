from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, request


class DeepSeekAdapter:
    """Suggestion-only DeepSeek adapter.

    Browser automation never calls this adapter. It is only used to propose
    product-library text that a human can accept or ignore.
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.api_url = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions").strip()
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
        self.live_enabled = _truthy(os.getenv("DEEPSEEK_ENABLE_LIVE", "0"))

    def generate_title(self, payload: dict[str, Any]) -> str:
        return self._fallback_title(payload)

    def suggest_title(
        self,
        product: dict[str, Any],
        assets: list[dict[str, Any]] | None = None,
        skus: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assets = assets or []
        skus = skus or []
        options = options or {}
        max_length = int(options.get("max_length") or 180)
        prompt = self._build_title_prompt(product, assets, skus, max_length=max_length)
        mode = "local_rules"
        raw_title = ""
        warning = ""

        if self.api_key and self.live_enabled:
            try:
                raw_title = self._call_deepseek(prompt, max_tokens=int(options.get("max_tokens") or 120))
                mode = "deepseek"
            except Exception as exc:  # pragma: no cover - network fallback boundary
                warning = f"DeepSeek unavailable, used local fallback: {exc}"

        if not raw_title:
            raw_title = self._fallback_title(product)

        title = self._normalize_title(raw_title, product, max_length=max_length)
        validation = self.validate_title(title, product, max_length=max_length)
        return {
            "ok": validation["ok"],
            "mode": mode,
            "model": self.model if mode == "deepseek" else "local-rules",
            "title": title,
            "prompt": prompt,
            "validation": validation,
            "warning": warning,
            "external_call_used": mode == "deepseek",
            "suggestion_only": True,
        }

    def summarize_failure(
        self,
        task: dict[str, Any],
        logs: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        max_length = int(options.get("max_length") or 900)
        prompt = self._build_failure_prompt(task, logs, max_length=max_length)
        mode = "local_rules"
        warning = ""
        raw_summary = ""
        if self.api_key and self.live_enabled:
            try:
                raw_summary = self._call_deepseek(prompt, max_tokens=int(options.get("max_tokens") or 260))
                mode = "deepseek"
            except Exception as exc:  # pragma: no cover - network fallback boundary
                warning = f"DeepSeek unavailable, used local fallback: {exc}"
        if not raw_summary:
            raw_summary = self._fallback_failure_summary(task, logs)
        summary = self._normalize_summary(raw_summary, max_length=max_length)
        validation = self.validate_summary(summary, max_length=max_length)
        return {
            "ok": validation["ok"],
            "mode": mode,
            "model": self.model if mode == "deepseek" else "local-rules",
            "summary": summary,
            "prompt": prompt,
            "validation": validation,
            "warning": warning,
            "external_call_used": mode == "deepseek",
            "suggestion_only": True,
        }

    def suggest_attributes(
        self,
        product: dict[str, Any],
        assets: list[dict[str, Any]] | None = None,
        skus: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assets = assets or []
        skus = skus or []
        options = options or {}
        max_description_length = int(options.get("max_description_length") or 1200)
        prompt = self._build_attribute_prompt(product, assets, skus, max_description_length=max_description_length)
        mode = "local_rules"
        warning = ""
        raw_attributes: dict[str, Any] | str = {}

        if self.api_key and self.live_enabled:
            try:
                raw_attributes = self._call_deepseek(prompt, max_tokens=int(options.get("max_tokens") or 360))
                mode = "deepseek"
            except Exception as exc:  # pragma: no cover - network fallback boundary
                warning = f"DeepSeek unavailable, used local fallback: {exc}"

        attributes = self._normalize_attributes(raw_attributes, product, assets, skus, max_description_length=max_description_length)
        if not attributes:
            attributes = self._fallback_attributes(product, assets, skus, max_description_length=max_description_length)
        validation = self.validate_attributes(attributes, max_description_length=max_description_length)
        return {
            "ok": validation["ok"],
            "mode": mode,
            "model": self.model if mode == "deepseek" else "local-rules",
            "attributes": attributes,
            "allowed_fields": sorted(_ATTRIBUTE_FIELDS),
            "prompt": prompt,
            "validation": validation,
            "warning": warning,
            "external_call_used": mode == "deepseek",
            "suggestion_only": True,
        }

    def _call_deepseek(self, prompt: str, max_tokens: int = 120) -> str:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a suggestion-only marketplace listing assistant. "
                        "Return only the requested text or JSON. Never suggest browser actions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "thinking": {"type": "disabled"},
        }
        req = request.Request(
            self.api_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {raw[:300]}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("empty choices")
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()

    def _build_title_prompt(
        self,
        product: dict[str, Any],
        assets: list[dict[str, Any]],
        skus: list[dict[str, Any]],
        max_length: int,
    ) -> str:
        colors = sorted({str(sku.get("color_cn") or sku.get("color_code") or "").strip() for sku in skus if sku.get("color_cn") or sku.get("color_code")})
        sizes = sorted({str(sku.get("size") or "").strip() for sku in skus if sku.get("size")})
        asset_counts: dict[str, int] = {}
        for asset in assets:
            asset_type = asset.get("asset_type") or "unknown"
            asset_counts[asset_type] = asset_counts.get(asset_type, 0) + 1
        return "\n".join([
            f"Product category: {product.get('category') or ''}",
            f"Group ID: {product.get('group_id') or ''}",
            f"Chinese/base name: {product.get('product_name_cn') or product.get('base_name') or ''}",
            f"Existing title: {product.get('listing_title') or product.get('generated_title') or ''}",
            f"Material: {product.get('material') or 'cotton'}",
            f"Gender/Fit: {product.get('gender') or 'men'} {product.get('fit') or 'casual'}",
            f"Colors: {', '.join(colors[:12])}",
            f"Sizes: {', '.join(sizes[:12])}",
            f"Asset counts: {json.dumps(asset_counts, ensure_ascii=False, sort_keys=True)}",
            f"Rules: English title, max {max_length} characters, include product type, style, material if useful, and include the Group ID once.",
        ])

    def _build_attribute_prompt(
        self,
        product: dict[str, Any],
        assets: list[dict[str, Any]],
        skus: list[dict[str, Any]],
        max_description_length: int,
    ) -> str:
        colors = sorted({str(sku.get("color_cn") or sku.get("color_code") or "").strip() for sku in skus if sku.get("color_cn") or sku.get("color_code")})
        sizes = sorted({str(sku.get("size") or "").strip() for sku in skus if sku.get("size")})
        asset_counts: dict[str, int] = {}
        for asset in assets:
            asset_type = asset.get("asset_type") or "unknown"
            asset_counts[asset_type] = asset_counts.get(asset_type, 0) + 1
        return "\n".join([
            "Suggest product-library attributes as compact JSON only.",
            f"Allowed JSON keys: {', '.join(sorted(_ATTRIBUTE_FIELDS))}",
            f"Product category: {product.get('category') or ''}",
            f"Group ID: {product.get('group_id') or ''}",
            f"Chinese/base name: {product.get('product_name_cn') or product.get('base_name') or ''}",
            f"Current material: {product.get('material') or ''}",
            f"Current gender: {product.get('gender') or ''}",
            f"Current fit: {product.get('fit') or ''}",
            f"Current template_id: {product.get('template_id') or ''}",
            f"Current description: {product.get('description') or ''}",
            f"Colors: {', '.join(colors[:16])}",
            f"Sizes: {', '.join(sizes[:16])}",
            f"Asset counts: {json.dumps(asset_counts, ensure_ascii=False, sort_keys=True)}",
            f"Rules: description max {max_description_length} characters. No browser steps. No publish instructions. Return JSON only.",
        ])

    def _build_failure_prompt(
        self,
        task: dict[str, Any],
        logs: list[dict[str, Any]],
        max_length: int,
    ) -> str:
        compact_logs = []
        for log in logs[-30:]:
            compact_logs.append({
                "level": log.get("level") or "",
                "step": log.get("step") or "",
                "message": log.get("message") or "",
                "screenshot_path": log.get("screenshot_path") or "",
                "payload": log.get("payload_json") or {},
            })
        return "\n".join([
            "Summarize this listing automation task failure for an operator.",
            f"Task ID: {task.get('id') or ''}",
            f"Group ID: {task.get('group_id') or ''}",
            f"Status: {task.get('status') or ''}",
            f"Error type: {task.get('last_error_type') or ''}",
            f"Error message: {task.get('last_error_message') or ''}",
            f"Logs JSON: {json.dumps(compact_logs, ensure_ascii=False, default=str)}",
            f"Rules: Chinese summary, max {max_length} characters. Include root cause, failed step, next action, screenshot path if present. Do not invent actions.",
        ])

    @staticmethod
    def validate_title(title: str, product: dict[str, Any], max_length: int = 180) -> dict[str, Any]:
        blocking = []
        warnings = []
        clean = title.strip()
        group_id = str(product.get("group_id") or "").strip()
        if not clean:
            blocking.append("empty_title")
        if len(clean) > max_length:
            blocking.append("title_too_long")
        if "\n" in clean or "\r" in clean:
            blocking.append("multi_line_title")
        if group_id and group_id.upper() not in clean.upper():
            warnings.append("group_id_missing")
        if re.search(r"https?://|www\\.", clean, flags=re.IGNORECASE):
            blocking.append("url_not_allowed")
        return {
            "ok": not blocking,
            "blocking": blocking,
            "warnings": warnings,
            "length": len(clean),
            "max_length": max_length,
        }

    @staticmethod
    def validate_summary(summary: str, max_length: int = 900) -> dict[str, Any]:
        blocking = []
        clean = summary.strip()
        if not clean:
            blocking.append("empty_summary")
        if len(clean) > max_length:
            blocking.append("summary_too_long")
        return {
            "ok": not blocking,
            "blocking": blocking,
            "warnings": [],
            "length": len(clean),
            "max_length": max_length,
        }

    @staticmethod
    def validate_attributes(attributes: dict[str, Any], max_description_length: int = 1200) -> dict[str, Any]:
        blocking = []
        warnings = []
        unknown = sorted(set(attributes) - _ATTRIBUTE_FIELDS)
        if unknown:
            blocking.append("unknown_fields")
        selected = {key: str(attributes.get(key) or "").strip() for key in _ATTRIBUTE_FIELDS if str(attributes.get(key) or "").strip()}
        if not selected:
            blocking.append("empty_attributes")
        for key, value in selected.items():
            if "\r" in value:
                blocking.append(f"{key}_invalid_newline")
            if key != "description" and "\n" in value:
                blocking.append(f"{key}_invalid_newline")
        description = selected.get("description") or ""
        if description and len(description) > max_description_length:
            blocking.append("description_too_long")
        if "browser" in description.lower() or "publish" in description.lower():
            warnings.append("description_mentions_execution")
        missing_core = [key for key in ["material", "gender", "fit"] if not selected.get(key)]
        if missing_core:
            warnings.append(f"missing_core_fields:{','.join(missing_core)}")
        return {
            "ok": not blocking,
            "blocking": blocking,
            "warnings": warnings,
            "field_count": len(selected),
            "fields": sorted(selected),
            "max_description_length": max_description_length,
        }

    def _normalize_title(self, raw_title: str, product: dict[str, Any], max_length: int) -> str:
        title = re.sub(r"^[\\s\"'`*_\\-]+|[\\s\"'`*_\\-]+$", "", raw_title or "")
        title = re.sub(r"\\s+", " ", title).strip()
        group_id = str(product.get("group_id") or "").strip()
        if group_id and group_id.upper() not in title.upper():
            title = f"{title} {group_id}".strip()
        if len(title) > max_length:
            title = title[:max_length].rstrip(" ,-/")
        return title

    @staticmethod
    def _normalize_summary(raw_summary: str, max_length: int) -> str:
        summary = re.sub(r"\n{3,}", "\n\n", raw_summary or "").strip()
        if len(summary) > max_length:
            summary = summary[:max_length].rstrip()
        return summary

    def _normalize_attributes(
        self,
        raw_attributes: dict[str, Any] | str,
        product: dict[str, Any],
        assets: list[dict[str, Any]],
        skus: list[dict[str, Any]],
        max_description_length: int,
    ) -> dict[str, str]:
        if isinstance(raw_attributes, dict):
            parsed = raw_attributes
        else:
            parsed = _extract_json_object(str(raw_attributes or ""))
        cleaned: dict[str, str] = {}
        for key in _ATTRIBUTE_FIELDS:
            value = parsed.get(key)
            if value is None:
                continue
            clean = re.sub(r"[ \t]+", " ", str(value)).strip()
            if key != "description":
                clean = re.sub(r"\s+", " ", clean).strip()
            else:
                clean = re.sub(r"\n{3,}", "\n\n", clean)
                if len(clean) > max_description_length:
                    clean = clean[:max_description_length].rstrip()
            if clean:
                cleaned[key] = clean
        fallback = self._fallback_attributes(product, assets, skus, max_description_length=max_description_length)
        for key in ["material", "gender", "fit", "template_id", "description"]:
            if not cleaned.get(key) and fallback.get(key):
                cleaned[key] = fallback[key]
        return cleaned

    @staticmethod
    def _fallback_title(product: dict[str, Any]) -> str:
        base_name = product.get("product_name_cn") or product.get("base_name") or product.get("source_title") or "Graphic"
        group_id = product.get("group_id") or ""
        category = str(product.get("category") or "").lower()
        material = product.get("material") or "Cotton"
        product_type = "T-Shirt" if "tee" in category or "t" in category or "pod" in category else "Product"
        parts = [base_name, f"{material} {product_type}", "Casual Short Sleeve Tee", group_id]
        return " ".join(str(part).strip() for part in parts if str(part).strip())[:255]

    @staticmethod
    def _fallback_attributes(
        product: dict[str, Any],
        assets: list[dict[str, Any]],
        skus: list[dict[str, Any]],
        max_description_length: int = 1200,
    ) -> dict[str, str]:
        category = str(product.get("category") or "").lower()
        product_name = product.get("product_name_cn") or product.get("base_name") or "Graphic tee"
        group_id = product.get("group_id") or ""
        material = product.get("material") or ("Cotton" if "t恤" in category or "tee" in category or "pod" in category else "Polyester")
        gender = product.get("gender") or ("Men" if "男" in category or "men" in category else "Unisex")
        fit = product.get("fit") or "Regular"
        template_id = product.get("template_id") or ("pod-mens-tee-v1" if "pod" in category or "t恤" in category or "tee" in category else "")
        color_count = len({str(sku.get("color_cn") or sku.get("color_code") or "").strip() for sku in skus if sku.get("color_cn") or sku.get("color_code")})
        size_values = sorted({str(sku.get("size") or "").strip() for sku in skus if sku.get("size")})
        asset_counts: dict[str, int] = {}
        for asset in assets:
            asset_type = asset.get("asset_type") or "unknown"
            asset_counts[asset_type] = asset_counts.get(asset_type, 0) + 1
        details = [
            f"{product_name} {material} T-Shirt",
            "soft regular fit for daily casual wear",
        ]
        if group_id:
            details.append(f"design group {group_id}")
        if color_count:
            details.append(f"{color_count} color options")
        if size_values:
            details.append(f"sizes {', '.join(size_values[:8])}")
        if asset_counts.get("main") or asset_counts.get("variant"):
            details.append("prepared with main and variant listing images")
        description = ". ".join(details).strip()
        if description and not description.endswith("."):
            description += "."
        if len(description) > max_description_length:
            description = description[:max_description_length].rstrip()
        return {
            "material": str(material),
            "gender": str(gender),
            "fit": str(fit),
            "template_id": str(template_id),
            "description": description,
        }

    @staticmethod
    def _fallback_failure_summary(task: dict[str, Any], logs: list[dict[str, Any]]) -> str:
        error_logs = [log for log in logs if (log.get("level") or "").lower() == "error"]
        last_error = error_logs[-1] if error_logs else (logs[-1] if logs else {})
        screenshot = next((log.get("screenshot_path") for log in reversed(logs) if log.get("screenshot_path")), "")
        error_type = task.get("last_error_type") or (last_error.get("step") or "unknown")
        error_message = task.get("last_error_message") or last_error.get("message") or "未记录明确错误"
        failed_step = last_error.get("step") or task.get("last_error_type") or "-"
        next_action = "检查任务日志、素材路径、页面 selector 或 Worker 状态后重试。"
        if error_type == "asset_file_unreachable":
            next_action = "先检查执行机器是否能访问 NAS 路径，并确认上传文件存在。"
        elif error_type == "readiness_blocked":
            next_action = "先补齐标题、重量、主图、SKU、价格或变体图，再重新入队。"
        elif error_type == "browser_step_failed":
            next_action = "优先查看失败步骤截图，校准页面选择器或弹窗处理。"
        elif error_type == "worker_stale":
            next_action = "检查 Worker 是否离线、浏览器是否卡住，再执行 stale recovery。"
        parts = [
            f"失败类型：{error_type}",
            f"失败步骤：{failed_step}",
            f"错误信息：{error_message}",
            f"建议处理：{next_action}",
        ]
        if screenshot:
            parts.append(f"截图路径：{screenshot}")
        return "\n".join(parts)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


_ATTRIBUTE_FIELDS = {"material", "gender", "fit", "template_id", "description"}


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
