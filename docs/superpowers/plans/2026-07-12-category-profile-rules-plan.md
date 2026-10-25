# 类目规则与模板必填项重构实施计划

> **面向执行代理：** 本计划按测试驱动方式执行。每个任务先写失败测试，再实现最小改动，运行指定命令确认通过。当前目录没有有效 Git 元数据，不能执行 commit；每个任务结束必须记录 `git` 不可用这一事实，并保留可审查的文件差异。

**目标：** 将现有固定五字段模板改造成“马帮真实采集、人工审核发布、版本化类目规则、模板覆盖、刊登快照、schema 驱动自动化”的完整链路。

**架构：** 保持现有 Python 标准库 API、JSON/Postgres JSONB 兼容存储和原生 Web 前端。把规则解析与校验放入纯函数模块；把采集、差异和发布流程放在独立服务；`storage.py` 只负责兼容读写和事务编排。自动化输入从刊登副本快照生成，不再在浏览器脚本中硬编码类目属性。

**技术栈：** Python 3、pytest、标准库 `json`/`hashlib`/`urllib`，现有 Browser-Use CLI，原生 HTML/CSS/JavaScript。

**执行顺序：** 规则核心 -> 存储兼容 -> 数据迁移 -> 采集与审核 API -> 动态模板 UI -> 自动化适配 -> 灰度和实地验收。

## 文件总览

### 新建

- `services/api/category_rules.py`：字段 schema、条件表达式、值解析、统一校验、快照哈希。
- `services/api/category_profile_service.py`：候选版本、差异分类、审核发布和采集运行的领域服务。
- `scripts/collect_mabang_category_profile.py`：使用现有 Chrome 登录态执行只读采集并输出原始 JSON。
- `scripts/migrate_category_profiles.py`：从静态 `CATEGORY_PROFILES` 和已有模板生成初始版本，支持 `--dry-run/--apply`。
- `tests/test_category_rules.py`：纯函数和条件必填测试。
- `tests/test_category_profile_service.py`：候选、差异、发布状态机测试。
- `tests/test_category_profile_migration.py`：迁移幂等、备份和旧数据兼容测试。
- `tests/fixtures/mabang/category_mens_tee.json`：成人男装类目采集 fixture。
- `tests/fixtures/mabang/category_second_profile.json`：字段集合不同的童装或非服装类目 fixture。
- `tests/fixtures/mabang/category_diff.json`：采集结果变化 fixture。

### 修改

- `services/api/db_store.py`：将类目规则、版本和采集运行加入 Postgres JSONB 兼容集合。
- `services/api/storage.py`：接入新服务；保留旧字段读取；创建模板/刊登副本时写入版本引用和快照；移除固定五字段 readiness 逻辑。
- `services/api/main.py`：增加类目版本、采集运行、审核发布和校验路由。
- `database/schema.sql`：补充未来关系型迁移参考表和索引，不改变当前 M1 的 JSONB 运行方式。
- `apps/web/index.html`：增加类目规则审核区、版本差异表和动态属性容器。
- `apps/web/app.js`：动态渲染字段、校验错误、版本升级预览和采集状态。
- `apps/web/styles.css`：为字段错误、差异状态、候选/已发布标签提供样式。
- `scripts/browser_use_runner.py`：按快照字段和 automation mapping 生成动作，删除类目硬编码和重复 payload 键。
- `tests/test_product_line_flow.py`：补充版本引用、模板覆盖、刊登快照断言。
- `tests/test_api_boundaries.py`：覆盖新增 API 路由和错误状态。
- `tests/test_self_run_listing.py`：验证新必填字段会阻断安全执行。
- `README.md`、`docs/M1_ARCHITECTURE.md`：记录采集、审核、版本和快照流程。

## 任务 1：建立字段 schema 与统一校验器

**文件：**

- 新建 `services/api/category_rules.py`
- 新建 `tests/test_category_rules.py`

- [ ] **步骤 1：先写失败测试**

```python
def test_different_profiles_resolve_different_required_fields():
    mens = profile(fields=[field("material", required=True)])
    kids = profile(fields=[field("age_group", required=True)])

    assert validate_attribute_values(mens, {}).missing_field_ids == ["material"]
    assert validate_attribute_values(kids, {}).missing_field_ids == ["age_group"]


def test_conditional_required_field_uses_whitelisted_operators():
    version = profile(fields=[
        field("age_group"),
        field("height", required_when={"field": "age_group", "op": "in", "value": ["baby"]}),
    ])

    result = validate_attribute_values(version, {"age_group": "baby"})

    assert result.missing_field_ids == ["height"]


def test_unsupported_condition_is_invalid_for_publish():
    version = profile(fields=[field("x", required_when={"python": "..."})])
    assert validate_profile_version(version).blocking_errors[0].code == "unsupported_condition"
```

