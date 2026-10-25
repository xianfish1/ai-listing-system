from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import browser_use_runner  # noqa: E402
from scripts.hybrid_automation_executor import HybridAutomationExecutor, BROWSER_USE_FALLBACK_ERRORS  # noqa: E402


DEFAULT_API_URL = "http://127.0.0.1:8787"
DEFAULT_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_POLL_SECONDS = 3.0
DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 30.0
# 相同状态（如等待浏览器）每轮都写日志只会刷屏。日志在状态变化时即时输出，
# 状态不变时最多每 LOG_EMIT_INTERVAL_SECONDS 秒补一行带时间戳的记录。
LOG_EMIT_INTERVAL_SECONDS = 60.0
ALLOWED_CLOSE_METHODS = {"success_dialog", "playwright_fallback"}
NEEDS_CONFIRMATION_ERRORS = {
    "save_result_ambiguous", "save_result_not_proven", "saved_success_dialog_not_unique",
    "saved_success_dialog_not_found", "saved_detail_page_not_closed",
}
SYSTEM_FAILURE_ERRORS = {
    "needs_manual_login_or_verification", "page_structure_changed", "save_button_not_unique",
    "mabang_detail_iframe_not_found", "workspace_frame_not_unique", "exact_action_not_unique",
    "field_not_unique", "category_dialog_not_visible", "trade_info_table_not_unique",
    "discount_table_not_unique", "discount_dialog_not_visible",
}
# 马帮自动登录恢复成功状态：执行器首次因 needs_manual_login_or_verification 被
# 阻止后，Worker 调 API 用设置页绑定的账号自动登录，成功即原地重跑一次任务。
LOGIN_RECOVERY_STATUSES = {"logged_in", "already_logged_in"}
# 预检发现 CDP 浏览器未启动时，通过 API 自动拉起浏览器并登录的冷却时间，
# 避免 Chrome 无法启动时每个轮询周期都反复拉起。
BROWSER_RECOVERY_COOLDOWN_SECONDS = 60.0
# 「这个账号是否正在做页面导入」的查询缓存：轮询间隔 3s，缓存 2s 既够用又
# 不会在弹窗打开的瞬间漏掉（导入刚开始时最多晚 2s 让路）。
PAGE_IMPORT_SESSION_CACHE_SECONDS = 2.0


def is_proven_saved_execution(execution: dict[str, Any]) -> bool:
    return (
        str(execution.get("status") or "").lower() == "saved"
        and execution.get("ok") is True
        and execution.get("exit_code") == 0
        and execution.get("save_clicked") is True
        and execution.get("publish_clicked") is False
        and execution.get("page_closed") is True
        and execution.get("close_method") in ALLOWED_CLOSE_METHODS
    )


def execution_has_media_warnings(execution: dict[str, Any]) -> bool:
    return any(
        isinstance(stage, dict) and str(stage.get("status") or "").lower() in {"warning", "skipped"}
        for name, stage in (execution.get("stages") or {}).items()
        if name != "warnings"
    )


