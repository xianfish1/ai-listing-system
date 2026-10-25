from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, unquote

import pytest

from services.api import mabang_browser


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _ExitedProcess:
    def poll(self) -> int:
        return 1


class _NoProcesses:
    stdout = "[]"
    returncode = 0


def test_windows_process_query_does_not_open_a_console(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Result:
        stdout = "[]"
        returncode = 0

    monkeypatch.setattr(
        mabang_browser,
        "no_console_window_kwargs",
        lambda: {"creationflags": 12345},
        raising=False,
    )
    monkeypatch.setattr(
        mabang_browser.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or Result(),
    )

    mabang_browser._run_windows_process_query()

    assert calls[0][1]["creationflags"] == 12345


def test_process_tree_cleanup_does_not_open_a_console(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        mabang_browser,
        "no_console_window_kwargs",
        lambda: {"creationflags": 12345},
        raising=False,
    )
    monkeypatch.setattr(
        mabang_browser.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    monkeypatch.setattr(mabang_browser.time, "sleep", lambda _seconds: None)

    mabang_browser._kill_process_tree(101)

    assert len(calls) == 2
    assert all(kwargs["creationflags"] == 12345 for _command, kwargs in calls)


def test_launch_starts_a_dedicated_chrome_when_cdp_is_offline(tmp_path: Path) -> None:
    calls: list[object] = []
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    responses = iter([
        URLError("connection refused"),
        _Response({"Browser": "Chrome/1"}),
    ])

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        calls.append(request)
        next_item = next(responses)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=chrome,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda command, **kwargs: calls.append((command, kwargs)),
        sleep_fn=lambda _seconds: None,
        process_query_fn=_NoProcesses,
        attempts=1,
    )

    assert result == {"ok": True, "status": "started", "login_required": True, "browser_mode": "headed"}
    command, kwargs = calls[1]
    assert "--remote-debugging-port=9223" in command
    assert any(part.startswith("--user-data-dir=") for part in command)
    assert not any(part.startswith("--headless") for part in command)
    for flag in mabang_browser.COLLECTION_CHROME_FLAGS:
        assert flag in command
    assert mabang_browser.MABANG_NEW_PRODUCT_URL in command
    assert kwargs["cwd"] == str(tmp_path)


def test_launch_headless_appends_the_headless_flags(tmp_path: Path) -> None:
    calls: list[object] = []
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    responses = iter([URLError("connection refused"), _Response({"Browser": "Chrome/1"})])

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        calls.append(request)
        next_item = next(responses)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=chrome,
        headless=True,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda command, **kwargs: calls.append((command, kwargs)),
        sleep_fn=lambda _seconds: None,
        process_query_fn=_NoProcesses,
        attempts=1,
    )

    assert result == {"ok": True, "status": "started", "login_required": True, "browser_mode": "headless"}
    command, _kwargs = calls[1]
    for flag in mabang_browser.HEADLESS_CHROME_FLAGS:
        assert flag in command
    assert "--headless=new" in command


def test_launch_reuses_a_running_browser_in_the_requested_mode(tmp_path: Path) -> None:
    calls: list[object] = []
    popen_commands: list[list[str]] = []

    class Result:
        stdout = json.dumps([
            {
                "ProcessId": 101,
                "CommandLine": f'chrome.exe --user-data-dir="{tmp_path / "profile"}" --remote-debugging-port=9223',
            },
        ])
        returncode = 0

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        calls.append(request)
        full_url = request.full_url
        if full_url.endswith("/json/version"):
            return _Response({"Browser": "Chrome/1"})
        if full_url.endswith("/json/list"):
            return _Response([{"id": "old-page-1", "type": "page"}])
        return _Response({"id": "closed"})

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        headless=False,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda command, **kwargs: popen_commands.append(command),
        sleep_fn=lambda _seconds: None,
        process_query_fn=lambda: Result(),
        attempts=1,
    )

    assert result["status"] == "running"
    assert result["browser_mode"] == "headed"
    assert popen_commands == []
    assert any(getattr(request, "full_url", "").endswith("/json/close/old-page-1") for request in calls)