- [ ] **步骤 2：运行失败测试**

运行：`python -m pytest tests/test_category_rules.py -q`

预期：FAIL，报告 `services.api.category_rules` 不存在。

- [ ] **步骤 3：实现最小纯函数模块**

实现 `FieldDefinition` 的字典归一化、`resolve_required_fields`、`validate_attribute_values`、`validate_profile_version`、`build_profile_snapshot` 和 `hash_profile_snapshot`。只接受 `text/number/boolean/single_select/multi_select`；只实现 `eq/neq/in/not_in/present` 及顶层 `all/any`。结果必须稳定排序，避免同一数据生成不同 hash。

- [ ] **步骤 4：运行通过测试**

运行：`python -m pytest tests/test_category_rules.py -q`

预期：全部 PASS，至少覆盖空值、类型错误、未知选项、条件必填、未支持条件和稳定 hash。

## 任务 2：增加类目规则领域服务和兼容存储

**文件：**

- 新建 `services/api/category_profile_service.py`
- 修改 `services/api/db_store.py`
- 修改 `services/api/storage.py`
- 新建 `tests/test_category_profile_service.py`

- [ ] **步骤 1：写失败测试**

覆盖：候选版本创建、差异分类、同一 profile 只能一个 published、拒绝后不可发布、发布幂等、旧版本保留、采集失败不生成候选版本。

- [ ] **步骤 2：运行失败测试**

运行：`python -m pytest tests/test_category_profile_service.py -q`

预期：FAIL，领域服务和新集合尚未存在。

- [ ] **步骤 3：扩展集合和服务**

在 `db_store.py` 的 `COLLECTIONS` 加入 `category_profiles`、`category_profile_versions`、`category_collection_runs`、`store_templates`、`store_listing_copies`、`listing_skus`、`listing_prices`、`automation_jobs`，与当前 JSON 存储实际使用的集合保持一致。

在 `category_profile_service.py` 实现：

```python
create_collection_run(payload)
save_raw_capture(run_id, raw_capture)
normalize_candidate(raw_capture, current_version)
classify_profile_diff(previous, candidate)
publish_version(version_id, reviewer, note)
reject_version(version_id, reviewer, reason)
```

所有状态转换在 `locked_store()` 内完成；发布必须校验 schema、阻断差异、审核人和审核备注。

- [ ] **步骤 4：通过服务测试**

运行：`python -m pytest tests/test_category_profile_service.py -q`

预期：全部 PASS，并确认旧版本仍可读取。

## 任务 3：迁移现有静态类目和模板数据

**文件：**

- 新建 `scripts/migrate_category_profiles.py`
- 修改 `services/api/storage.py`
- 新建 `tests/test_category_profile_migration.py`

- [ ] **步骤 1：写失败测试**

测试五个现有静态档案被转换成 `published` 的 `version=1`；多次执行不重复；模板能获得 `category_profile_version_id`；无法关联版本的旧副本标记 `legacy-v1/needs_manual`。

- [ ] **步骤 2：运行失败测试**

运行：`python -m pytest tests/test_category_profile_migration.py -q`

预期：FAIL，迁移脚本和新字段尚不存在。

- [ ] **步骤 3：实现迁移**

迁移脚本默认只读并输出报告；`--apply` 前创建 `store.json.backup.category-profile.<timestamp>`。使用稳定 ID/hash 去重；不删除 `category_attributes`，同时写入新 `attribute_values`。为现有静态字段创建低置信度来源标记，要求首次真实采集后复核。

- [ ] **步骤 4：执行 dry-run 验证**

运行：`python scripts/migrate_category_profiles.py --data-dir data --dry-run`

预期：输出 profile/template/copy 数量、缺失字段和将被标记 `needs_manual` 的条目，不修改 `data/store.json`。

- [ ] **步骤 5：运行迁移回归**

运行：`python -m pytest tests/test_category_profile_migration.py tests/test_product_line_flow.py -q`

预期：全部 PASS，旧产品线测试行为不回退。

## 任务 4：模板、刊登副本和自动化改用版本与快照

**文件：**

- 修改 `services/api/storage.py`
- 修改 `services/api/main.py`
- 修改 `tests/test_product_line_flow.py`
- 修改 `tests/test_self_run_listing.py`

- [ ] **步骤 1：写失败测试**

覆盖：模板只引用已发布版本；模板值覆盖规则默认值；创建刊登副本保存 `category_profile_snapshot` 和 hash；新版本发布后旧副本 hash 不变；自动化 readiness 报告类目字段错误而不是固定五字段缺失。

