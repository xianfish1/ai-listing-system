# 跨境（CNSC）全流程梳理 + 类目必填属性「没采集下来」的缺口分析

> 生成日期：2026-09-28（基于仓库当前工作副本）
> 范围：`outputs/ai-listing-system` 的跨境（`listing_mode = "cnsc_global"`）自动化主链路，以及它与本土（`site`）的分叉点；
> 重点回答：**类目必填属性「有些没采集下来」时，会在哪一层出问题、今天的具体表现是什么。**
> 本文只做梳理与缺口分析，不改跨境代码。

---

## 0. 一页结论

1. **跨境与本土共用同一套执行器**（`scripts/mabang_playwright_executor.cjs`），靠任务里的 `store.listing_mode` 分叉；分叉点集中在「店铺选择 / 分类属性 / 价格库存 / SKU 网格 / 保存前后校验」五处（见 §1）。
2. **分类属性这条链有三层，每一层对「没采集下来」都是盲的**：
   - **采集层**：只有「采集结果完全为空」才会复用现役版本；**部分缺失会照常发布**（`field_removed` / `required_rule_changed` 只标 `requires_review`，不阻断）。
   - **就绪层**：`validate_attribute_values(snapshot, values)` **只遍历快照里有的字段** → 没采到的必填项在 readiness 里完全不可见，副本照样显示「就绪」。
   - **执行层**：唯一兜底是「读现场必填项 → 差集 → 调 AI 补齐」；而这条兜底在**跨境路径上目前是最弱的**（现场读取单次无重试、标签匹配无宽松回退、没有本土那套「写页面表单模型」通道）。
3. 因此典型表现是两种：**保存被拦**（`pre_save_validation_failed`，nav 报「N 必填项未填」）或**静默漏填**（属性留在空值，靠马帮/Shopee 侧再报）。
4. 五种具体缺失形态与今天的后果见 §3；建议修法（含「哪些属于跨境代码、需单独验证」）见 §4。

---

## 1. 跨境 vs 本土：整条流程对照

执行器的预保存阶段表在 `scripts/mabang_playwright_executor.cjs` 的 `PRE_SAVE_STAGES`（≈ L5683-5710），按顺序：

| # | 阶段 | 本土（site） | 跨境（cnsc_global） | 代码位置 |
|---|---|---|---|---|
| 1 | `select_store` | 选 `店铺名称`（店铺名 / mabang_store_id） | 选 `CNSC店铺 / 全球商品店铺`（cnsc_store + 店铺名兜底） | `selectStore` L2165-2185 |
| 2 | `enable_cnsc_auto_publish` | 跳过 | 勾选「上架后自动发布到站点店铺」并回读 | L1240-1255 |
| 3 | `select_cnsc_target_sites` | 跳过 | `#shops-wap` 站点店铺选择器（逐站点勾选 → 确认 → 回读数量） | L955-975 |
| 4 | `select_category` | ✅ 同一个函数 | ✅ 同一个函数（打开「选择类目」→ 三段路径逐段匹配） | `selectCategory` L2318+ / `waitForCategoryPathSegment` L2311+ |
| 5 | `set_category_attributes` | ✅ 宽松标签匹配 + **写页面表单模型** + 多选 Esc 收面板 | ✅（但走**原来的点选老路径**、严格匹配、无模型写入） | `setCategoryAttributes` L2560+；分叉 `siteMode` L2564 |
| 6 | `fill_basic_info` | 相同（Parent SKU / 标题 / 描述） | 相同 | L2688+ |
| 7 | `upload_product_images` | 相同（含尺码图：模板 / 上传） | 相同 | L2791+ |
| 8 | `append_description_media` | ✅ 描述末尾插尺码表 + 全部产品图 | **整段跳过**（`cnsc_global` 直接 `{skipped:true}`） | L3555 |
| 9 | `__optional_media__` | 水印 + 模板默认视频 | 同结构（跨境无「模板默认视频」字段，走上传路径） | L3990+ |
| 10 | `configure_variants` | 颜色/尺码自定义值 | 相同 | — |
| 11 | `disable_sku_weight` / `fill_sku_grid` | SKU 行**按颜色+尺码身份匹配**后写入 | SKU 行**按行号写入**（DOM 网格），列必须存在（全球价 + 三仓库存） | `fillCNSCSkuGridFromDom` L4790+；回读 `validateCNSCDomGridReadback` |
| 12 | `fill_cnsc_site_prices_and_discounts` | 跳过 | 全球价 + 站点价格矩阵 + 各站点折扣活动，逐站点回读比对 | L1000-1240 |
| 13 | `fill_logistics_info` | 相同 | 相同 | — |
| 14 | `select_discount_activity` | ✅ 普通折扣活动 | 跳过 | L5126 附近 |
| 15 | `upload_variant_thumbnails` | 相同 | 相同 | L4790+ |
| 16 | `validate_before_save` | 必需阶段含 `select_discount_activity` | 必需阶段含 `enable_cnsc_auto_publish / select_cnsc_target_sites / fill_cnsc_site_prices_and_discounts`；额外做 CNSC 网格回读 | L5084-5098, L5209-5253 |
| — | 保存前查重 | 无 | **同 Parent SKU 草稿已存在 → 直接拦**（`cnsc_draft_inbox_parent_sku_exists`） | `assertCNSCDraftInboxCleanForParentSku` L5543+ |
| — | 保存后核对 | 无 | 草稿箱里按 Parent SKU 核对唯一命中 | `verifyCNSCDraftInbox` L5577+ |
| — | 就绪检查（API 侧） | 通用字段 + 尺码图 + 默认 SKU + 普通折扣 + 成本价兜底 | 通用字段 + CNSC 价目/站点折扣/三仓库存 + **基础商品成本价必填** + 类目快照必填属性 | `_check_automation_readiness` `storage.py` L6644-6766 |

