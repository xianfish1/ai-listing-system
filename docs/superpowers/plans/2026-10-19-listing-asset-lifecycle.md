# 商品素材生命周期治理实施计划（压缩 + 超期清理）

依据：`docs/superpowers/specs/2026-10-19-listing-asset-lifecycle-design.md`。

现场基线（2026-10-19）：本地素材 5.82 GB / 2,474 文件，其中 .png 1,769 个 / 5.54 GB；孤儿上传目录 80 个 / 2.24 GB；API 在 8788 运行（pid 4104），Worker 已停，在途任务 0 条。

## 任务 0：安全基线（不提交、不重启）

- [x] 0.1 备份将被改写的生产数据到 `.scratch/`：
  `data/store.json`、`data/storage_cleanup_state.json`、`data/listing_copy_revisions.json`
- [x] 0.2 确认在途任务为 0、Worker 已停（`GET /api/automation/jobs`、`data/worker-runtime/worker-state.json`）
- [x] 0.3 记录基线测试结果（本机全量 pytest 的通过与失败数，供最后 A/B 对比）
- [x] 0.4 认证：本计划**不改动现场 `data/` 与 `storage/`**；真实数据只在最后一步以隔离目录只读验收

## 任务 1：新增压缩核心模块 `services/api/asset_compaction.py`

纯逻辑 + 文件操作，不碰 store.json；改写记录统一交给任务 2 的入口。

- [x] 1.1 `CompactionSettings.from_config(config)`：读 `storage_cleanup` 组的
  `compact_enabled` / `compact_delay_days` / `compact_max_edge` / `compact_jpeg_quality`，含边界钳制
- [x] 1.2 `local_asset_root(base_product, storage_dir) -> Path | None`：只认 `storage/uploads/base-products/` 之下的 `asset_root`，NAS 与其它路径返回 None
- [x] 1.3 `compaction_eligibility(base_product, copies, active_job_ids, *, settings, now)`：按规格 1~5 条判定，返回 `(bool, reason)`；`reason` 用于日志与验收（`not_completed` / `too_recent` / `copy_open` / `job_active` / `not_local` / `missing_dir` / `nothing_to_do`）
- [x] 1.4 `plan_directory(root, settings) -> list[PlanItem]`：只遍历不写；PNG 一律入选，JPEG 只在「超边长」或「预计省 ≥15%」时入选，`.mp4`/`.psd`/其它跳过
- [x] 1.5 `convert_image(path, settings) -> ConvertResult`：Pillow 解码 → `exif_transpose` → 缩到 `max_edge` → 写同名 `.tmp` → 校验体积确实变小 → `os.replace` 落盘；异常返回失败而不抛
- [x] 1.6 `compact_base_product(base_product_id, *, settings, now)`：判定 → 计划 → 逐张转换 → 汇总 `name_map {旧名: 新名}` 与每张的 `size_bytes/width/height/file_hash` → 调 `storage.apply_asset_name_map(...)` 落记录 → 返回报告
- [x] 1.7 `scan_compaction(store, *, settings, storage_dir, now)`：只读统计（可压缩商品数 / 文件数 / 预计释放），供 `/api/settings/storage/usage` 显示
- [x] 1.8 `run_compaction(store_loader, *, settings, now)`：批量执行、单飞、逐商品 best-effort，返回 `{compacted_products, compacted_files, saved_bytes, skipped, errors}`

## 任务 2：`storage.py` 增加一次事务内的引用改写

- [x] 2.1 新增 `apply_asset_name_map(base_product_id, name_map, file_stats, *, now)`：
  一次 `locked_store()` 内改写四类集合（规格「名字改写规则」表）：
  - `assets`：`file_name` / `file_path` / `size_bytes` / `width` / `height` / `file_hash`
  - `base_products`：`main_image` / `product_images` / `size_chart_image` / `variant_image_map`
  - `store_listing_copies`：同上字段
  - `listing_skus`：`variant_image`
  取值精确等于旧文件名的才替换（列表逐项、映射逐个 value），不做子串替换
