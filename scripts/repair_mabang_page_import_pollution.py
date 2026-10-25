from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import storage
from services.api.mabang_page_import_repair import MabangPageImportRepairError, repair_import_pollution


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair polluted Mabang page-import data from a trusted snapshot")
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--category-id", required=True)
    parser.add_argument("--snapshot-run-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _snapshot(data: dict, run_id: str) -> dict:
    run = next((item for item in data.get("mabang_page_import_runs", []) if item.get("id") == run_id), None)
    snapshot = (run or {}).get("snapshot")
    if not isinstance(snapshot, dict):
        raise MabangPageImportRepairError("指定导入任务没有可信快照")
    return snapshot


def _backup(data: dict) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = storage.settings.DATA_DIR / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mabang-import-repair-{timestamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    with storage.locked_store() as (data, save):
        snapshot = _snapshot(data, args.snapshot_run_id)
        repaired, report = repair_import_pollution(
            data,
            store_id=args.store_id,
            category_id=args.category_id,
            snapshot=snapshot,
        )
        if args.dry_run:
            print(json.dumps({"mode": "dry-run", **report}, ensure_ascii=False))
            return 0
        backup = _backup(deepcopy(data))
        if report["changed"]:
            data.clear()
            data.update(repaired)
            save(data)
        print(json.dumps({"mode": "apply", "backup": str(backup), **report}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