class AutomationApiClient:
    def __init__(self, base_url: str = DEFAULT_API_URL, *, timeout_seconds: int = 30) -> None:
        self.base_url = str(base_url or DEFAULT_API_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.reason
            try:
                response_payload = json.loads(exc.read().decode("utf-8"))
                message = response_payload.get("error") or response_payload.get("message") or message
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise RuntimeError(f"Automation API returned HTTP {exc.code}: {message}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Automation API is unavailable: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("Automation API returned a non-object response")
        return decoded

    def claim(
        self,
        worker_id: str,
        lease_seconds: int,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/automation/jobs/claim",
            {
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                **({"account_id": account_id} if account_id else {}),
            },
        )

    def next_account(self) -> dict[str, Any]:
        """下一个 ready 任务归属的马帮账号及其运行时（CDP 端口 / Profile 目录）。"""

        return self._request("GET", "/api/automation/jobs/next-account")

    def page_import_session(self, account_id: str | None = None, slot: int = 0) -> dict[str, Any]:
        """该账号（该槽位）此刻是否正在做「从马帮当前页导入」（采集浏览器被操作员占用）。"""

        params: dict[str, str] = {}
        if account_id:
            params["account_id"] = str(account_id)
        params["slot"] = str(max(0, int(slot or 0)))
        return self._request("GET", f"/api/mabang-page-imports/active?{urllib.parse.urlencode(params)}")

    def payload(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/automation/jobs/{job_id}/payload")

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        phase: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/automation/jobs/{job_id}/heartbeat",
            {
                "worker_id": worker_id,
                "lease_token": lease_token,
                "phase": phase,
                "lease_seconds": lease_seconds,
            },
        )

    def finish(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        status: str,
        result: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/automation/jobs/{job_id}/finish",
            {
                "worker_id": worker_id,
                "lease_token": lease_token,
                "status": status,
                "result": result,
                "error": error,
            },
        )

    def auto_login_mabang(self, account_id: str | None = None, slot: int = 0) -> dict[str, Any]:
        """Ask the API to start (if needed) one account's browser and auto-login Mabang.

        `slot` 指向同一个账号的哪一只浏览器（0 = 账号自己的那只）：同账号多槽位并行时
        每个 Worker 只能恢复自己那个槽位的浏览器，否则会把别人的页面顶掉。
        Omitting the account id keeps the legacy single-account behaviour.
        """

        payload: dict[str, Any] = {}
        if account_id:
            payload["account_id"] = account_id
        if int(slot or 0) > 0:
            payload["slot"] = int(slot)
        return self._request("POST", "/api/mabang-browser/auto-login", payload)


class LeaseHeartbeat:
    def __init__(
        self,
        client: AutomationApiClient,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        interval_seconds: float,
        phase: str,
    ) -> None:
        self.client = client
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.phase = phase
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.interval_seconds <= 0:
            return
        self._thread = threading.Thread(target=self._run, name=f"lease-{self.job_id}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.client.heartbeat(
                    self.job_id,
                    self.worker_id,
                    self.lease_token,
                    self.phase,
                    self.lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)[:500]
                self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.interval_seconds + 1.0))


