from __future__ import annotations

import argparse
import json
import shutil
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.category_rules import build_profile_snapshot, hash_profile_snapshot


def migrate_store(
    data: dict[str, Any],
    static_profiles: dict[str, dict[str, Any]],
    *,
    now: str | None = None,
) -> dict[str, int]:
    timestamp = now or datetime.now(timezone.utc).isoformat()
    profiles = data.setdefault("category_profiles", [])
    versions = data.setdefault("category_profile_versions", [])
    data.setdefault("category_collection_runs", [])
    report = {
        "profiles_created": 0,
        "versions_created": 0,
        "templates_migrated": 0,
        "listing_copies_migrated": 0,
        "listing_copies_needing_manual_review": 0,
    }

    profile_by_id = {item.get("id"): item for item in profiles}
    version_by_profile: dict[str, dict[str, Any]] = {
        item.get("profile_id"): item
        for item in versions
        if item.get("status") == "published" and item.get("profile_id")
    }

    for legacy_id, raw in static_profiles.items():
        profile_id = str(raw.get("profile_id") or legacy_id).strip()
        version_id = f"legacy-{profile_id}-v1"
        profile = profile_by_id.get(profile_id)
        if profile is None:
            profile = {
                "id": profile_id,
                "platform": str(raw.get("platform") or "shopee").strip().lower(),
                "country": str(raw.get("country") or "").strip().upper(),
                "marketplace_category_id": str(raw.get("mabang_category_id") or "").strip(),
                "marketplace_category_path": str(raw.get("marketplace_category_path") or "").strip(),
                "display_name": str(raw.get("display_name") or raw.get("product_line") or profile_id).strip(),
                "published_version_id": version_id,
                "status": "active",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            profiles.append(profile)
            profile_by_id[profile_id] = profile
            report["profiles_created"] += 1

        version = next((item for item in versions if item.get("id") == version_id), None)
        if version is None:
            fields = _legacy_fields(raw.get("required_attributes"))
            version = {
                "id": version_id,
                "profile_id": profile_id,
                "version": 1,
                "status": "published",
                "source": {
                    "type": "legacy_static_profile",
                    "run_id": "",
                    "page_url": "",
                    "collected_at": timestamp,
                },
                "fields": fields,
                "variation": _legacy_variation(raw.get("variation")),
                "schema_hash": "",
                "review": {
                    "reviewed_by": "migration",
                    "reviewed_at": timestamp,
                    "note": "Migrated from the static CATEGORY_PROFILES constant",
                },
                "created_at": timestamp,
                "published_at": timestamp,
            }
            version["schema_hash"] = hash_profile_snapshot(build_profile_snapshot(profile, version))
            versions.append(version)
            report["versions_created"] += 1
        version_by_profile[profile_id] = version

    for template in data.setdefault("store_templates", []):
        profile_id = str(template.get("category_profile_id") or "").strip()
        version = version_by_profile.get(profile_id)
        if not version or template.get("category_profile_version_id"):
            continue
        template["category_profile_version_id"] = version["id"]
        template["attribute_values"] = deepcopy(template.get("category_attributes") or {})
        report["templates_migrated"] += 1

    for listing_copy in data.setdefault("store_listing_copies", []):
        if listing_copy.get("category_profile_snapshot"):
            continue
        profile_id = str(listing_copy.get("category_profile_id") or "").strip()
        profile = profile_by_id.get(profile_id)
        version = version_by_profile.get(profile_id)
        values = deepcopy(listing_copy.get("attribute_values") or listing_copy.get("category_attributes") or {})
        listing_copy["attribute_values"] = values
        if profile and version:
            snapshot = build_profile_snapshot(profile, version)
            listing_copy["category_profile_version_id"] = version["id"]
        else:
            snapshot = {
                "profile_id": profile_id or "legacy-unknown",
                "platform": str(listing_copy.get("platform") or ""),
                "country": str(listing_copy.get("country") or ""),
                "marketplace_category_id": str(listing_copy.get("mabang_category_id") or ""),
                "marketplace_category_path": str(listing_copy.get("marketplace_category_path") or ""),
                "version_id": "legacy-v1",
                "version": 1,
                "fields": _legacy_fields(values),
                "variation": _legacy_variation(listing_copy.get("variation")),
            }
            listing_copy["status"] = "needs_manual"
            report["listing_copies_needing_manual_review"] += 1
        listing_copy["category_profile_snapshot"] = snapshot
        listing_copy["profile_snapshot_hash"] = hash_profile_snapshot(snapshot)
        report["listing_copies_migrated"] += 1
    return report


def _legacy_fields(value: Any) -> list[dict[str, Any]]:
    values = value if isinstance(value, dict) else {}
    return [
        {
            "field_id": str(field_id),
            "label": str(field_id).replace("_", " ").title(),
            "type": "text",
            "required": True,
            "required_when": None,
            "options": [],
            "default_value": deepcopy(default_value),
            "automation": {"strategy": "label_or_field_id", "page_field_id": str(field_id)},
            "identity_confidence": "legacy",
        }
        for field_id, default_value in sorted(values.items())
        if field_id != "display"
    ]


def _legacy_variation(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    axes = [str(item) for item in value.get("axes") or ["color", "size"] if str(item).strip()]
    return {"type": str(value.get("type") or "variant"), "required": True, "axes": axes, "allowed_axes": list(axes)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate static category profiles to versioned rules")
    parser.add_argument("--data-dir", default="data")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    from services.api import settings, storage

    data_dir = Path(args.data_dir).resolve()
    settings.DATA_DIR = data_dir
    settings.STORAGE_DIR = data_dir / "storage"
    settings.EXPORT_DIR = data_dir / "exports"
    settings.STORE_FILE = data_dir / "store.json"
    current = storage.load_store()
    working = deepcopy(current)
    report = migrate_store(working, storage.CATEGORY_PROFILES)

    if args.apply:
        backup = settings.STORE_FILE.with_name(
            f"{settings.STORE_FILE.stem}.backup.category-profile.{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        if settings.STORE_FILE.exists():
            shutil.copy2(settings.STORE_FILE, backup)
        storage.save_store(working)
        report["backup_created"] = int(backup.exists())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
