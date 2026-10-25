# 本土模板属性优先与 AI 必填属性补齐 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 本土自动化先使用店铺模板属性；页面仍缺必填属性时，根据商品标题、详情和页面选项调用 AI 推荐并完成填写，同时修复多选下拉选中回读导致的阻断。

**架构：** 浏览器任务快照继续携带数字属性值和冻结类目字段快照。Node 执行器读取现场类目属性，将模板值映射为页面标签；只有映射后仍缺必填字段时才调用本地 AI 推荐端点。AI 输出仍经过页面选项约束，并复用既有选择/文本填写及回读流程。跨境 `cnsc_global` 路径保持现有行为。

**技术栈：** Python `browser_use_runner.py`、Node.js Playwright 执行器、Node `node:test`、本地 HTTP AI 推荐端点。

---

### 任务 1：锁定失败行为

**文件：**
- 修改：`outputs/ai-listing-system/tests/mabang_playwright_executor.test.cjs`
- 测试：同一文件中的执行器源码/纯函数测试

- [ ] **步骤 1：编写失败测试**：覆盖模板属性映射优先、AI 请求上下文包含标题和详情、以及多选选项已由控件提交但标签延迟时不重复点击。
- [ ] **步骤 2：运行测试验证失败**：运行 `node --test tests/mabang_playwright_executor.test.cjs`，预期新断言失败。

### 任务 2：实现模板优先和 AI 补齐

**文件：**
- 修改：`outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`
- 修改：`outputs/ai-listing-system/scripts/browser_use_runner.py`

- [ ] **步骤 1：增加现场字段映射辅助函数**：按 `field_id` 从 `task.category_attributes` 读取模板值，映射为 live field 的 option label；保留 text、普通 select、自填 select 三种 input_kind。
- [ ] **步骤 2：调整自动补齐顺序**：把模板映射字段合并到 resolvedFields；只把剩余必填字段发送到 `/api/ai/attribute-recommendations`。
- [ ] **步骤 3：扩大 AI 上下文**：发送标题、详情、类目路径、识别属性/颜色等已有商品事实和页面选项，限制详情长度，避免把用户输入当指令执行。
- [ ] **步骤 4：约束 AI 输出**：只接受页面必填字段 ID；枚举值必须命中页面 option value 或 label，未知值不写入；多选保持数组。
- [ ] **步骤 5：修复多选回读**：点击前读取选项 selected/class 和已提交标签；点击后允许控件值已更新但标签延迟的稳定窗口，避免重试取消已选值。

### 任务 3：验证与运行态检查

**文件：**
- 无新增生产文件

- [ ] **步骤 1：运行 Node 聚焦回归**：`node --test tests/mabang_playwright_executor.test.cjs`。
- [ ] **步骤 2：运行 Python 任务构造回归**：`python -m pytest tests/test_browser_use_runner.py tests/test_automation_worker.py -q`。
- [ ] **步骤 3：运行静态检查**：`node --check scripts/mabang_playwright_executor.cjs`、`python -m compileall scripts/browser_use_runner.py`、`git diff --check`。
- [ ] **步骤 4：检查本地服务**：读取 `http://127.0.0.1:8788/api/health` 与 Worker 状态；确认现有任务快照属性不丢失，未执行真实保存/发布。
