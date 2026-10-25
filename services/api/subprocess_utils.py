"""Cross-platform subprocess launch options used by background services."""

from __future__ import annotations

import os
import subprocess
from typing import Any


def no_console_window_kwargs() -> dict[str, Any]:
    """Prevent console executables from opening a window under Windows GUI hosts.

    现场反馈（2026-09-28）：导入文件、生成自动化、自动化接任务时会闪 cmd 黑框。
    这里同时给 ``creationflags=CREATE_NO_WINDOW`` 和 ``startupinfo(SW_HIDE)``：
    前者对控制台程序有效，后者兜底（某些包装/转发进程只看 startupinfo）。
    """

    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flag:
        kwargs["creationflags"] = flag
    try:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startup
    except Exception:  # noqa: BLE001 - 拿不到就只靠 creationflags
        pass
    return kwargs
