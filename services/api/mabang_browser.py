from __future__ import annotations

import json
import ntpath
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from services.api.subprocess_utils import no_console_window_kwargs


MABANG_NEW_PRODUCT_URL = (
    "https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1"
)

# Chrome flags that keep this scraper-facing browser alive on this Windows host.
# The GPU child has repeatedly crashed with STATUS_ACCESS_DENIED while opening
# GPUPersistentCache/DawnGraphiteCache/<hash>, which trips the browser's LOG_FATAL
# path and exits with STATUS_BREAKPOINT (0x80000003). SkiaGraphite is disabled so
# the Graphite/Dawn persistent cache is not written at all; --disable-gpu-sandbox
# bypasses the sandbox ACL that surfaced the original ACCESS_DENIED on this profile
# path (other Chrome sandboxes stay on); the disk-cache flags stop shader caches
# from re-collecting in restricted directories.
COLLECTION_CHROME_FLAGS: tuple[str, ...] = (
    "--disable-features=SkiaGraphite",
    "--disable-gpu-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu-shader-disk-cache",
)

# Window mode for the automation/worker browser, chosen in 设置 → 马帮连接.
# 有头 keeps the operator-facing window that manual sliding-captcha verification
# needs; 无头 runs the same browser stack without a visible window.
BROWSER_MODE_HEADLESS = "headless"
BROWSER_MODE_HEADED = "headed"

# New headless (Chrome 112+) keeps CDP, the auto-login helper and the capture
# script on the same code path as a visible window, so no script needs to know
# which mode it drives. The viewport flag matters because headless defaults to a
# phone-sized 800x600 window, which collapses Mabang's vxe-table layouts.
HEADLESS_CHROME_FLAGS: tuple[str, ...] = (
    "--headless=new",
    "--window-size=1600,1000",
)


class MabangBrowserError(ValueError):
    pass


def browser_mode_label(mode: str | None) -> str:
    return "无头模式" if mode == BROWSER_MODE_HEADLESS else "窗口模式"


def _command_requests_headless(command_line: str) -> bool:
    return bool(re.search(r"(?:^|\s)--headless(?:=(?!false)|$|\s)", str(command_line or "")))


