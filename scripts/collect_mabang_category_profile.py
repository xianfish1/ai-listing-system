from __future__ import annotations

import argparse
from services.api.subprocess_utils import no_console_window_kwargs
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.browser_use_runner import build_category_collection_audit_script, find_browser_use_exe


def normalize_capture(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Capture must be an object")
    required = ["platform", "country", "store_id", "marketplace_category_id", "observed_category_id", "fields"]
    missing = [key for key in required if not str(value.get(key) or "").strip() and key != "fields"]
    if missing or not isinstance(value.get("fields"), list) or not value["fields"]:
        raise ValueError(f"Capture is incomplete: {', '.join(missing or ['fields'])}")
    fields = []
    seen = set()
    for raw in value["fields"]:
        if not isinstance(raw, dict):
            continue
        field_id = str(raw.get("field_id") or raw.get("name") or "").strip()
        label = str(raw.get("label") or field_id).strip()
        if not field_id or not label or field_id in seen:
            continue
        seen.add(field_id)
        fields.append({**raw, "field_id": field_id, "label": label})
    if not fields:
        raise ValueError("Capture contains no identifiable fields")
    return {**value, "fields": fields}


def run_browser_collection(plan: dict[str, Any], *, timeout_seconds: int = 180) -> dict[str, Any]:
    exe = find_browser_use_exe()
    if not exe:
        return {"ok": False, "status": "blocked", "error": "browser-use.exe not found in .venv-browser-use"}
    script = build_category_collection_audit_script(plan)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=str(Path(__file__).resolve().parents[1]),
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "Browser-Use category collection timed out"}
    output = (result.stdout or "") + (result.stderr or "")
    marker = "CATEGORY_COLLECTION_RESULT "
    capture = None
    for line in output.splitlines():
        if line.startswith(marker):
            capture = json.loads(line[len(marker):]).get("capture")
            break
    if result.returncode != 0 or not isinstance(capture, dict):
        return {"ok": False, "status": "failed", "error": "Category collection did not return a capture", "output": output}
    try:
        normalized = normalize_capture(capture)
    except ValueError as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "output": output}
    return {"ok": True, "status": "completed", "capture": normalized, "output": output}


def submit_capture(base_url: str, run_id: str, capture: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"raw_capture": capture}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}/api/category-profile-collection-runs/{run_id}/complete",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Mabang category fields without saving a product")
    parser.add_argument("--platform", default="shopee")
    parser.add_argument("--country", required=True)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--category-id", required=True)
    parser.add_argument("--category-path", default="")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8791")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--input-json", help="Use a saved Browser-Use result instead of launching Browser-Use")
    args = parser.parse_args(argv)

    plan = {
        "platform": args.platform,
        "country": args.country,
        "store_id": args.store_id,
        "category_id": args.category_id,
        "category_path": args.category_path,
        "display_name": args.display_name,
    }
    if args.input_json:
        raw = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        result = {"ok": True, "status": "fixture", "capture": normalize_capture(raw.get("capture") or raw)}
    else:
        result = run_browser_collection(plan)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result["capture"], ensure_ascii=False, indent=2), encoding="utf-8")
    if args.run_id:
        result["submitted"] = submit_capture(args.api_url, args.run_id, result["capture"])
    print(json.dumps({"ok": True, "output": str(output_path), "submitted": result.get("submitted")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
