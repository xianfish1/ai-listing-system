from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import storage


UPLOAD_TYPES = {"main", "model", "variant", "size_chart", "detail", "video"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify worker access to NAS upload assets")
    parser.add_argument("--group-id", default="", help="Limit check to one group, for example AL0003")
    parser.add_argument("--limit", type=int, default=50, help="Maximum upload files to check")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("ASSET_ACCESS_TIMEOUT_SECONDS", "3") or 3))
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    return parser.parse_args()


def _is_file_readable(file_path: str, timeout_seconds: float) -> dict:
    if not file_path:
        return {"ok": False, "reason": "empty_path", "timed_out": False}
    path = Path(file_path)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    last_reason = "not_found"
    while time.monotonic() < deadline:
        try:
            if path.is_file():
                with path.open("rb") as handle:
                    handle.read(1)
                return {"ok": True, "reason": "", "timed_out": False}
            last_reason = "not_found"
        except OSError as exc:
            last_reason = str(exc)
        time.sleep(0.05)
    return {"ok": False, "reason": last_reason, "timed_out": True}


def main() -> None:
    args = parse_args()
    group_key = args.group_id.strip().upper()
    assets = storage.list_items("assets")
    if group_key:
        assets = [asset for asset in assets if (asset.get("group_id") or "").strip().upper() == group_key]
    upload_assets = [asset for asset in assets if (asset.get("asset_type") or "") in UPLOAD_TYPES]
    upload_assets = upload_assets[: max(0, args.limit)]

    checked = []
    missing = []
    for asset in upload_assets:
        file_path = asset.get("file_path") or ""
        result = _is_file_readable(file_path, args.timeout)
        item = {
            "group_id": asset.get("group_id") or "",
            "asset_type": asset.get("asset_type") or "",
            "file_name": asset.get("file_name") or "",
            "file_path": file_path,
            "ok": bool(result.get("ok")),
            "reason": "" if result.get("ok") else (result.get("reason") or "not_found"),
            "timed_out": bool(result.get("timed_out")),
        }
        checked.append(item)
        if not item["ok"]:
            missing.append(item)

    result = {
        "ok": not missing,
        "group_id": group_key,
        "checked_count": len(checked),
        "missing_count": len(missing),
        "timed_out_count": len([item for item in missing if item["timed_out"]]),
        "timeout_seconds": args.timeout,
        "missing": missing,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(
        "nas_access "
        f"ok={result['ok']} checked={result['checked_count']} "
        f"missing={result['missing_count']} timed_out={result['timed_out_count']} "
        f"timeout={result['timeout_seconds']}s"
    )
    for item in missing[:20]:
        print(f"- {item['group_id']} {item['asset_type']} {item['file_name']} :: {item['reason']}")
    if len(missing) > 20:
        print(f"... {len(missing) - 20} more")
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