- [x] 2.2 同一事务里在 `base_products` 上写 `assets_compacted_at` / `assets_compacted_bytes`
- [x] 2.3 新增 `mark_assets_cleaned(base_product_id, deleted_bytes, *, now)` 写 `assets_cleaned_at` / `assets_cleaned_bytes`
- [x] 2.4 不改 `_EDIT_DETECTION` 口径（压缩不是人工编辑，不得把商品打回未完成）
- [x] 2.5 新增只读查询 `asset_lifecycle_candidates()`：给 disk_cleanup 用的 `(base_products, copies, active_job_ids)` 组合，避免各调用点重复拼装

## 任务 3：`disk_cleanup.py` 新增 `product_assets` 类目

- [x] 3.1 `CATEGORY_IDS` 增加 `product_assets`；`AUTOMATIC_CATEGORIES` 增加它；补 `_CATEGORY_LABELS`「商品素材」与 `_PROTECTED_NOTES`（说明只删已上架完成超期、孤儿目录；NAS 素材不动）
- [x] 3.2 `scan_usage(config, *, active_job_ids, store=None, now=None)` 与
  `cleanup_categories(categories, config, *, active_job_ids, cdp_available, store=None, now=None)` 增加可选 `store` 参数（缺省时行为与现在完全一致）
- [x] 3.3 `_cleanup_files` 增加 `product_assets` 分支：
  - 已完成且 `completed_at` 超 `assets_retention_days`、所有副本 completed、无在途任务、本地 `asset_root` 的商品目录
  - 无任何 `base_product.asset_root` 引用、目录最新 mtime 超期的孤儿上传目录；带 `session.json` 的在途会话跳过
  - 复用 `_safe_files`（含 `_within_allowed_root` 白名单）与既有逐文件删除 + `_prune_empty_parents`
- [x] 3.4 `_category_roots("product_assets")` 返回 `(STORAGE_DIR / "uploads" / "base-products",)`，保证 prune 不会删到 `uploads/`
- [x] 3.5 删除前对每个商品写 `mark_assets_cleaned`；结果里带 `cleaned_products`
- [x] 3.6 `run_automatic_cleanup_if_due` 内先跑压缩（`run_compaction`）再跑清理；两者结果分别写入 `data/storage_cleanup_state.json` 的
  `last_compaction_at` / `last_compaction` 与既有 `last_cleanup*`

## 任务 4：`runtime_configuration.py` 与 `main.py` 接线

- [x] 4.1 `GROUP_FIELDS["storage_cleanup"]` 增加 `assets_retention_days` / `compact_enabled` / `compact_delay_days` / `compact_max_edge` / `compact_jpeg_quality`
- [x] 4.2 `default_configuration()["storage_cleanup"]` 默认值：`assets_retention_days=30`、`compact_enabled=True`、`compact_delay_days=3`、`compact_max_edge=1000`、`compact_jpeg_quality=88`
- [x] 4.3 `main.py` GET `/api/settings/storage/usage` 与 POST `/api/settings/storage/cleanup`：只加载一次 store，同时把 `store` 传给 `scan_usage` / `cleanup_categories`
- [x] 4.4 压缩类设置项走既有 `PUT /api/settings` 校验路径（白名单已覆盖，无需新接口）
- [x] 4.5 手动清理 `product_assets` 时返回体含 `cleaned_products` 与文件计数，前端可显示

## 任务 5：设置页 UI

- [x] 5.1 `apps/web/index.html`「存储与清理」区块：新增「素材保留（天）」输入框 `settings-storage-assets-retention`；新增压缩设置：`settings-storage-compact-enabled`（勾选）、`settings-storage-compact-delay`（天）、`settings-storage-compact-edge`（长边）、`settings-storage-compact-quality`（质量）
- [x] 5.2 区块说明文案补一句：只处理已上架完成且超期的本地素材，NAS 素材与商品资料不动
- [x] 5.3 `apps/web/app.js` `renderRuntimeSettings()`：回填上述字段（缺省 30 / 3 / 1000 / 88）
- [x] 5.4 `app.js` 保存分支（约 1562 行的 `storage_cleanup` 块）写入这 5 个字段
- [x] 5.5 `renderStorageUsage()`：显示「上次自动压缩」一行（压缩张数 / 释放字节 / 失败数），类目行本身由后端类目自动渲染（无需改列表代码）
- [x] 5.6 `cleanupSelectedStorage()` 的确认文案对 `product_assets` 追加一句「已清理的素材无法再用于上款，需要重新导入」