class AutomationWorker:
    def __init__(
        self,
        client: AutomationApiClient,
        *,
        worker_id: str,
        upload_root: Path,
        executor: Callable[..., dict[str, Any]] = browser_use_runner.run_playwright_save_draft,
        cdp_url: str = DEFAULT_CDP_URL,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
        pending_completion_path: Path | None = None,
        enable_hybrid_execution: bool = True,
        max_browser_use_cost: float = 0.25,
        account_id: str = "",
        slot: int = 0,
    ) -> None:
        self.client = client
        self.worker_id = str(worker_id or "").strip()
        self.upload_root = Path(upload_root)
        self.executor = executor
        self.cdp_url = cdp_url
        # 当前生效的马帮账号。
        # - 固定账号（并行部署）：`account_id` 由启动参数给定，Worker 只领这个账号的任务，
        #   不探测、不切换；一个账号一个进程，各自独立的 runtime 目录/锁/日志。
        # - 未固定（单 Worker 部署）：`account_id` 留空，领取前按下一个 ready 任务切换账号，
        #   顺序服务所有账号，同一时刻只驱动一个浏览器。
        self.account_id = str(account_id or "").strip()
        self.pinned_account_id = self.account_id
        # 这个 Worker 服务账号的哪个并行槽位（0 = 账号自己的那只浏览器）。同账号多槽位
        # 时它决定：自己的 CDP 端口（启动参数已给定）、浏览器恢复/自动登录该拉哪一只、
        # 以及「页面导入让路」只认哪个槽位的导入窗口。
        try:
            self.slot = max(0, int(slot or 0))
        except (TypeError, ValueError):
            self.slot = 0
        self.lease_seconds = int(lease_seconds)
        self.heartbeat_interval = float(heartbeat_interval)
        self.pending_completion_path = Path(pending_completion_path or self.upload_root / "pending-completion.json")
        self._pending_saved_completion = self._load_pending_saved_completion()
        self.paused_reason = ""
        self._last_browser_recovery_at = 0.0
        self._last_browser_recovery_result: dict[str, Any] | None = None
        self._startup_browser_recovery_done = False
        self._startup_login_ready = False
        self._page_import_session_cache: tuple[float, dict[str, Any] | None] | None = None

        # 初始化混合执行器。playwright_executor 显式传入 self.executor，
        # 这样注入的测试用 executor（或自定义执行器）在降级判断前依然会被调用，
        # 混合执行器只负责在它失败后决定是否降级到 Browser-Use。
        self.enable_hybrid = enable_hybrid_execution
        self.hybrid_executor = None
        if self.enable_hybrid:
            try:
                self.hybrid_executor = HybridAutomationExecutor(
                    enable_browser_use_fallback=True,
                    max_browser_use_cost=max_browser_use_cost,
                    cdp_url=cdp_url,
                    playwright_executor=self.executor,
                )
                print(f"✓ 混合执行器已启用 (Browser-Use 最大成本: ${max_browser_use_cost})")
            except Exception as exc:
                print(f"⚠️ 混合执行器初始化失败: {exc}")
                print(f"   将使用标准 Playwright 执行")
                self.enable_hybrid = False

        if not self.worker_id:
            raise ValueError("worker_id is required")

    def _load_pending_saved_completion(self) -> dict[str, Any] | None:
        if not self.pending_completion_path.is_file():
            return None
        try:
            pending = json.loads(self.pending_completion_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Pending saved completion file is unreadable") from exc
        execution = pending.get("execution") if isinstance(pending, dict) else None
        if (
            not isinstance(pending, dict)
            or not str(pending.get("job_id") or "")
            or not str(pending.get("worker_id") or "")
            or not str(pending.get("lease_token") or "")
            or not isinstance(execution, dict)
            or not is_proven_saved_execution(execution)
        ):
            raise RuntimeError("Pending saved completion file is invalid")
        return pending

    def _persist_pending_saved_completion(self, pending: dict[str, Any]) -> None:
        self._pending_saved_completion = pending
        self.pending_completion_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pending_completion_path.with_name(
            f"{self.pending_completion_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.pending_completion_path)

    def _clear_pending_saved_completion(self) -> None:
        self.pending_completion_path.unlink(missing_ok=True)
        self._pending_saved_completion = None

    def _retry_pending_saved_completion(self) -> dict[str, Any]:
        pending = self._pending_saved_completion
        if pending is None:
            raise RuntimeError("No saved completion is pending")
        final_status = str(pending.get("status") or "saved")
        try:
            finished = self.client.finish(
                pending["job_id"],
                pending["worker_id"],
                pending["lease_token"],
                final_status,
                pending["execution"],
                "",
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": "completion_pending",
                "job_id": pending["job_id"],
                "error": str(exc)[:500],
            }
        self._clear_pending_saved_completion()
        return {
            "ok": True,
            "status": final_status,
            "job_id": pending["job_id"],
            "job": finished,
            "error": "",
        }

    def _heartbeat(self, job_id: str, lease_token: str, phase: str) -> dict[str, Any]:
        return self.client.heartbeat(
            job_id,
            self.worker_id,
            lease_token,
            phase,
            self.lease_seconds,
        )

    def _cdp_is_reachable(self, *, timeout_seconds: float = 2.0) -> bool:
        """Return whether the dedicated automation browser is listening on the CDP port.

        The worker attaches to an already-running Chrome via ``connectOverCDP``.
        If that browser is not running, every claimed job would fail with
        ``connect ECONNREFUSED`` and be burned to ``failed``. Checking first lets
        the worker stay idle without consuming jobs until the browser is back.
        """

        parsed = urllib.parse.urlparse(self.cdp_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9223
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return True
        except OSError:
            return False

    def _auto_login_for_current_account(self) -> dict[str, Any]:
        """自动登录当前账号；没绑定账号实体时完全走旧调用形态（单账号兼容）。"""

        if self.account_id:
            return self.client.auto_login_mabang(self.account_id, self.slot)
        return self.client.auto_login_mabang()

    def _activate_account(self, account_id: str, runtime: dict[str, Any]) -> None:
        """把 Worker 切到某个马帮账号的浏览器。

        换账号等于换 Chrome Profile（因此换 cookie jar）与登录态，所以必须让下一次
        登录预检重新跑一遍，绝不能沿用上一个账号的登录判断。
        """

        wanted = str(account_id or "").strip()
        cdp_url = str((runtime or {}).get("cdp_url") or "").strip()
        if wanted == self.account_id and (not cdp_url or cdp_url == self.cdp_url):
            return
        self.account_id = wanted
        if cdp_url:
            self.cdp_url = cdp_url
            if self.hybrid_executor is not None:
                self.hybrid_executor.cdp_url = cdp_url
        self._startup_login_ready = False
        self._startup_browser_recovery_done = False
        self._last_browser_recovery_result = None
        self._page_import_session_cache = None
        label = str((runtime or {}).get("login_account") or wanted or "默认账号")
        print(f"→ 切换到马帮账号 {label}（CDP {self.cdp_url}）")

    def _active_page_import_session(self) -> dict[str, Any] | None:
        """当前账号是否正在做「从马帮当前页导入」；是则返回那条导入任务。

        导入期间采集浏览器就是操作员的可见窗口：
        - Worker 的浏览器恢复会按设置里的「无头」把它杀掉重起 → 窗口凭空消失；
        - 执行器会在同一个浏览器里开页面 → 顶掉操作员正在填的编辑页。

        所以两件事都要让路（见 ``run_once``）。查询带 2s 缓存，轮询里只多一次 GET；
        查询失败按「没有导入」处理（保持旧行为，不能让 API 抖动把整个 Worker 卡住）。
        """

        now = time.monotonic()
        cached = self._page_import_session_cache
        if cached is not None and now - cached[0] < PAGE_IMPORT_SESSION_CACHE_SECONDS:
            return cached[1]
        fetch = getattr(self.client, "page_import_session", None)
        if not callable(fetch):
            # 注入的测试/自定义客户端没有这个方法：按「没有导入」处理。
            return None
        session: dict[str, Any] | None = None
        try:
            payload = fetch(self.account_id, self.slot)
        except Exception as exc:  # noqa: BLE001 - 探测失败不阻塞轮询
            print(f"⚠️ 读取页面导入会话失败：{exc}")
        else:
            run = payload.get("run") if isinstance(payload, dict) and payload.get("active") else None
            session = run if isinstance(run, dict) else None
        self._page_import_session_cache = (now, session)
        return session

    @staticmethod
    def _waiting_for_page_import(session: dict[str, Any]) -> dict[str, Any]:
        run_id = str(session.get("id") or "")[:8]
        return {
            "ok": False,
            "status": "waiting_for_page_import",
            "job_id": "",
            "error": (
                f"页面导入进行中（{run_id or '当前会话'}）：采集浏览器正被导入窗口使用，"
                "Worker 不动浏览器、不领任务；导入确认后自动恢复。"
            ),
        }

    def _activate_account_for_next_job(self) -> None:
        """领取前先确定下一个 ready 任务属于哪个账号，并切到那个账号的浏览器。

        只有真实 Playwright 执行器需要切换（注入的测试执行器不碰 CDP）。切换失败时
        保持当前账号：领取会按账号过滤，最坏情况下这一步只是暂时领不到活，
        绝不会把某个账号的任务填进另一个账号的登录态里。
        """

        if self.executor is not browser_use_runner.run_playwright_save_draft:
            return
        if self.pinned_account_id:
            # 固定账号的 Worker：cdp_url 由启动参数给定，不探测也不切换。
            return
        try:
            payload = self.client.next_account()
        except Exception as exc:  # noqa: BLE001 - 探测失败不阻塞轮询
            print(f"⚠️ 读取下一个任务的马帮账号失败：{exc}")
            return
        runtime = payload.get("runtime") if isinstance(payload, dict) else None
        self._activate_account(
            str((payload or {}).get("account_id") or ""),
            runtime if isinstance(runtime, dict) else {},
        )

    def _recover_browser_before_wait(self, *, force: bool = False) -> dict[str, Any] | None:
        """Try to bring the automation browser back via the API auto-login endpoint.

        The endpoint launches Chrome when the CDP port is closed and signs in to
        Mabang with the credentials bound in 设置 → 马帮连接. The cooldown keeps a
        broken Chrome setup from being respawned on every poll cycle. The worker
        startup uses ``force=True`` so every fresh worker starts from a clean
        browser window instead of reusing stale tabs.
        """

        now = time.monotonic()
        if not force and now - self._last_browser_recovery_at < BROWSER_RECOVERY_COOLDOWN_SECONDS:
            return None
        self._last_browser_recovery_at = now
        try:
            result = self._auto_login_for_current_account()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "auto_login": {"status": "failed", "error": str(exc)[:200]}}
        self._last_browser_recovery_result = result
        return result

    def _auto_login_mabang(self) -> dict[str, Any]:
        """Sign in to Mabang with the bound account; returns the auto_login payload."""

        try:
            response = self._auto_login_for_current_account()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)[:200]}
        auto_login = response.get("auto_login") if isinstance(response, dict) else None
        if isinstance(auto_login, dict) and auto_login.get("status"):
            return auto_login
        return {"status": "failed", "error": "auto_login response missing"}

    @staticmethod
    def _recovery_login_status(recovery: dict[str, Any] | None) -> str:
        auto_login = (recovery or {}).get("auto_login") or {}
        return str(auto_login.get("status") or "unavailable")

    def _waiting_for_login(self, recovery: dict[str, Any] | None) -> dict[str, Any]:
        status = self._recovery_login_status(recovery)
        auto_login = (recovery or {}).get("auto_login") or {}
        error = str(auto_login.get("error") or "")
        detail = f" - {error}" if error else ""
        return {
            "ok": False,
            "status": "waiting_for_login",
            "job_id": "",
            "error": f"马帮登录未确认：{status}{detail}",
        }

    def _waiting_for_browser(self, recovery: dict[str, Any] | None) -> dict[str, Any]:
        recovery_status = self._recovery_login_status(recovery)
        return {
            "ok": False,
            "status": "waiting_for_browser",
            "job_id": "",
            "error": (
                f"自动化浏览器未就绪：{self.cdp_url} 无法连接。"
                f"已尝试自动启动并登录马帮（结果：{recovery_status}）。"
                "请在 设置 → 马帮连接 绑定自动登录账号，或手动启动自动化浏览器。"
            ),
        }

    def _invoke_executor(self, task_plan: dict[str, Any]) -> dict[str, Any]:
        if self.enable_hybrid and self.hybrid_executor:
            return self.hybrid_executor.execute_save_draft(
                task_plan,
                timeout_seconds=max(300, self.lease_seconds * 4),
            )
        # 把本地 API 地址传给执行器（现场 AI 判断必填项要用）：只对标准
        # playwright 执行器传，注入的测试 executor 可能没有这个参数。
        extra_kwargs: dict[str, Any] = {}
        try:
            import inspect

            params = inspect.signature(self.executor).parameters
            if "api_url" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                extra_kwargs["api_url"] = self.client.base_url
        except (TypeError, ValueError):
            extra_kwargs = {}
        return self.executor(
            task_plan,
            cdp_url=self.cdp_url,
            timeout_seconds=max(300, self.lease_seconds * 4),
            **extra_kwargs,
        )

    @staticmethod
    def _is_login_blocked(execution: dict[str, Any] | None) -> bool:
        return bool(execution) and str((execution or {}).get("status") or "").lower() == "blocked" and str(
            (execution or {}).get("error") or ""
        ) == "needs_manual_login_or_verification"

    def _retry_after_auto_login(self, task_plan: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        """Sign in with the bound account and re-run the executor once.

        Only one recovery attempt per run: if the second pass is still blocked on
        login, the job is finished blocked and the worker pauses as before. The
        auto-login outcome is attached to the execution for the job detail view.
        """

        recovery = self._auto_login_mabang()
        if str(recovery.get("status") or "") not in LOGIN_RECOVERY_STATUSES:
            return {**execution, "auto_login": recovery}
        retry = self._invoke_executor(task_plan)
        return {**retry, "auto_login": recovery, "auto_login_retry": True}

    def run_once(self) -> dict[str, Any]:
        if self.paused_reason:
            return {"ok": False, "status": "paused", "job_id": "", "error": self.paused_reason}
        if self._pending_saved_completion is not None:
            return self._retry_pending_saved_completion()
        # 领取前先切到「下一个 ready 任务所属账号」的浏览器：每个账号有自己的 CDP
        # 端口、Chrome Profile（cookie 隔离）与登录凭据。切好之后再走登录预检，
        # 预检不通过就不领取——任务留在 ready，不烧租约也不烧重试次数。
        self._activate_account_for_next_job()
        # 页面导入期间让路：既不能「恢复浏览器」（会按设置里的模式把操作员的可见窗口
        # 杀掉重起），也不能领任务（执行器会在同一个浏览器里顶掉操作员的编辑页）。
        # 放在登录预检之前，这一轮连浏览器都不碰。
        page_import_session = self._active_page_import_session()
        if page_import_session is not None:
            return self._waiting_for_page_import(page_import_session)
        # 每次 Worker 进程启动先主动清理浏览器窗口并打开登录页，不等到浏览器掉线
        # 才恢复。预检逻辑只针对真实 Playwright 执行器，注入的测试执行器不接触 CDP。
        uses_collection_browser = self.executor is browser_use_runner.run_playwright_save_draft
        if uses_collection_browser and not self._startup_login_ready:
            force = not self._startup_browser_recovery_done
            self._startup_browser_recovery_done = True
            recovery = self._recover_browser_before_wait(force=force)
            if recovery is None:
                recovery = self._last_browser_recovery_result
            if self._recovery_login_status(recovery) not in LOGIN_RECOVERY_STATUSES:
                if not self._cdp_is_reachable():
                    return self._waiting_for_browser(recovery)
                return self._waiting_for_login(recovery)
            self._startup_login_ready = True
        # Preflight only guards the real Playwright executor, which attaches to a
        # running Chrome over CDP. Injected executors (tests / custom drivers) do
        # not open that browser, so they must not be gated on the CDP port.
        if (
            uses_collection_browser
            and not self._cdp_is_reachable()
        ):
            self._startup_login_ready = False
            # 浏览器不在时先尝试通过 API 自动拉起并登录（带冷却），失败才继续等待。
            recovery = self._recover_browser_before_wait()
            if recovery is None and self._last_browser_recovery_result is not None:
                recovery = self._last_browser_recovery_result
            if not self._cdp_is_reachable():
                return self._waiting_for_browser(recovery)
            if self._recovery_login_status(recovery) not in LOGIN_RECOVERY_STATUSES:
                return self._waiting_for_login(recovery)
            self._startup_login_ready = True
        # 领取按当前账号过滤：绝不把 A 账号的店铺任务交给登录着 B 账号的浏览器。
        # 没有账号（旧单账号库）时保持原调用形态。
        claimed = (
            self.client.claim(self.worker_id, self.lease_seconds, account_id=self.account_id)
            if self.account_id
            else self.client.claim(self.worker_id, self.lease_seconds)
        )
        job = claimed.get("job") or None
        lease_token = str(claimed.get("lease_token") or "")
        if not job:
            return {"ok": True, "status": "idle", "job_id": ""}
        job_id = str(job.get("id") or "")
        if not job_id or not lease_token:
            raise RuntimeError("Automation API returned an incomplete claim")

        execution: dict[str, Any] | None = None
        try:
            self._heartbeat(job_id, lease_token, "load_payload")
            payload = self.client.payload(job_id)
            task_plan = browser_use_runner.build_browser_use_task_plan(
                payload,
                self.upload_root / job_id / "variant-thumbnails",
                allow_save=True,
            )
            if not task_plan.get("ok"):
                error = str(task_plan.get("error") or "Automation payload is not ready")[:500]
                result = {"save_clicked": False, "publish_clicked": False}
                finished = self.client.finish(
                    job_id,
                    self.worker_id,
                    lease_token,
                    "blocked",
                    result,
                    error,
                )
                return {"ok": False, "status": "blocked", "job_id": job_id, "job": finished, "error": error}

            self._heartbeat(job_id, lease_token, "playwright_save_draft")
            lease = LeaseHeartbeat(
                self.client,
                job_id=job_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                lease_seconds=self.lease_seconds,
                interval_seconds=self.heartbeat_interval,
                phase="playwright_save_draft",
            )
            lease.start()
            try:
                # 使用混合执行器或标准执行器；登录被阻止时用绑定账号自动登录并重跑一次
                # 把执行上下文交给执行器：它据此把进度实时 POST 回本地 API（面板实时步骤）。
                task_plan["runtime"] = {
                    "job_id": job_id,
                    "worker_id": self.worker_id,
                    "lease_token": lease_token,
                    "api_url": getattr(self.client, "base_url", "") or "",
                }
                execution = self._invoke_executor(task_plan)
                if self._is_login_blocked(execution):
                    execution = self._retry_after_auto_login(task_plan, execution)
            finally:
                lease.stop()
            if lease.last_error:
                execution = {**execution, "lease_heartbeat_error": lease.last_error}

            execution_status = str(execution.get("status") or "failed").lower()
            if is_proven_saved_execution(execution):
                final_status = "saved_with_warnings" if execution_has_media_warnings(execution) else "saved"
                error = ""
            elif execution_status == "saved" and execution.get("ok") is not True:
                final_status = "failed"
                error = "Browser execution reported saved with ok=false"
            elif execution_status == "saved" and execution.get("exit_code") != 0:
                final_status = "failed"
                error = "Browser execution reported saved with a nonzero exit code"
            elif execution_status == "saved" and execution.get("publish_clicked") is not False:
                final_status = "failed"
                error = "Browser execution did not prove publish_clicked=false"
            elif execution_status == "saved" and execution.get("save_clicked") is not True:
                final_status = "failed"
                error = "Browser execution did not prove the save action"
            elif execution_status == "saved":
                final_status = "failed"
                if execution.get("page_closed") is not True:
                    error = "Browser execution did not close the saved detail page"
                else:
                    error = "Browser execution reported saved with an unknown close method"
            elif execution_status in {"blocked", "harness_blocked"}:
                error = str(execution.get("error") or "Browser execution blocked")[:500]
                if execution_status == "harness_blocked" and error == "needs_confirmation":
                    final_status = "needs_confirmation"
                elif error in NEEDS_CONFIRMATION_ERRORS and execution.get("save_clicked") is True:
                    final_status = "needs_confirmation"
                elif execution.get("system_failure") is True or error in SYSTEM_FAILURE_ERRORS:
                    final_status = "blocked"
                    self.paused_reason = error
                else:
                    final_status = "blocked"
            else:
                final_status = "failed"
                error = str(execution.get("error") or "Browser execution failed")[:500]
            if final_status in {"saved", "saved_with_warnings"}:
                self._persist_pending_saved_completion(
                    {
                        "job_id": job_id,
                        "worker_id": self.worker_id,
                        "lease_token": lease_token,
                        "status": final_status,
                        "execution": execution,
                    }
                )
            self._heartbeat(job_id, lease_token, f"finish_{final_status}")
            finished = self.client.finish(
                job_id,
                self.worker_id,
                lease_token,
                final_status,
                execution,
                error,
            )
            if final_status in {"saved", "saved_with_warnings"}:
                self._clear_pending_saved_completion()
            if self.paused_reason:
                return {"ok": False, "status": "paused", "job_id": job_id, "job": finished, "error": error}
            return {
                "ok": final_status in {"saved", "saved_with_warnings"},
                "status": final_status,
                "job_id": job_id,
                "job": finished,
                "error": error,
            }
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:500]
            if self._pending_saved_completion is not None and execution is not None and is_proven_saved_execution(execution):
                return {
                    "ok": False,
                    "status": "completion_pending",
                    "job_id": job_id,
                    "error": error,
                }
            result = {"save_clicked": False, "publish_clicked": False}
            try:
                self._heartbeat(job_id, lease_token, "finish_failed")
                finished = self.client.finish(
                    job_id,
                    self.worker_id,
                    lease_token,
                    "failed",
                    result,
                    error,
                )
            except Exception as finish_exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": "failed",
                    "job_id": job_id,
                    "error": error,
                    "finish_error": str(finish_exc)[:500],
                }
            return {"ok": False, "status": "failed", "job_id": job_id, "job": finished, "error": error}


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("r+b" if self.path.exists() else "w+b", buffering=0)
            self.handle.seek(0)
            if self.handle.read(1) == b"":
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.handle:
                self.handle.close()
            self.handle = None
            raise RuntimeError("Another automation worker is already running") from exc
        self.handle.seek(1)
        self.handle.write(str(os.getpid()).encode("ascii"))
        self.handle.truncate()
        self.handle.flush()

    def release(self) -> None:
        if not self.handle:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def emit_worker_result(result: dict[str, Any], *, repeated: int = 0) -> None:
    """Write one worker-result line with a timestamp inside the JSON payload.

    The line keeps the ``AUTOMATION_WORKER_RESULT `` prefix so the API's
    ``_latest_worker_activity`` can still find and parse it; the extra
    ``at``/``repeated`` fields are ignored by that parser. ``repeated``
    counts how many consecutive polls produced the identical result before
    this line was written.
    """
    payload = dict(result)
    payload["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if repeated:
        payload["repeated"] = repeated
    print("AUTOMATION_WORKER_RESULT " + json.dumps(payload, ensure_ascii=True), flush=True)


def _force_utf8_streams() -> None:
    """Make stdout/stderr tolerate emoji on non-UTF-8 consoles (Windows GBK/cp936).

    The supervisor pipes the worker's stdout/stderr to ``worker.log``. When the
    stream is a pipe rather than a console, CPython falls back to the locale
    encoding (GBK on zh-CN Windows), which cannot encode characters like ``✓``
    or ``⚠``. A single decorative ``print`` then raises ``UnicodeEncodeError``
    during ``AutomationWorker`` construction and kills the worker before it can
    claim any job. Reconfiguring to UTF-8 keeps those prints harmless.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            # Stream already detached/closed or does not support reconfigure;
            # nothing we can safely do, and it must never block worker startup.
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(description="Automatically claim Mabang jobs and save drafts without publishing.")
    parser.add_argument("--once", action="store_true", help="claim at most one job and exit")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--worker-id", default=default_worker_id())
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--lock-file", default=str(PROJECT_ROOT / "data" / "automation_worker.lock"))
    parser.add_argument("--account-id", default="", help="固定服务某一个马帮账号（并行部署时每个账号一个 Worker）")
    parser.add_argument(
        "--worker-slot",
        type=int,
        default=0,
        help="同账号的并行槽位号（0 = 账号自己的那只浏览器；1 起是额外槽位，各有独立 CDP 端口与 Profile）",
    )
    parser.add_argument(
        "--pending-completion-file",
        default="",
        help="「保存成功但没上报完」的续期文件；同账号多槽位时必须每个 Worker 一份，默认放上传根目录",
    )
    parser.add_argument("--enable-hybrid", action="store_true", default=True, help="启用混合执行器（Playwright + Browser-Use 降级）")
    parser.add_argument("--no-hybrid", dest="enable_hybrid", action="store_false", help="禁用混合执行器（仅使用 Playwright）")
    parser.add_argument("--max-browser-use-cost", type=float, default=0.25, help="Browser-Use 单次最大成本（美元），默认 $0.25")
    args = parser.parse_args(argv)

    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        stop.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), request_stop)

    client = AutomationApiClient(args.api_url)
    worker = AutomationWorker(
        client,
        worker_id=args.worker_id,
        upload_root=PROJECT_ROOT / "tmp_upload" / "automation-jobs",
        cdp_url=args.cdp_url,
        lease_seconds=args.lease_seconds,
        heartbeat_interval=args.heartbeat_seconds,
        enable_hybrid_execution=args.enable_hybrid,
        max_browser_use_cost=args.max_browser_use_cost,
        account_id=args.account_id,
        slot=args.worker_slot,
        pending_completion_path=(
            Path(args.pending_completion_file) if str(args.pending_completion_file or "").strip() else None
        ),
    )
    try:
        with SingleInstanceLock(Path(args.lock_file)):
            if args.once:
                result = worker.run_once()
                emit_worker_result(result)
                return 1 if result.get("status") == "failed" else 0
            last_result: dict[str, Any] | None = None
            last_emit_at = 0.0
            repeated_count = 0
            while not stop.is_set():
                try:
                    result = worker.run_once()
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "status": "api_error", "job_id": "", "error": str(exc)[:500]}
                now = time.monotonic()
                if result == last_result:
                    repeated_count += 1
                else:
                    repeated_count = 0
                    last_result = result
                if repeated_count == 0 or now - last_emit_at >= LOG_EMIT_INTERVAL_SECONDS:
                    emit_worker_result(result, repeated=repeated_count)
                    last_emit_at = now
                stop.wait(max(0.1, float(args.poll_seconds)))
    except RuntimeError as exc:
        emit_worker_result({"ok": False, "status": "already_running", "job_id": "", "error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
