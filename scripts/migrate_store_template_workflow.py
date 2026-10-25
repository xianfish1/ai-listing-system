from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKFLOW_COLLECTIONS = (
    "ai_model_connections",
    "description_generation_profiles",
    "store_template_versions",
    "store_promotion_activities",
    "store_promotion_catalog_runs",
    "mabang_page_import_runs",
    "listing_copy_revisions",
)


def migrate_store(data: dict[str, Any], *, now: str) -> dict[str, int]:
    for collection in WORKFLOW_COLLECTIONS:
        data.setdefault(collection, [])

    result = {
        "templates_needing_import": 0,
        "jobs_needing_regeneration": 0,
    }
    for profile in data.setdefault("category_profiles", []):
        if not profile.get("active_version_id") and profile.get("published_version_id"):
            profile["active_version_id"] = profile["published_version_id"]
    for version in data.setdefault("category_profile_versions", []):
        if version.get("status") == "published":
            version["status"] = "active"

    for template in data.setdefault("store_templates", []):
        if (
            not template.get("category_profile_version_id")
            and template.get("readiness_status") != "needs_mabang_import"
        ):
            template["readiness_status"] = "needs_mabang_import"
            result["templates_needing_import"] += 1
        template.pop("executor_defaults", None)

    for listing_copy in data.setdefault("store_listing_copies", []):
        listing_copy.setdefault(
            "description_status",
            "ready" if str(listing_copy.get("description") or "").strip() else "pending",
        )

    for job in data.setdefault("automation_jobs", []):
        if job.get("status") in {"ready", "claimed", "running"} and not job.get(
            "execution_snapshot"
        ):
            job["status"] = "needs_regeneration"
            job["updated_at"] = now
            result["jobs_needing_regeneration"] += 1
    return result


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".workflow.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the store-template workflow data")
    parser.add_argument("--data-dir", default="data")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    store_path = Path(args.data_dir).resolve() / "store.json"
    if not store_path.is_file():
        parser.error(f"Store file not found: {store_path}")
    data = json.loads(store_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    result = migrate_store(data, now=now)

    backup_path = ""
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = store_path.with_name(f"store.workflow-backup-{stamp}.json")
        shutil.copy2(store_path, backup)
        _write_atomic(store_path, data)
        backup_path = str(backup)

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "apply" if args.apply else "dry-run",
                "store_path": str(store_path),
                "backup_path": backup_path,
                **result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
