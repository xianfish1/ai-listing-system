# NAS 商品维护性能优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。

**目标：** 让“从 NAS 创建”弹窗先显示、目录读取不被旧请求拖慢，批量创建立即进入后台并可查看逐项进度。

**架构：** 前端先打开弹窗，再异步加载 NAS 根目录，并用 AbortController/请求序号丢弃过期响应。后端保留浅目录缓存和后台叶子探测，新增受限并发的批量导入任务状态；每个商品仍复用现有扫描、落库和 AI 增强逻辑。

**技术栈：** 原生浏览器 JavaScript、Python `ThreadingHTTPServer`、现有 JSON 存储、pytest。

---

### 任务 1：为前端即时打开和过期请求保护编写回归测试

**文件：**
- 修改：`outputs/ai-listing-system/tests/test_product_maintenance_reorg.py`
- 修改：`outputs/ai-listing-system/apps/web/app.js`

- [x] **步骤 1：编写失败的静态行为测试**

断言 `browseNasProductFolder` 在调用 `nasBrowserNavigate("")` 前打开 dialog，且脚本包含请求取消/序号判断；断言导航期间旧路径响应不会写入 `nasBrowserState`。

- [x] **步骤 2：运行聚焦测试确认失败**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py -q`

- [x] **步骤 3：实现最小前端改动**

将 `showModal()` 移到根目录请求之前；在 NAS 状态中加入当前 AbortController 与递增 requestId；每次导航先取消上一次请求，响应回写前检查 requestId 和目标路径。

- [x] **步骤 4：运行聚焦测试确认通过**

运行同上，预期 NAS UI 测试通过。

### 任务 2：强化 NAS 目录读取的缓存和后台探测边界

**文件：**
- 修改：`outputs/ai-listing-system/services/api/main.py`
- 修改：`outputs/ai-listing-system/tests/test_product_maintenance_reorg.py`

- [x] **步骤 1：编写缓存并发回归测试**

覆盖同一路径的并发浅请求只产生一次扫描、`refresh=1` 仍强制刷新、后台完整探测失败不阻塞浅结果。

- [x] **步骤 2：运行测试确认失败或暴露缺口**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py -q`

- [x] **步骤 3：实现最小后端改动**

复用现有按路径去重的完整探测、内存/磁盘缓存和浅扫描路径；前端只渲染前 300 行，避免超大目录响应后同步构造海量 DOM，完整探测继续在后台执行。

- [x] **步骤 4：运行 NAS API 测试**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py -q`

### 任务 3：新增后台批量创建任务接口

**文件：**
- 修改：`outputs/ai-listing-system/services/api/main.py`
- 修改：`outputs/ai-listing-system/tests/test_product_maintenance_reorg.py`

- [x] **步骤 1：编写失败的任务 API 测试**

提交两个 NAS 路径后断言 POST 返回 `202`、任务 ID 和 `queued` 状态；GET 状态能返回 `total/completed/success/failed/items`，单个失败不会阻止另一个完成。

- [x] **步骤 2：实现任务状态与受限 worker**

增加进程内任务字典和锁；POST 去重路径、创建任务记录并启动最多 2 个 worker；worker 逐项调用 `storage.create_base_product_from_folder` 与 `_enrich_with_vision_listing`，更新阶段、错误和结果；GET 返回快照。任务完成后保留结果供前端读取。

- [x] **步骤 3：运行 API 聚焦测试**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py -q`

### 任务 4：接入前端批量任务轮询和进度展示

**文件：**
- 修改：`outputs/ai-listing-system/apps/web/app.js`
- 修改：`outputs/ai-listing-system/tests/test_product_maintenance_reorg.py`

- [x] **步骤 1：编写前端静态回归测试**

断言确认创建调用批量任务接口后立即关闭弹窗，存在任务轮询、进度计数和失败明细渲染。

- [x] **步骤 2：实现最小前端改动**

将 `runNasBatchImport` 改为 POST 一次提交路径列表，再按固定间隔 GET 任务状态；任务运行中显示 `completed/total` 与当前商品，完成后刷新商品、素材和 readiness 数据；轮询始终可清理，避免重复计时器。

- [x] **步骤 3：运行静态和 API 测试**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py -q`。

### 任务 5：完整验证与差异检查

**文件：**
- 修改：无

- [x] **步骤 1：运行 NAS、商品流程和 API 边界测试**

运行：`pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py outputs/ai-listing-system/tests/test_product_line_flow.py outputs/ai-listing-system/tests/test_api_boundaries.py -q`

- [x] **步骤 2：运行语法与差异检查**

运行：`python -m py_compile outputs/ai-listing-system/services/api/main.py outputs/ai-listing-system/services/api/storage.py`、`node --check outputs/ai-listing-system/apps/web/app.js`、`git diff --check`。

- [x] **步骤 3：记录结果**

只报告本次改动相关测试结果；若全量测试存在既有失败，明确列出而不误报为本次回归。
