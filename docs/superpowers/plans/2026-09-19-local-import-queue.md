# 本地导入队列（连续选目录）实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让“从本地批量创建”支持排队——上一个目录还在导入时，操作员可以继续选择下一个目录，新目录排在队列末尾自动接着跑，不需要干等。

**架构：** 在现有无 DOM 依赖的 `apps/web/local-folder-batch.js` 里补一个 `createImportQueue` 串行调度器；`app.js` 把每个商品目录作为一项入队，负责队列面板渲染、逐项明细、移除/清空交互和完成后刷新。后端与上传/创建接口完全不变（仍是单商品上传会话 + `complete`）。

**技术栈：** 原生 JavaScript、CommonJS/浏览器 UMD、Node `node:test`、Python `pytest`、现有 HTML/CSS/REST API。

---

## 文件结构

- 修改：`apps/web/local-folder-batch.js` — 新增 `createImportQueue`（串行、可查看、可丢弃等待项、`whenIdle`）。
- 修改：`tests/local_folder_batch.test.cjs` — 覆盖串行不并发、后入队排队、失败不阻塞、移除/清空、通知时序。
- 修改：`apps/web/app.js` — 队列单例、队列面板渲染、`uploadLocalProductGroup` 回传明细、入口改为入队即返回、清空/移除点击处理、刷新拦截。
- 修改：`apps/web/index.html` — 队列面板 DOM 与静态资源 `?v=` 版本号。
- 修改：`apps/web/styles.css` — 队列面板样式（含显式 `[hidden]` 隐藏规则）。
- 修改：`tests/test_store_template_workflow_ui.py` — 本地导入队列的静态契约。

### 任务 1：串行队列核心

**文件：**
- 修改：`apps/web/local-folder-batch.js`
- 修改：`tests/local_folder_batch.test.cjs`

- [x] **步骤 1：先写队列行为测试**

覆盖：入队后立刻开始第一项；第一项未结束时第二项不开始；`run` 抛错时记录失败并继续；`removePending`/`clearPending` 只影响未开始项；`onChange` 通知时序为 `running:- → running:AL0001 → running:- → idle:-`。

- [x] **步骤 2：实现 `createImportQueue`**

稳定接口：

```javascript
const queue = createImportQueue({ run, onChange });
queue.enqueue(tasks);        // 返回带 queue_id 的入队项，立即返回（内部自驱）
queue.removePending(id);     // 丢弃某一项等待中的商品
queue.clearPending();        // 清空等待区
queue.snapshot();            // { running, active, pending, results }
await queue.whenIdle();      // 队列跑空
```

`drain()` 在 `running` 为真时只发通知不重入，保证同一时刻只有一个 `run` 在执行。

- [x] **步骤 3：运行测试**

运行：`node --test tests/local_folder_batch.test.cjs`

预期：9 个测试全部 PASS。

### 任务 2：界面接入队列

**文件：**
- 修改：`apps/web/app.js`
- 修改：`apps/web/index.html`
- 修改：`apps/web/styles.css`

- [x] **步骤 1：入口改为入队即返回**

`createBaseProductFromLocalFolder` 只做分组、确认和 `queue.enqueue(...)`；不再 `await` 整批导入，也不再禁用“从本地批量创建”和“从 NAS 创建”按钮。批量确认文案追加一句“当前队列还有 N 个商品在导入，新目录会排在它们之后。”，同名目录已在队列中时再补一句“其中 X 已在队列中，继续会重复创建。”，最后问“加入导入队列？”。

- [x] **步骤 2：队列面板**

`#local-import-queue` 显示“本地导入队列：进行中 1 · 等待 3 · 已完成 5（失败 1）”，列表按“正在跑 → 等待中 → 最新完成”排序；等待项带“移除”，等待区非空时显示“清空待处理”。运行中那一行的明细由 `setLocalImportDetail` 就地更新（避免每个上传进度事件都重画整表）。

- [x] **步骤 3：进度与刷新**

`uploadLocalProductGroup` 增加 `onDetail`，把同一段文案同时写进页面状态行和队列行。每个商品创建成功后立刻 `loadBaseProducts()`；队列跑空后再 `loadAssets()` + `loadReadiness()`。状态行汇总文案保持“本地批量导入完成：成功 N 个，失败 M 个…”。

- [x] **步骤 4：刷新拦截**

队列非空时注册 `beforeunload` 确认，避免误刷新丢掉等待中的项（`File` 句柄无法持久化）。

- [x] **步骤 5：样式与缓存版本**

新增 `.local-import-queue*` 样式；因为没有全局 `[hidden]` 规则，额外写 `.local-import-queue[hidden], .local-import-queue .chip[hidden] { display: none; }`。`index.html` 里 `local-folder-batch.js`、`app.js`、`styles.css` 都加上 `local-import-queue-v1` 版本标记。

### 任务 3：回归验证

**文件：**
- 验证：`tests/local_folder_batch.test.cjs`
- 验证：`tests/test_local_folder_uploads.py`
- 验证：`tests/test_store_template_workflow_ui.py`

- [x] **步骤 1：静态契约**

`tests/test_store_template_workflow_ui.py::test_local_import_queue_lets_operators_queue_several_directories` 校验队列核心、面板 DOM、入队即返回（入口函数体内不再 `.disabled = true`、不再 `await runSequentialImport`）。

- [x] **步骤 2：运行相关测试集**

```bash
node --test tests/local_folder_batch.test.cjs
python -m pytest tests/test_local_folder_uploads.py tests/test_store_template_workflow_ui.py -q
node --check apps/web/local-folder-batch.js
node --check apps/web/app.js
git diff --check
```

预期：队列相关测试全部 PASS；`test_store_template_workflow_ui.py` 中 8 个与本地导入无关的红项为本工作区既有的未完成改动（`listing-copy-warehouse-batch` 样式、全局 `[hidden]` 规则、CNSC 价格粘贴等），不在本计划范围内。