def launch_mabang_browser(
    cdp_url: str,
    *,
    profile_dir: Path,
    chrome_executable: Path | None = None,
    headless: bool = False,
    require_mode: bool = False,
    reuse_running_only: bool = False,
    urlopen_fn: Callable[..., Any] = urlopen,
    popen_fn: Callable[..., Any] = subprocess.Popen,
    sleep_fn: Callable[[float], None] = time.sleep,
    process_query_fn: Callable[[], Any] | None = None,
    attempts: int = 20,
) -> dict[str, Any]:
    """Start or reuse the Chrome reserved for read-only Mabang collection.

    ``headless`` selects the window mode from 设置 → 马帮连接. A already-running
    collection Chrome is only reused when it matches the requested mode; otherwise
    it is stopped first so the setting actually takes effect on this launch.

    require_mode 用在「调用方明确要求某种窗口模式」的场景（例如页面导入必须开一个
    可见窗口）。此时如果探测不出正在运行的浏览器是什么模式，就**不能乐观复用**——
    否则会出现「报告已切成窗口模式、实际还是无头、窗口根本不出现」。

    reuse_running_only 用在「这只浏览器正在被别人用」的场景（页面导入期间：它可能
    是操作员正在填的可见窗口）。此时同一个 profile 的浏览器无论什么窗口模式都直接
    复用，没有可复用的就报错——绝不为了「模式跟设置一致」把它杀掉重起。
    """

    base_url, port = _validated_local_cdp_url(cdp_url)
    requested_mode = BROWSER_MODE_HEADLESS if headless else BROWSER_MODE_HEADED
    if _cdp_is_available(base_url, urlopen_fn):
        running_mode, running_profile_matches = _collection_browser_identity(
            profile_dir, port, process_query_fn=process_query_fn
        )
        can_reuse = running_mode == requested_mode and running_profile_matches
        if running_mode is None and not require_mode:
            # 旧行为：模式探测不出来时乐观复用，避免每次无谓重启。
            # 但调用方明确指定了模式时不能赌（见 docstring 的 require_mode）。
            can_reuse = True
        if reuse_running_only and running_profile_matches:
            # 同一个登录态（profile 一致）就够用：模式不同也复用，不动这只浏览器。
            can_reuse = True
        if can_reuse:
            _close_mabang_browser_pages(base_url, urlopen_fn)
            _open_mabang_new_product_page(base_url, urlopen_fn)
            return {
                "ok": True,
                "status": "running",
                "login_required": False,
                "browser_mode": running_mode or requested_mode,
            }
        _terminate_collection_chrome(profile_dir, port, process_query_fn=process_query_fn)
        _wait_for_cdp_shutdown(base_url, urlopen_fn, sleep_fn=sleep_fn)

    if reuse_running_only:
        # 端口上没有可复用的浏览器（页面导入窗口可能正在重启中）：这时另起一只只会
        # 去抢同一个端口/Profile，把操作员的窗口顶掉。如实报错，让调用方稍后再来。
        raise MabangBrowserError(
            "页面导入正在进行：采集浏览器正由导入窗口使用，导入期间不自动重启采集浏览器"
        )

    if chrome_executable:
        candidates = [chrome_executable]
    else:
        candidates = chrome_executable_candidates()
    candidates = [candidate for candidate in candidates if candidate and Path(candidate).is_file()]
    if not candidates:
        raise MabangBrowserError("未找到 Google Chrome，无法启动采集浏览器")

    profile_dir = profile_dir.expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_collection_chrome(profile_dir, port, process_query_fn=process_query_fn)
    _reset_dawn_graphite_cache(profile_dir)

    last_error: MabangBrowserError | None = None
    for chrome in candidates:
        command = [
            str(chrome),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            *COLLECTION_CHROME_FLAGS,
            *(HEADLESS_CHROME_FLAGS if headless else ()),
            MABANG_NEW_PRODUCT_URL,
        ]
        launched_at = time.time()
        process = popen_fn(command, cwd=str(profile_dir.parent))
        exited_early = False
        for _ in range(max(1, attempts)):
            exit_code = _process_exit_code(process)
            if exit_code is not None:
                reason = _extract_chrome_crash_reason(profile_dir, launched_at)
                hint = f"（Chrome 崩溃日志：{reason}）" if reason else ""
                # 这个候选起不来（内置 Chrome 被 crashpad 挡住的现场就是这样）→ 换下一个候选，
                # 全部候选都失败才把最后一个错误抛出去。
                last_error = MabangBrowserError(
                    f"采集浏览器的 Chrome 进程已退出（退出码 {exit_code}）{hint}。"
                    f"（已尝试：{chrome}）"
                    "请关闭残留的采集浏览器窗口后重试；若仍失败，请检查 Chrome 是否被安全软件拦截。"
                )
                exited_early = True
                break
            sleep_fn(0.25)
            if _cdp_is_available(base_url, urlopen_fn):
                return {
                    "ok": True,
                    "status": "started",
                    "login_required": True,
                    "browser_mode": requested_mode,
                }
        if not exited_early:
            last_error = MabangBrowserError(f"采集浏览器未能启动或未监听 {base_url}")
    if last_error is not None:
        raise last_error
    raise MabangBrowserError(f"采集浏览器未能启动或未监听 {base_url}")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_CHROME_DIR = PROJECT_ROOT / "vendor" / "chrome"


