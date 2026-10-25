# Browser Harness 受限接管实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Playwright 页面结构或时序错误后，复用当前详情页让 AI 执行受限恢复动作，并从失败阶段继续确定性流程，最终只允许保存草稿。

**架构：** 初次 Playwright 执行在可恢复错误的最终尝试中保留详情页并返回失败阶段。Python `HybridAutomationExecutor` 收集同一 CDP 会话的脱敏快照，调用 AI 生成版本化动作计划，由 Node Browser Harness 通过白名单执行恢复动作；恢复成功后，Node 执行器从失败阶段继续剩余预保存阶段并使用现有 `verifySavedDraft()` 证明保存结果。

**技术栈：** Python 3、现有 ModelGateway、Node.js、Playwright、Chrome DevTools Protocol、Node `node:test`、pytest。

---

## 文件清单

- 创建：`outputs/ai-listing-system/scripts/browser_harness.py`，负责动作计划 schema、安全校验、AI 请求和 Node Harness 调用。
- 创建：`outputs/ai-listing-system/scripts/mabang_browser_harness.cjs`，连接现有 CDP 页面，生成 DOM 快照并执行受限恢复动作。
- 修改：`outputs/ai-listing-system/scripts/browser_use_adaptive_executor.py`，增加带页面上下文的动作计划生成入口，保留原诊断 API。
- 修改：`outputs/ai-listing-system/scripts/hybrid_automation_executor.py`，把一次性诊断替换为一次性接管并串联恢复后的续跑。
- 修改：`outputs/ai-listing-system/scripts/browser_use_runner.py`，透传 `preserve_detail_on_failure` 和 `resume_from_stage` 参数。
- 修改：`outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`，保留失败详情页、从指定阶段续跑，并导出 Harness 所需的保存/安全辅助函数。
- 修改：`outputs/ai-listing-system/scripts/automation_worker.py`，识别 Harness 的 `harness_blocked` 与 `needs_confirmation`，继续使用真实保存证据判定。
- 创建：`outputs/ai-listing-system/tests/test_browser_harness.py`，覆盖 schema、动作白名单、成本和 Node 调用编排。
- 创建：`outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs`，覆盖快照、CNSC 选择、发布拦截和动作执行。
- 修改：`outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs`，覆盖失败页保留和阶段续跑。
- 修改：`outputs/ai-listing-system/tests/test_browser_use_runner.py`、`outputs/ai-listing-system/tests/test_automation_worker.py`，覆盖新参数和最终状态。

### 任务 1：建立 Harness 动作协议和失败测试

**文件：**
- 创建：`outputs/ai-listing-system/scripts/browser_harness.py`
- 测试：`outputs/ai-listing-system/tests/test_browser_harness.py`

- [ ] **步骤 1：编写失败测试，锁定动作协议。**

```python
from scripts.browser_harness import HarnessPlanError, validate_harness_plan


def test_validate_harness_plan_accepts_cnsc_recovery_actions() -> None:
    plan = {
        "version": 1,
        "actions": [
            {"type": "wait_for", "target": "shop_options"},
            {"type": "select_shop", "site": "TW", "name": "海宁TW", "id": "59760739"},
        ],
    }
    assert validate_harness_plan(plan)["actions"][1]["type"] == "select_shop"


def test_validate_harness_plan_rejects_publish_and_unknown_actions() -> None:
    for action in (
        {"type": "click", "target": "保存并刊登"},
        {"type": "eval", "script": "document.body.innerHTML=''"},
    ):
        try:
            validate_harness_plan({"version": 1, "actions": [action]})
        except HarnessPlanError as exc:
            assert exc.code in {"forbidden_action", "unknown_action"}
        else:
            raise AssertionError("unsafe action was accepted")


def test_validate_harness_plan_enforces_action_limit_and_required_identity() -> None:
    too_many = {"version": 1, "actions": [{"type": "wait_for", "target": "shop_options"}] * 21}
    try:
        validate_harness_plan(too_many)
    except HarnessPlanError as exc:
        assert exc.code == "action_limit_exceeded"
    else:
        raise AssertionError("action limit was not enforced")

    try:
        validate_harness_plan({"version": 1, "actions": [{"type": "select_shop", "site": "TW"}]})
    except HarnessPlanError as exc:
        assert exc.code == "shop_identity_incomplete"
    else:
        raise AssertionError("incomplete shop identity was accepted")
```

