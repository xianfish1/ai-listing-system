from __future__ import annotations

import argparse
from services.api.subprocess_utils import no_console_window_kwargs
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.browser_use_runner import build_category_catalog_collection_audit_script, find_browser_use_exe
from services.api.marketplace_category_catalog import sanitize_source_url


def normalize_capture(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Category catalog capture must be an object")
    capture = value.get("capture") if isinstance(value.get("capture"), dict) else value
    categories = capture.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Category catalog capture contains no categories")
    if not any(_has_category_identity(item) for item in categories):
        raise ValueError("Category catalog capture contains no identifiable categories")
    normalized = dict(capture)
    if normalized.get("source_url") or normalized.get("url"):
        normalized["source_url"] = sanitize_source_url(normalized.get("source_url") or normalized.get("url"))
        normalized.pop("url", None)
    if normalized.get("discovery_source") != "mabang_shopee_getCategory_api":
        raise ValueError("Category catalog capture is not from the authoritative Mabang API")
    return normalized


def run_browser_collection(plan: dict[str, Any], *, timeout_seconds: int = 300) -> dict[str, Any]:
    exe = find_browser_use_exe()
    if not exe:
        return {"ok": False, "status": "blocked", "error": "browser-use.exe not found in .venv-browser-use"}
    script = build_category_catalog_collection_audit_script(plan)
    browser_env = os.environ.copy()
    try:
        from services.api import storage as _storage
        configured_cdp = str((_storage.get_runtime_configuration().get("worker") or {}).get("cdp_url") or "").strip()
    except Exception:
        configured_cdp = ""
    if configured_cdp:
        # Reuse the worker's dedicated Mabang Chrome (remote debugging) session so the
        # collection runs against the logged-in Mabang tab instead of spawning a fresh
        # Chrome that asks for the "Allow remote debugging" permission.
        browser_env.setdefault("BU_CDP_URL", configured_cdp)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=str(PROJECT_ROOT),
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=browser_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "error": "Browser-Use category catalog collection timed out"}
    output = (result.stdout or "") + (result.stderr or "")
    marker = "CATEGORY_CATALOG_RESULT "
    capture = None
    for line in output.splitlines():
        if line.startswith(marker):
            capture = json.loads(line[len(marker) :]).get("capture")
            break
    if result.returncode != 0 or not isinstance(capture, dict):
        return {
            "ok": False,
            "status": "failed",
            "error": "Category catalog collection did not return a capture",
            "output": output,
        }
    try:
        normalized = normalize_capture(capture)
    except ValueError as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "output": output}
    return {"ok": True, "status": "completed", "capture": normalized, "output": output}


def submit_capture(base_url: str, run_id: str, capture: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"raw_capture": capture}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{base_url.rstrip('/')}/api/marketplace-category-catalog-runs/{run_id}/complete",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect the complete Mabang marketplace category catalog read-only")
    parser.add_argument("--platform", default="shopee")
    parser.add_argument("--country", required=True)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8787")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--input-json", help="Use a saved capture instead of launching Browser-Use")
    args = parser.parse_args(argv)

    plan = {"platform": args.platform, "country": args.country, "store_id": args.store_id}
    if args.input_json:
        raw = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        result = {"ok": True, "status": "fixture", "capture": normalize_capture(raw)}
    else:
        result = run_browser_collection(plan)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result["capture"], ensure_ascii=False, indent=2), encoding="utf-8")
    submitted = submit_capture(args.api_url, args.run_id, result["capture"]) if args.run_id else None
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output_path),
                "category_count": result["capture"].get("category_count"),
                "submitted": submitted,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _has_category_identity(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    category_id = item.get("category_id") or item.get("categoryId") or item.get("id") or item.get("value")
    name = item.get("category_name") or item.get("categoryName") or item.get("name") or item.get("label")
    return bool(str(category_id or "").strip() and str(name or "").strip())


if __name__ == "__main__":
    raise SystemExit(main())
