"""防回归：运行期子进程调用必须带"不弹控制台窗口"参数；并实测关键路径确实传了。

现场反馈（2026-09-28）：导入文件、生成自动化、自动化接任务时偶尔弹 cmd 黑框。
Windows 下 GUI 宿主起控制台程序（ffprobe / node / browser-use.exe）默认会闪窗口。
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

import pytest

from services.api import subprocess_utils

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_GLOBS = ("services/api/*.py", "scripts/*.py", "workers/**/*.py")
# 允许不带该参数：explorer 是 GUI 程序（本来就不开控制台）；bundle_chrome 是构建脚本，不在运行期。
ALLOWLIST_BLOCK_TOKENS = ("explorer",)
ALLOWLIST_FILE_NAMES = ("bundle_chrome.py",)


def _call_blocks(text: str) -> list[tuple[int, str]]:
    """按括号配平切出每个 subprocess.* 调用的完整代码块。"""

    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not re.search(r"subprocess\.(Popen|run|check_output|check_call|call)\(", line):
            continue
        depth = line.count("(") - line.count(")")
        chunk = [line]
        cursor = index
        while depth > 0 and cursor + 1 < len(lines):
            cursor += 1
            chunk.append(lines[cursor])
            depth += lines[cursor].count("(") - lines[cursor].count(")")
        blocks.append((index + 1, "\n".join(chunk)))
    return blocks


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    for pattern in RUNTIME_GLOBS:
        files.extend(ROOT.glob(pattern))
    return [path for path in files if ".venv" not in str(path)]


def test_every_runtime_subprocess_call_suppresses_the_console_window() -> None:
    offenders: list[str] = []
    for path in _candidate_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "subprocess." not in text:
            continue
        for line_no, block in _call_blocks(text):
            if any(token in block for token in ALLOWLIST_BLOCK_TOKENS) or path.name in ALLOWLIST_FILE_NAMES:
                continue
            if "no_console_window_kwargs" in block or "CREATE_NO_WINDOW" in block:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{line_no}")
    assert not offenders, "这些子进程调用会弹黑框，请补 **no_console_window_kwargs():\n  " + "\n  ".join(offenders)


def test_no_console_window_kwargs_hides_both_flags() -> None:
    kwargs = subprocess_utils.no_console_window_kwargs()
    if subprocess_utils.os.name != "nt":
        assert kwargs == {}
        return
    assert kwargs.get("creationflags")
    assert kwargs.get("startupinfo") is not None, "还要给 startupinfo(SW_HIDE) 兜底"


def test_node_probe_and_ffprobe_really_pass_the_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """关键路径实测：node --version 探测与 ffprobe 校验都要带参数（这两条正是现场闪框点）。"""

    from scripts import browser_use_runner
    from services.api import video_assets

    captured: list[dict] = []

    class FakeCompleted:
        returncode = 0
        stdout = "v22.0.0"
        stderr = ""

    def fake_run(command, **kwargs):
        captured.append({"command": command, "kwargs": kwargs})
        output = FakeCompleted()
        if "ffprobe" in str(command[0]):
            output.stdout = '{"streams": [{"width": 100, "height": 200}], "format": {"duration": "5.0"}}'
        return output

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(video_assets.subprocess, "run", fake_run)

    browser_use_runner._NODE_PROBE_CACHE.clear()
    browser_use_runner._resolve_node_executable("node", {})
    video_assets._probe_metadata(ROOT / "README.md", ffprobe_path="ffprobe")

    assert len(captured) == 2, captured
    for item in captured:
        assert "no_console_window_kwargs" not in item["kwargs"]
        assert item["kwargs"].get("creationflags") or item["kwargs"].get("startupinfo"), item