- [ ] **步骤 2：运行失败测试确认接口尚未实现。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_browser_harness.py -q`

预期：收集错误，显示 `ModuleNotFoundError` 或 `ImportError: cannot import name validate_harness_plan`。

- [ ] **步骤 3：实现最小 schema 和安全校验。**

实现 `HarnessPlanError(code, message)`、`MAX_ACTIONS = 20`、动作集合 `wait_for/click/fill/select_shop/save_draft/verify`，并让 `validate_harness_plan()` 返回深拷贝后的规范化计划。`click`、`fill` 只能接受语义 target；`select_shop` 必须同时包含非空 `site/name/id`；任何发布词或 `eval/javascript` 字段立即抛出对应错误码。

- [ ] **步骤 4：运行测试确认协议通过。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_browser_harness.py -q`

预期：3 个测试通过。

- [ ] **步骤 5：提交协议变更。**

```text
git add outputs/ai-listing-system/scripts/browser_harness.py outputs/ai-listing-system/tests/test_browser_harness.py
git commit -m "feat: add browser harness action protocol"
```

### 任务 2：实现 Node CDP 快照和受限动作执行器

**文件：**
- 创建：`outputs/ai-listing-system/scripts/mabang_browser_harness.cjs`
- 测试：`outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs`
- 修改：`outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`

- [ ] **步骤 1：编写失败测试，锁定快照和安全行为。**

```javascript
const assert = require('node:assert/strict');
const test = require('node:test');
const harness = require('../scripts/mabang_browser_harness.cjs');

test('harness rejects publish targets before touching the page', () => {
  assert.throws(
    () => harness.validateAction({ type: 'click', target: '发布' }),
    (error) => error.code === 'forbidden_action',
  );
});

test('harness resolves CNSC shop by site, name, and id', () => {
  const candidates = [{ siteCode: 'TW', labelText: '海宁TW', storeIds: ['59760739'], names: ['海宁TW'] }];
  assert.deepEqual(
    harness.resolveShopTarget(candidates, { site: 'TW', name: '海宁TW', id: '59760739' }),
    candidates[0],
  );
});

test('harness snapshot is bounded and excludes credential-like fields', async () => {
  const page = { url: () => 'https://publish.mabangerp.com/productDetailShopee', title: async () => '商品详情',
    locator: () => ({ evaluate: async () => '<input name="password" value="secret">' }) };
  const snapshot = await harness.buildPageSnapshot(page, { failedStage: 'select_cnsc_target_sites' });
  assert.equal(snapshot.url.includes('productDetailShopee'), true);
  assert.equal(snapshot.dom.includes('secret'), false);
  assert.equal(snapshot.dom.length <= 12000, true);
});
```

- [ ] **步骤 2：运行 Node 测试确认失败。**

运行：`node --test outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs`

预期：模块缺失或导出函数缺失导致测试失败。

- [ ] **步骤 3：实现 CDP 快照和动作执行。**

实现以下导出：`validateAction(action)`、`resolveShopTarget(candidates, target)`、`buildPageSnapshot(page, context)`、`executeHarnessPlan(browser, task, plan, context, options)`。执行器通过 `chromium.connectOverCDP()` 找到已有商品详情页，限制在详情 iframe；支持 `wait_for`、语义 `click`、允许字段 `fill`、`select_shop`、`save_draft`、`verify`。`select_shop` 复用主执行器的 `collectCNSCSitePickerCandidates`、`resolveCNSCTargetCandidate` 和 `verifyCNSCTargetSites`；`save_draft` 复用 `clickSaveDraftOnce`、`verifySavedDraft`。

安装与复用主执行器的发布安全 guard，并在每个动作前检查 target 是否命中发布词。CLI 从 stdin 读取 `{mode, task, plan, context}`，输出 `MABANG_HARNESS_RESULT <json>`；快照只保留 URL、标题、目标区域文本和最多 12000 字符脱敏 DOM，不保存 Cookie、密码值或完整商品描述。

- [ ] **步骤 4：运行 Node 测试确认通过。**

运行：`node --test outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs`