- [ ] **步骤 2：运行失败测试**

运行：`python -m pytest tests/test_product_line_flow.py tests/test_self_run_listing.py -q`

预期：FAIL，现有实现没有版本引用和快照校验。

- [ ] **步骤 3：最小实现**

在模板归一化时解析 `category_profile_version_id`，仅接受 `published` 版本；保留 `category_attributes` 读取兼容，新的写入字段为 `attribute_values`。在 `_build_store_listing_copy` 时调用 `build_profile_snapshot`，写入 `profile_snapshot_hash` 和 `validation_result`。

把 `_check_automation_readiness` 改为调用 `validate_attribute_values`，并合并通用字段、SKU、价格错误。删除 `_normalize_category_attributes` 对五个键的硬编码限制。

更新模板同步逻辑：模板修改只更新仍引用同一模板的草稿默认值，不改写已有快照中的类目规则和已排队任务。

- [ ] **步骤 4：通过产品线和安全执行测试**

运行：`python -m pytest tests/test_product_line_flow.py tests/test_self_run_listing.py -q`

预期：全部 PASS；旧快照不被新版本改写。

## 任务 5：实现真实马帮只读采集和差异 API

**文件：**

- 新建 `scripts/collect_mabang_category_profile.py`
- 修改 `scripts/browser_use_runner.py`
- 修改 `services/api/category_profile_service.py`
- 修改 `services/api/main.py`
- 新建 `tests/test_category_collection.py`
- 新建 `tests/fixtures/mabang/category_mens_tee.json`
- 新建 `tests/fixtures/mabang/category_second_profile.json`
- 新建 `tests/fixtures/mabang/category_diff.json`

- [ ] **步骤 1：写 fixture 回放测试**

测试原始页面 JSON 能规范化为稳定字段；字段 ID 冲突、缺少 label、无法解析条件和低置信度映射会产生 blocking diff；新增非必填字段为 non-breaking，新增必填字段为 requires-review。

- [ ] **步骤 2：运行失败测试**

运行：`python -m pytest tests/test_category_collection.py -q`

预期：FAIL，采集器和规范化流程尚不存在。

- [ ] **步骤 3：实现只读采集脚本**

复用 `browser_use_runner.py` 的 Chrome/Browser-Use 启动方式，加入明确的只读动作计划：打开工作区、选择店铺、选择类目、读取字段、保存原始 JSON、退出。禁止保存/发布按钮文本；超时、未登录、类目未确认时返回非零状态。

脚本接口：

```text
python scripts/collect_mabang_category_profile.py \
  --store-id <id> --category-id <id> --output <raw.json> \
  --mode audit
```

- [ ] **步骤 4：增加 API 路由**

在 `main.py` 增加：

```text
POST /api/category-profiles/collection-runs
GET  /api/category-profile-collection-runs/{id}
GET  /api/category-profiles/{id}/versions
POST /api/category-profile-versions/{id}/publish
POST /api/category-profile-versions/{id}/reject
POST /api/category-profile-versions/{id}/validate
```

路由层只负责 payload/状态码，状态转换交给领域服务。采集失败返回 `409` 或 `422`，不得返回可发布版本。

- [ ] **步骤 5：回放并通过测试**

运行：`python -m pytest tests/test_category_collection.py tests/test_api_boundaries.py -q`

预期：fixture 回放全部 PASS，未连接马帮时不要求实时网络。

## 任务 6：构建动态模板和规则审核界面

**文件：**

- 修改 `apps/web/index.html`
- 修改 `apps/web/app.js`
- 修改 `apps/web/styles.css`

- [ ] **步骤 1：添加浏览器行为测试或可复用渲染 fixture**

使用现有静态页面测试方式，覆盖选择 profile、显示版本、字段类型渲染、必填错误、条件字段出现、版本差异审核和升级预览。

- [ ] **步骤 2：实现动态字段渲染**

删除固定 `brand/pattern/material/sleeve_length/plus_size` 表单依赖；根据 `version.fields` 创建控件。切换类目时按 `field_id` 和类型兼容性保留值，把不兼容值放入确认列表。

- [ ] **步骤 3：实现审核视图**

展示 raw capture 状态、候选版本、差异类型、字段映射置信度、阻断原因。发布按钮只在没有 blocking diff 且填写审核备注时可用。候选版本不能直接绑定模板。

- [ ] **步骤 4：本地静态验收**

启动 API 后运行现有前端 smoke/Playwright 命令；检查两个 fixture 类目之间字段集合、必填标记和错误文案确实不同。

## 任务 7：让 Browser-Use schema 驱动并删除重复硬编码

