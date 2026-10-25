# 商品素材生命周期治理设计：上架完成后压缩与超期清理

## 目标

给本地商品素材加一条明确的生命周期：**上架完成后满 3 天自动压缩体积，满 30 天可从设置页清理**。压缩在不动文件名主干、不动颜色识别、不动再次上款链路的前提下，把现场 5.82 GB 的本地素材压到约 2.7 GB；再叠加两类清理（已上架完成超期 18 个商品 + 已删商品遗留素材 80 个目录），可降到约 **1.2 GB**。

## 预期效果（2026-10-19 现场实测）

| 步骤 | 释放 | 剩余 |
| --- | --- | --- |
| 起点（本地素材） | — | 5.82 GB / 2,474 文件 |
| 自动压缩（95 个商品 / 972 张图，零失败，244s） | 3.10 GB | 2.72 GB |
| 手动清理「已删商品遗留素材」（960 个文件） | 1.55 GB | 1.17 GB |
| 手动/自动清理「商品素材」（20 个商品 / 223 文件） | 0.06 GB | **1.11 GB / 1,291 文件** |

剩下的 1.11 GB 由「78 个未到 30 天的商品素材（已压缩）」与「未到期的遗留素材（约 0.70 GB）」构成，会随时间进入清理范围；新建商品在导入时约 +30 MB，完成满 3 天压到约 1.5 MB，满 30 天删除。

执行中补的两道防线（现场踩到过）：清理后必须把「指向已删目录、但同组存在同名活文件」的资产记录就地改指（`plan_asset_pointer_repair`），否则解析器按名字取到的第一条会是死链；无归属目录若是某个未清理商品某张图的**唯一现存副本**，则拒绝删除（`_only_copy_dirs`）。

## 背景

现场实测（2026-10-19，只读统计；判定数字由模块判定与独立手工判定双向核对一致）：

- 本地商品素材 `storage/uploads/base-products/`：**191 个上传目录 / 2,474 个文件 / 5.82 GB**。按归属拆开：有基础商品归属的 111 个目录（3.57 GB / 1,215 文件），无归属的 80 个目录（2.24 GB / 1,259 文件）。NAS 引用型素材（32 个商品的 `asset_root` 指向 `\\Juns\…`）不在本地，一律不动。
- 按扩展名拆开看，体积几乎全在 PNG：**.png 1,769 个 / 5.54 GB**、.jpg 666 个 / 0.15 GB、.mp4 36 个 / 0.11 GB、.psd 1 个 / 0.01 GB。也就是说「颜色图」是这里唯一值得动的东西，视频与源文件不该碰。
- 抽样实测按长边 1000、质量 88 转 JPEG：PNG 平均 **3.28 MB → 0.18 MB（省 94.6%）**；已是 JPEG 的图重压平均只再省 58%（0.171 MB → 0.071 MB，个别 4096×3072 的能省 93%）。只降分辨率保 PNG 只能省 62~75%。这是本次选 JPEG、且对已是 JPEG 的图设「没省够就不动」阈值的量化依据。
- **符合压缩条件的商品：95 个 / 982 张图 / 预计释放 2.94 GB**（判定条件见下）；隔离环境实测压缩释放 **3.10 GB**，95 个商品零失败。
- **符合清理条件的商品素材：18 个商品 / 201 个文件 / 0.49 GB**（完成满 30 天）；压缩后再清理这批只再省 0.06 GB（体积已在压缩阶段降下来了）。
- **无归属遗留素材：80 个目录 / 2.24 GB**，其中按 30 天口径当前可清 1.51 GB / 949 个文件。之所以单列，是因为 187/187 个目录都还有 `assets` 记录指向它们——它们是**商品已被删除（或同款重复导入）后遗留的素材**，删除基础商品只删 store 记录、不碰磁盘。真正「任何地方都没引用」的只有 6 个 / 0.02 GB。
- 图片在库里是**按文件名引用**的：`base_products` / `store_listing_copies` 保存 `main_image` / `product_images` / `size_chart_image` / `variant_image_map`（颜色→文件名），由 `assets` 集合（按 `group_id` 过滤，见 `_assets_for_group_in_store`）解析成磁盘绝对路径后才交给执行器（`_build_browser_inputs` 输出 `product_image_paths` / `variant_image_paths` / `main_image_path` / `size_chart_image_path`）。
- 链路本身**已经兼容 `.jpg` 颜色图**：代码注释与既有测试里既有 `A白色.jpg` 也有 `.png`，颜色识别按文件名主干匹配、不吃扩展名。
- 上架完成的信号是现成的：任务保存成功后 `_cascade_completed_from_job` 把副本置 `completed`，**该基础商品的所有副本都 completed 时**才把基础商品置 `completed` 并写 `completed_at`；`rollback_base_product` 会把基础商品退回 `base_ready`、副本退回 `copy_ready` 并清空 `completed_at`。
- 重排队（「再次尝试」）会**按当前副本重新冻结执行快照**（`requeue_automation_job` → `_build_browser_inputs`），因此素材被压缩或删除后不存在「拿旧快照去上传旧路径」的路径。

