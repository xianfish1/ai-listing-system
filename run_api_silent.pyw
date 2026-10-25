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

runpy.run_module("services.api.main", run_name="__main__")