## 任务 6：测试

- [x] 6.1 新增 `tests/test_asset_compaction.py`：
  - 判定：未完成 / 完成未满 3 天 / 有未完成副本 / 有在途任务 / NAS `asset_root` / 目录缺失 → 都不可压缩；正常完成商品可压缩
  - 转换：PNG → 同名 `.jpg`；`assets`、`base_products`、`store_listing_copies`、`listing_skus.variant_image` 四处引用同步改写且一致；`variant_image_map` 颜色键不变
  - 阈值：已是 JPEG 且没省够 → 文件与原记录都不变；长边超限 → 被缩边
  - 幂等：连跑两次，第二次 `compacted_files == 0`
  - 失败隔离：单张坏图不影响同目录其它文件
- [x] 6.2 扩展 `tests/test_disk_cleanup.py`：`product_assets` 的 scan/cleanup；孤儿目录被清、在途会话与会话目录不被清、未到期不被清、非本地 `asset_root` 不被清
- [x] 6.3 扩展 `tests/test_storage_cleanup_api.py`：usage 返回 `product_assets` 类目；清理接口接受该类目并返回计数；未确认仍 422
- [x] 6.4 扩展设置页断言（沿用 `test_store_template_workflow_ui.py` 的脚本断言风格）：新输入框 id 与保存字段存在
- [x] 6.5 全量 pytest 与基线对比，新增失败必须为 0

## 任务 7：验收

- [x] 7.1 隔离环境跑真实数据只读验收：`DATA_DIR`/`STORAGE_DIR` 指向 `%TEMP%` 下的 store.json + 素材副本，先 `scan_compaction` 看预计释放，再执行压缩，核对
  「压缩后总字节 / 压缩张数 / 第二次幂等为 0 / 引用一致」
- [x] 7.2 隔离环境验收清理：确认只删符合规则的目录，未完成商品、在途任务商品、NAS 商品、在途会话全部保留
- [x] 7.3 页面验收（隔离端口 + 项目自带 Chromium）：设置页出现「商品素材」类目与保留期、可勾选、确认文案正确、结果显示释放字节
- [x] 7.4 复核真实 `data/store.json` 与 `storage/uploads/base-products` 未被验收改动（mtime 与字节）
- [x] 7.5 向用户报告：可直接执行的建议（含需重启 API 才生效的提示），不在未经确认的情况下对真实数据执行压缩/删除

## 验收结果（2026-10-19，隔离环境全量实测）

- 素材体积：**5.82 GB → 压缩后 2.72 GB → 清理后 1.11 GB**（压缩省 3.10 GB，清理再省 1.60 GB）。
- 压缩：95 个商品 / 972 张图，0 失败，235 秒；第二次执行 0 张（幂等）。
- 引用解析：压缩前后坏链 572 = 572（全是 NAS 离线素材），零劣化。
- 清理：商品素材删 18 个商品 / 201 文件；遗留素材删 960 文件 / 1.55 GB；93 个目录按规则保留（`not_completed` 15、`too_recent` 78）；在途会话 0 个被误删。
- 真实数据指纹（store.json 字节数 + mtime、素材字节数 + 文件数）验收前后一致。
- 页面：两个新类目、默认 30/3/1000/88、「立即压缩素材」「清理所选项目」均按预期工作，控制台 0 错误。

## 运行命令

```bash
cd outputs/ai-listing-system
python -m pytest tests/test_asset_compaction.py tests/test_disk_cleanup.py tests/test_storage_cleanup_api.py -q
python -m pytest -q            # 全量，与基线对比
python -c "import services.api.asset_compaction as m; print(m.__doc__)"
node --check apps/web/app.js
```

## 回滚

- 代码：`git diff` 只涉及新增文件与 5 个既有文件的局部改动；不改数据文件即可回滚代码。
- 数据：任务 0 的 `.scratch/` 备份 + 压缩前的原图不再保留（有损），因此**真实验收一律在隔离目录做**，确认无误后再由用户决定是否对生产素材执行。
