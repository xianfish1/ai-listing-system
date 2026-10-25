from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import browser_use_runner


API_BASE_URL = "http://127.0.0.1:8787"
CHROME_CDP_URL = "http://127.0.0.1:9223"

SAFE_PHASES = [
    "api_health",
    "chrome_cdp_health",
    "browser_use_doctor",
    "job_dry_run",
    "login_recovery",
    "open_workspace",
    "open_local_pending",
    "open_or_create_detail_draft",
    "iframe_safe_audit",
    "iframe_safe_fill",
    "operator_review",
]

MODE_PHASES = {
    "health": ["api_health", "chrome_cdp_health", "browser_use_doctor"],
    "audit": [
        "api_health",
        "chrome_cdp_health",
        "browser_use_doctor",
        "job_dry_run",
        "login_recovery",
        "open_workspace",
        "open_local_pending",
        "open_or_create_detail_draft",
        "iframe_safe_audit",
    ],
    "fill": SAFE_PHASES,
}


def build_self_run_plan(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "mode": "guarded_self_run",
        "phases": [
            {"name": name, "can_save": False, "can_publish": False}
            for name in SAFE_PHASES
        ],
    }


def build_health_summary(*, api_ok: bool, cdp_ok: bool, browser_use_ok: bool) -> dict[str, Any]:
    if not api_ok:
        return {"ok": False, "status": "blocked", "next_step": "start_local_api"}
    if not cdp_ok:
        return {"ok": False, "status": "blocked", "next_step": "start_dedicated_chrome_cdp_9223"}
    if not browser_use_ok:
        return {"ok": False, "status": "blocked", "next_step": "restart_browser_use_daemon"}
    return {"ok": True, "status": "ready", "next_step": "run_job_dry_run"}


def check_api_health(base_url: str = API_BASE_URL) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/health"
    try:
        with request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "status": "blocked",
            "phase": "api_health",
            "next_step": "start_local_api",
            "error": str(exc),
        }
    return {
        "ok": bool(payload.get("ok")),
        "status": "completed" if payload.get("ok") else "blocked",
        "phase": "api_health",
        "summary": payload,
        "next_step": "" if payload.get("ok") else "inspect_local_api",
    }


def check_chrome_cdp(cdp_url: str = CHROME_CDP_URL) -> dict[str, Any]:
    url = cdp_url.rstrip("/") + "/json/version"
    try:
        with request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "status": "blocked",
            "phase": "chrome_cdp_health",
            "next_step": "start_dedicated_chrome_cdp_9223",
            "error": str(exc),
        }
    return {
        "ok": bool(payload.get("webSocketDebuggerUrl") or payload.get("Browser")),
        "status": "completed",
        "phase": "chrome_cdp_health",
        "summary": {
            "browser": payload.get("Browser") or "",
            "web_socket_debugger_url_present": bool(payload.get("webSocketDebuggerUrl")),
        },
    }


def run_phase(phase: dict[str, Any], job_id: str) -> dict[str, Any]:
    name = phase.get("name") or ""
    if name == "api_health":
        return check_api_health()
    if name == "chrome_cdp_health":
        return check_chrome_cdp()
    if name == "browser_use_doctor":
        doctor = browser_use_runner.run_doctor()
        return {
            "ok": bool(doctor.get("ok")),
            "status": "completed" if doctor.get("ok") else "blocked",
            "phase": name,
            "summary": doctor,
            "next_step": doctor.get("next_step") or "",
        }
    if name == "job_dry_run":
        result = browser_use_runner.run_job(job_id)
        dry_run = result.get("dry_run") or {}
        return {
            "ok": bool(dry_run.get("ok")),
            "status": "completed" if dry_run.get("ok") else "blocked",
            "phase": name,
            "summary": dry_run,
            "next_step": dry_run.get("next_step") or "",
        }
    if name in {"login_recovery", "open_workspace", "open_local_pending", "open_or_create_detail_draft"}:
        return {
            "ok": True,
            "status": "skipped",
            "phase": name,
            "summary": {
                "reason": "phase_script_pending",
                "safe": True,
            },
            "next_step": "run_iframe_safe_audit" if name == "open_or_create_detail_draft" else "",
        }
    if name == "iframe_safe_audit":
        result = browser_use_runner.run_job(job_id, execute_safe_audit=True)
        audit = result.get("safe_audit") or {}
        return {
            "ok": bool(audit.get("ok")),
            "status": "completed" if audit.get("ok") else "blocked",
            "phase": name,
            "summary": audit,
            "next_step": audit.get("next_step") or "",
        }
    if name == "iframe_safe_fill":
        result = browser_use_runner.run_job(job_id, execute_safe_fill=True)
        fill = result.get("safe_fill") or {}
        return {
            "ok": bool(fill.get("ok")),
            "status": "completed" if fill.get("ok") else "blocked",
            "phase": name,
            "summary": fill,
            "next_step": fill.get("next_step") or "",
        }
    if name == "operator_review":
        return {
            "ok": True,
            "status": "stop_for_operator_review",
            "phase": name,
            "summary": {"save_clicked": False, "publish_clicked": False},
        }
    return {"ok": False, "status": "blocked", "phase": name, "next_step": "unknown_phase"}


def run_self_listing(job_id: str, mode: str) -> dict[str, Any]:
    plan = build_self_run_plan(job_id)
    selected = set(MODE_PHASES[mode])
    results = []
    status = "completed"
    for phase in plan["phases"]:
        if phase["name"] not in selected:
            continue
        result = run_phase(phase, job_id)
        results.append(result)
        if not result.get("ok"):
            status = "blocked"
            break
    return {
        "ok": status != "blocked",
        "status": status,
        "job_id": job_id,
        "mode": mode,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run guarded self-listing phases for a Browser-Use automation job.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--mode", choices=sorted(MODE_PHASES), default="audit")
    args = parser.parse_args(argv)

    result = run_self_listing(args.job_id, args.mode)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