**读法**：类目选择（第 4 步）与分类属性（第 5 步）**两步本土跨境都在跑同一个函数**；差异只体现在第 5 步的「怎么把值写进页面」以及第 11-12 步的表格/价格语义上。所以「跨境选类目 + 必填属性」的问题，本质是**分类属性这条链的缺口**，不是类目选择本身。

---

## 2. 分类属性的数据链路（采集 → 就绪 → 执行）

```
① 采集          services/api/category_profile_service.py
   create_collection_run → 页面采集 fields[{field_id,label,type,required,required_when,options,automation}]
   → create_candidate_from_capture（L81-136）→ 版本 → 人工审核 → publish_version（L243+）

② 冻结到副本     services/api/storage.py
   store_templates.category_profile_id/version → 创建副本时写
   listing_copy.category_profile_snapshot（字段+选项+required 规则） + attribute_values{field_id: value_id}

③ 就绪检查      storage.py _check_automation_readiness L6744-6760
   validate_attribute_values(snapshot, values)   ← category_rules.py L76-108
   → missing += "category_attributes.<field_id>"（只针对 snapshot.fields 里的必填项）

④ 任务计划      scripts/browser_use_runner.py
   resolve_category_attribute_fields(category_attributes, snapshot) L212-275
   → 任务里的 category_attribute_fields[{field_id,label,type,required,value_ids,option_labels,input_kind}]
   → 同时把原始 category_attributes 一起下发

⑤ 执行          scripts/mabang_playwright_executor.cjs setCategoryAttributes L2560+
   a) resolveTemplateAttributeFields(live, task.category_attributes)  ← 用**现场页面**的标签/选项映射模板值
   b) 现场必填项（readLiveCategoryAttributes）里，合并后仍缺的 → autoFillMissingRequiredAttributes
       → POST /api/ai/attribute-recommendations（值必须命中页面枚举，否则丢弃）
   c) 逐项写入：本土走「写页面表单模型」；跨境走 selectElementOptionOrCustom 点选

⑥ 保存闸        validateBeforeSave L5084+ → nav「N 必填项未填」/ form_errors → pre_save_validation_failed
                （installPublishGuard 仍只拦发布，不拦保存）
```

---

## 3. 「没采集下来」的五种形态与今天的后果

