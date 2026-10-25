# 马帮自动领取与保存草稿实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度。

**目标：** 让本机单 worker 自动领取 `ready` 任务，完整填写马帮 Shopee 表单，只保存草稿并永久禁止发布。

**架构：** 在现有 JSON 原子锁上增加 job lease 状态机，新建单实例 worker 驱动浏览器执行器；浏览器计划将保存和发布拆开，外层与 iframe 永久拦截发布，保存后用页面与草稿记录双证据确认。

**技术栈：** Python 3.14/3.12、pytest、JSON 原子存储、Browser-Use daemon、Chrome CDP 9223、马帮 Shopee publish UI。

---

## 文件职责

- `services/api/storage.py`：任务原子领取、lease、心跳、完成、阻塞与恢复。
- `services/api/main.py`：任务 claim/heartbeat/complete API 与 worker 状态查询。
- `scripts/browser_use_runner.py`：save-draft 计划、完整 iframe 执行、发布保护与保存验证。
- `scripts/automation_worker.py`：单 worker 轮询、领取、阶段运行、心跳与结果回写。
- `scripts/run_automation_worker.ps1`：Windows 后台 worker 启动入口。
- `scripts/run_api.ps1`：启动 API 后确保 worker 单实例运行。
- `tests/test_automation_worker.py`：worker 调度、重试与状态回写测试。
- `tests/test_product_line_flow.py`：存储/API lease 状态机测试。
- `tests/test_browser_use_runner.py`：保存/发布安全边界和完整表单计划测试。
- `apps/web/app.js`：展示 worker 和任务运行状态。

### 任务 1：原子任务领取与 lease（已完成）

- [x] 先写失败测试：两个 worker 只能有一个 claim；错误 token 不能 heartbeat/complete；过期 lease 回收；saved 不再 claim。
- [x] 运行 `python -m pytest tests/test_product_line_flow.py -k automation_job_lease -q`，确认因 API 缺失失败。
- [x] 在 `storage.py` 实现 `claim_next_automation_job()`、`heartbeat_automation_job()`、`finish_automation_job()`、`recover_expired_automation_jobs()`。
- [x] 增加 API POST 路由 `/api/automation/jobs/claim`、`/{id}/heartbeat`、`/{id}/finish`。
- [x] 重跑目标测试并确认通过。

### 任务 2：保存草稿安全计划（已完成）

- [x] 先写失败测试：save 模式 `allow_save=true`、`allow_publish=false`，动作末尾是 `save_draft`；发布词始终被保护；精确“保存”才允许。
- [x] 运行 `python -m pytest tests/test_browser_use_runner.py -k 'save_draft or publish_guard' -q`，确认失败。
- [x] 修改 task plan builder，分离 `SAVE_BUTTON_TEXT` 与 `PUBLISH_BUTTON_TEXTS`，增加一次性 save token。
- [x] 生成外层和 iframe 发布 guard，拒绝“保存并刊登/刊登/发布/提交审核”。
- [x] 重跑目标测试并确认通过。

### 任务 3：完整 iframe 表单执行器（已完成，实机适配持续在任务 6）

- [x] 先写失败测试，要求脚本包含店铺、类目、属性、基本信息、商品图、变体、SKU grid、折扣、颜色缩略图、保存前校验和保存后验证函数。
- [x] 运行目标测试确认功能缺失。
- [x] 按马帮实际 DOM/API 状态实现每个阶段；每阶段返回结构化结果，歧义立即阻塞。
- [x] 修复上传文件准备为 job 独立目录与原子替换，消除并发文件锁。
- [x] 重跑浏览器 runner 和商品链路测试。

### 任务 4：常驻单 worker（已完成）

- [x] 新建失败测试：worker 仅领取 ready、每 3 秒轮询、执行时 heartbeat、成功写 saved、可恢复失败最多 2 次、blocked 不重试。
- [x] 实现 `automation_worker.py`，支持 `--once` 和常驻模式，使用单实例 lock。
- [x] 实现 `run_automation_worker.ps1`，并在 `run_api.ps1` 启动后台 worker。
- [x] 重跑 worker 测试。

### 任务 5：运行状态可见性（已完成）

- [x] 增加源码契约测试，要求自动化任务显示当前阶段、attempt、worker、lease、last_error 和 saved 证据。
- [x] 扩展任务 API/UI 渲染，不提供发布按钮。
- [x] 运行 `node --check apps/web/app.js` 和 UI 测试。

### 任务 6：真实任务保存验收

- [x] 保存前完整 dry run 返回 `ready_to_save`，并证明未点击保存或发布。
- [x] 运行全量测试与编译检查。
- [x] 对任务 `60ad68ad-5dc3-4d81-a290-67c1e792c544` 运行 worker `--once`。
- [x] 观察阶段日志，只有完整表单和保存前校验通过才允许保存。
- [x] 验证马帮待发布草稿存在、任务状态为 `saved`、`publish_clicked=false`。
- [x] 再运行 worker `--once`，验证该任务不会重复领取。
- [x] 最终运行 `python -m pytest -q`、compileall、`node --check` 和浏览器截图检查。