def stop_mabang_browser(
    cdp_url: str,
    *,
    profile_dir: Path,
    urlopen_fn: Callable[..., Any] = urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
    process_query_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Stop one collection Chrome, matched by its Profile dir or CDP port.

    只有一个调用场景：把「每账号并行槽位」调小以后，被裁掉的槽位那只浏览器不能
    就这么挂着（白占内存、端口还被占着）。绝不误杀普通 Chrome：匹配条件同时要求
    `--user-data-dir` 或 `--remote-debugging-port` 与这只采集浏览器一致。
    """

    base_url, port = _validated_local_cdp_url(cdp_url)
    process_ids = _terminate_collection_chrome(
        Path(profile_dir), port, process_query_fn=process_query_fn
    )
    _wait_for_cdp_shutdown(base_url, urlopen_fn, sleep_fn=sleep_fn)
    return {
        "ok": not _cdp_is_available(base_url, urlopen_fn),
        "status": "stopped",
        "port": port,
        "stopped_pids": process_ids,
    }


def find_bundled_chrome() -> Path | None:
    """Return the project-local Chrome shipped under vendor/chrome, if present."""

    if not BUNDLED_CHROME_DIR.is_dir():
        return None
    # Playwright layout keeps the binary at chrome-win64/chrome.exe (build folders vary),
    # so glob rather than hardcode a build number.
    for pattern in ("chrome.exe", "*/chrome.exe", "*/*/chrome.exe"):
        match = next((candidate for candidate in sorted(BUNDLED_CHROME_DIR.glob(pattern)) if candidate.is_file()), None)
        if match:
            return match
    return None


def find_chrome_executable() -> Path | None:
    candidates = chrome_executable_candidates()
    return candidates[0] if candidates else None


def chrome_executable_candidates() -> list[Path]:
    """按优先级列出可用于采集的 Chrome：内置 → CHROME_EXECUTABLE → 系统安装路径。

    现场 2026-09-27：内置 Chrome for Testing 起不来（crashpad
    ``settings.dat`` 被拒绝访问 0x5），只认内置就永远起不了采集浏览器，Worker 一直卡在
    waiting_for_browser。所以这里给出可回退的候选清单，由 launch_mabang_browser 依次尝试。
    """

    candidates: list[Path] = []
    bundled = find_bundled_chrome()
    if bundled:
        candidates.append(bundled)
    for raw in (
        os.environ.get("CHROME_EXECUTABLE", ""),
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
    ):
        if not raw:
            continue
        path = Path(raw)
        if path.is_file() and path not in candidates:
            candidates.append(path)
    return candidates


def _validated_local_cdp_url(cdp_url: str) -> tuple[str, int]:
    parsed = urlparse(str(cdp_url or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        raise MabangBrowserError("采集浏览器仅支持本机 http CDP 地址，例如 http://127.0.0.1:9223")
    return f"http://{parsed.hostname}:{parsed.port}", parsed.port


def _process_exit_code(process: Any) -> int | None:
    poll = getattr(process, "poll", None)
    if not callable(poll):
        return None
    try:
        return poll()
    except OSError:
        return None


def _run_windows_process_query() -> subprocess.CompletedProcess[str]:
    """Return Chrome process ids and command lines from Windows WMI."""

    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
    command = (
        "$ErrorActionPreference='Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" "
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        **no_console_window_kwargs(),
    )


def _is_collection_chrome_command(command_line: str, *, profile_dir: Path, port: int) -> bool:
    """Match only a Chrome command using this profile or this CDP port."""

    command = str(command_line or "").replace("/", "\\").casefold()
    if not command:
        return False
    if _command_uses_profile(command_line, profile_dir):
        return True
    port_match = re.search(r"(?:^|\s)--remote-debugging-port(?:=|\s+)[\"']?(\d+)", command)
    return bool(port_match and int(port_match.group(1)) == int(port))


def _command_uses_profile(command_line: str, profile_dir: Path) -> bool:
    """Return whether a Chrome command uses the exact dedicated profile."""

    command = str(command_line or "").replace("/", "\\").casefold()
    if not command:
        return False
    profile_match = re.search(
        r"(?:^|\s)--user-data-dir(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        command,
    )
    profile_value = next((value for value in profile_match.groups() if value), None) if profile_match else None
    if not profile_value:
        return False
    profile = _normalise_windows_path(str(profile_dir.expanduser().resolve()))
    return _normalise_windows_path(profile_value) == profile


def _normalise_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value or "").strip().strip('"').replace("/", "\\")))


def _collection_chrome_entries(
    process_query_fn: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the Chrome process rows (ProcessId/CommandLine) or an empty list."""

    query = process_query_fn or _run_windows_process_query
    try:
        result = query()
        if getattr(result, "returncode", 1) not in (0, None):
            return []
        raw = getattr(result, "stdout", "") or ""
        payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, json.JSONDecodeError):
        return []
    entries = payload if isinstance(payload, list) else [payload]
    return [entry for entry in entries if isinstance(entry, dict)]


def _collection_chrome_process_ids(
    profile_dir: Path, port: int, *, process_query_fn: Callable[[], Any] | None = None
) -> list[int]:
    """Pids of Chrome browser processes using this collection profile or CDP port."""

    process_ids: list[int] = []
    seen_ids: set[int] = set()
    for entry in _collection_chrome_entries(process_query_fn):
        try:
            pid = int(entry.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        if pid <= 0 or not _is_collection_chrome_command(
            str(entry.get("CommandLine") or ""), profile_dir=profile_dir, port=port
        ):
            continue
        if pid not in seen_ids:
            seen_ids.add(pid)
            process_ids.append(pid)
    return process_ids


def _collection_browser_mode(
    profile_dir: Path,
    port: int,
    *,
    process_query_fn: Callable[[], Any] | None = None,
) -> str | None:
    """Window mode of the running collection Chrome, or None when it cannot be read.

    Only the browser process carries the full switch list; renderer/GPU children
    are skipped via ``--type=`` so a child's missing ``--headless`` cannot report a
    headless browser as headed.
    """

    mode, _profile_matches = _collection_browser_identity(
        profile_dir,
        port,
        process_query_fn=process_query_fn,
    )
    return mode


def _collection_browser_identity(
    profile_dir: Path,
    port: int,
    *,
    process_query_fn: Callable[[], Any] | None = None,
) -> tuple[str | None, bool]:
    """Return the collection browser mode and whether its profile is exact."""

    if os.name != "nt":
        return None, False
    for entry in _collection_chrome_entries(process_query_fn):
        command = str(entry.get("CommandLine") or "")
        if "--type=" in command:
            continue
        if not _is_collection_chrome_command(command, profile_dir=profile_dir, port=port):
            continue
        mode = BROWSER_MODE_HEADLESS if _command_requests_headless(command) else BROWSER_MODE_HEADED
        return mode, _command_uses_profile(command, profile_dir)
    return None, False


def _terminate_collection_chrome(
    profile_dir: Path,
    port: int,
    *,
    process_query_fn: Callable[[], Any] | None = None,
) -> list[int]:
    """Stop only the collection Chrome matched by this profile or CDP port."""

    process_ids = _collection_chrome_process_ids(
        profile_dir, port, process_query_fn=process_query_fn
    )
    for pid in process_ids:
        _kill_process_tree(pid)
    return process_ids


def _wait_for_cdp_shutdown(
    base_url: str,
    urlopen_fn: Callable[..., Any],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    attempts: int = 20,
) -> None:
    for _ in range(max(1, attempts)):
        if not _cdp_is_available(base_url, urlopen_fn):
            return
        sleep_fn(0.25)


def _cleanup_stale_collection_chrome(
    profile_dir: Path,
    port: int,
    *,
    process_query_fn: Callable[[], Any] | None = None,
) -> list[int]:
    """Close stale collection Chrome processes without touching normal Chrome."""

    if os.name != "nt":
        return []
    process_ids = _collection_chrome_process_ids(
        profile_dir, port, process_query_fn=process_query_fn
    )
    for pid in process_ids:
        _kill_process_tree(pid)
    return process_ids


def _kill_process_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_console_window_kwargs(),
        )
        time.sleep(0.2)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_console_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return


