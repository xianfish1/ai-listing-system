"""POSIX 分支（macOS / Linux）的采集浏览器进程管理。

Windows 仍由 tests/test_mabang_browser.py 覆盖，且行为未变。
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from urllib.error import URLError

import pytest

from services.api import mabang_browser


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_posix_process_entries_parses_ps_rows() -> None:
    result = _Result(
        "  101 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        " --remote-debugging-port=9223\n"
        "  202 /usr/bin/other --flag\n"
        "\n"
        "garbage line\n"
    )

    assert mabang_browser._posix_process_entries(result) == [
        {
            "ProcessId": 101,
            "CommandLine": (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                " --remote-debugging-port=9223"
            ),
        },
        {"ProcessId": 202, "CommandLine": "/usr/bin/other --flag"},
    ]


def test_posix_process_entries_ignores_failed_query() -> None:
    assert mabang_browser._posix_process_entries(_Result("101 chrome", returncode=1)) == []


def test_posix_process_entries_ignores_empty_output() -> None:
    assert mabang_browser._posix_process_entries(_Result("")) == []


def test_kill_process_tree_posix_signals_the_whole_tree() -> None:
    signals: list[tuple[int, int]] = []

    mabang_browser._kill_process_tree_posix(
        101,
        kill_fn=lambda pid, sig: signals.append((pid, sig)),
        sleep_fn=lambda _seconds: None,
        tree_pids_fn=lambda pid: [pid, 202],
    )

    assert signals == [
        (101, signal.SIGTERM),
        (202, signal.SIGTERM),
        (101, getattr(signal, "SIGKILL", signal.SIGTERM)),
        (202, getattr(signal, "SIGKILL", signal.SIGTERM)),
    ]


def test_kill_process_tree_posix_ignores_gone_processes() -> None:
    def kill(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    mabang_browser._kill_process_tree_posix(
        101,
        kill_fn=kill,
        sleep_fn=lambda _seconds: None,
        tree_pids_fn=lambda pid: [pid],
    )


def test_kill_process_tree_posix_refuses_pid_one() -> None:
    signals: list[tuple[int, int]] = []

    mabang_browser._kill_process_tree_posix(
        1,
        kill_fn=lambda pid, sig: signals.append((pid, sig)),
        sleep_fn=lambda _seconds: None,
        tree_pids_fn=lambda pid: [pid],
    )

    assert signals == []


def test_posix_process_tree_pids_walks_children(monkeypatch: pytest.MonkeyPatch) -> None:
    class RunResult:
        stdout = "101 1\n202 101\n303 202\n404 1\n"
        returncode = 0

    monkeypatch.setattr(mabang_browser.subprocess, "run", lambda *_args, **_kwargs: RunResult())

    assert mabang_browser._posix_process_tree_pids(101) == [101, 202, 303]


def test_posix_process_tree_pids_falls_back_to_single_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("ps missing")

    monkeypatch.setattr(mabang_browser.subprocess, "run", explode)

    assert mabang_browser._posix_process_tree_pids(101) == [101]


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only behavior")
def test_find_bundled_chrome_locates_macos_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_dir = tmp_path / "vendor" / "chrome"
    exe = (
        bundled_dir
        / "chromium-1228"
        / "chrome-mac"
        / "Chromium.app"
        / "Contents"
        / "MacOS"
        / "Chromium"
    )
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "BUNDLED_CHROME_DIR", bundled_dir)

    assert mabang_browser.find_bundled_chrome() == exe


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only behavior")
def test_launch_rejects_windows_binary_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """误留的 Windows chrome.exe 报 Exec format error 时，必须继续试下一个候选。"""

    stale = tmp_path / "chrome.exe"
    stale.write_text("", encoding="utf-8")
    usable = tmp_path / "Chromium"
    usable.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "chrome_executable_candidates", lambda: [stale, usable])

    started: list[str] = []

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        started.append(command[0])
        if command[0] == str(stale):
            raise OSError(8, "Exec format error")
        return None

    responses = iter([URLError("offline"), _Response({"Browser": "Chrome/1"})])

    def fake_urlopen(_request: object, timeout: float = 0) -> _Response:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        urlopen_fn=fake_urlopen,
        popen_fn=fake_popen,
        sleep_fn=lambda _seconds: None,
        process_query_fn=lambda: _Result(""),
        attempts=1,
    )

    assert result["status"] == "started"
    assert started == [str(stale), str(usable)]