预期：3 个测试通过，发布动作测试不产生页面调用。

- [ ] **步骤 5：提交 Node Harness。**

```text
git add outputs/ai-listing-system/scripts/mabang_browser_harness.cjs outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs
git commit -m "feat: add constrained CDP browser harness"
```

### 任务 3：让 Playwright 保留失败页并支持阶段续跑

**文件：**
- 修改：`outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`
- 修改：`outputs/ai-listing-system/scripts/browser_use_runner.py`
- 测试：`outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs`
- 测试：`outputs/ai-listing-system/tests/test_browser_use_runner.py`

- [ ] **步骤 1：编写失败测试。**

```javascript
test('terminal pre-save failure preserves detail when harness takeover is enabled', async () => {
  const events = [];
  const error = new Error('terminal');
  await assert.rejects(
    executor.handleFailedPreSaveAttempt({}, error, [{ attempt: 3 }], false, {
      preserveDetails: true,
      closeDetails: async () => { events.push('closed'); },
    }),
    (thrown) => thrown === error && thrown.attempts.length === 1,
  );
  assert.deepEqual(events, []);
});
```

```python
import subprocess


def test_run_playwright_save_draft_passes_harness_resume_flags(tmp_path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(_ready_payload(), tmp_path / "uploads")
    calls = {}
    def fake_run(args, **kwargs):
        calls["args"] = args
        return subprocess.CompletedProcess(
            args, 0,
            stdout='MABANG_PLAYWRIGHT_RESULT {"ok":false,"status":"blocked","error":"resume_test","publish_clicked":false}',
            stderr="",
        )
    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)
    browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        preserve_detail_on_failure=True,
        resume_from_stage="select_category",
    )
    assert "--preserve-detail-on-failure" in calls["args"]
    assert calls["args"][-2:] == ["--resume-from-stage", "select_category"]
```

The Python test must assert that `--preserve-detail-on-failure` and `--resume-from-stage select_category` are added only when their corresponding arguments are set.

- [ ] **步骤 2：运行现有相关测试确认失败。**

运行：`node --test outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs -t "terminal pre-save failure preserves"` 和 `python -m pytest outputs/ai-listing-system/tests/test_browser_use_runner.py -q`

预期：新测试失败，旧测试保持通过。

- [ ] **步骤 3：实现失败页保留和续跑参数。**

在 `handleFailedPreSaveAttempt()` 增加 `preserveDetails`；仅当 `willRetry === false && preserveDetails === true` 时跳过关闭详情页。CLI 增加 `--preserve-detail-on-failure` 和 `--resume-from-stage <stage>`。`run()` 在续跑模式下查找已存在详情页、安装 guard，从 `PRE_SAVE_STAGES` 中指定 stage 开始执行，完成后继续现有保存和 `verifySavedDraft()`；续跑失败仍返回 `failed_stage`、`attempts`、`publish_clicked: false`。

Python `run_playwright_save_draft()` 增加关键字参数 `preserve_detail_on_failure=False`、`resume_from_stage=None`，并将参数转换为 CLI flags；默认行为保持不变。

- [ ] **步骤 4：运行测试确认通过。**

运行：`node --test outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs` 和 `python -m pytest outputs/ai-listing-system/tests/test_browser_use_runner.py -q`

预期：Node 与 Python 相关测试全部通过，默认执行路径未携带新 flags。

- [ ] **步骤 5：提交续跑能力。**

```text
git add outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs outputs/ai-listing-system/scripts/browser_use_runner.py outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs outputs/ai-listing-system/tests/test_browser_use_runner.py
git commit -m "feat: preserve and resume failed browser stages"
```

### 任务 4：接入 AI 动作计划和一次性接管编排

**文件：**
- 修改：`outputs/ai-listing-system/scripts/browser_use_adaptive_executor.py`
- 修改：`outputs/ai-listing-system/scripts/browser_harness.py`
- 修改：`outputs/ai-listing-system/scripts/hybrid_automation_executor.py`
- 测试：`outputs/ai-listing-system/tests/test_browser_harness.py`
- 测试：新增或修改 `outputs/ai-listing-system/tests/test_hybrid_automation_executor.py`

- [ ] **步骤 1：编写失败测试，锁定“失败页保留 → AI → 恢复 → 续跑”顺序。**