## 术语

- **本地商品素材（local product assets）**：`storage/uploads/base-products/<upload_id>/<文件夹>` 下的文件，`base_product.asset_root` 指向它。NAS 引用型素材不属于本设计范围。
- **压缩（compaction）**：把符合条件的图片转成 JPEG（同名换扩展名）+ 同步改写引用记录。
- **素材清理（asset cleanup）**：删除符合条件的基础商品素材目录，或删除无引用的孤儿上传目录。

## 归属与判定规则

所有判定都在服务端唯一入口完成，避免前端各写一套。

### 可压缩（compaction eligibility）

同时满足才算：

1. `base_product.status == "completed"` 且 `completed_at` 非空，且距今 ≥ `compaction_delay_days`（默认 **3** 天）。
2. 该基础商品的**所有** `store_listing_copies` 状态都是 `completed`（新增一个待上款副本即自动退出压缩范围）。
3. 没有任何自动化任务处于在途状态（`ready` / `claimed` / `running` / `needs_confirmation` / `needs_manual` / `needs_regeneration`）引用它的副本。
4. `asset_root` 落在本地 `storage/uploads/base-products/` 之下（NAS 路径直接跳过）。
5. 目录存在；单张图已经是 JPEG 且长边 ≤ `image_max_edge`、体积 ≤ 目标体积时跳过（幂等，不重复转码）。
6. 单张图转换失败只记失败计数，不影响同商品其余图片与其它商品。

### 转换范围与阈值

- 只处理本地商品素材目录**之内**的图片；`asset_root` 指向 NAS 的商品整单跳过。
- **PNG 一律转 JPEG**（同名换扩展名），并按 `image_max_edge`（默认 1000）等比缩边。
- **已经是 JPEG 的图**：只有「长边 > `image_max_edge`」或「转码后体积 ≤ 原体积的 `recompress_min_gain`（默认 85%）」才替换，否则保持原文件不动——避免为 15% 的收益做一次有损重编码。
- `.mp4`、`.psd` 等非图片文件跳过；`.png` 转换被中断时保留原文件（先写临时文件再原子替换）。

### 可清理（cleanup eligibility）

拆成两个类目，天数都用 `assets_retention_days`（默认 **30**）：

**类目一：商品素材（`product_assets`）—— 自动 + 手动**

1. **已完成商品的素材**：`status == completed` 且 `completed_at` 距今 ≥ 保留期；所有副本 `completed`；无在途任务引用其副本；`asset_root` 在本地 base-products 之下且目录存在。
2. 回滚过的商品（`rolled_back_at` 非空 / `completed_at` 被清空）自动落回规则 1 之外，绝不会被删。

**类目二：已删商品遗留素材（`product_assets_legacy`）—— 仅手动**

