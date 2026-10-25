from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any


POLLUTED_FIELD_IDS = ("尺码图表:上传图片选择模板", "变种属性:")


class MabangPageImportRepairError(ValueError):
    pass


def repair_import_pollution(
    data: dict[str, Any],
    *,
    store_id: str,
    category_id: str,
    snapshot: dict[str, Any],
    account_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_snapshot(snapshot, store_id=store_id, category_id=category_id)
    repaired = deepcopy(data)
    for key in ("mabang_store_options", "category_profiles", "category_profile_versions", "store_templates", "store_template_versions"):
        repaired.setdefault(key, [])
    store = snapshot["store"]
    category = snapshot["category"]
    payload = _template_values(snapshot)
    changed = False
    changed |= _upsert_store_option(repaired, store, account_id=account_id)
    changed |= _repair_profiles(repaired, store=store, category=category, snapshot=snapshot)
    changed |= _repair_templates(repaired, store_id=store_id, category_id=category_id, payload=payload)
    return repaired, {
        "changed": changed,
        "store_id": store_id,
        "category_id": category_id,
        "removed_field_ids": list(POLLUTED_FIELD_IDS) if changed else [],
    }


def _validate_snapshot(snapshot: dict[str, Any], *, store_id: str, category_id: str) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") not in {1, 2, 3}:
        raise MabangPageImportRepairError("缺少可信快照")
    store = snapshot.get("store") if isinstance(snapshot.get("store"), dict) else {}
    category = snapshot.get("category") if isinstance(snapshot.get("category"), dict) else {}
    if str(store.get("id") or "") != str(store_id) or str(category.get("id") or "") != str(category_id):
        raise MabangPageImportRepairError("可信快照与目标店铺或类目不匹配")


def _upsert_store_option(data: dict[str, Any], store: dict[str, Any], *, account_id: str = "") -> bool:
    options = data["mabang_store_options"]
    wanted_account = str(account_id or store.get("account_id") or "").strip()
    existing = next(
        (
            item
            for item in options
            if str(item.get("account_id") or "").strip() == wanted_account
            and item.get("mabang_store_id") == store["id"]
            and item.get("platform") == store["platform"]
        ),
        None,
    )
    value = {
        "id": (existing or {}).get("id") or str(uuid.uuid4()), "platform": store["platform"], "country": store["country"],
        "store_name": store["name"], "mabang_store_id": store["id"], "account_id": wanted_account,
        "source": "mabang_page_import_repair", "status": "active",
    }
    if existing is None:
        options.append(value)
        return True
    if all(existing.get(key) == value[key] for key in value if key != "id"):
        return False
    existing.update(value)
    return True


def _repair_profiles(data: dict[str, Any], *, store: dict[str, Any], category: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    profile_id = f"{store['platform']}-{store['country'].lower()}-{category['id']}"
    profiles = data["category_profiles"]
    profile = next((item for item in profiles if item.get("id") == profile_id), None)
    if profile is None:
        profile = {"id": profile_id, "platform": store["platform"], "country": store["country"], "marketplace_category_id": category["id"], "marketplace_category_path": category["path"]}
        profiles.append(profile)
    fields = deepcopy(snapshot.get("category_fields") or [])
    current = next((item for item in data["category_profile_versions"] if item.get("id") == profile.get("active_version_id")), None)
    if current and current.get("fields") == fields:
        return False
    for version in data["category_profile_versions"]:
        if version.get("profile_id") == profile_id and version.get("status") in {"active", "published"}:
            version["status"] = "superseded"
    next_version = 1 + max((int(item.get("version") or 0) for item in data["category_profile_versions"] if item.get("profile_id") == profile_id), default=0)
    version = {"id": str(uuid.uuid4()), "profile_id": profile_id, "version": next_version, "status": "active", "fields": fields, "variation": deepcopy(snapshot.get("variation") or {}), "source": {"type": "mabang_page_import_repair"}}
    data["category_profile_versions"].append(version)
    profile.update({"active_version_id": version["id"], "published_version_id": version["id"], "marketplace_category_path": category["path"], "status": "active"})
    return True


def _repair_templates(data: dict[str, Any], *, store_id: str, category_id: str, payload: dict[str, Any]) -> bool:
    changed = False
    for template in data["store_templates"]:
        if template.get("mabang_store_id") != store_id or template.get("mabang_category_id") != category_id:
            continue
        desired = {"attribute_values": payload["attribute_values"], "category_attributes": payload["attribute_values"], "sizes": payload["sizes"]}
        if all(template.get(key) == value for key, value in desired.items()):
            continue
        template.update(deepcopy(desired))
        template["version"] = int(template.get("version") or 1) + 1
        template["current_version_id"] = str(uuid.uuid4())
        data["store_template_versions"].append({"id": template["current_version_id"], "template_id": template["id"], "version": template["version"], "snapshot": deepcopy(template)})
        changed = True
    return changed


def _template_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    selected = snapshot.get("selected_attributes") or {}
    return {
        "attribute_values": {field_id: deepcopy(field.get("value")) for field_id, field in selected.items() if isinstance(field, dict)},
        "sizes": list((snapshot.get("variation") or {}).get("sizes") or []),
    }