| # | 形态 | 谁看得见 | 今天的后果 | 依据 |
|---|---|---|---|---|
| 1 | **必填字段整条没采到**（不在 `snapshot.fields`） | 只有执行器（现场读页面时） | readiness 仍报「就绪」；跨境只能靠「现场必填 − 合并字段」的 AI 差集补。若现场读取返回空（异步加载竞态）→ 差集为空 → 该字段没人填 → 保存被 `pre_save_validation_failed` 拦（可重试 3 轮后整单 blocked） | `category_rules.py:82`（只遍历 definitions）；`storage.py:6746`（`if snapshot.get("fields")` 才校验）；执行器 `readLiveCategoryAttributes` 单次读（跨境无 `…WhenReady` 重试） |
| 2 | **字段在、选项没采到**（`field.options` 缺） | 任务计划层就歪了 | `resolve_category_attribute_fields` 的 `option_labels` 回退成**数字 ID** → 执行器拿数字 ID 当选项文案去找下拉项 → 找不到 → 走「添加自填选项」（可能把数字 ID 当自选值写进下拉）或报 `select_option_not_verified` | `browser_use_runner.py:241-246`（`option_labels = [label or value_id ...]`） |
| 3 | **字段在、label 没采到** | 任务计划层 | `label` 回退成 `field_id`（如 `100134`）→ 执行器按这个标签在页面找表单项 → `select_field_not_found`（在可重试列表里，会重开表单再试 3 轮；但字段名确实对不上时每轮都失败，最终整单 blocked） | `browser_use_runner.py:270`；执行器 `setCategoryAttributes` 的 `select_field_not_found` 抛出点 |
| 4 | **快照过期**（平台/Mabang 后来新增必填项） | 谁都看不见（就绪用的是快照，不是现场） | 同形态 1：readiness 放行，执行器靠 AI 兜底 | 同上 |
| 5 | **部分缺失的采集不被拦** | 采集层只拦「完全为空」 | 只有 `fields == []` 才复用现役版本（2026-09-26「两次空确认把 100244 的 16 字段顶成空版本」事故的护栏）；`field_removed` / `required_rule_changed` / `options_removed` 都只标 `requires_review`，**照常发布** | `category_profile_service.py:108-113`（空版本护栏）、L217-230（diff 严重度） |

补充两点（真实存在、但属于相邻问题）：

- **跨境比本土少三条容错**（本土这几条是 2026-09-27 现场验证过有效的）：① 现场属性定义「等就绪再读」的重试；② 标签宽松匹配（剥掉「（Material）」这类括号后缀 + 前缀锚定）；③ 「写页面表单模型」这条可靠落值通道（多选点选的值只停在"待提交"态，收面板/重渲染就丢）。
- **`detailPageMatchesTask` 用 `global=1` 区分本土/跨境是错的**：本土详情页 URL 也带 `global=1`（实测），于是 **CNSC 任务可能复用一个残留的本土详情页**（返回 true → 直接复用），而本土任务永远不会复用自己留下的页面。这是跨境/本土互相踩线的隐患，建议单独验证后修。

---

## 3.5 现场数据核对：当前跨境副本到底有没有类目档案

> 数据来源：`data/store.json`（2026-09-28 读取）。这是「没采集下来」在你们这条产线上的**实际形态**。

**类目档案（5 个，全部是 Shopee）**

| profile_id | country | 版本 | 字段 | 必填 | 选项 | 最近采集 |
|---|---|---|---|---|---|---|
| `shopee-ph-100244` | PH | v20 | 16 | 4 | 87 | 2026-09-26 |
| `shopee-ph-100226` | PH | v3 | 22 | 3 | 190 | 2026-08-28 |
| `shopee-ph-101757` | PH | v11 | 15 | 3 | 105 | 2026-09-21 |
| `shopee-ph-101773` | PH | v1 | 15 | 3 | 103 | 2026-09-07 |
| `shopee-global-100244` | GLOBAL | v1 | **0** | 0 | 0 | 2026-08-27 |

**模板 → 类目档案绑定**

| 模板模式 | 店铺 | 引用档案 | 版本 | 字段 |
|---|---|---|---|---|
| `cnsc_global` | 2021145489 | `shopee-ph-100226` | v3 | 22 |
| `site` | 2022625115 | `shopee-ph-101757` | v11 | 15 |
| `site` | 2022625115 | `shopee-ph-100244` | v20 | 16 |

**跨境副本（45 个副本里只有 2 个跨境，两个的快照都是空的）**

