"""Start the AI Listing System API on port 8788 without any console window.

Run this file with pythonw.exe (double-clicking a .pyw does that automatically).
pythonw is a GUI-subsystem executable, so Windows never allocates a console for
it -- which is what keeps the window away: on Windows 11 a new console (from a
.bat, or from cmd.exe started by a tool) is handed to Windows Terminal and shows
up as a visible window even when the launcher asked for a hidden one.

Output still goes to data/api-runtime/api.out.log and api.err.log, but through a
size-bounded rotating stream: the operator's UI polls every second, so the access
log reached 49.6 MB in under two hours on 2026-10-13. See services/api/log_files.py
for the threshold (API_LOG_MAX_BYTES / API_LOG_BACKUPS can override it).
"""

import os
import pathlib
import runpy
import sys

ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "api-runtime"
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("API_PORT", "8788")

from services.api.log_files import open_rotating_log  # noqa: E402 - ROOT 必须先上 sys.path

sys.stdout = open_rotating_log(LOG_DIR / "api.out.log")
sys.stderr = open_rotating_log(LOG_DIR / "api.err.log")

# 部署初始化兜底：系统没有 node 时尝试自动下载安装。这里是静默双击入口（无控制台），
# 安装失败不能拦住 API 启动——装不上就维持旧行为，让 worker 报 node 相关错误。
try:
    from scripts.install_node import ensure_node, find_existing_node

    if not find_existing_node():
        print("[install-node] 未检测到系统 Node，尝试自动安装…")
        ensure_node("v22.21.1", dry_run=False)
except SystemExit as error:  # ensure_node 的中文报错（缺管理员等）
    print(f"[install-node] {error}")
except Exception as error:  # noqa: BLE001 - 兜底失败不影响 API 启动
    print(f"[install-node] Node 初始化兜底失败：{error}")

runpy.run_module("services.api.main", run_name="__main__")
