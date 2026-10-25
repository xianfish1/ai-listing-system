# CNSC 当前页完整采集与站点折扣自动填写实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将 CNSC 当前页导入升级为 v3，可靠采集目标店铺、逐站点价格和折扣活动，并让草稿执行器按店铺填写价格及 `%OFF`，始终受草稿保存安全守卫保护。

**架构：** 页面采集脚本只读取马帮 Vue/VXE 与已打开选择器的可验证状态，Python 归一化层负责 v1/v2/v3 兼容和“未加载不覆盖”合并，模板与刊登副本固化配置，Playwright 执行器按站点分组完成选择、填写和回读验证。页面结构匹配全部使用稳定 ID/语义表头优先，名称唯一匹配回退，任何歧义均阻断保存。

**技术栈：** Python 3、FastAPI、原生 JavaScript、Node `node:test`、Playwright、静态 HTML/CSS。

---

### 任务 1：v3 采集与归一化

**文件：**
- 修改：`tests/mabang_page_import_capture.test.cjs`
- 修改：`tests/test_mabang_import_snapshot.py`
- 修改：`tests/fixtures/mabang_page_capture_cnsc.json`
- 修改：`scripts/mabang_page_import_capture.js`
- 修改：`services/api/mabang_import_snapshot.py`

- [ ] 添加“站点复选框 + 已选店铺单选框”、SIP 排除、活动目录加载状态、逐 SKU 站点价格的失败测试。
- [ ] 运行 `node --test tests/mabang_page_import_capture.test.cjs` 与 `pytest tests/test_mabang_import_snapshot.py -q`，确认因 v3 字段缺失而失败。
- [ ] 实现 v3 快照、目标店铺归一化、活动/折扣与逐站点价格归并；v1/v2 缺失字段使用兼容默认值。
- [ ] 重跑上述测试，确认通过且快照不包含 `cKey` 或完整查询串。

### 任务 2：价格语义、模板持久化和界面

**文件：**
- 修改：`tests/test_cnsc_price_matrix.py`
- 修改：`tests/test_mabang_page_import.py`
- 修改：`tests/test_store_template_workflow_api.py`
- 修改：`tests/test_store_template_workflow_ui.py`
- 修改：`services/api/cnsc_price_matrix.py`
- 修改：`services/api/mabang_page_import.py`
- 修改：`services/api/storage.py`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`

- [ ] 添加任意列序“价格/原价”与“折扣价/售价”识别、默认 40、逐店覆盖、未加载配置保留和缺失项阻断的失败测试。
- [ ] 运行对应 Python/UI 测试，确认期望失败。
- [ ] 实现语义价格解析、模板字段合并与完整性校验、店铺配置表和导入预览。
- [ ] 重跑对应测试，确认普通站点模式无回归。

### 任务 3：只读准备与草稿执行器

**文件：**
- 修改：`tests/test_browser_use_runner.py`
- 修改：`tests/mabang_playwright_executor.test.cjs`
- 修改：`scripts/browser_use_runner.py`
- 修改：`scripts/mabang_page_import_executor.cjs`
- 修改：`scripts/mabang_playwright_executor.cjs`

- [ ] 添加只读打开/取消店铺与活动选择器、精确店铺选择、活动 ID 优先、批量 `%OFF` 和折扣价回读的失败测试。
- [ ] 运行 Node/Python 聚焦测试，确认因执行阶段缺失而失败。
- [ ] 实现只读准备和 CNSC 站点执行阶段；所有不唯一匹配、价格偏差或 SKU 数量差异抛出阻断错误。
- [ ] 验证 `stop_before_save` 不点击保存，发布安全守卫持续拒绝刊登类按钮。

### 任务 4：回归与验收

**文件：**
- 检查：上述所有修改文件

- [ ] 运行聚焦 Python 与 Node 测试。
- [ ] 运行全量 Python 测试并将既有 8 个失败与新增失败分开记录。
- [ ] 运行静态语法检查和 `git diff --check`。
- [ ] 使用本地 `8794` 页面做桌面/移动视口检查。
- [ ] 对真实马帮页面只做只读采集或 `stop_before_save` 演练；不执行真实保存。
- [ ] 完成代码审查，逐项对照本计划和用户规格。
