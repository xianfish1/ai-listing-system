"""测试会话级隔离。

两件事：

1. **CDP 端口换段**：生产用 9223 起（`storage.MABANG_CDP_PORT_BASE`）。测试统一
   用 19223 段，pytest 里被触发的浏览器/Worker 永远不会占用正在运行的生产端口
   —— 2026-11-17 现场就是测试残留 Chrome 霸占 9223，导致 Worker 连不上浏览器。
2. **兜底清理**：会话结束时只结束 `--user-data-dir` 位于 pytest 临时目录下的
   Chrome（绝不碰生产 profile），防止某次测试异常退出留下僵尸浏览器。
"""

from __future__ import annotations

import os
import subprocess

import pytest

# 必须在 services.api.storage 导入前设置：端口基址是模块导入时求值的常量。
os.environ.setdefault("AI_LISTING_CDP_PORT_BASE", "19223")

TEST_CDP_PORT_BASE = 19223


@pytest.fixture(scope="session", autouse=True)
def _isolate_cdp_port_base():
    """双保险：即使 storage 已被更早导入，也把模块常量改成测试端口段。"""

    from services.api import storage

    previous = storage.MABANG_CDP_PORT_BASE
    storage.MABANG_CDP_PORT_BASE = TEST_CDP_PORT_BASE
    yield
    storage.MABANG_CDP_PORT_BASE = previous


def _kill_pytest_chrome() -> None:
    if os.name != "nt":
        return
    from services.api.subprocess_utils import no_console_window_kwargs

    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like '*pytest-of-*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            timeout=20,
            **no_console_window_kwargs(),
        )
    except Exception:  # noqa: BLE001 - 清理是兜底，失败不能影响测试结果
        pass


@pytest.fixture(scope="session", autouse=True)
def _cleanup_pytest_browsers():
    yield
    _kill_pytest_chrome()