1. 目录**没有任何** `base_product.asset_root` 归属（商品已被删除），目录内最新 mtime 距今 ≥ 保留期。
2. 带 `session.json` 的在途上传会话跳过（归既有 `temporary_files` 的 7 天口径）。
3. **不进 `AUTOMATIC_CATEGORIES`**：这类素材牵涉已删商品且不可逆，只在操作员显式勾选并二次确认时删除。

**两个类目共同排除的类型**（判定与用例都要覆盖）：`status != completed` 的商品、回滚过的商品、有在途任务的副本、NAS `asset_root`、含 `session.json` 的会话、符号链接/junction、白名单（`STORAGE_DIR` 与 `tmp_upload`）之外的路径。

### 不可逆与保护

- 压缩与清理都在基础商品上留痕：`assets_compacted_at` / `assets_compacted_bytes` / `assets_cleaned_at` / `assets_cleaned_bytes`。
- 素材被删除后，回滚再上款会在既有就绪检查里报文件缺失（`upload_file_missing` 这一类），界面据此提示「素材已清理，需要重新导入」，不静默失败。
- 删除前先在「存储与清理」里显示可释放空间，界面二次确认（沿用现有 confirm 交互）。
- 同一时刻只允许一个压缩/清理任务在跑（沿用 `_CLEANUP_LOCK` 的单飞语义），并始终跳过在途任务引用的素材。

## 名字改写规则

压缩是**同主名换扩展名**：`A白色.png` → `A白色.jpg`。颜色识别、`variant_image_map` 的颜色键、产品图排序都只依赖文件名主干，因此都不受影响。必须在**同一次整库写入**里完成的改写：

| 集合 | 字段 |
| --- | --- |
| `assets` | `file_name`、`file_path`、`size_bytes`、`width`、`height`、`file_hash`（sha256 重算） |
| `base_products` | `main_image`、`product_images`、`size_chart_image`、`variant_image_map` |
| `store_listing_copies` | `main_image`、`product_images`、`variant_image_map`、`size_chart_image` |

不写回的对象：

- **历史快照**（`automation_job_snapshots`、`listing_copy_revisions`）保持不可变审计语义，不追改；重排队会按当前副本重建 payload。
- `storage/thumbnails/` 不用手工清：缓存文件名带源文件 mtime+size（`_target_path`），内容变了自动失效并在下次生成时 `_prune_stale` 掉旧份。

## 运行时配置

`cleanup` 配置组新增 `assets_retention_days`（默认 30）；新增 `compaction` 配置组：`enabled`（默认 true）、`compaction_delay_days`（默认 3）、`image_max_edge`（默认 1000）、`jpeg_quality`（默认 88）。全部走既有 `runtime_configuration` 的字段白名单、默认值与审计路径。

## 每日执行

复用既有的每日自动清理节拍（`run_automatic_cleanup_if_due`，24 小时一次）：

1. **先压缩**：按可压缩规则转码并改写记录。
2. **再清理**：按可清理规则删目录。
3. 结果写入 `data/storage_cleanup_state.json`，供设置页显示「上次自动执行」的释放字节、压缩张数、跳过与失败计数。

## 接口与页面

- `GET /api/settings/storage/usage` 增加两个类目：`product_assets`（商品素材：总占用、可释放空间、文件数、是否自动执行、保护说明）与 `product_assets_legacy`（已删商品遗留素材：同上，但 `automatic` 为 false）。与现有五个类目同构，前端列表自动渲染。
- `POST /api/settings/storage/cleanup` 接受 `categories: ["product_assets"]` 与 `["product_assets_legacy"]`；后者沿用同一条 `confirm: true` 门槛。
- 「设置 → 存储与清理」的保留期一行新增「素材保留（天）」输入框（默认 30）；新增压缩设置项（开关、延后天数、长边、质量）；区块顶部显示上次压缩的结果摘要。

## 已知风险与取舍

实测过、接受或规避掉的几处，写在这里免得以后当成新问题重新查：