```python
def test_hybrid_executor_uses_harness_once_then_resumes_from_failed_stage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "scripts.hybrid_automation_executor.BrowserHarness",
        FakeHarness(recovery={"ok": True, "status": "recovered", "failed_stage": "select_cnsc_target_sites"}),
    )

    def fake_playwright(task, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert kwargs["preserve_detail_on_failure"] is True
            return {"ok": False, "status": "blocked", "error": "cnsc_target_site_not_found", "failed_stage": "select_cnsc_target_sites"}
        assert kwargs["resume_from_stage"] == "select_cnsc_target_sites"
        return {"ok": True, "status": "saved", "save_clicked": True, "publish_clicked": False,
                "page_closed": True, "close_method": "success_dialog", "exit_code": 0}

    executor = HybridAutomationExecutor(playwright_executor=fake_playwright)
    executor.harness = FakeHarness(recovery={"ok": True, "status": "recovered"})
    result = executor.execute_save_draft({"job_id": "job-1", "safety": {"allow_save": True, "allow_publish": False}})
    assert result["status"] == "saved"
    assert len(calls) == 2
```

- [ ] **步骤 2：运行新测试确认失败。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_hybrid_automation_executor.py -q`

预期：导入缺失或当前执行器仍只调用诊断，测试失败。

- [ ] **步骤 3：实现 Harness 编排和 AI 提示。**

在 `BrowserUseAdaptiveExecutor` 增加 `build_takeover_plan(task_plan, playwright_error, page_snapshot, failed_stage, max_cost)`：要求模型只输出 `{"version":1,"actions":[...]}`，并明确 `save_draft` 是唯一保存动作、不能发布。解析后调用 `validate_harness_plan()`；成本超限、JSON 非法和安全校验失败均返回 `harness_blocked`。

在 `BrowserHarness` 增加 `takeover()`：

```python
def takeover(self, task_plan, *, playwright_error, failed_stage, cdp_url, max_cost, timeout_seconds=120):
    snapshot = self._node_call("snapshot", task_plan, {"failed_stage": failed_stage}, cdp_url)
    plan = self.ai.build_takeover_plan(task_plan, playwright_error, snapshot, failed_stage, max_cost)
    validated = validate_harness_plan(plan)
    recovery = self._node_call("execute", task_plan, {"failed_stage": failed_stage}, cdp_url, plan=validated)
    if recovery.get("ok") is not True:
        return {"ok": False, "status": "harness_blocked", "error": recovery.get("error", "harness_action_failed"), "trace": recovery.get("trace", [])}
    return {"ok": True, "status": "recovered", "failed_stage": failed_stage, "trace": recovery.get("trace", [])}
```

在 `HybridAutomationExecutor.execute_save_draft()` 中：初次调用 Playwright 时传 `preserve_detail_on_failure=True`；只对错误白名单触发一次 takeover；takeover 成功后以 `resume_from_stage=failed_stage` 调用 Playwright；任何异常返回原始错误加 `harness` 轨迹，不覆盖 `publish_clicked` 或保存证据字段。成功续跑必须仍满足 `status=saved` 的原始字段。

- [ ] **步骤 4：运行编排测试确认通过。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_hybrid_automation_executor.py outputs/ai-listing-system/tests/test_browser_harness.py -q`

预期：AI 只调用一次，Playwright 最多初次执行加一次续跑；成功结果为真实保存结果。

- [ ] **步骤 5：提交 AI 接管编排。**

```text
git add outputs/ai-listing-system/scripts/browser_use_adaptive_executor.py outputs/ai-listing-system/scripts/browser_harness.py outputs/ai-listing-system/scripts/hybrid_automation_executor.py outputs/ai-listing-system/tests/test_browser_harness.py outputs/ai-listing-system/tests/test_hybrid_automation_executor.py
git commit -m "feat: recover browser failures with bounded AI takeover"
```

### 任务 5：接入 Worker 状态并验证安全边界

**文件：**
- 修改：`outputs/ai-listing-system/scripts/automation_worker.py`
- 测试：`outputs/ai-listing-system/tests/test_automation_worker.py`
- 测试：`outputs/ai-listing-system/tests/test_browser_use_runner.py`