| 副本 | 类目 | 档案引用 | 快照 fields | attribute_values |
|---|---|---|---|---|
| `FBDC11` | Men Clothes > Costumes（100059） | **空** | **0** | `{100134:["1257"], 100168:"1480", 101004:"5487"}` |
| `B6BF84A8` | Women Clothes > Costumes（100115） | **空** | **0** | `{}` |

**结论：这两个跨境副本的类目（100059 / 100115，Cosplay 服装）从来没有采集过类目档案。** 副本创建时的重绑逻辑是这样的（`storage.py` L6020-6060）：

```python
# 生效类目有自己的已发布方案就换绑过去（属性值重置为该方案默认值）；
# 没有就清空快照与绑定，执行器跳过属性填写——宁可少填，也不填错类目的属性。
if category_from_base and copy_category_id and copy_category_id != template.mabang_category_id:
    effective_profile_id, effective_version_id, snapshot = _profile_binding_for_category(
        copy_category_id, data, platform=..., country=template.country or "MY")
```

即：基础商品的类目（Costumes）与模板类目不同 → 按生效类目重找方案 → **找不到 → 清空快照与绑定（设计如此）**。于是：

1. **就绪检查完全失真**：`_check_automation_readiness` 里 `if isinstance(snapshot, dict) and snapshot.get("fields")` 不成立 → 整段类目属性校验被跳过 → 副本显示「就绪」，操作员看不到任何提示（§3 形态 1 的真实案例，而且是「一个字段都没继承」的极端版）。
2. **执行器只能从零开始**：任务里 `category_attribute_fields = []`，只能靠「现场读必填 + AI 补齐」；跨境这条路目前最弱（§3 补充），保存容易被 `pre_save_validation_failed` 拦（可重试 3 轮后整单 blocked）。
3. `FBDC11` 里那 3 个历史 `attribute_values`（100134/100168/101004）在快照为空时**无法映射成标签**：任务计划里 `label` 会退化成 `field_id`、`option_labels` 退化成数字 ID（§3 形态 2/3），反而可能触发 `select_field_not_found`。
4. `shopee-global-100244` 这个 GLOBAL 档案自己是 **v1 且 0 字段**（2026-08-27 采集），说明「空采集」在过去确实落过库——现有的空版本护栏（`category_profile_service.py:108-113`）只保护「已有非空现役版本」的情况，**首个版本就是空的**它拦不住。

---

## 4. 建议修法（按优先级；★=跨境代码，需单独真机验证）

### P0 让「没采到」至少可见（改动小、风险低）
1. **就绪检查补一条现场/清单对照**：在 `_check_automation_readiness` 里，除 `validate_attribute_values(snapshot, values)` 外，把「快照里**根本没有**但该副本所属类目**当前必填**的属性」也报出来。
   - **最小可用版（不改跨境执行逻辑，也不依赖现场）**：把「副本没有任何类目档案快照」直接报成缺项/警告，例如 `missing_fields += ["category_profile_missing"]`（当 `marketplace_category_path` 有值、但 `category_profile_snapshot` 为空时）。今天的 100059 / 100115 两个跨境副本就会立刻在就绪页暴露，而不是显示"就绪"。
   - 数据来源可以是最近一次采集版本的 diff（`field_added` 且 `required`），或采集端在版本里额外记一份「本次没渲染出来的字段」清单。
   - 产出形式建议：`warnings: ["category_attributes_missing_from_snapshot.<field_id>"]`（不阻塞，但操作员能看到）。
2. **采集端：部分缺失也要人工确认**。把 `field_removed` / `required_rule_changed` / `options_removed` 三类 diff 的处置从「照常发布」改成「必须审核备注后才 publish」（现在已经有 `publish_version` 的 reviewer/note 字段可用）。★（这条改的是采集/审核侧，跨境本土共用）
3. **报错带清单**：执行器在 `pre_save_validation_failed` 时，把「现场必填但快照/模板里没有的字段」一并写进 details，操作员一眼知道是采集漏了还是模板没填。

### P1 执行端按模式补齐容错（跨境专属，需真机验证一轮）★
4. 跨境也走「读现场等就绪」（`readLiveCategoryAttributesWhenReady`）：避免现场读取返回空导致**整条 AI 差集被跳过**。
5. 跨境也开宽松标签匹配（`loose`）：现场标签常带「（Material）」后缀，严格匹配会 `select_field_not_found`。
6. 评估跨境是否也走「写页面表单模型」：本土实测这是唯一可靠落值通道（多选点选的值会丢）。跨境属性集与本土同类目时行为应一致；但跨境有「全球价/站点价格」等本土没有的字段，必须跑一遍真实 CNSC 任务再定。
7. 数值/文案类必填（型号、条件等）：确认「枚举里没有的值」的策略（自填选项 vs 直接写模型）。