1. **改名必须是「整组一致」**：图片解析入口（`storage._asset_by_name`、`mabang_export._find_asset_by_name`）都是「整名全等」（含扩展名，大小写规则还不统一：`_assets_for_group_in_store` 走小写全等，`_build_browser_inputs` 主图查找区分大小写），而 `_assets_for_group_in_store` 只按 `group_id` 过滤、不看目录。因此压缩**按商品（= 按 group）**进行，且一次事务内改写四处引用。实测现场 143 个 group 各只有 1 个基础商品、本地目录内 0 个同名冲突，所以不存在「同组只改一半」的情况；将来若出现同组多会话，判定应升级为「同组全部可压才压」。
2. **不能靠重扫素材修复**：扫描型资产的 `id = uuid5(file_path|size|mtime_ns)`（`workers/asset_scanner/scanner.py`），而 `POST /api/assets/scan` 走 `replace_assets` 会**整表替换** `assets`。所以压缩后**不要**对该目录重新扫描素材；本项目按记录原地改写、保留 asset id（前端与副本都按 asset id 取图）。
3. **在途任务的快照不需要改写**：`automation_job_snapshots` 里 `browser_inputs` 存了绝对路径，改了必须重算 `execution_snapshot_hash`，否则就绪检查报 `execution_snapshot_changed`。因为压缩与清理都**排除有在途任务的商品**，所以首期不动任何快照；「再次尝试」会按当前副本重建快照，自动拿到新文件名。
4. **导出兜底的行为变化**：`mabang_export.py` 里有一个 `Path(asset_root)/f"{color_code}.jpg"` 的硬编码兜底。改名后它从「永远落空」变成「可能命中真实文件」——方向上更正确（本来就在找 .jpg），但属于可观察的行为变化，验收时按导出用例回归。
5. **清理后素材库会出现缺图条目**：`assets` 记录与历史快照都保留（审计需要），素材库里该商品的预览会变成缺图；重新扫描该目录会按新路径重建资产 id，属于既有扫描语义，不在本次范围。
6. **回滚窗口**：素材删除按 `completed_at` 计时，回滚（`rollback_base_product`）会清空 `completed_at`，所以「回滚过、又被重新上款完成」的商品会重新计时——不会出现「刚回滚就被删」。

## 非目标

- 不动 NAS 上的文件，也不删 `assets` 里指向 NAS 的 668 条索引。
- 不动 `storage/uploads/product-images`、`size-charts`、`videos`、`thumbnails`、浏览器 Profile。
- 不做「按单个商品手动压缩/删除」的按钮（首期只有自动 + 设置页类目）。
- 不改动上架执行链路、就绪度口径与颜色识别规则本身。
- 清理后不删除对应的 `assets` 记录与历史执行快照：它们是审计与追溯证据，素材库中该商品会显示为缺图；要打扫素材库索引属于后续独立需求。

## 验收标准

1. 可压缩集合正确：未完成、有在途副本/任务、NAS 引用、目录缺失的商品一律不进入。
2. 压缩后：文件名主干不变、扩展名变 `.jpg`；`base_products` / `store_listing_copies` / `listing_skus.variant_image` / `assets` 四处引用一致，颜色顺序与 `variant_image_map` 颜色键不变。
3. 压缩省字节数实测 ≥ 80%（现场样本口径 94.6%）。
4. 幂等：重复执行第二次不产生新增转换（计数为 0）。
5. 清理只删符合规则的商品素材；对每个「不该删」的样本都有对应用例（未完成、回滚过、在途任务、未过期、NAS、在途会话、符号链接、白名单外）。
6. `product_assets_legacy` 不在自动清理集合内：`run_automatic_cleanup_if_due` 的自动类目清单里没有它。
7. 设置页能看到「商品素材」与「已删商品遗留素材」两个类目及各自可释放空间，能手动清理并显示结果；每日自动执行后 `storage_cleanup_state.json` 有压缩与清理两条结果。
8. 全量 pytest 无新增失败（`test_disk_cleanup.py` 中两处精确断言按新类目同步更新）；真实数据只读验收（隔离 DATA_DIR/STORAGE_DIR）通过。