**文件：**

- 修改 `services/api/storage.py`
- 修改 `scripts/browser_use_runner.py`
- 新建 `tests/test_browser_schema_payload.py`

- [ ] **步骤 1：写失败测试**

测试 payload 中 `category_attributes` 和 `variation` 各只出现一次；字段顺序稳定；字段映射来自 profile snapshot；新必填字段没有 mapping 时安全阻断；选项不匹配时不产生近似值。

- [ ] **步骤 2：实现 schema 驱动 payload**

`_build_browser_inputs` 只组装快照中的 `fields`、`attribute_values` 和 `automation`；移除默认 NoBrand/Cotton/Short Sleeves 等硬编码。`browser_use_runner.py` 按字段定义执行 label/field-id/semantic locator，并在填写后读取值验证。

- [ ] **步骤 3：增加 hash 和页面变化保护**

任务开始核对 `profile_snapshot_hash`；页面出现快照外的新必填字段、字段 ID 冲突或选项缺失时，返回 `blocked`，保存诊断信息和截图路径，不点击保存/发布。

- [ ] **步骤 4：运行自动化单测**

运行：`python -m pytest tests/test_browser_schema_payload.py tests/test_self_run_listing.py -q`

预期：全部 PASS，安全执行仍停在 `operator_review_before_save`。

## 任务 8：数据库参考 schema、文档和回归收口

**文件：**

- 修改 `database/schema.sql`
- 修改 `README.md`
- 修改 `docs/M1_ARCHITECTURE.md`
- 修改 `tests/test_api_boundaries.py`

- [ ] **步骤 1：补齐 API 和存储回归**

运行：`python -m pytest tests -q`

预期：在当前 Python 环境中通过；如存在既有失败，记录原始失败并与本次变更分离，不得静默忽略。

- [ ] **步骤 2：更新关系型 schema 参考**

为 `category_profiles`、`category_profile_versions`、`category_collection_runs` 增加 JSONB/索引定义，明确当前不自动执行该 SQL 迁移。

- [ ] **步骤 3：更新运维文档**

记录采集前提（登录态、Chrome CDP、只读模式）、审核发布流程、迁移命令、回滚方式、快照升级方式和安全停止条件。

- [ ] **步骤 4：回归检查**

运行：

```powershell
python -m pytest tests -q
python scripts/migrate_category_profiles.py --data-dir data --dry-run
python scripts/verify_marketplace_category_fields.py
```

预期：全量测试通过；迁移 dry-run 不改数据；类目字段验证脚本输出新版本规则而非固定五字段。

## 任务 9：真实双类目灰度验收

**前置条件：** 可用的马帮登录态、专用 Chrome CDP、Browser-Use CLI、测试店铺和两个真实类目。

- [ ] **步骤 1：采集成人男装 T 恤**

运行只读采集命令，保存 raw JSON、截图和日志；人工确认字段 ID、必填状态、选项和变体轴。

- [ ] **步骤 2：采集第二个差异类目**

优先选择童装或非服装类目；确认字段集合和条件规则与男装类目不同。

- [ ] **步骤 3：审核发布两个版本**

通过 API/界面查看 diff，发布后确认模板只能引用 published version，候选版本不能直接创建自动化任务。

- [ ] **步骤 4：生成刊登副本并做自动化安全填写**

每个类目生成一个刊登副本，检查动态字段、缺失错误、SKU、价格和快照 hash；运行 audit/fill 模式，确认停在人工复核且没有保存/发布。

- [ ] **步骤 5：验证版本隔离**

为其中一个类目采集并发布新版本；确认旧刊登副本、旧任务 payload、旧 hash 和旧日志不变，新副本才使用新版本。

## 验收清单

- [ ] 不同类目返回不同的必填字段集合。
- [ ] 条件必填只使用白名单操作符，无法解析时阻断发布。
- [ ] 规则来源可追溯到采集 run、页面 URL、时间和原始 JSON。
- [ ] 候选版本必须人工审核后才能发布。
- [ ] 模板只保存覆盖值，刊登副本和任务保存规则快照。
- [ ] 自动化消费快照，不含固定类目属性，不点击保存/发布。
- [ ] 规则升级不改变旧任务。
- [ ] JSON 和 Postgres JSONB 后端都保留新集合。
- [ ] 全量 pytest、迁移 dry-run、类目字段验证和双类目实地验收均有输出证据。

## 执行方式

推荐在当前会话内按任务顺序执行，每完成一个任务运行其命令并停下来检查结果。由于仓库没有有效 Git 元数据，不能创建 commit 检查点；改用每任务的测试输出、迁移报告、原始采集 JSON、差异报告和截图作为检查点。