### P2 采集侧根治
8. 采集前**先展开「显示所有属性」**再抓 `fields`（现场已确认马帮默认只渲染必填项，其余折在按钮后面；展开后字段数会从 4 变 15，本土现场实测）。
9. 采集完成后做一次**自检**：把抓到的字段与页面 `mandatory=1` 的字段对一遍，不一致就拒绝发布（把「少采」变成硬失败）。
10. 定期/按需重采：给类目档案加「距上次采集天数」并在就绪页提醒（形态 4 是时间问题，不重采永远修不掉）。

### P3 相邻问题（独立评估）
11. `detailPageMatchesTask` 的本土/跨境判据（见 §3 补充）。
12. 跨境 `set_category_attributes` 的耗时：目前仍走点选老路径（本土已优化到 ~8s；跨境同类目预计仍在 20~30s 量级，且带 60s 级别的诊断兜底）。

---

## 5. 需要你确认的三件事

1. **采集漏项的责任边界**：是「采集端必须自动重采到齐」，还是「漏了就报给操作员，由人工在类目档案里补」？（决定 P0-1 与 P2-8/9 的做法）
   - 就现有数据看，**100059（男装 Cosplay）与 100115（女装 Cosplay）必须补采一次类目档案**，否则这两个跨境副本的属性只能靠现场 AI 现猜。
2. **跨境要不要一起享用本土那三条容错**（P1-4/5/6）？如果要，我会先在真机跑一遍 CNSC 任务做前后对照，再改。
3. **采集触发频率**：现在采集是手动/按需还是要挂进 Worker 定时？（决定形态 4 能不能自动消除）

---

## 6. 已落地（2026-09-28）

### 6.1 就绪检查补「缺少类目方案」提醒（已实现并验证）

- 位置：`services/api/storage.py` `_check_automation_readiness`（快照为空的分支）+ 前端文案
  `LISTING_COPY_READINESS_LABELS.category_profile_missing`（`apps/web/app.js`）。
- 判据：副本**有类目**（`marketplace_category_path` / `mabang_category_id` 非空）但**连档案绑定都没有**
  （`category_profile_id` / `category_profile_version_id` 均为空、快照为空）→ 报 `category_profile_missing`。
- **只提醒、不阻塞**（进 `warnings`）：无档案副本历史上一直能生成任务（执行器会在现场读必填 + AI 补齐），
  把它变成硬缺项会打断 30+ 个既有流程（实测：第一版做成 `missing_fields` 后全量 pytest 由 15 红升到 46 红）。
  「采过但确实没有属性」的类目（有绑定、快照字段为空）**不会**被误报。
- 真实数据验证：45 个副本里只有目标那两个报出提醒，零误报
  ```
  FBDC11   cat=100059 ready=True missing=[] warnings=['category_profile_missing']
  B6BF84A8 cat=100115 ready=True missing=[] warnings=['category_profile_missing']
  ```
- 测试：`tests/test_automation_readiness.py` 新增 3 条（无绑定要提醒 / 有绑定空快照不提醒 / 没选类目不提醒）；
  全量 pytest 回到基线 15 红（867 passed）。

### 6.2 补采 100059 / 100115（已完成）

- 官方采集入口（`.venv-browser-use` 的 browser-harness）在本机起不来
  （`PermissionError: bu-default.port` → `RuntimeError: AssertionError`），改用已跑通的 CDP 链路自采：
  `.scratch/capture_category_profile.cjs`（只读读 `ProductAttributeInfo.attributesNew`，先点开「显示所有属性」再读）
  + `.scratch/collect_category_profile_via_cdp.py`（借真实任务计划把店铺+目标类目选上，再建 run、提交 capture）。
