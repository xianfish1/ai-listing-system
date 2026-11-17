"""一次性迁移：生产 CDP 端口基址 9223 → 18223（账号端口 + 旧 worker.cdp_url）。

背景：9222/9223 是 Chrome 远程调试最常被其它工具/残留实例占用的端口。
2026-11-17 起生产基址改为 18223，测试固定 19223 段（两者不重叠）。
只改端口，不动 profile_dir，所以马帮登录态（cookie）保持不变。

用法（先停 API，避免两个进程同时写 store.json）：
    python scripts/migrate_cdp_port_base.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.api import storage  # noqa: E402

OLD_BASE = 9223
NEW_BASE = 18223
TEST_BASE = 19223


def _migrated_port(port: int) -> int:
    """只迁移老端口段 [9223, 18223)：新基址、自定义端口与测试端口段一律不动。

    上界是 NEW_BASE 而不是 TEST_BASE：18223/18224 已经是迁移后的端口，落在
    [18223, 19223) 里；若按 TEST_BASE 判，重复执行会把 18223 再迁成 27223。
    """

    if OLD_BASE <= port < NEW_BASE:
        return port - OLD_BASE + NEW_BASE
    return port


def _migrated_url(url: str) -> str:
    text = str(url or "").strip()
    prefix = "http://127.0.0.1:"
    if text.startswith(prefix):
        try:
            port = int(text[len(prefix):])
        except ValueError:
            return text
        migrated = _migrated_port(port)
        return f"{prefix}{migrated}" if migrated != port else text
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    accounts = storage.list_mabang_accounts()
    plan: list[tuple[str, str, int, int]] = []
    for account in accounts:
        old_port = int(account.get("cdp_port") or 0)
        new_port = _migrated_port(old_port)
        if new_port != old_port:
            plan.append((str(account.get("id") or ""), str(account.get("name") or ""), old_port, new_port))

    config = storage.get_runtime_configuration()
    old_url = str((config.get("worker") or {}).get("cdp_url") or "")
    new_url = _migrated_url(old_url)

    for account_id, name, old_port, new_port in plan:
        print(f"账号 {name} ({account_id}): {old_port} -> {new_port}")
    print(f"worker.cdp_url: {old_url or '(空)'} -> {new_url or '(空)'}")
    if args.dry_run:
        print("dry-run：未写入")
        return 0
    if not plan and new_url == old_url:
        print("没有需要迁移的端口")
        return 0

    for account_id, name, _old_port, new_port in plan:
        storage.update_mabang_account(account_id, {"cdp_port": new_port})
    if new_url != old_url:
        storage.save_runtime_configuration({"worker": {"cdp_url": new_url}})
    print("迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
