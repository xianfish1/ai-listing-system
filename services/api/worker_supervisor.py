"""Lifecycle control for the single automation worker process.

The supervisor is deliberately independent from the HTTP API and storage layer so
it can be used by both the API and a desktop settings surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from services.api.subprocess_utils import no_console_window_kwargs


# Worker 每 10s 刷一次心跳；超过这个年龄就认为「进程还活着但主循环卡死」。
# 阈值给足余量：执行器单次最长 480s，但心跳由独立线程写，不受执行器阻塞影响。
WORKER_STALE_HEARTBEAT_SECONDS = 120.0
WORKER_HEARTBEAT_FILENAME = "worker-heartbeat.json"
# 这些暂停原因是可恢复的（浏览器/登录掉了）：看护轮可以自动重启。
# 其它暂停（页面结构变了、字段不唯一…）重启只会重复烧任务，留给人工。
RECOVERABLE_PAUSE_REASONS = frozenset({"needs_manual_login_or_verification"})


RESTART_REQUIRED_KEYS = frozenset(
    {
        "api_url",
        "cdp_url",
        "worker_id",
        "poll_seconds",
        "lease_seconds",
        "heartbeat_seconds",
        "upload_root",
        "command",
    }
)


def config_requires_restart(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Return whether a worker process must restart for the desired config."""
    return any(current.get(key) != desired.get(key) for key in RESTART_REQUIRED_KEYS)


def _pid_is_running(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(value)
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_running(pid: int, *, kernel32: Any = None) -> bool:
    if kernel32 is None:
        import ctypes

        kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _windows_terminate_process(pid: int, *, kernel32: Any = None) -> None:
    if kernel32 is None:
        import ctypes

        kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0001, False, int(pid))
    if not handle:
        raise OSError(f"cannot open worker process {pid}")
    try:
        if not kernel32.TerminateProcess(handle, 1):
            raise OSError(f"cannot terminate worker process {pid}")
    finally:
        kernel32.CloseHandle(handle)


def _terminate_pid(pid: int) -> None:
    """Kill a worker process the supervisor never spawned.

    Only needed for a worker that outlived the API process that launched it:
    its Popen handle is gone, so ``terminate()`` cannot reach it.
    """

    if os.name == "nt":
        _windows_terminate_process(pid)
        return
    import signal

    os.kill(int(pid), signal.SIGTERM)


def _file_lock_is_held(path: Path) -> bool:
    """Return whether another process currently owns the first-byte lock."""
    try:
        handle = Path(path).open("r+b", buffering=0)
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return False

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("a+b", buffering=0)
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.release()
            raise RuntimeError("Automation worker is already running") from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None


