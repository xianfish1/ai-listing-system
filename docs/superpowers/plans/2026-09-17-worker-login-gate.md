# Worker 启动与登录门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 自动化任务执行前必须按当前设置启动专用浏览器并确认马帮已登录；登录失败时 Worker 不启动、不领取任务。

**架构：** 在 API 层增加统一的浏览器/登录门禁，供 Worker 的 start、resume、restart 以及任务创建后的自动唤醒共同调用。浏览器层严格校验端口占用进程的专用配置目录和无头模式；Worker 进程保留领取前的故障兜底，并把任何未确认登录状态视为 `waiting_for_login`。

**技术栈：** Python 3.14、标准库 HTTP 服务、Playwright/CDP、Node.js 登录助手、pytest、原生 JavaScript UI。

---

## 文件结构

- 修改：`services/api/mabang_browser.py` — 判断正在运行的 Chrome 是否同时匹配专用配置目录、CDP 端口和窗口模式。
- 修改：`services/api/main.py` — 提供统一登录门禁，保护 Worker 控制入口，并在创建任务后安全唤醒 Worker。
- 修改：`scripts/automation_worker.py` — Worker 首次领取和浏览器恢复时验证登录结果，失败时不 claim。
- 修改：`apps/web/app.js` — 展示浏览器准备、自动登录和 Worker 启动结果。
- 修改：`tests/test_mabang_browser.py` — 覆盖错误配置目录占用端口的替换行为。
- 修改：`tests/test_worker_supervisor.py` — 覆盖 Worker 启动门禁与失败时不启动。
- 修改：`tests/test_automation_worker.py` — 覆盖登录失败不 claim、登录成功才 claim。
- 修改：`tests/test_store_template_workflow_api.py` — 覆盖创建任务后的门禁唤醒响应。
- 修改：`tests/test_store_template_workflow_ui.py` — 覆盖前端反馈接线。

### 任务 1：严格识别专用采集浏览器

**文件：**
- 修改：`tests/test_mabang_browser.py`
- 修改：`services/api/mabang_browser.py`

- [ ] **步骤 1：编写错误配置目录占用同一端口的失败测试**

在 `tests/test_mabang_browser.py` 添加：

```python
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
        stdout = json.dumps([{
            "ProcessId": 303,
            "CommandLine": (
                f'chrome.exe --headless=new --user-data-dir="{wrong_profile}" '
                "--remote-debugging-port=9223"
            ),
        }])
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
```

- [ ] **步骤 2：运行测试确认正确失败**

运行：`python -m pytest tests/test_mabang_browser.py::test_launch_restarts_same_mode_browser_using_the_wrong_profile -q`

预期：FAIL；现有实现只比较 `headless` 模式，因此复用错误配置目录并且 `killed == []`。

- [ ] **步骤 3：实现配置目录与模式联合校验**

在 `services/api/mabang_browser.py` 增加：

```python
def _command_uses_profile(command_line: str, profile_dir: Path) -> bool:
    command = str(command_line or "").replace("/", "\\").casefold()
    profile_match = re.search(
        r"(?:^|\s)--user-data-dir(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        command,
    )
    profile_value = next((value for value in profile_match.groups() if value), None) if profile_match else None
    if not profile_value:
        return False
    expected = _normalise_windows_path(str(profile_dir.expanduser().resolve()))
    return _normalise_windows_path(profile_value) == expected


def _collection_browser_matches(
    profile_dir: Path,
    port: int,
    requested_mode: str,
    *,
    process_query_fn: Callable[[], Any] | None = None,
) -> bool:
    for entry in _collection_chrome_entries(process_query_fn):
        command = str(entry.get("CommandLine") or "")
        if "--type=" in command:
            continue
        if not _is_collection_chrome_command(command, profile_dir=profile_dir, port=port):
            continue
        mode = BROWSER_MODE_HEADLESS if _command_requests_headless(command) else BROWSER_MODE_HEADED
        return _command_uses_profile(command, profile_dir) and mode == requested_mode
    return False
```

在 `launch_mabang_browser` 的 CDP 已就绪分支中，仅在 `_collection_browser_matches(...)` 为真时复用；否则调用 `_terminate_collection_chrome(...)`、等待端口关闭并用目标配置重启。保留进程查询失败时的兼容策略：只有无法读到任何进程条目时才沿用现有 CDP 会话。

- [ ] **步骤 4：运行浏览器测试确认通过**