def _reset_dawn_graphite_cache(profile_dir: Path) -> None:
    """Delete the DawnGraphite persistent cache before launch.

    The GPU child crashed repeatedly with STATUS_ACCESS_DENIED while opening
    entries in this directory, which surfaced as STATUS_BREAKPOINT on the
    browser process. Chrome recreates the cache on next start.
    """

    target = profile_dir / "GPUPersistentCache" / "DawnGraphiteCache"
    try:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    except OSError:
        return


_CRASH_MARKERS: tuple[bytes, ...] = (b"FATAL:", b"CHECK failed", b"ACCESS_DENIED", b"STATUS_ACCESS_DENIED")


def _extract_chrome_crash_reason(profile_dir: Path, since: float) -> str | None:
    """Best-effort: pull the first FATAL/ACCESS_DENIED line out of the newest crashdump.

    Chromium's minidumps embed the last log lines as plain ASCII, so a quick
    string scan is enough to surface the real cause (GPU sandbox denial, DCHECK,
    etc.) without a full breakpad parser.
    """

    reports_dir = profile_dir / "Crashpad" / "reports"
    try:
        candidates = [
            entry
            for entry in reports_dir.iterdir()
            if entry.suffix.lower() == ".dmp" and entry.stat().st_mtime >= since - 1.0
        ]
    except (FileNotFoundError, OSError):
        return None
    if not candidates:
        return None
    latest = max(candidates, key=lambda entry: entry.stat().st_mtime)
    try:
        blob = latest.read_bytes()
    except OSError:
        return None
    for run in re.finditer(rb"[\x20-\x7e]{16,}", blob):
        text = run.group(0)
        for marker in _CRASH_MARKERS:
            idx = text.find(marker)
            if idx < 0:
                continue
            snippet = text[idx : idx + 240].decode("ascii", "ignore").strip()
            if snippet:
                return snippet
    return None


def _cdp_is_available(base_url: str, urlopen_fn: Callable[..., Any]) -> bool:
    try:
        with urlopen_fn(Request(f"{base_url}/json/version", method="GET"), timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return isinstance(payload, dict) and bool(payload.get("Browser"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def _close_mabang_browser_pages(base_url: str, urlopen_fn: Callable[..., Any]) -> list[str]:
    """Close every page target so worker startup starts from a clean window."""

    try:
        with urlopen_fn(Request(f"{base_url}/json/list", method="GET"), timeout=1) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(targets, list):
        return []
    page_ids = [
        str(target["id"])
        for target in targets
        if isinstance(target, dict) and target.get("type") == "page" and target.get("id")
    ]
    for target_id in page_ids:
        try:
            with urlopen_fn(Request(f"{base_url}/json/close/{quote(str(target_id), safe='')}", method="DELETE"), timeout=2):
                pass
        except (HTTPError, URLError, OSError):
            continue
    return page_ids


def _open_mabang_new_product_page(base_url: str, urlopen_fn: Callable[..., Any]) -> None:
    encoded_url = quote(MABANG_NEW_PRODUCT_URL, safe="")
    try:
        with urlopen_fn(Request(f"{base_url}/json/new?{encoded_url}", method="PUT"), timeout=4):
            return
    except (HTTPError, URLError, OSError) as exc:
        raise MabangBrowserError("采集浏览器已启动，但无法打开马帮新建商品页") from exc
