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


def migrate_store(data: dict[str, Any], *, now: str, dry_run: bool) -> dict[str, Any]:
    working = deepcopy(data)
    preview = {
        "templates_updated": 0,
        "base_products_updated": 0,
        "listing_copies_updated": 0,
        "jobs_preserved": len(working.get("automation_jobs") or []),
    }
    for template in working.setdefault("store_templates", []):
        changed = False
        for key, value in {
            "video_enabled": True,
            "main_image_watermark": {},
            "product_image_watermark": {},
        }.items():
            if key not in template:
                template[key] = deepcopy(value)
                changed = True
        if "size_chart_source" not in template:
            template["size_chart_source"] = "upload" if _has_size_chart(template) else "upload"
            changed = True
        if changed:
            template.setdefault("updated_at", now)
            preview["templates_updated"] += 1

    for product in working.setdefault("base_products", []):
        if "video" not in product:
            product["video"] = _missing_video()
            product.setdefault("updated_at", now)
            preview["base_products_updated"] += 1

    for listing_copy in working.setdefault("store_listing_copies", []):
        changed = False
        if "video" not in listing_copy:
            listing_copy["video"] = _missing_video()
            changed = True
        if "size_chart_snapshot" not in listing_copy:
            listing_copy["size_chart_snapshot"] = {
                "asset_id": str(listing_copy.get("size_chart_asset_id") or ""),
                "source": "upload" if _has_size_chart(listing_copy) else "",
                "path": str(listing_copy.get("size_chart_image_path") or ""),
            }
            changed = True
        if listing_copy.pop("promotion_activity_expired", None) is not None:
            listing_copy["promotion_resolution_status"] = "unresolved"
            changed = True
        elif listing_copy.get("promotion_activity_id") and "promotion_resolution_status" not in listing_copy:
            listing_copy["promotion_resolution_status"] = "unresolved"
            changed = True
        if changed:
            listing_copy.setdefault("updated_at", now)
            preview["listing_copies_updated"] += 1

    return {"mode": "dry-run" if dry_run else "apply", "preview": preview, "data": working}


def _has_size_chart(item: dict[str, Any]) -> bool:
    return any(item.get(key) for key in ("size_chart_asset_id", "size_chart_image", "size_chart_image_path"))


def _missing_video() -> dict[str, Any]:
    return {"validation_status": "missing", "validation_errors": ["video_missing"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate store configuration and media fields")
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
    now = datetime.now(timezone.utc).isoformat()
    result = migrate_store(current, now=now, dry_run=args.dry_run)
    backup_path = ""
    if args.apply:
        if settings.STORE_FILE.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = settings.STORE_FILE.with_name(f"store.backup.configuration-media.{stamp}.json")
            shutil.copy2(settings.STORE_FILE, backup)
            backup_path = str(backup)
        storage.save_store(result["data"])
    output = {key: value for key, value in result.items() if key != "data"}
    output["backup_path"] = backup_path
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
