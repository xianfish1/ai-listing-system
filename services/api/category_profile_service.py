from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from services.api.category_rules import normalize_field_definition, validate_profile_version
from services.api.marketplace_category_catalog import sanitize_source_url


class CategoryProfileConflict(RuntimeError):
    pass


class CategoryProfileNotFound(LookupError):
    pass


def create_collection_run(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _utc_now()
    run = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "platform": str(payload.get("platform") or "shopee").strip().lower(),
        "country": str(payload.get("country") or "").strip().upper(),
        "store_id": str(payload.get("store_id") or payload.get("mabang_store_id") or "").strip(),
        "marketplace_category_id": str(payload.get("category_id") or payload.get("marketplace_category_id") or "").strip(),
        "page_url": str(payload.get("page_url") or "").strip(),
        "raw_capture": {},
        "active_version_id": "",
        "error": "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    if not run["country"] or not run["marketplace_category_id"]:
        raise CategoryProfileConflict("country and category_id are required for a collection run")
    data.setdefault("category_collection_runs", []).append(run)
    return run


def complete_collection_run(
    data: dict[str, Any],
    run_id: str,
    raw_capture: dict[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    run = next((item for item in data.setdefault("category_collection_runs", []) if item.get("id") == run_id), None)
    if run is None:
        raise CategoryProfileNotFound(f"Category collection run not found: {run_id}")
    timestamp = now or _utc_now()
    _validate_capture_matches_run(run, raw_capture)
    sanitized_capture = dict(raw_capture)
    if sanitized_capture.get("page_url") or sanitized_capture.get("source_url") or sanitized_capture.get("url"):
        sanitized_capture["page_url"] = sanitize_source_url(
            sanitized_capture.get("page_url") or sanitized_capture.get("source_url") or sanitized_capture.get("url")
        )
        sanitized_capture.pop("source_url", None)
        sanitized_capture.pop("url", None)
    run["raw_capture"] = sanitized_capture
    run["updated_at"] = timestamp
    try:
        result = create_candidate_from_capture(data, run_id=run_id, raw_capture=sanitized_capture, now=timestamp)
    except CategoryProfileConflict as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        raise
    run["status"] = "completed"
    run["active_version_id"] = result["version"]["id"]
    return {"run": run, **result}


def create_candidate_from_capture(
    data: dict[str, Any],
    *,
    run_id: str,
    raw_capture: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    if not isinstance(raw_capture, dict) or raw_capture.get("error"):
        raise CategoryProfileConflict("The category capture did not complete successfully")
    platform = str(raw_capture.get("platform") or "").strip().lower()
    country = str(raw_capture.get("country") or "").strip().upper()
    category_id = str(raw_capture.get("marketplace_category_id") or "").strip()
    fields = raw_capture.get("fields")
    if not platform or not country or not category_id or not isinstance(fields, list):
        raise CategoryProfileConflict("The category capture is incomplete")

    timestamp = now or _utc_now()
    profile_id = _profile_id(platform, country, category_id)
    versions = data.setdefault("category_profile_versions", [])
    normalized_fields = [normalize_field_definition(item) for item in fields]
    next_version = 1 + max(
        [int(item.get("version") or 0) for item in versions if item.get("profile_id") == profile_id],
        default=0,
    )
    profiles = data.setdefault("category_profiles", [])
    profile = next((item for item in profiles if item.get("id") == profile_id), None)
    current = _active_version(data, profile or {})
    # 采集页没渲染出属性（如新增刊登的空表单）时 fields 会是空列表：绝不能让空
    # 版本顶掉已发布的规则（现场 2026-09-26：两次空确认把 100244 的 16 字段 v18
    # 顶成 v19/v20 空版本，副本属性阶段无字段可填 → 马帮「2 必填项未填」保存被拦）。
    # 已有带字段的现役版本时沿用现役版本，不发布空版本。
    if not normalized_fields and current and (current.get("fields") or []):
        return {"profile": profile, "version": current, "reused": True}
    version = {
        "id": str(uuid.uuid4()),
        "profile_id": profile_id,
        "version": next_version,
        "status": "active",
        "source": {
            "type": "mabang_browser_collection",
            "run_id": str(run_id or "").strip(),
            "page_url": sanitize_source_url(raw_capture.get("page_url") or ""),
            "collected_at": timestamp,
        },
        "fields": normalized_fields,
        "variation": _normalize_variation(raw_capture.get("variation")),
        "schema_hash": _schema_hash(normalized_fields, raw_capture.get("variation")),
        "created_at": timestamp,
        "activated_at": timestamp,
        "updated_at": timestamp,
    }
    version["diff"] = classify_profile_diff(current or {"fields": [], "variation": {}}, version)
    validation = validate_profile_version(version)
    if validation.blocking_errors or version["diff"]["severity"] == "blocking":
        raise CategoryProfileConflict("The captured category version has blocking rule errors")

    if profile is None:
        profile = {
            "id": profile_id,
            "platform": platform,
            "country": country,
            "marketplace_category_id": category_id,
            "marketplace_category_path": str(raw_capture.get("marketplace_category_path") or "").strip(),
            "display_name": str(
                raw_capture.get("display_name")
                or raw_capture.get("marketplace_category_path")
                or category_id
            ).strip(),
            "active_version_id": "",
            "published_version_id": "",
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        profiles.append(profile)
    else:
        profile["marketplace_category_path"] = str(
            raw_capture.get("marketplace_category_path")
            or profile.get("marketplace_category_path")
            or ""
        ).strip()
        profile["display_name"] = str(
            raw_capture.get("display_name") or profile.get("display_name") or ""
        ).strip()
        profile["updated_at"] = timestamp

    for existing in versions:
        if existing.get("profile_id") == profile_id and existing.get("status") in {"active", "published"}:
            existing["status"] = "superseded"
            existing["updated_at"] = timestamp
    versions.append(version)
    profile["active_version_id"] = version["id"]
    profile["published_version_id"] = version["id"]
    return {"profile": profile, "version": version}


def _validate_capture_matches_run(run: dict[str, Any], raw_capture: dict[str, Any]) -> None:
    if not isinstance(raw_capture, dict):
        raise CategoryProfileConflict("The category capture is incomplete")
    expected = {
        "platform": str(run.get("platform") or "").strip().lower(),
        "country": str(run.get("country") or "").strip().upper(),
        "store_id": str(run.get("store_id") or "").strip(),
        "category_id": str(run.get("marketplace_category_id") or "").strip(),
    }
    actual = {
        "platform": str(raw_capture.get("platform") or "").strip().lower(),
        "country": str(raw_capture.get("country") or "").strip().upper(),
        "store_id": str(raw_capture.get("store_id") or raw_capture.get("mabang_store_id") or "").strip(),
        "category_id": str(raw_capture.get("marketplace_category_id") or "").strip(),
        "observed_category_id": str(raw_capture.get("observed_category_id") or "").strip(),
    }
    mismatches = []
    for key in ("platform", "country", "category_id"):
        if not expected[key] or actual[key] != expected[key]:
            mismatches.append(key)
    if expected["store_id"] and actual["store_id"] != expected["store_id"]:
        mismatches.append("store_id")
    if actual["observed_category_id"] != expected["category_id"]:
        mismatches.append("observed_category_id")
    if mismatches:
        raise CategoryProfileConflict(f"Category capture does not match its collection run: {', '.join(mismatches)}")


def classify_profile_diff(previous: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    previous_fields = {item.get("field_id"): normalize_field_definition(item) for item in previous.get("fields") or []}
    candidate_fields = {item.get("field_id"): normalize_field_definition(item) for item in candidate.get("fields") or []}
    items: list[dict[str, Any]] = []

    for field_id in sorted(candidate_fields.keys() - previous_fields.keys()):
        definition = candidate_fields[field_id]
        severity = "requires_review" if definition.get("required") or definition.get("required_when") else "non_breaking"
        if definition.get("identity_confidence") == "low":
            severity = "blocking"
        items.append({"change": "field_added", "field_id": field_id, "severity": severity})

    for field_id in sorted(previous_fields.keys() - candidate_fields.keys()):
        items.append({"change": "field_removed", "field_id": field_id, "severity": "requires_review"})

    for field_id in sorted(previous_fields.keys() & candidate_fields.keys()):
        before = previous_fields[field_id]
        after = candidate_fields[field_id]
        if before["type"] != after["type"]:
            items.append({"change": "field_type_changed", "field_id": field_id, "severity": "requires_review"})
        if before["required"] != after["required"] or before.get("required_when") != after.get("required_when"):
            items.append({"change": "required_rule_changed", "field_id": field_id, "severity": "requires_review"})
        before_options = {item["value"] for item in before.get("options") or []}
        after_options = {item["value"] for item in after.get("options") or []}
        if before_options - after_options:
            items.append({"change": "options_removed", "field_id": field_id, "severity": "requires_review"})
        elif after_options - before_options:
            items.append({"change": "options_added", "field_id": field_id, "severity": "non_breaking"})
        if after.get("identity_confidence") == "low":
            items.append({"change": "field_identity_uncertain", "field_id": field_id, "severity": "blocking"})

    if previous.get("variation") and previous.get("variation") != candidate.get("variation"):
        items.append({"change": "variation_changed", "field_id": "", "severity": "requires_review"})

    severity = _highest_severity(item["severity"] for item in items)
    return {"severity": severity, "items": items}


def publish_version(
    data: dict[str, Any],
    version_id: str,
    *,
    reviewer: str,
    note: str,
    now: str | None = None,
) -> dict[str, Any]:
    version = _find_version(data, version_id)
    if version.get("status") == "active":
        return version
    if version.get("status") == "published":
        return version
    if version.get("status") != "candidate":
        raise CategoryProfileConflict("Only a candidate version can be published")
    if not str(reviewer or "").strip() or not str(note or "").strip():
        raise CategoryProfileConflict("Reviewer and review note are required")
    validation = validate_profile_version(version)
    blocking_diff = (version.get("diff") or {}).get("severity") == "blocking"
    if validation.blocking_errors or blocking_diff:
        raise CategoryProfileConflict("The candidate has blocking rule errors")

    profile = _find_profile(data, str(version.get("profile_id") or ""))
    timestamp = now or _utc_now()
    for existing in data.setdefault("category_profile_versions", []):
        if existing is version or existing.get("profile_id") != version.get("profile_id"):
            continue
        if existing.get("status") == "published":
            existing["status"] = "superseded"
            existing["updated_at"] = timestamp
    version["status"] = "published"
    version["published_at"] = timestamp
    version["updated_at"] = timestamp
    version["review"] = {"reviewed_by": str(reviewer).strip(), "reviewed_at": timestamp, "note": str(note).strip()}
    profile["published_version_id"] = version["id"]
    profile["updated_at"] = timestamp
    return version


def reject_version(
    data: dict[str, Any],
    version_id: str,
    *,
    reviewer: str,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    version = _find_version(data, version_id)
    if version.get("status") != "candidate":
        raise CategoryProfileConflict("Only a candidate version can be rejected")
    if not str(reviewer or "").strip() or not str(reason or "").strip():
        raise CategoryProfileConflict("Reviewer and rejection reason are required")
    timestamp = now or _utc_now()
    version["status"] = "rejected"
    version["updated_at"] = timestamp
    version["review"] = {"reviewed_by": str(reviewer).strip(), "reviewed_at": timestamp, "note": str(reason).strip()}
    return version


def _find_version(data: dict[str, Any], version_id: str) -> dict[str, Any]:
    version = next((item for item in data.get("category_profile_versions", []) if item.get("id") == version_id), None)
    if version is None:
        raise CategoryProfileNotFound(f"Category profile version not found: {version_id}")
    return version


def _find_profile(data: dict[str, Any], profile_id: str) -> dict[str, Any]:
    profile = next((item for item in data.get("category_profiles", []) if item.get("id") == profile_id), None)
    if profile is None:
        raise CategoryProfileNotFound(f"Category profile not found: {profile_id}")
    return profile


def _published_version(data: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    published_id = profile.get("active_version_id") or profile.get("published_version_id")
    return next((item for item in data.get("category_profile_versions", []) if item.get("id") == published_id), None)


def _active_version(data: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    return _published_version(data, profile)


def _profile_id(platform: str, country: str, category_id: str) -> str:
    raw = f"{platform}-{country}-{category_id}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def _schema_hash(fields: list[dict[str, Any]], variation: Any) -> str:
    payload = {"fields": fields, "variation": _normalize_variation(variation)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_variation(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "required": bool(value.get("required")),
        "axes": [str(item) for item in value.get("axes") or [] if str(item).strip()],
        "allowed_axes": [str(item) for item in value.get("allowed_axes") or value.get("axes") or [] if str(item).strip()],
    }


def _highest_severity(values) -> str:
    order = {"none": 0, "non_breaking": 1, "requires_review": 2, "blocking": 3}
    return max(values, key=lambda value: order.get(value, 0), default="none")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