- [ ] **步骤 1：编写 Worker 状态测试。**

```python
def test_worker_finishes_harness_blocked_without_marking_saved(tmp_path: Path) -> None:
    client = FakeClient()
    worker = automation_worker.AutomationWorker(
        client, worker_id="worker-test", upload_root=tmp_path,
        executor=lambda plan, **kwargs: {
            "ok": False, "status": "harness_blocked", "error": "forbidden_action",
            "publish_clicked": False, "save_clicked": False,
        }, heartbeat_interval=0,
    )
    result = worker.run_once()
    assert result["status"] == "blocked"
    assert client.finishes[0]["status"] == "blocked"
    assert client.finishes[0]["result"]["publish_clicked"] is False
```

- [ ] **步骤 2：运行测试确认失败。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_automation_worker.py -q`

预期：当前分支把未知状态归为 `failed`，新断言失败。

- [ ] **步骤 3：实现状态映射。**

将 `harness_blocked` 纳入 Worker 的 blocked 分支，将 Harness 返回的 `needs_confirmation` 映射到 `needs_confirmation`；保留 `is_proven_saved_execution()` 的全部条件，不放宽 `publish_clicked is False`、`save_clicked is True`、页面关闭和退出码检查。Harness trace 作为结果字段传递，不作为成功证据。

- [ ] **步骤 4：运行聚焦测试。**

运行：`python -m pytest outputs/ai-listing-system/tests/test_automation_worker.py outputs/ai-listing-system/tests/test_browser_use_runner.py -q`

预期：所有相关测试通过，非法 Harness 结果不会完成任务。

- [ ] **步骤 5：提交 Worker 状态变更。**

```text
git add outputs/ai-listing-system/scripts/automation_worker.py outputs/ai-listing-system/tests/test_automation_worker.py outputs/ai-listing-system/tests/test_browser_use_runner.py
git commit -m "fix: keep harness failures blocked in worker"
```

### 任务 6：全量验证和运行时检查

**文件：**
- 修改：仅在测试发现问题时修改上述实现文件；不修改无关工作区文件。

- [ ] **步骤 1：运行所有 Harness、CNSC 和 Worker 测试。**

运行：`node --test outputs/ai-listing-system/tests/mabang_browser_harness.test.cjs outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs` 和 `python -m pytest outputs/ai-listing-system/tests/test_browser_harness.py outputs/ai-listing-system/tests/test_hybrid_automation_executor.py outputs/ai-listing-system/tests/test_automation_worker.py outputs/ai-listing-system/tests/test_browser_use_runner.py -q`

预期：新增测试和原有相关测试全部通过；若其他全量测试已有失败，单独记录其原始失败，不把它们归因于 Harness。

- [ ] **步骤 2：运行语法和安全静态检查。**

运行：`node --check outputs/ai-listing-system/scripts/mabang_browser_harness.cjs`、`node --check outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`、`python -m compileall -q outputs/ai-listing-system/scripts/browser_harness.py outputs/ai-listing-system/scripts/hybrid_automation_executor.py outputs/ai-listing-system/scripts/browser_use_adaptive_executor.py`。

预期：命令全部以退出码 0 完成；源码中 Harness 不出现 `eval(`、`page.goto(`、`browser.newPage(` 或发布按钮点击路径。

- [ ] **步骤 3：执行只读 CDP 冒烟检查。**

使用现有已登录 CDP 地址运行 Harness snapshot 模式，只读取当前详情页 URL、frame 和 `#shops-wap` 脱敏 DOM；不执行 `save_draft`，不点击任何发布或保存按钮。确认初始状态能够识别“所选店铺 0 个”触发器，展开后能够识别 `TW / 海宁TW / 59760739` 候选。

- [ ] **步骤 4：检查最终差异和工作区边界。**

运行：`git diff --check`、`git status --short`、`git diff --stat HEAD~5..HEAD`。

预期：无空白错误；提交只包含规格、计划和本计划列出的 Harness 相关文件；用户已有无关改动不被重置或覆盖。

- [ ] **步骤 5：提交验证结果。**

```text
git add outputs/ai-listing-system/docs/superpowers/plans/2026-08-29-browser-harness.md
git commit -m "docs: add browser harness implementation plan"
```
