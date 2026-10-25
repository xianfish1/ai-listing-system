#!/usr/bin/env python
"""安装已暂存的更新：等待 API 退出与端口释放 -> 替换文件 -> （失败回滚）-> 重启 API。

由 services/api/github_updates.spawn_update_runner 以 detached 方式拉起，也可以
手工执行：

    python scripts/apply_update.py --pending storage/updates/pending.json --root . --wait-pid 1234

只依赖标准库 + services.api.github_updates。

安全约定：等待 API 进程退出或端口释放超时，一律中止安装（不替换任何文件），
只写失败状态并（必要时）重启 API，避免在 API 仍在运行时覆盖文件。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api import github_updates  # noqa: E402
from services.api import settings as _settings  # noqa: E402 - import 即加载 .env，等待端口与重启端口才能对齐


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log_line(log_path: Path | None, message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        pass


def _process_alive_windows(pid: int) -> bool:
    """Windows 进程存活判断。

    必须显式声明 ctypes 签名：HANDLE 在 64 位下是指针，默认 c_int 会把句柄截断，
    从而把活着的进程误判为已退出，让更新器在 API 仍运行时覆盖文件。
    """

    if pid <= 0:
        return False
    try:
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 查不到就当作已退出
        return False


def _process_alive(pid: int) -> bool:
    """判断进程是否仍存活；Windows 与 POSIX 分支分开，方便单测 monkeypatch。"""

    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_alive_windows(int(pid))
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _wait_for_pid_exit(pid: int, timeout: float, log_path: Path | None) -> bool:
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            _log_line(log_path, f"API 进程 {pid} 已退出")
            return True
        time.sleep(0.5)
    if not _process_alive(pid):
        _log_line(log_path, f"API 进程 {pid} 已退出")
        return True
    _log_line(log_path, f"等待 API 进程 {pid} 退出超时（{timeout:.0f}s）")
    return False


def _api_port() -> int:
    try:
        return max(1, int(str(os.getenv("API_PORT", "8787")).strip()))
    except (TypeError, ValueError):
        return 8787


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _wait_for_port_release(port: int, timeout: float, log_path: Path | None) -> bool:
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if _port_free(port):
            _log_line(log_path, f"端口 {port} 已释放")
            return True
        time.sleep(0.5)
    if _port_free(port):
        _log_line(log_path, f"端口 {port} 已释放")
        return True
    _log_line(log_path, f"等待端口 {port} 释放超时（{timeout:.0f}s）")
    return False


def _updates_dir(root: Path, pending: dict) -> Path:
    for key, marker in (("staged_dir", "staged"), ("backup_dir", "backups")):
        raw = str(pending.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if path.parent.name == marker:
            return path.parent.parent
    return root / "storage" / "updates"


def _write_state(root: Path, pending: dict, status: str, message: str, log_path: Path | None) -> None:
    updates_dir = _updates_dir(root, pending)
    state = {
        "status": status,
        "version": str(pending.get("version") or ""),
        "error": message,
        "applied_at": _now_iso(),
        "backup_dir": str(pending.get("backup_dir") or ""),
    }
    try:
        github_updates.write_update_state(updates_dir, state)
        _log_line(log_path, f"已写入更新状态：{updates_dir / 'state.json'}（{status}）")
    except Exception as exc:  # noqa: BLE001 - 状态写不进去也要继续回滚/重启
        _log_line(log_path, f"写入更新状态出错：{exc}")


def _clear_pending(root: Path, pending: dict, log_path: Path | None) -> None:
    """清掉 pending.json：中止/失败后让设置页恢复「检查更新 / 立即更新」，可重试。"""

    pending_file = _updates_dir(root, pending) / github_updates.PENDING_FILE
    try:
        if pending_file.is_file():
            pending_file.unlink()
            _log_line(log_path, f"已清理待安装状态：{pending_file}")
    except OSError as exc:  # noqa: BLE001 - 清理失败不影响重启
        _log_line(log_path, f"清理 pending 失败：{exc}")


def _restart_api(root: Path, log_dir: Path, log_path: Path | None) -> int:
    try:
        pid = github_updates.restart_api(project_root=root, log_dir=log_dir)
        _log_line(log_path, f"API 已重启（pid {pid}）")
        return pid
    except Exception as exc:  # noqa: BLE001 - 重启失败也要把日志写清楚
        _log_line(log_path, f"重启 API 失败：{exc}")
        return 0


def _abort_before_apply(
    root: Path,
    pending: dict,
    log_dir: Path,
    log_path: Path | None,
    reason: str,
    restart: bool,
) -> int:
    """等待超时：不替换任何文件，写失败状态，需要时把 API 重新拉起来。"""

    _log_line(log_path, f"已中止安装：{reason}")
    _write_state(root, pending, "failed", reason, log_path)
    _clear_pending(root, pending, log_path)
    if restart:
        _restart_api(root, log_dir, log_path)
    else:
        _log_line(log_path, "API 仍在运行（或端口仍被占用），跳过重启")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装已暂存的更新并重启 API")
    parser.add_argument("--pending", required=True, help="pending.json 路径")
    parser.add_argument("--root", default="", help="项目根目录（默认脚本上一级）")
    parser.add_argument("--wait-pid", type=int, default=0, help="等待退出的 API 进程号")
    parser.add_argument("--timeout", type=float, default=180.0, help="等待 API/端口释放的秒数")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if str(args.root or "").strip() else PROJECT_ROOT
    log_dir = root / "storage" / "updates" / "logs"
    log_path = log_dir / f"apply-{_timestamp()}.log"
    _log_line(log_path, f"更新器启动：root={root} pending={args.pending}")

    pending_path = Path(args.pending)
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log_line(log_path, f"读取 pending.json 失败：{exc}")
        return 2
    if not isinstance(pending, dict):
        _log_line(log_path, "pending.json 格式不正确")
        return 2

    timeout = max(10.0, float(args.timeout))
    pid_exited = False
    if args.wait_pid:
        _log_line(log_path, f"等待 API 进程 {args.wait_pid} 退出")
        pid_exited = _wait_for_pid_exit(int(args.wait_pid), timeout, log_path)
        if not pid_exited:
            alive = _process_alive(int(args.wait_pid))
            return _abort_before_apply(
                root,
                pending,
                log_dir,
                log_path,
                f"等待 API 进程 {args.wait_pid} 退出超时，未安装更新",
                restart=not alive,
            )

    port = _api_port()
    port_free = _wait_for_port_release(port, min(timeout, 60.0), log_path)
    if not port_free and not pid_exited:
        return _abort_before_apply(
            root,
            pending,
            log_dir,
            log_path,
            f"等待端口 {port} 释放超时，API 可能仍在运行，未安装更新",
            restart=_port_free(port),
        )

    version = str(pending.get("version") or "")
    try:
        state = github_updates.apply_staged_update(
            project_root=root,
            pending=pending,
            log=lambda line: _log_line(log_path, line),
        )
    except Exception as exc:  # noqa: BLE001 - 安装失败必须回滚并重启
        _log_line(log_path, f"安装失败：{exc}")
        try:
            rolled = github_updates.rollback_update(
                project_root=root,
                backup_dir=Path(str(pending.get("backup_dir") or "")),
                version=version,
                log=lambda line: _log_line(log_path, line),
            )
            _log_line(log_path, f"已回滚 {rolled.get('files')} 个文件")
        except Exception as rollback_exc:  # noqa: BLE001
            _log_line(log_path, f"回滚失败：{rollback_exc}")
        _write_state(root, pending, "failed", str(exc), log_path)
        _restart_api(root, log_dir, log_path)
        return 1

    _log_line(log_path, f"更新完成：v{state.get('version') or version}")
    _restart_api(root, log_dir, log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