- 两个关键坑（都已解决）：
  1. **选中类目 id 只在 `selectCategoryDialog.categoryIdAll` 上**（`storeCategoryModule.categorySettings.categoryIdAll`
     是空的、也没有 `input[name=category_id]`）；官方脚本读表单控件那套在这个页面上取不到值。
     采集必须带 `observed_category_id`，且后端会强校验它等于 run 的类目。
  2. 马帮默认只渲染必填属性，其余折在「显示所有属性」后面 → 采集前必须先展开，否则漏采。
- 采集结果（写入 `data/store.json`，状态 active）：
  | profile | 版本 | 字段 | 必填 | 选项 | 采集时间 |
  |---|---|---|---|---|---|
  | `shopee-ph-100059` | v1 | 10 | 0 | 68 | 2026-09-27T16:45:46 |
  | `shopee-ph-100115` | v1 | 12 | 1（加号 101004） | 81 | 2026-09-27T16:47:06 |
- 必填为 0 是**真的**：现场页面上带 `*` 的只有「尺码图表」一个，10 个类目属性都没打星
  （Costumes 类目没有必填分类属性）；100115 则只有「加号」必填。

### 6.3 已执行：把新档案重绑到两个副本（无损，2026-09-28）

- 做法（用户选 A′：只重绑，不删副本）：用 `_profile_binding_for_category(副本生效类目, platform, country=模板国家)`
  解析出档案与版本 → `PUT /api/store-listing-copies/<id>` 写入 `category_profile_id` /
  `category_profile_version_id` / `category_profile_snapshot` / `attribute_values`（只保留落在新快照字段上的
  基础商品值 + 快照默认值，对齐副本创建路径）。工具：`.scratch/rebind_cnsc_copy_profiles.py`（默认空跑，`--apply` 才写）。
- 前后对照（真机数据）：

  | 副本 | 生效类目 | 重绑前 | 重绑后 | 就绪 |
  |---|---|---|---|---|
  | `FBDC11` | 100059 男装 Costumes | 无档案、快照 0 字段 | `shopee-ph-100059` / 10 字段 / 0 必填 | `ready=True`，提醒消失 |
  | `B6BF84A8` | 100115 女装 Costumes | 无档案、快照 0 字段 | `shopee-ph-100115` / 12 字段 / 1 必填（加号 101004） | `ready=False`，缺 `category_attributes.101004`（**这是正确的**，见下） |

- SKU / 价格 / 两条已保存作业（`f03ceb15`、`da08fba1`）**全部保留**，副本 id 未变。
- `B6BF84A8` 缺的那个必填项要人工给值：执行器的 AI 补齐只负责「页面上必填、但快照里没有的字段」
  （`missing = required.filter((field) => !known.has(...))`，避免覆盖模板值），快照里已有的必填项不会被自动填。
  现场问了一次 AI 推荐接口，建议值是 `101004 = 5488`（加号 = 否），需要操作员在副本编辑器里确认或改选。
- 不要用「重存店铺模板」来同步：模板的档案是 100226（Hoodies），会把错类目的字段推给 Costumes 副本。

## 附：本次梳理用到的关键代码位置

| 主题 | 位置 |
|---|---|
| 预保存阶段表 / 跨境阶段差异 | `scripts/mabang_playwright_executor.cjs` `PRE_SAVE_STAGES` ≈L5683-5710 |
| 分类属性填写（本土/跨境分叉） | 同文件 `setCategoryAttributes` L2560-2686；`siteMode` L2564 |
| 现场必填 + AI 补齐 | 同文件 `autoFillMissingRequiredAttributes` L2426-2540；`readLiveCategoryAttributes` L2470 / `…WhenReady` L2404 |
| 模板值 → 现场标签映射 | 同文件 `resolveTemplateAttributeFields` L2386-2430 |
| 保存闸 | 同文件 `validateBeforeSave` L5084-5285 |
| 跨境保存前查重 / 保存后核对 | 同文件 L5543-5603 |
| 就绪检查（含类目属性） | `services/api/storage.py` `_check_automation_readiness` L6644-6766 |
| 类目属性校验器（只遍历快照字段） | `services/api/category_rules.py` `validate_attribute_values` L76-108 |
| 采集候选版本 / diff 严重度 / 空版本护栏 | `services/api/category_profile_service.py` L81-136、L205-232 |
| 任务计划里的属性字段解析 | `scripts/browser_use_runner.py` `resolve_category_attribute_fields` L212-275 |