运行：`python -m pytest tests/test_mabang_browser.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交浏览器识别修复**

```bash
git add services/api/mabang_browser.py tests/test_mabang_browser.py
git commit -m "fix: enforce collection browser profile and mode"
```

### 任务 2：为 Worker 控制入口增加统一登录门禁

**文件：**
- 修改：`tests/test_worker_supervisor.py`
- 修改：`services/api/main.py`

- [ ] **步骤 1：编写启动成功与登录失败的测试**

在 `tests/test_worker_supervisor.py` 添加两个测试，使用真实的门禁函数和轻量替身：

```python
def test_start_worker_with_login_gate_prepares_browser_before_start(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    class Supervisor:
        def start(self):
            events.append("worker_start")
            return {"status": "running", "pid": 1234}

    monkeypatch.setattr(
        main,
        "_prepare_worker_browser_login",
        lambda: events.append("login") or {
            "ok": True,
            "browser_mode": "headless",
            "auto_login": {"status": "logged_in", "error": ""},
        },
    )
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Supervisor())

    result = main._run_worker_action("start")

    assert events == ["login", "worker_start"]
    assert result["browser_mode"] == "headless"
    assert result["auto_login"]["status"] == "logged_in"


def test_start_worker_with_login_gate_does_not_start_when_login_fails(monkeypatch: pytest.MonkeyPatch):
    started: list[bool] = []

    class Supervisor:
        def start(self):
            started.append(True)
            return {"status": "running"}

    monkeypatch.setattr(
        main,
        "_prepare_worker_browser_login",
        lambda: {
            "ok": False,
            "browser_mode": "headless",
            "auto_login": {"status": "failed", "error": "login_did_not_complete_check_credentials"},
        },
    )
    monkeypatch.setattr(main, "_worker_supervisor", lambda: Supervisor())

    with pytest.raises(RuntimeError, match="马帮登录未确认"):
        main._run_worker_action("start")

    assert started == []
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_worker_supervisor.py -k "login_gate" -q`

预期：FAIL，提示 `_prepare_worker_browser_login` 或 `_run_worker_action` 不存在。

- [ ] **步骤 3：实现统一准备函数和动作调度**

在 `services/api/main.py` 增加：

```python
def _prepare_worker_browser_login() -> dict[str, Any]:
    result, cdp_url = _launch_collection_browser()
    _attach_mabang_auto_login(result, cdp_url)
    login = result.get("auto_login") or {}
    status = str(login.get("status") or "failed")
    result["ok"] = status in _AUTO_LOGIN_SUCCESS_STATUSES
    return result


def _run_worker_action(action: str) -> dict[str, Any]:
    supervisor = _worker_supervisor()
    operations = {
        "start": supervisor.start,
        "pause": supervisor.pause,
        "resume": supervisor.resume,
        "stop": supervisor.stop,
        "restart": _restart_worker_supervisor,
    }
    operation = operations.get(action)
    if operation is None:
        raise LookupError("Unknown worker action")
    preparation: dict[str, Any] | None = None
    if action in {"start", "resume", "restart"}:
        preparation = _prepare_worker_browser_login()
        if not preparation.get("ok"):
            login = preparation.get("auto_login") or {}
            status = str(login.get("status") or "failed")
            error = str(login.get("error") or "")
            raise RuntimeError(f"马帮登录未确认：{status}{(' - ' + error) if error else ''}")
    result = operation()
    if preparation is not None:
        result = {**result, "browser_mode": preparation.get("browser_mode"), "auto_login": preparation.get("auto_login")}
    return result