class WorkerSupervisor:
    """Own at most one worker subprocess and expose a small lifecycle API."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        command: Sequence[str] | None = None,
        process_factory: Callable[..., Any] | None = None,
        pid_is_running: Callable[[int], bool] | None = None,
        terminate_pid: Callable[[int], None] | None = None,
        worker_pid_path: Path | None = None,
        worker_lock_is_held: Callable[[Path], bool] | None = None,
        env: dict[str, str] | None = None,
        working_directory: Path | None = None,
        stop_timeout: float = 5.0,
        heal_window_seconds: float = 600.0,
        max_heals_in_window: int = 5,
        max_log_bytes: int = 5 * 1024 * 1024,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.runtime_dir / "worker-state.json"
        self.lock_path = self.runtime_dir / "worker.lock"
        self.log_path = self.runtime_dir / "worker.log"
        self.heartbeat_path = self.runtime_dir / WORKER_HEARTBEAT_FILENAME
        self.command = list(command or [sys.executable, "-m", "scripts.automation_worker"])
        self.process_factory = process_factory or subprocess.Popen
        self.pid_is_running = pid_is_running or _pid_is_running
        self.terminate_pid = terminate_pid or _terminate_pid
        self.worker_pid_path = Path(worker_pid_path) if worker_pid_path else None
        self.worker_lock_is_held = worker_lock_is_held or _file_lock_is_held
        self.env = env
        self.working_directory = Path(working_directory) if working_directory else self.runtime_dir.parent.parent
        self.stop_timeout = stop_timeout
        self.heal_window_seconds = float(heal_window_seconds)
        self.max_heals_in_window = int(max_heals_in_window)
        self.max_log_bytes = int(max_log_bytes)
        self._monotonic = monotonic or time.monotonic
        self._heal_events: list[float] = []
        self._process = None
        self._log_handle = None
        self._lock = _FileLock(self.lock_path)
        self._state = self._read_state()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"status": "stopped", "pid": None, "last_error": "", "updated_at": None}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"status": "stopped", "pid": None, "last_error": ""}
        except (OSError, json.JSONDecodeError):
            return {"status": "failed", "pid": None, "last_error": "worker state is unreadable"}

    def _write_state(self) -> None:
        self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def _maybe_rotate_log(self) -> None:
        """Rotate an oversized worker.log before a fresh append handle opens.

        A healthy worker that is blocked (e.g. waiting for the automation
        browser) can otherwise write the same line every poll until the log
        reaches many megabytes. Rotation keeps the on-disk log bounded; the
        previous file is kept once as ``worker.log.1``.
        """
        try:
            if not self.log_path.is_file():
                return
            if self.log_path.stat().st_size < self.max_log_bytes:
                return
            archive = self.log_path.with_name(self.log_path.name + ".1")
            if archive.exists():
                archive.unlink()
            self.log_path.replace(archive)
        except OSError:
            pass

    def _read_heartbeat(self) -> dict[str, Any]:
        """当前 Worker 心跳（文件缺失/坏掉都返回空 dict，不误判）。"""

        if not self.heartbeat_path.is_file():
            return {}
        try:
            value = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        age: float | None = None
        raw_at = str(value.get("at") or "").strip()
        if raw_at:
            try:
                parsed = datetime.strptime(raw_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                age = max(0.0, time.time() - parsed.timestamp())
            except ValueError:
                age = None
        return {**value, "heartbeat_age_seconds": age}

    def _heartbeat_state(self) -> dict[str, Any]:
        heartbeat = self._read_heartbeat()
        if not heartbeat:
            return {}
        age = heartbeat.get("heartbeat_age_seconds")
        stale = isinstance(age, (int, float)) and age > WORKER_STALE_HEARTBEAT_SECONDS
        return {
            "heartbeat_at": str(heartbeat.get("at") or ""),
            "heartbeat_age_seconds": age,
            "heartbeat_status": str(heartbeat.get("status") or ""),
            # 心跳里带着当前任务与阶段：长任务期间日志不会写新的结果行，面板要靠它
            # 才能显示「正在跑哪一单」，而不是上一单的结论。
            "heartbeat_job_id": str(heartbeat.get("job_id") or ""),
            "heartbeat_phase": str(heartbeat.get("phase") or ""),
            "paused": str(heartbeat.get("status") or "") == "paused",
            "paused_reason": str(heartbeat.get("paused_reason") or ""),
            "stale": bool(stale),
            "stale_reason": f"worker heartbeat is stale ({int(age)}s old)" if stale else "",
        }

    def _heartbeat_is_stale(self) -> bool:
        return bool(self._heartbeat_state().get("stale"))

    def _remove_heartbeat(self) -> None:
        """新进程启动前/停止后清掉旧心跳，避免把上一轮的状态当成当前存活。"""

        try:
            self.heartbeat_path.unlink()
        except OSError:
            pass

    def _refresh_process_state(self) -> None:
        if self._process is None:
            if self._state.get("status") != "running":
                return
            pid = self._state.get("pid")
            if not self.pid_is_running(pid):
                self._state.update(
                    status="failed",
                    pid=None,
                    last_error=f"worker process is no longer running (PID {pid or 'unknown'})",
                )
                self._write_state()
                return
            # PID 活着不代表它是我们的 Worker：Windows 会复用 PID。真正的判据是
            # Worker 自己持有的锁；锁没拿住就绝不能领养（否则状态永远 running，
            # 唤醒逻辑会一直跳过这个目标）。
            if self.worker_pid_path is not None and self._live_worker_pid() is None:
                self._state.update(
                    status="failed",
                    pid=None,
                    last_error=f"worker lock is not held (PID {pid})",
                )
                self._write_state()
            return
        code = self._process.poll()
        if code is None:
            return
        try:
            if self._state.get("status") == "running":
                self._state.update(status="failed", pid=None, last_error=f"worker exited with exit code {code}")
                self._write_state()
        finally:
            self._process = None
            self._release_runtime_resources()

    def status(self) -> dict[str, Any]:
        self._refresh_process_state()
        payload = dict(self._state)
        # 心跳字段只用于展示/看护判断，不写回 state 文件（旧 Worker 没心跳文件时
        # 这些键整体缺席，行为与改动前一致）。
        payload.update(self._heartbeat_state())
        return payload

    def hold(self, *, reason: str = "") -> dict[str, Any]:
        """记下「操作员手动停/暂停」——按需唤醒要跳过这个目标。

        压在 state 文件里，所以 API 重启后依然算数：操作员停掉的 Worker 不会因为
        「队列里还有旧任务」被自动拉起来（新任务会解除这条压制，见
        ``main._release_worker_holds_for_jobs``）。
        """

        self._state.update(
            manual_hold_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            manual_hold_reason=str(reason or "")[:200],
        )
        self._write_state()
        return self.status()

    def release_hold(self) -> dict[str, Any]:
        had_hold = "manual_hold_at" in self._state
        self._state.pop("manual_hold_at", None)
        self._state.pop("manual_hold_reason", None)
        if had_hold:
            self._write_state()
        return self.status()

    def manual_hold_at(self) -> str:
        return str(self._state.get("manual_hold_at") or "")

    def is_manually_held(self) -> bool:
        return bool(self._state.get("manual_hold_at"))

    def start(self) -> dict[str, Any]:
        # 任何一次显式启动都解除「手动停」的压制：操作员点了启动/恢复，或者新任务把它唤醒。
        had_hold = "manual_hold_at" in self._state
        self._state.pop("manual_hold_at", None)
        self._state.pop("manual_hold_reason", None)
        self._refresh_process_state()
        if self._process is not None and self._state.get("status") == "running":
            if had_hold:
                self._write_state()
            return self.status()
        persisted_pid = self._state.get("pid")
        recovered_pid = self._live_worker_pid()
        if recovered_pid is not None:
            # 锁 + PID 都指向真实 Worker。心跳过期说明主循环卡死：杀掉换新进程，
            # 否则它会一直占着锁、永远显示 running。
            if self._heartbeat_is_stale():
                self._terminate_live_worker()
                if self._live_worker_pid() is not None:
                    self._state.update(
                        status="failed",
                        last_error="worker heartbeat is stale and it did not exit after restart",
                        stale=True,
                        stale_reason="worker heartbeat is stale",
                    )
                    self._write_state()
                    return dict(self._state)
            else:
                self._state.update(status="running", pid=recovered_pid, last_error="", adopted=True, heal_suspended=False)
                self._write_state()
                return self.status()
        elif persisted_pid and self.pid_is_running(persisted_pid):
            # PID 活着但 Worker 锁没拿住：不是我们的进程（Windows PID 复用），
            # 绝不能领养，清掉状态后正常新起一个。
            self._state.update(status="stopped", pid=None, adopted=False, last_error="")
        self._lock.acquire()
        self._maybe_rotate_log()
        # 旧心跳属于上一轮进程；不清掉会在新 Worker 写下第一份心跳前被当成 stale。
        self._remove_heartbeat()
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = self.log_path.open("a", encoding="utf-8")
            self._log_handle = log_handle
            kwargs: dict[str, Any] = {
                "cwd": str(self.working_directory),
                "stdout": log_handle,
                "stderr": log_handle,
                **no_console_window_kwargs(),
            }
            if self.env is not None:
                kwargs["env"] = self.env
            self._process = self.process_factory(self.command, **kwargs)
            self._state.update(
                status="running",
                pid=getattr(self._process, "pid", None),
                last_error="",
                adopted=False,
                heal_suspended=False,
            )
            self._write_state()
            return self.status()
        except Exception as exc:
            if self._process is not None:
                try:
                    self._terminate_process()
                except Exception:
                    self._process = None
            self._release_runtime_resources()
            message = f"Worker 启动失败：{exc}"
            self._state.update(status="failed", pid=None, last_error=message)
            try:
                self._write_state()
            except Exception:
                pass
            raise RuntimeError(message) from exc

    def _live_worker_pid(self) -> int | None:
        path = self.worker_pid_path
        if path is None or not path.is_file() or not self.worker_lock_is_held(path):
            return None
        try:
            with path.open("rb") as metadata:
                metadata.seek(1)
                pid = int(metadata.read().decode("ascii").strip())
        except (OSError, ValueError):
            return None
        return pid if self.pid_is_running(pid) else None

    def _terminate_live_worker(self) -> int | None:
        """Kill a worker this supervisor has no handle for, and wait it out.

        A worker that outlived the API process that launched it keeps holding
        the pid lock, so ``start()`` happily adopts it back. That turns
        ``stop``/``restart`` into no-ops that still report success — and a
        worker parked in its in-memory ``paused_reason`` (a systemic executor
        failure such as ``field_not_unique``) only recovers from a fresh
        process. Stopping therefore has to mean the process is gone.
        """

        pid = self._live_worker_pid()
        if pid is None:
            return None
        try:
            self.terminate_pid(pid)
        except OSError:
            return pid
        deadline = time.monotonic() + self.stop_timeout
        while self.pid_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        return pid

    def pause(self) -> dict[str, Any]:
        self._refresh_process_state()
        if self._process is not None:
            self._terminate_process()
        self._terminate_live_worker()
        self._state.update(status="paused", pid=None)
        self._write_state()
        self._lock.release()
        return self.status()

    def resume(self) -> dict[str, Any]:
        if self._state.get("status") not in {"paused", "stopped", "failed"}:
            return self.status()
        return self.start()

    def stop(self) -> dict[str, Any]:
        self._refresh_process_state()
        if self._process is not None:
            self._terminate_process()
        self._terminate_live_worker()
        self._remove_heartbeat()
        self._state.update(status="stopped", pid=None)
        self._write_state()
        self._lock.release()
        return self.status()

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    def auto_heal(self) -> dict[str, Any]:
        """Restart the worker if it crashed, hung, or is recoverably paused.

        ``failed`` and stale-heartbeat (``stale``) workers are healed. A worker
        paused on a recoverable reason (browser/login lost) is also healed; other
        pauses are deliberate operator state and are left untouched. ``stopped``
        stays stopped. A rolling window caps how many automatic restarts may
        happen, so a worker that dies immediately on every launch is not
        restarted forever — after the cap it is left ``failed`` with an
        explanatory ``last_error`` for a human.
        """

        status = self.status()
        healable = status.get("status") == "failed" or status.get("stale") is True
        if not healable and status.get("status") == "running" and status.get("paused"):
            healable = str(status.get("paused_reason") or "").strip() in RECOVERABLE_PAUSE_REASONS
        if not healable:
            return status

        now = self._monotonic()
        self._heal_events = [t for t in self._heal_events if now - t < self.heal_window_seconds]
        if len(self._heal_events) >= self.max_heals_in_window:
            minutes = int(self.heal_window_seconds // 60) or 1
            reason = (
                f"Worker 连续崩溃：{self.heal_window_seconds/60:.0f} 分钟内已自动重启 "
                f"{len(self._heal_events)} 次，暂停自愈，请查看 worker.log 排查后手动重启。"
            )
            if self._state.get("last_error") != reason or self._state.get("heal_suspended") is not True:
                self._state.update(last_error=reason, heal_suspended=True)
                self._write_state()
            return dict(self._state)

        self._heal_events.append(now)
        result = self.restart()
        result.update(self.status())
        heal_count = int(self._state.get("heal_count") or 0) + 1
        self._state.update(
            heal_count=heal_count,
            last_heal_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            heal_suspended=False,
        )
        self._write_state()
        return dict(self._state)

    def mark_error(self, message: str) -> dict[str, Any]:
        self._state["last_error"] = str(message or "")[:1000]
        if self._state.get("status") == "running":
            self._state["status"] = "failed"
        self._write_state()
        return self.status()

    def logs(self, *, tail: int | None = None) -> dict[str, Any]:
        if not self.log_path.is_file():
            return {"path": str(self.log_path), "lines": []}
        if tail is None:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return {"path": str(self.log_path), "lines": lines}
        # 只读文件末尾 256KB：长跑 Worker 的日志会到几十 MB，读整份会让
        # /api/worker/status 越跑越慢（UI 每个轮询都读一次）。
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as handle:
                handle.seek(max(0, size - 256 * 1024))
                chunk = handle.read().decode("utf-8", errors="replace")
            lines = chunk.splitlines()
        except OSError:
            return {"path": str(self.log_path), "lines": []}
        if tail >= 0:
            lines = lines[-tail:]
        return {"path": str(self.log_path), "lines": lines}

    def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        process.terminate()
        try:
            process.wait(timeout=self.stop_timeout)
        except Exception:
            process.kill()
            process.wait(timeout=self.stop_timeout)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _release_runtime_resources(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._lock.release()

    def __del__(self) -> None:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            lock.release()

def worker_target_key(account_id: str, slot: int = 0) -> str:
    """一个 Worker 目标（账号 × 槽位）的稳定键。

    1 号槽位（slot 0）沿用账号 id 本身：老的 runtime 目录、日志接口与
    `/api/worker/status` 的 `account_id` 字段都保持不变；2 号槽位起加后缀。
    """

    account = str(account_id or "").strip()
    if not account:
        return ""
    index = 0
    try:
        index = max(0, int(slot))
    except (TypeError, ValueError):
        index = 0
    return account if index <= 0 else f"{account}#slot{index + 1}"


def normalize_worker_target(raw: Any) -> dict[str, Any] | None:
    """把 configure() 的入参规整成 {"key","account_id","slot"}；空账号返回 None。

    兼容旧的「只传账号 id 字符串」写法（等于 1 号槽位）。
    """

    if isinstance(raw, dict):
        account_id = str(raw.get("account_id") or raw.get("id") or "").strip()
        slot = raw.get("slot")
    else:
        account_id = str(raw or "").strip()
        slot = 0
    if not account_id:
        return None
    try:
        index = max(0, int(slot or 0))
    except (TypeError, ValueError):
        index = 0
    return {"key": worker_target_key(account_id, index), "account_id": account_id, "slot": index}


class MultiWorkerSupervisor:
    """一个「马帮账号 × 并行槽位」一个 Worker 进程。

    账号之间、同一账号的槽位之间都完全隔离：每个目标有自己的 runtime 目录
    （状态 / 锁 / 日志）与自己的命令行（CDP 端口、Profile 目录、--account-id、
    --worker-slot）。停一个目标不会影响其他目标，一个目标启动失败也不阻止其他目标。

    只有 1 个目标时调用方应继续走单 Worker 的 WorkerSupervisor（旧路径）。
    """

    def __init__(
        self,
        runtime_root: Path,
        *,
        build: Callable[[dict[str, Any]], "WorkerSupervisor"],
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self._build = build
        self._supervisors: dict[str, "WorkerSupervisor"] = {}
        self._targets: dict[str, dict[str, Any]] = {}

    def configure(self, targets: Sequence[Any]) -> list[str]:
        """对齐目标集合：新增缺的、停掉不再需要的，已存在的原样保留（含进程）。"""

        wanted: dict[str, dict[str, Any]] = {}
        for raw in targets:
            target = normalize_worker_target(raw)
            if target is None or target["key"] in wanted:
                continue
            wanted[target["key"]] = target
        for key in list(self._supervisors):
            if key not in wanted:
                self._supervisors.pop(key).stop()
        for key, target in wanted.items():
            if key in self._supervisors:
                continue
            self._supervisors[key] = self._build(dict(target))
        # 状态/日志顺序跟着配置顺序走（槽位号变化后界面不会乱序）。
        self._supervisors = {key: self._supervisors[key] for key in wanted if key in self._supervisors}
        self._targets = {key: target for key, target in wanted.items() if key in self._supervisors}
        return list(self._targets)

    def targets(self) -> list[dict[str, Any]]:
        return [dict(target) for target in self._targets.values()]

    def accounts(self) -> list[str]:
        seen: list[str] = []
        for target in self._targets.values():
            account_id = str(target.get("account_id") or "")
            if account_id and account_id not in seen:
                seen.append(account_id)
        return seen

    def get(self, key: str) -> "WorkerSupervisor | None":
        return self._supervisors.get(str(key or "").strip())

    def manual_holds(self) -> dict[str, str]:
        """还没解除的「操作员手动停」压制：key → 压制时间。"""

        return {
            key: supervisor.manual_hold_at()
            for key, supervisor in self._supervisors.items()
            if supervisor.is_manually_held()
        }

    def hold(self, keys: Sequence[str] | None = None, *, reason: str = "") -> list[dict[str, Any]]:
        """给目标记下「操作员手动停」（``keys`` 为空 = 全部目标）。"""

        wanted = {str(key) for key in (keys or []) if str(key)} or set(self._supervisors)
        results: list[dict[str, Any]] = []
        for key, supervisor in self._supervisors.items():
            if key not in wanted:
                continue
            try:
                supervisor.hold(reason=reason)
                results.append({"key": key, "ok": True, **self._targets.get(key, {})})
            except Exception as exc:  # noqa: BLE001 - 一个目标写不进压制不该拖垮整次操作
                results.append({"key": key, "ok": False, "error": str(exc)[:200]})
        return results

    def release_holds(self, keys: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """解除「操作员手动停」压制（``keys`` 为空 = 全部目标）。"""

        wanted = {str(key) for key in (keys or []) if str(key)} or set(self._supervisors)
        results: list[dict[str, Any]] = []
        for key, supervisor in self._supervisors.items():
            if key not in wanted:
                continue
            try:
                supervisor.release_hold()
                results.append({"key": key, "ok": True})
            except Exception as exc:  # noqa: BLE001
                results.append({"key": key, "ok": False, "error": str(exc)[:200]})
        return results

    def get_target(self, account_id: str, slot: int = 0) -> "WorkerSupervisor | None":
        return self.get(worker_target_key(account_id, slot))

    def statuses(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, supervisor in self._supervisors.items():
            target = self._targets.get(key) or {}
            try:
                status = dict(supervisor.status())
            except Exception as exc:  # noqa: BLE001 - 一个目标读不到状态不该让整页失败
                status = {"status": "error", "last_error": str(exc)[:200]}
            rows.append(
                {
                    "key": key,
                    "account_id": str(target.get("account_id") or ""),
                    "slot": int(target.get("slot") or 0),
                    **status,
                }
            )
        return rows

    def summarize(self) -> dict[str, Any]:
        rows = self.statuses()
        if not rows:
            return {"status": "offline", "pid": None, "last_error": "", "workers": []}
        running = [row for row in rows if str(row.get("status") or "") == "running"]
        if len(running) == len(rows):
            aggregate = "running"
        elif running:
            aggregate = "partial"
        else:
            aggregate = str(rows[0].get("status") or "offline")
        return {
            "status": aggregate,
            "pid": None,
            "last_error": next(
                (str(row.get("last_error") or "") for row in rows if row.get("last_error")), ""
            ),
            "workers": rows,
        }

    def logs(self, account_id: str, *, slot: int = 0, tail: int | None = None) -> dict[str, Any]:
        supervisor = self.get_target(account_id, slot) or self.get(str(account_id or "").strip())
        if supervisor is None:
            return {"path": "", "lines": []}
        return supervisor.logs(tail=tail)

    def run(self, action: str) -> list[dict[str, Any]]:
        if action not in {"start", "pause", "resume", "stop", "restart"}:
            raise LookupError("Unknown worker action")
        results: list[dict[str, Any]] = []
        for key, supervisor in self._supervisors.items():
            target = self._targets.get(key) or {}
            try:
                outcome = dict(getattr(supervisor, action)())
                results.append(
                    {
                        "key": key,
                        "account_id": str(target.get("account_id") or ""),
                        "slot": int(target.get("slot") or 0),
                        "ok": True,
                        **outcome,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 单个目标失败不影响其他目标
                results.append(
                    {
                        "key": key,
                        "account_id": str(target.get("account_id") or ""),
                        "slot": int(target.get("slot") or 0),
                        "ok": False,
                        "error": str(exc)[:300],
                    }
                )
        return results

    def auto_heal_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for key, supervisor in self._supervisors.items():
            target = self._targets.get(key) or {}
            try:
                results.append(
                    {
                        "key": key,
                        "account_id": str(target.get("account_id") or ""),
                        "slot": int(target.get("slot") or 0),
                        **supervisor.auto_heal(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append({"key": key, "status": "error", "last_error": str(exc)[:200]})
        return results


__all__ = [
    "MultiWorkerSupervisor",
    "WorkerSupervisor",
    "config_requires_restart",
    "normalize_worker_target",
    "worker_target_key",
]

