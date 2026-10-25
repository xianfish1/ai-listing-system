# NAS 浏览「秒开」优化实现计划

**目标：** 让「从 NAS 导入」弹窗和逐层点进去几乎不再等网络——凡是曾经看过的目录直接从本地缓存出，只有点「刷新」才真正读 NAS；同时后台预取下一层和预热根目录，把首次访问也提前铺好。

**根因：** 现在每进入一个未缓存目录都要对 UNC 共享同步 `os.scandir`，网络往返本身就几秒；缓存只在 300 秒 TTL 内第二次访问才命中，首访和过期后都要重新阻塞等网络。

**策略（stale-while-revalidate）：** 缓存改为「先出旧的、后台补新的」。只要有缓存（内存或磁盘预热）就立即返回，绝不因过期而同步阻塞；过期条目在后台悄悄重扫，下次更新。叠加：进入某层后后台预取其子目录的浅列表；启动/开弹窗时后台预热根目录及其子层；`refresh=1`（刷新按钮）才强制读实盘。

**技术栈：** Python `ThreadingHTTPServer`、现有 JSON 磁盘缓存、原生浏览器 JS、pytest。全部改动集中在 `services/api/main.py`、`apps/web/app.js`、`tests/test_product_maintenance_reorg.py`。

---

### 任务 1：缓存改为 stale-while-revalidate（永不因过期阻塞）

**文件：** `services/api/main.py`、`tests/test_product_maintenance_reorg.py`

- **步骤 1：** 新增 `_NAS_BROWSE_CACHE_FRESH_SECONDS = 300.0`（判定“需后台重扫”的阈值）。保留 `_NAS_BROWSE_CACHE_DISK_TTL_SECONDS`（7 天）作为“最久仍可展示”的上限。
- **步骤 2：** 改 `_browse_nas_directory` 的缓存读取块（现约 2377–2387 行）：`refresh` 为假时，只要命中缓存且未超过磁盘 TTL 就**立即返回**（带 `from_cache=True` 与 `cache_age`）；若 `cache_age >= FRESH_SECONDS`，返回前调度一次后台重扫（同 key 的浅/完整），标记 `stale=True`。这样过期不再触发同步 scandir。
- **步骤 3：** 新增 `_schedule_nas_browse_rescan(root_path, target, shallow)` —— 复用 `_NAS_BROWSE_PROBE_INFLIGHT` 去重，在后台重扫并回写缓存 + `_nas_browse_cache_save`。
- **步骤 4：** 测试：同一目录第二次访问即便已过 `FRESH_SECONDS` 仍 `from_cache=True` 且不阻塞；`refresh=1` 仍走实盘（沿用现有 `test_nas_browse_serves_second_visit_from_cache_and_refresh_bypasses` 扩展）。

### 任务 2：预取下一层子目录（点进去秒开）

**文件：** `services/api/main.py`、`tests/test_product_maintenance_reorg.py`

- **步骤 1：** 新增受限执行器 `_NAS_PREFETCH_EXECUTOR = ThreadPoolExecutor(max_workers=2)`（与导入执行器隔离，避免争抢/压垮 NAS）。
- **步骤 2：** 新增 `_schedule_nas_children_prefetch(root_path, target, entries)`：对当前层每个子目录（上限如前 200 个），若其浅 key 未在缓存中，提交后台浅扫描并写缓存。用 inflight 集合按子路径去重。
- **步骤 3：** 在浅打开成功缓存后（现约 2446 行 `if shallow and entries:` 附近）调用预取；已有的 `_schedule_nas_browse_probe`（补当前层 `has_subdirs`）保持不变。
- **步骤 4：** 测试：浅打开父目录后，其子目录的浅缓存在后台完成后被填充（对子目录再请求命中 `from_cache=True`）。

### 任务 3：启动与开弹窗预热根目录

**文件：** `services/api/main.py`、`apps/web/app.js`、`tests/test_product_maintenance_reorg.py`

- **步骤 1：** 新增 `_start_nas_browse_warm()` 守护线程：预热磁盘缓存 → 浅扫描根目录 → 预取根的子目录。在 `main()` 内 `ensure_dirs()` 后启动（NAS 不可达时静默跳过，绝不拖慢启动）。
- **步骤 2：** 前端：`browseNasProductFolder` 已先 `showModal` 再请求，保持不变；确认状态栏文案对 `stale`/`from_cache` 有清晰提示（“已缓存，秒开；后台刷新中”）。
- **步骤 3：** 测试：断言 `main.py` 启动路径注册了预热线程；前端脚本包含缓存/后台刷新的状态提示分支。

### 任务 4：完整验证

**文件：** 无

- **步骤 1：** `pytest outputs/ai-listing-system/tests/test_product_maintenance_reorg.py outputs/ai-listing-system/tests/test_product_line_flow.py -q`
- **步骤 2：** `python -m py_compile services/api/main.py`、`node --check apps/web/app.js`、`git diff --check`。
- **步骤 3：** 只报告本次改动相关结果；既有失败单独列出，不误报为回归。

---

**验收：** 第二次起访问任何看过的目录、以及逐层点进被预取的子目录，均由本地缓存秒回，不再等 NAS；数据陈旧时点「刷新」强制读实盘。首次开弹窗因启动预热通常也已就绪。