```

将 `/api/worker/*` 路由改为调用 `_run_worker_action(action)`；`LookupError` 映射 404，`RuntimeError` 继续映射 409。pause 和 stop 不触发登录门禁。

- [ ] **步骤 4：运行 Worker 控制测试确认通过**

运行：`python -m pytest tests/test_worker_supervisor.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交 API 启动门禁**

```bash
git add services/api/main.py tests/test_worker_supervisor.py
git commit -m "feat: gate worker startup on Mabang login"
```

### 任务 3：创建自动化任务后通过同一门禁唤醒 Worker

**文件：**
- 修改：`tests/test_store_template_workflow_api.py`
- 修改：`services/api/main.py`

- [ ] **步骤 1：编写任务已创建但登录失败时保持 ready 的 API 测试**

在 `tests/test_store_template_workflow_api.py` 的现有 API 服务器测试中，为 `/api/automation/jobs/from-listing-copies` 增加断言：门禁成功时响应包含 `worker_start.status == "running"`；门禁失败时响应仍保留 `created_count`，并包含 `worker_start.status == "waiting_for_login"`，新任务状态仍为 `ready`、`attempt_count == 0`。

门禁失败替身使用：

```python
monkeypatch.setattr(
    main,
    "_run_worker_action",
    lambda action: (_ for _ in ()).throw(RuntimeError("马帮登录未确认：failed")),
)
```

- [ ] **步骤 2：运行目标测试确认失败**

运行：`python -m pytest tests/test_store_template_workflow_api.py -k "automation" -q`

预期：FAIL；任务创建响应目前没有 `worker_start`。

- [ ] **步骤 3：在任务创建路由中安全唤醒 Worker**

在 `/api/automation/jobs/from-listing-copies` 路由中，先创建任务，再在存在 `ready` 任务时调用 `_run_worker_action("start")`。登录失败不得撤销已创建任务，也不得调用 claim：

```python
result = storage.create_automation_jobs_from_listing_copies(payload.get("listing_copy_ids") or [])
ready_jobs = [item for item in result.get("created") or [] if item.get("status") == "ready"]
if ready_jobs:
    try:
        result["worker_start"] = {"ok": True, **_run_worker_action("start")}
    except RuntimeError as exc:
        result["worker_start"] = {
            "ok": False,
            "status": "waiting_for_login",
            "error": str(exc)[:500],
        }
self._json(result, status=201)
```

保持任务创建成功的 HTTP 状态；失败只表示尚未执行，避免用户重复生成同一任务。

- [ ] **步骤 4：运行 API 测试确认通过**

运行：`python -m pytest tests/test_store_template_workflow_api.py -k "automation or worker" -q`

预期：全部 PASS。

- [ ] **步骤 5：提交任务启动编排**

```bash
git add services/api/main.py tests/test_store_template_workflow_api.py
git commit -m "feat: prepare worker when automation jobs are created"
```

### 任务 4：Worker 领取前失败关闭

**文件：**
- 修改：`tests/test_automation_worker.py`
- 修改：`scripts/automation_worker.py`

- [ ] **步骤 1：编写登录失败不 claim 的测试**

在 `tests/test_automation_worker.py` 添加：

```python
def test_worker_startup_login_failure_does_not_claim_even_when_cdp_is_reachable(
    monkeypatch, tmp_path: Path
) -> None:
    client = AutoLoginClient(status="failed")
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)

    result = worker.run_once()

    assert result["status"] == "waiting_for_login"
    assert client.auto_login_calls == 1
    assert client.claim_count == 0


def test_worker_startup_login_success_claims_after_gate(monkeypatch, tmp_path: Path) -> None:
    client = AutoLoginClient(status="already_logged_in")
    worker = automation_worker.AutomationWorker(
        client,
        worker_id="worker-test",
        upload_root=tmp_path,
        heartbeat_interval=0,
    )
    monkeypatch.setattr(worker, "_cdp_is_reachable", lambda **_: True)
    monkeypatch.setattr(worker, "_invoke_executor", lambda plan: dict(_SAVED_EXECUTION))

    result = worker.run_once()

    assert client.auto_login_calls == 1
    assert client.claim_count == 1
    assert result["status"] == "saved"
```

- [ ] **步骤 2：运行测试确认第一项失败**

运行：`python -m pytest tests/test_automation_worker.py -k "startup_login" -q`

预期：失败用例中 `client.claim_count == 1`，证明现有代码忽略了登录失败结果。

- [ ] **步骤 3：实现登录结果门禁**

在 `AutomationWorker` 增加读取登录结果的辅助方法：

```python
@staticmethod
def _recovery_login_status(recovery: dict[str, Any] | None) -> str:
    auto_login = (recovery or {}).get("auto_login") or {}
    return str(auto_login.get("status") or "unavailable")


def _waiting_for_login(self, recovery: dict[str, Any] | None) -> dict[str, Any]:
    status = self._recovery_login_status(recovery)
    auto_login = (recovery or {}).get("auto_login") or {}
    error = str(auto_login.get("error") or "")
    return {
        "ok": False,
        "status": "waiting_for_login",
        "job_id": "",
        "error": f"马帮登录未确认：{status}{(' - ' + error) if error else ''}",
    }
```

首次启动强制恢复后，只有 `LOGIN_RECOVERY_STATUSES` 中的状态才能继续。CDP 不可达恢复时同样同时要求“CDP 已恢复”和“登录状态成功”；否则返回 `waiting_for_browser` 或 `waiting_for_login`，均在 `client.claim(...)` 之前结束。

- [ ] **步骤 4：运行 Worker 测试确认通过**

运行：`python -m pytest tests/test_automation_worker.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交领取门禁**

```bash
git add scripts/automation_worker.py tests/test_automation_worker.py
git commit -m "fix: prevent claims before login is confirmed"
```

### 任务 5：前端展示真实启动阶段和失败原因

**文件：**
- 修改：`tests/test_store_template_workflow_ui.py`
- 修改：`apps/web/app.js`

- [ ] **步骤 1：编写 UI 接线失败测试**

在 `tests/test_store_template_workflow_ui.py` 添加：

```python
def test_worker_controls_and_job_creation_surface_login_gate_results():
    script = APP.read_text(encoding="utf-8")
    control = script[script.index("async function controlWorker"):script.index("async function refreshWorkerLogs")]
    create_one = script[
        script.index("async function createAutomationJobForListingCopy"):
        script.index("async function saveStoreTemplateIds")
    ]
    create_many = script[
        script.index("async function createAutomationJobsFromSelection"):
        script.index("async function scanAssets")
    ]

    assert "正在准备自动化浏览器并检查马帮登录" in control
    assert "result.auto_login?.status" in control
    assert 'data.worker_start?.status === "waiting_for_login"' in create_one
    assert 'data.worker_start?.status === "waiting_for_login"' in create_many
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_store_template_workflow_ui.py -k "login_gate_results" -q`

预期：FAIL，现有文案只显示 Worker 已启动，任务创建流程不读取 `worker_start`。

- [ ] **步骤 3：更新 Worker 控制和任务创建反馈**

在 `controlWorker` 中，对 start/resume/restart 先显示“正在准备自动化浏览器并检查马帮登录…”，成功后根据 `result.browser_mode` 和 `result.auto_login.status` 显示“马帮已登录，Worker 已启动（无头模式）”。保留 pause/stop 的原有反馈。

在单个和批量任务创建函数中读取 `data.worker_start`：

```javascript
const waitingForLogin = data.worker_start?.status === "waiting_for_login";
if (waitingForLogin) {
  message += ` 任务尚未执行：${data.worker_start.error || "马帮登录未确认"}。`;
} else if (data.worker_start?.ok) {
  message += data.worker_start.browser_mode === "headless"
    ? " 马帮已登录，Worker 已在无头模式开始处理。"
    : " 马帮已登录，Worker 已开始处理。";
}
```

单任务结果行使用同样规则，不把“已生成”误报成“正在执行”。

- [ ] **步骤 4：运行 UI 测试确认通过**

运行：`python -m pytest tests/test_store_template_workflow_ui.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交 UI 反馈**

```bash
git add apps/web/app.js tests/test_store_template_workflow_ui.py
git commit -m "feat: show worker login gate status"
```

### 任务 6：回归验证与现场检查

**文件：**
- 验证：`services/api/mabang_browser.py`
- 验证：`services/api/main.py`
- 验证：`scripts/automation_worker.py`
- 验证：`apps/web/app.js`

- [ ] **步骤 1：运行定向回归测试**

运行：

```bash
python -m pytest tests/test_mabang_browser.py tests/test_worker_supervisor.py tests/test_automation_worker.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py -q
```

预期：全部 PASS，无新警告和错误。

- [ ] **步骤 2：运行项目测试集**

运行：`python -m pytest tests -q`

预期：全部 PASS；如果存在与本改动无关的既有失败，记录准确测试名和失败信息，不修改无关代码。

- [ ] **步骤 3：检查差异边界**

运行：

```bash
git diff --check
git status --short
```

预期：本功能只涉及本计划列出的代码与测试文件；工作区原有改动保持原样。

- [ ] **步骤 4：重启本地 API 后验证真实配置**

通过现有应用启动方式重启 API，使 Worker 加载新代码。读取 `/api/settings`，确认 `mabang.headless=true`、`mabang.login_configured=true`；启动 Worker 后确认专用 Chrome 的浏览器主进程命令包含 `--headless=new`、正确的 `storage/browser-use-profile` 和 `--remote-debugging-port=9223`。

- [ ] **步骤 5：验证任务安全顺序**

生成一个已就绪自动化任务，确认界面先显示浏览器/登录准备，再显示 Worker 启动；Worker 日志必须先出现登录成功结果，随后才出现 claim。模拟登录失败时确认任务仍为 `ready`、`attempt_count=0`。

- [ ] **步骤 6：提交最终验证性调整（仅在验证产生必要改动时）**

```bash
git add services/api/mabang_browser.py services/api/main.py scripts/automation_worker.py apps/web/app.js tests/test_mabang_browser.py tests/test_worker_supervisor.py tests/test_automation_worker.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py
git commit -m "test: verify worker login gate end to end"
```
