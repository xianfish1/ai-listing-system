from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.browser_use_runner import PROJECT_ROOT, _resolve_node_executable
from services.api.subprocess_utils import no_console_window_kwargs

AUTO_LOGIN_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "mabang_auto_login.cjs"
RESULT_MARKER = "MABANG_LOGIN_RESULT "
ACCOUNT_ENV = "MABANG_ACCOUNT"
PASSWORD_ENV = "MABANG_PASSWORD"


def _node_environment() -> dict[str, str]:
    """Same NODE_PATH discovery as run_playwright_save_draft so `require('playwright')`
    resolves under the pnpm node_modules layout."""

    node_env = os.environ.copy()
    node_modules = (PROJECT_ROOT / "node_modules").resolve()
    configured_module_dir = str(node_env.get("PLAYWRIGHT_NODE_MODULE_DIR") or "").strip()
    module_paths = [
        *([Path(configured_module_dir)] if configured_module_dir else []),
        node_modules / ".pnpm" / "node_modules",
        node_modules,
    ]
    discovered = [str(path) for path in module_paths if str(path) and path.is_dir()]
    inherited_node_path = str(node_env.get("NODE_PATH") or "").strip()
    if inherited_node_path:
        discovered.append(inherited_node_path)
    if discovered:
        node_env["NODE_PATH"] = os.pathsep.join(dict.fromkeys(discovered))
    return node_env


def _parse_login_result(output: str) -> dict[str, Any] | None:
    for line in reversed((output or "").splitlines()):
        if not line.startswith(RESULT_MARKER):
            continue
        try:
            decoded = json.loads(line[len(RESULT_MARKER) :])
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def run_mabang_auto_login(
    cdp_url: str,
    *,
    account: str,
    password: str,
    node_executable: str = "node",
    popen_fn: Callable[..., Any] = subprocess.run,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Fill the Mabang login form with the bound credentials via the CDP helper.

    Credentials travel through the child process environment only (never argv),
    and the result is parsed from the MABANG_LOGIN_RESULT marker; the raw child
    output is never echoed back so the password cannot leak through it.
    """

    if not account or not password:
        return {"ok": False, "status": "no_credentials", "error": "missing_login_environment"}
    if not AUTO_LOGIN_SCRIPT_PATH.is_file():
        return {"ok": False, "status": "failed", "error": "自动登录脚本缺失"}

    node_env = _node_environment()
    node_env[ACCOUNT_ENV] = str(account)
    node_env[PASSWORD_ENV] = str(password)
    node_cmd, node_version = _resolve_node_executable(node_executable, node_env)

    command = [node_cmd, str(AUTO_LOGIN_SCRIPT_PATH), "--cdp-url", cdp_url]
    try:
        completed = popen_fn(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=node_env,
            timeout=timeout_seconds,
            check=False,
            **no_console_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "failed", "error": "自动登录执行超时"}
    except OSError as exc:
        return {"ok": False, "status": "failed", "error": f"无法启动 Node：{exc}"}

    output = (completed.stdout or "") + (completed.stderr or "")
    payload = _parse_login_result(output)
    if payload is None:
        return {
            "ok": False,
            "status": "failed",
            "exit_code": completed.returncode,
            "raw_output_tail": (output or "").strip()[-2000:],
            "error": "自动登录脚本未返回结构化结果",
        }
    payload.pop("account", None)
    payload.pop("password", None)
    payload.setdefault("exit_code", completed.returncode)
    if node_version:
        payload.setdefault("node_version", node_version)
    return payload