def test_launch_restarts_the_browser_when_the_running_mode_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless is a settings toggle: a headed browser must not silently satisfy it."""

    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    killed: list[list[str]] = []
    popen_commands: list[list[str]] = []

    class Result:
        stdout = json.dumps([
            {
                "ProcessId": 303,
                "CommandLine": f'chrome.exe --user-data-dir="{tmp_path / "profile"}" --remote-debugging-port=9223',
            },
        ])
        returncode = 0

    class Gone:
        stdout = "[]"
        returncode = 0

    # Mode detection and the termination pass each query the process table once;
    # after that the stale browser is gone, so the launch-time stale cleanup that
    # follows must not report it again.
    queries = {"count": 0}

    def fake_query() -> object:
        queries["count"] += 1
        return Result() if queries["count"] <= 2 else Gone()

    # CDP answers on entry, goes away once the stale browser is killed, then comes
    # back for the fresh headless launch.
    probes: list[bool] = [True, False, True]

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        if not request.full_url.endswith("/json/version"):
            return _Response({"Browser": "Chrome/1"})
        if not probes.pop(0):
            raise URLError("offline")
        return _Response({"Browser": "Chrome/1"})

    monkeypatch.setattr(mabang_browser, "_kill_process_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(mabang_browser.subprocess, "run", lambda command, **kwargs: None)

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=chrome,
        headless=True,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda command, **kwargs: popen_commands.append(command),
        sleep_fn=lambda _seconds: None,
        process_query_fn=fake_query,
        attempts=1,
    )

    assert killed == [303]
    assert result["status"] == "started"
    assert result["browser_mode"] == "headless"
    assert popen_commands and "--headless=new" in popen_commands[0]


def test_launch_reuses_a_mismatched_browser_when_it_is_driven_by_someone_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """页面导入期间（reuse_running_only）：同一个 profile 的有头窗口必须原样留着。

    设置里是「无头」，但操作员正在用导入窗口。此时不能按设置把它杀掉重起——
    现场表现就是「窗口刚出来就自己关了」。
    """

    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    profile = tmp_path / "profile"
    killed: list[int] = []
    popen_commands: list[list[str]] = []

    class HeadedBrowser:
        stdout = json.dumps([
            {
                "ProcessId": 303,
                "CommandLine": f'chrome.exe --user-data-dir="{profile}" --remote-debugging-port=9223',
            },
        ])
        returncode = 0

    opened: list[str] = []

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        if getattr(request, "full_url", "").endswith("/json/version"):
            return _Response({"Browser": "Chrome/1"})
        opened.append(str(getattr(request, "full_url", "")))
        return _Response({})

    monkeypatch.setattr(mabang_browser, "_kill_process_tree", lambda pid: killed.append(pid))

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=profile,
        chrome_executable=chrome,
        headless=True,
        require_mode=True,
        reuse_running_only=True,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda command, **kwargs: popen_commands.append(command),
        sleep_fn=lambda _seconds: None,
        process_query_fn=lambda: HeadedBrowser(),
        attempts=1,
    )

    assert killed == []
    assert popen_commands == []
    assert result == {"ok": True, "status": "running", "login_required": False, "browser_mode": "headed"}
    # 复用路径：关掉旧页面后重新打开马帮新建商品页（而不是重启浏览器）
    assert any("/json/new?" in url for url in opened)
    assert any(quote(mabang_browser.MABANG_NEW_PRODUCT_URL, safe="") in url for url in opened)


def test_launch_reuse_running_only_refuses_to_start_a_second_browser(
    tmp_path: Path,
) -> None:
    """导入窗口正在重启（CDP 暂时连不上）时也不许另起一只去抢端口/Profile。"""

    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    launched: list[list[str]] = []

    def offline(request: object, timeout: float = 0) -> _Response:
        raise URLError("offline")

    with pytest.raises(mabang_browser.MabangBrowserError) as excinfo:
        mabang_browser.launch_mabang_browser(
            "http://127.0.0.1:9223",
            profile_dir=tmp_path / "profile",
            chrome_executable=chrome,
            headless=True,
            reuse_running_only=True,
            urlopen_fn=offline,
            popen_fn=lambda command, **kwargs: launched.append(command),
            sleep_fn=lambda _seconds: None,
            process_query_fn=_NoProcesses,
            attempts=1,
        )

    assert "页面导入" in str(excinfo.value)
    assert launched == []


def test_launch_restarts_same_mode_browser_using_the_wrong_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted_profile = tmp_path / "wanted-profile"
    wrong_profile = tmp_path / "smoke2" / "browser-use-profile"
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    killed: list[int] = []
    launched: list[list[str]] = []

    class WrongProfile:
        stdout = json.dumps([
            {
                "ProcessId": 303,
                "CommandLine": (
                    f'chrome.exe --headless=new --user-data-dir="{wrong_profile}" '
                    "--remote-debugging-port=9223"
                ),
            },
        ])
        returncode = 0

    class Gone:
        stdout = "[]"
        returncode = 0

    queries = {"count": 0}

    def process_query() -> object:
        queries["count"] += 1
        return WrongProfile() if queries["count"] <= 2 else Gone()

    probes = iter([True, False, True])

    def urlopen_probe(request: object, timeout: float = 0) -> _Response:
        if not request.full_url.endswith("/json/version"):
            return _Response({})
        if not next(probes):
            raise URLError("offline")
        return _Response({"Browser": "Chrome/1"})

    monkeypatch.setattr(mabang_browser, "_kill_process_tree", lambda pid: killed.append(pid))

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=wanted_profile,
        chrome_executable=chrome,
        headless=True,
        urlopen_fn=urlopen_probe,
        popen_fn=lambda command, **kwargs: launched.append(command),
        sleep_fn=lambda _seconds: None,
        process_query_fn=process_query,
        attempts=1,
    )

    assert killed == [303]
    assert result["status"] == "started"
    assert result["browser_mode"] == "headless"
    assert any(part == f"--user-data-dir={wanted_profile.resolve()}" for part in launched[0])


def test_collection_browser_mode_ignores_child_processes(tmp_path: Path) -> None:
    profile = tmp_path / "browser-use-profile"

    class Result:
        # The renderer child has no --headless flag of its own; only the browser
        # process carries the full switch list.
        stdout = json.dumps([
            {"ProcessId": 11, "CommandLine": f'chrome.exe --type=renderer --user-data-dir="{profile}"'},
            {
                "ProcessId": 12,
                "CommandLine": f'chrome.exe --user-data-dir="{profile}" --remote-debugging-port=9223',
            },
        ])
        returncode = 0

    assert mabang_browser._collection_browser_mode(profile, 9223, process_query_fn=Result) == "headed"

    class HeadlessResult:
        stdout = json.dumps([{
            "ProcessId": 13,
            "CommandLine": f'chrome.exe --headless=new --user-data-dir="{profile}" --remote-debugging-port=9223',
        }])
        returncode = 0

    assert mabang_browser._collection_browser_mode(profile, 9223, process_query_fn=HeadlessResult) == "headless"


def test_launch_reuses_cdp_browser_and_opens_the_mabang_page(tmp_path: Path) -> None:
    calls: list[object] = []

    class NoProcesses:
        stdout = "[]"
        returncode = 0

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        calls.append(request)
        full_url = request.full_url
        method = request.get_method()
        if method == "GET" and full_url.endswith("/json/version"):
            return _Response({"Browser": "Chrome/1"})
        if method == "GET" and full_url.endswith("/json/list"):
            return _Response([{"id": "old-page-1", "type": "page"}, {"id": "worker-1", "type": "worker"}])
        if method == "DELETE":
            return _Response({"id": "closed"})
        return _Response({"id": "new-tab"})

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=Path("C:/Chrome/chrome.exe"),
        urlopen_fn=fake_urlopen,
        popen_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not relaunch Chrome")),
        sleep_fn=lambda _seconds: None,
        # An unknown window mode reuses the running browser, which keeps this test
        # independent of whatever Chrome happens to run on the host.
        process_query_fn=lambda: NoProcesses(),
    )

    assert result == {"ok": True, "status": "running", "login_required": False, "browser_mode": "headed"}
    assert [request.get_method() for request in calls] == ["GET", "GET", "DELETE", "PUT"]
    assert "old-page-1" in calls[2].full_url
    assert mabang_browser.MABANG_NEW_PRODUCT_URL in unquote(calls[3].full_url)


def test_launch_falls_back_to_the_next_chrome_when_the_first_exits(tmp_path: Path, monkeypatch) -> None:
    """内置 Chrome 起不来（crashpad settings.dat 被拒绝访问）时必须回退到系统 Chrome。

    现场 2026-09-27：只认内置 Chrome 的话采集浏览器永远起不来，Worker 一直 waiting_for_browser。
    """
    bundled = tmp_path / "vendor-chrome.exe"
    bundled.write_text("", encoding="utf-8")
    system = tmp_path / "ProgramFiles-chrome.exe"
    system.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "chrome_executable_candidates", lambda: [bundled, system])

    started: list[str] = []

    def fake_popen(command: list[str], **_kwargs: object) -> object:
        started.append(command[0])
        return _ExitedProcess() if command[0] == str(bundled) else None

    responses = iter([URLError("offline"), _Response({"Browser": "Chrome/154"})])

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
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
        process_query_fn=_NoProcesses,
        attempts=1,
    )

    assert result == {"ok": True, "status": "started", "login_required": True, "browser_mode": "headed"}
    assert started == [str(bundled), str(system)]


def test_launch_reports_the_tried_chrome_when_every_candidate_exits(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "vendor-chrome.exe"
    bundled.write_text("", encoding="utf-8")
    system = tmp_path / "system-chrome.exe"
    system.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "chrome_executable_candidates", lambda: [bundled, system])

    with pytest.raises(mabang_browser.MabangBrowserError) as excinfo:
        mabang_browser.launch_mabang_browser(
            "http://127.0.0.1:9223",
            profile_dir=tmp_path / "profile",
            urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
            popen_fn=lambda *_args, **_kwargs: _ExitedProcess(),
            sleep_fn=lambda _seconds: None,
            process_query_fn=_NoProcesses,
            attempts=1,
        )

    assert "退出码 1" in str(excinfo.value)
    assert str(system) in str(excinfo.value)


def test_launch_reports_when_chrome_exits_before_cdp_is_ready(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")

    with pytest.raises(mabang_browser.MabangBrowserError, match="Chrome 进程已退出.*退出码 1"):
        mabang_browser.launch_mabang_browser(
            "http://127.0.0.1:9223",
            profile_dir=tmp_path / "profile",
            chrome_executable=chrome,
            urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
            popen_fn=lambda *_args, **_kwargs: _ExitedProcess(),
            sleep_fn=lambda _seconds: None,
            process_query_fn=_NoProcesses,
            attempts=1,
        )


def test_launch_surfaces_chrome_fatal_from_recent_crashdump(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    profile = tmp_path / "profile"
    reports = profile / "Crashpad" / "reports"
    reports.mkdir(parents=True)
    dump = reports / "recent.dmp"
    fatal = (
        b"\x00\x00some binary padding\x00\x00"
        b"[12600:5388:0821/150118.335:FATAL:content\\browser\\gpu\\gpu_data_manager_impl_private.cc:418] "
        b"GPU process isn't usable. Goodbye.\x00\x00"
    )
    dump.write_bytes(fatal)

    with pytest.raises(mabang_browser.MabangBrowserError, match="GPU process isn't usable"):
        mabang_browser.launch_mabang_browser(
            "http://127.0.0.1:9223",
            profile_dir=profile,
            chrome_executable=chrome,
            urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
            popen_fn=lambda *_args, **_kwargs: _ExitedProcess(),
            sleep_fn=lambda _seconds: None,
            process_query_fn=_NoProcesses,
            attempts=1,
        )


def test_launch_prunes_dawn_graphite_cache_before_starting(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    profile = tmp_path / "profile"
    dawn = profile / "GPUPersistentCache" / "DawnGraphiteCache" / "shard"
    dawn.mkdir(parents=True)
    (dawn / "entry").write_bytes(b"cached")
    responses = iter([URLError("offline"), _Response({"Browser": "Chrome/1"})])

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=profile,
        chrome_executable=chrome,
        urlopen_fn=fake_urlopen,
        popen_fn=lambda *_args, **_kwargs: None,
        sleep_fn=lambda _seconds: None,
        process_query_fn=_NoProcesses,
        attempts=1,
    )

    assert not (profile / "GPUPersistentCache" / "DawnGraphiteCache").exists()


def test_collection_chrome_command_line_matches_profile_or_port_only(tmp_path: Path) -> None:
    profile = tmp_path / "browser-use-profile"
    profile_arg = f'--user-data-dir="{profile}"'

    assert mabang_browser._is_collection_chrome_command(
        f'"C:/Chrome/chrome.exe" {profile_arg} --remote-debugging-port=9223',
        profile_dir=profile,
        port=9223,
    )
    assert mabang_browser._is_collection_chrome_command(
        '"C:/Chrome/chrome.exe" --remote-debugging-port=9223',
        profile_dir=profile,
        port=9223,
    )
    assert mabang_browser._is_collection_chrome_command(
        '"C:/Chrome/chrome.exe" --user-data-dir="C:/other-profile" --remote-debugging-port=9223',
        profile_dir=profile,
        port=9223,
    )
    assert not mabang_browser._is_collection_chrome_command(
        '"C:/Chrome/chrome.exe" --user-data-dir="C:/other-profile" --remote-debugging-port=9333',
        profile_dir=profile,
        port=9223,
    )
    assert not mabang_browser._is_collection_chrome_command(
        f'"C:/Chrome/chrome.exe" --user-data-dir="{profile}2" --remote-debugging-port=9333',
        profile_dir=profile,
        port=9223,
    )


def test_cleanup_stale_collection_chrome_terminates_only_matching_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "browser-use-profile"
    calls: list[list[str]] = []

    class Result:
        stdout = json.dumps([
            {"ProcessId": 101, "CommandLine": f'chrome.exe --user-data-dir="{profile}" --remote-debugging-port=9223'},
            {"ProcessId": 202, "CommandLine": 'chrome.exe --user-data-dir="C:/other-profile" --remote-debugging-port=9333'},
        ])
        returncode = 0

    monkeypatch.setattr(mabang_browser, "_run_windows_process_query", lambda: Result())
    monkeypatch.setattr(mabang_browser.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(mabang_browser.time, "sleep", lambda _seconds: None)

    removed = mabang_browser._cleanup_stale_collection_chrome(profile, 9223)

    assert removed == [101]
    assert calls == [["taskkill", "/PID", "101", "/T"], ["taskkill", "/PID", "101", "/T", "/F"]]


def test_launch_cleans_stale_collection_chrome_before_popen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")
    events: list[str] = []
    responses = iter([URLError("offline"), _Response({"Browser": "Chrome/1"})])

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mabang_browser, "_cleanup_stale_collection_chrome", lambda profile, port, **_kwargs: events.append("cleanup"))

    def fake_popen(*_args: object, **_kwargs: object) -> None:
        events.append("popen")
        return None

    mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=chrome,
        urlopen_fn=fake_urlopen,
        popen_fn=fake_popen,
        sleep_fn=lambda _seconds: None,
        process_query_fn=_NoProcesses,
        attempts=1,
    )

    assert events == ["cleanup", "popen"]


def test_close_mabang_browser_pages_ignores_non_page_targets(tmp_path: Path) -> None:
    closed: list[str] = []

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        if request.get_method() == "GET":
            return _Response([
                {"id": "page-1", "type": "page"},
                {"id": "worker-1", "type": "worker"},
                {"type": "page"},
            ])
        closed.append(request.full_url)
        return _Response({})

    result = mabang_browser._close_mabang_browser_pages("http://127.0.0.1:9223", fake_urlopen)

    assert result == ["page-1"]
    assert closed == ["http://127.0.0.1:9223/json/close/page-1"]


def test_cdp_probe_uses_a_short_timeout() -> None:
    observed: list[float] = []

    def offline_probe(*_args: object, timeout: float) -> _Response:
        observed.append(timeout)
        raise URLError("offline")

    assert mabang_browser._cdp_is_available("http://127.0.0.1:9223", offline_probe) is False
    assert observed == [0.5]


def test_find_bundled_chrome_locates_playwright_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled_dir = tmp_path / "vendor" / "chrome"
    exe = bundled_dir / "chromium-1228" / "chrome-win64" / "chrome.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "BUNDLED_CHROME_DIR", bundled_dir)

    assert mabang_browser.find_bundled_chrome() == exe


def test_find_bundled_chrome_returns_none_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mabang_browser, "BUNDLED_CHROME_DIR", tmp_path / "vendor" / "chrome")

    assert mabang_browser.find_bundled_chrome() is None


def test_find_chrome_executable_prefers_bundled_over_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled_dir = tmp_path / "vendor" / "chrome"
    exe = bundled_dir / "chrome-win64" / "chrome.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mabang_browser, "BUNDLED_CHROME_DIR", bundled_dir)
    # A CHROME_EXECUTABLE env pointing elsewhere must lose to the bundled copy.
    monkeypatch.setenv("CHROME_EXECUTABLE", str(tmp_path / "system-chrome.exe"))
    (tmp_path / "system-chrome.exe").write_text("", encoding="utf-8")

    assert mabang_browser.find_chrome_executable() == exe


def test_require_mode_relaunches_when_the_running_mode_is_unknown(tmp_path: Path) -> None:
    """调用方明确要求窗口模式时，探测不出当前模式必须重启，不能乐观复用。

    现场症状：页面导入要求开一个可见窗口，但代码复用了正在跑的无头浏览器、
    还把模式报成 headed —— 提示写着「窗口已显示」，窗口其实没出现。
    """

    class NoProcesses:
        stdout = "[]"
        returncode = 0

    def fake_urlopen(request: object, timeout: float = 0) -> _Response:
        return _Response({"Browser": "Chrome/1"})

    launched: list[list] = []
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("", encoding="utf-8")

    class _Started:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        launched.append(list(command))
        return _Started()

    result = mabang_browser.launch_mabang_browser(
        "http://127.0.0.1:9223",
        profile_dir=tmp_path / "profile",
        chrome_executable=chrome,
        headless=False,
        require_mode=True,
        urlopen_fn=fake_urlopen,
        popen_fn=fake_popen,
        sleep_fn=lambda _seconds: None,
        process_query_fn=lambda: NoProcesses(),
        attempts=1,
    )

    assert launched, "探测不到窗口模式时必须重启浏览器，而不是复用"
    assert result == {"ok": True, "status": "started", "login_required": True, "browser_mode": "headed"}

