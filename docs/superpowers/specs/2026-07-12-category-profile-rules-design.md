# 模板分类与必填项规则重构设计

## 1. 背景

当前系统已经区分 `Category profile`、`Store template` 和 `Store listing copy`，但类目规则仍由 `services/api/storage.py` 中的静态常量提供。现有类目档案共用同一组服装属性，属性标准化函数也只接受五个固定字段。因此系统无法准确表达不同平台、站点和类目的字段差异，也无法可靠判断一条刊登数据是否可以进入自动化。

本设计把马帮真实商品编辑页作为类目规则的采集来源。采集结果先成为候选版本，经人工审核后发布。模板引用已发布版本，刊登副本保存不可变快照，自动化只消费校验通过的快照。

## 2. 目标与非目标

### 目标

- 不同平台、站点、类目拥有各自的字段定义、必填规则、选项和变体要求。
- 马帮真实页面变化可以被采集、比较和审核，而不是靠开发人员修改硬编码。
- 模板编辑、刊登副本就绪检查和自动化预检使用同一套校验逻辑。
- 已排队自动化任务不受后续模板或类目规则更新影响。
- 首批在两个字段差异明显的真实类目上完成端到端验证。

### 非目标

- 第一阶段不自动发布候选规则，也不让采集器直接覆盖生产规则。
- 第一阶段不迁移到完整关系型类目表；继续使用现有 JSON/Postgres JSONB 兼容存储。
- 第一阶段不实现通用脚本语言。条件规则只支持经过白名单约束的操作符。
- 不允许自动化点击最终保存或发布按钮，仍停在人工复核关卡。

## 3. 领域边界

### Category profile

表示一个稳定的业务类目身份，由 `platform + country + marketplace_category_id` 唯一确定。它只记录当前发布版本和基本标识，不直接承载可变字段规则。

### Category profile version

表示一次可审核、可发布的类目规则快照。状态只能按以下方向变化：

```text
candidate -> published -> superseded
          -> rejected
```

任何时候一个 Category profile 最多只有一个 `published` 版本。发布新版本时，旧版本变为 `superseded`，但不会被删除。

### Store template

引用一个已发布的 `category_profile_version_id`，保存店铺、价格、折扣、标题、描述、尺码和类目字段默认值。它不复制字段定义，只保存 `attribute_values`。模板切换类目版本时必须重新校验。

### Store listing copy

创建时冻结类目规则和值：保存 `category_profile_snapshot`、`attribute_values`、`validation_result`。之后模板或类目版本变化不会自动改写已有副本。

### Automation job

创建时复制刊登副本的发布输入摘要和快照哈希。执行前再次校验哈希与快照内容；发现未知字段、缺失映射或快照被修改时阻断任务。

## 4. 数据结构

现有兼容存储新增以下集合：

- `category_profiles`
- `category_profile_versions`
- `category_collection_runs`

`services/api/db_store.py` 的 `COLLECTIONS` 必须同步加入这些集合，并补齐当前已在 JSON 存储中使用但未被 Postgres 兼容层声明的产品线集合，避免切换后端时静默丢数据。

### Category profile

```json
{
  "id": "shopee-ph-100244",
  "platform": "shopee",
  "country": "PH",
  "marketplace_category_id": "100244",
  "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
  "display_name": "Shopee PH 男装 T 恤",
  "published_version_id": "cpv-...",
  "status": "active",
  "created_at": "...",
  "updated_at": "..."
}
```

### Category profile version

```json
{
  "id": "cpv-...",
  "profile_id": "shopee-ph-100244",
  "version": 3,
  "status": "candidate",
  "source": {
    "type": "mabang_browser_collection",
    "run_id": "cpr-...",
    "page_url": "https://www.mabangerp.com/...",
    "collected_at": "..."
  },
  "fields": [
    {
      "field_id": "brand",
      "label": "品牌",
      "type": "single_select",
      "required": true,
      "required_when": null,
      "options": [{"value": "NoBrand", "label": "No Brand"}],
      "default_value": "NoBrand",
      "automation": {
        "strategy": "label_or_field_id",
        "page_field_id": "brand"
      }
    }
  ],
  "variation": {
    "required": true,
    "axes": ["color", "size"],
    "allowed_axes": ["color", "size"]
  },
  "schema_hash": "sha256:...",
  "review": {
    "reviewed_by": "",
    "reviewed_at": "",
    "note": ""
  },
  "created_at": "...",
  "published_at": ""
}
```

字段类型第一阶段限定为：`text`、`number`、`boolean`、`single_select`、`multi_select`。每个字段必须有稳定 `field_id`；无法取得平台字段 ID 时，使用采集器生成的稳定 ID，并在候选版本中标记 `identity_confidence: low`，禁止直接发布。

### 条件必填规则

`required_when` 采用结构化表达式，不执行任意代码：

```json
{
  "all": [
    {"field": "age_group", "op": "in", "value": ["baby", "toddler"]},
    {"field": "is_customized", "op": "eq", "value": false}
  ]
}
```

允许操作符仅为 `eq`、`neq`、`in`、`not_in`、`present`。表达式支持一个顶层 `all` 或 `any`，不支持无限嵌套。采集器无法可靠表达的页面条件必须标记为 `unsupported_condition`，候选版本不能发布。

## 5. 真实页面采集与审核

### 采集流程

1. 操作员在类目规则页面选择店铺和马帮类目。
2. API 创建 `category_collection_run`，状态为 `queued`。
3. 采集脚本复用当前 Chrome 登录态，进入商品编辑草稿页并选择指定类目。
4. 脚本只读取页面，不保存商品；采集字段标签、控件类型、必填标志、选项、条件显示信息、页面字段 ID 和变体区域。
5. 原始结果作为 `raw_capture` 保存，规范化器生成候选版本和相对当前发布版本的差异。
6. 操作员检查新增、删除、必填变化、选项变化和映射置信度。
7. 只有不存在阻断项时，操作员才能发布候选版本。

### 采集安全边界

- 采集脚本禁止点击包含“保存”“发布”“提交”含义的按钮。
- 页面未登录、类目未成功选中、字段身份冲突或解析不完整时，采集运行标记为 `failed`，不创建可发布候选版本。
- 原始采集结果永不覆盖；重试创建新的 run。
- DOM 选择器只是采集实现细节，自动化映射优先使用页面字段 ID、关联 label 和控件语义。

### 差异分类

- `non_breaking`：新增非必填字段、增加选项、文案变化。
- `requires_review`：新增必填字段、字段变为必填、选项删除、字段类型变化、变体规则变化。
- `blocking`：字段身份冲突、不支持的条件表达式、必填字段缺少自动化映射。

## 6. 统一校验

新增独立的 `category_rules.py`，提供纯函数接口：

```python
validate_attribute_values(version, values) -> ValidationResult
resolve_required_fields(version, values) -> list[str]
build_profile_snapshot(profile, version) -> dict
hash_profile_snapshot(snapshot) -> str
```

`ValidationResult` 固定包含：

```json
{
  "valid": false,
  "errors": [
    {
      "code": "required",
      "field_id": "material",
      "label": "材质",
      "message": "材质为当前类目的必填项"
    }
  ],
  "warnings": [],
  "required_field_ids": ["brand", "material"]
}
```

模板保存允许存在未填字段，但状态为 `needs_manual`。创建刊登副本可以保存草稿，但自动化任务必须在通用刊登字段、类目字段、变体、SKU 和价格全部通过校验后才能进入 `ready`。

## 7. API 与界面

### API

- `GET /api/category-profiles`：返回类目身份及当前发布版本摘要。
- `GET /api/category-profiles/{id}/versions`：返回版本历史。
- `POST /api/category-profiles/collection-runs`：创建真实页面采集任务。
- `GET /api/category-profile-collection-runs/{id}`：查询采集状态、原始结果和差异。
- `POST /api/category-profile-versions/{id}/publish`：执行阻断检查并原子发布。
- `POST /api/category-profile-versions/{id}/reject`：拒绝候选版本并记录原因。
- `POST /api/category-profile-versions/{id}/validate`：供界面即时校验字段值。

发布接口必须校验候选状态、schema hash、阻断差异和审核信息。重复发布使用幂等语义。

### 界面

产品维护页新增“类目规则”工作区，显示类目、当前版本、最近采集时间和变更状态。候选版本使用字段差异表进行审核。

店铺模板表单根据当前发布版本动态生成控件：下拉选项使用 swatch/label，布尔值使用开关，数字使用输入框。必填标记和校验错误显示在字段附近。切换类目时，同 `field_id` 且类型兼容的值可以保留；其他值进入待确认清单，用户确认后才丢弃。

界面不允许直接编辑字段规则。规则只能来自采集候选版本并经过审核发布，避免运营值和平台规则混在一起。

## 8. 快照与升级策略

- 店铺模板保存 `category_profile_version_id` 和 `attribute_values`。
- 刊登副本保存完整 `category_profile_snapshot`、`profile_snapshot_hash` 和 `attribute_values`。
- 自动化任务保存同一 hash，并在执行前核对。
- 发布新类目版本后，现有模板显示“可升级”，但不自动升级。
- 用户执行模板升级时先展示字段差异和数据迁移预览，确认后更新模板。
- 现有刊登副本永不自动升级；需要使用新规则时，显式执行“从模板重新生成”或“升级副本”，并创建新的快照。

## 9. 迁移

迁移脚本从现有 `CATEGORY_PROFILES` 生成初始 `published` 版本，并从现有 `store_templates.category_attributes` 生成 `attribute_values`。迁移必须：

- 可重复运行，使用稳定 profile ID 和 schema hash 去重。
- 先生成备份，再执行转换。
- 不删除旧字段；过渡期保留读取兼容，写入只使用新结构。
- 输出每个模板和刊登副本的迁移状态及缺失字段。
- 支持 `--dry-run`，实际写入需要显式 `--apply`。

旧刊登副本无法确认规则来源时，使用迁移生成的 `legacy-v1` 快照并标记 `needs_manual`，不得直接进入自动化。

## 10. 自动化落地

`browser_use_runner.py` 不再认识品牌、材质等具体字段。它按快照中的 `fields` 和 `automation` 映射逐项填写，并在每项后读取页面值进行核对。

自动化执行规则：

- 未发布版本、快照 hash 不一致、必填字段缺失、必填字段无映射时立即阻断。
- 页面出现快照中不存在的新必填字段时，停止填写，保存页面证据并建议重新采集该类目。
- 页面选项不包含快照值时，停止在该字段，不使用模糊近似值。
- 全部字段填写完成后仍停在人工复核，不保存、不发布。

首批实地验证选择“成人男装 T 恤”和一个字段集合明显不同的类目。第二个类目由采集结果确定，优先选择童装或非服装类目，以验证条件必填和不同字段集合，而不是只验证两条相似服装路径。

## 11. 测试与验收

### 自动测试

- 两个类目拥有不同字段集合，验证器分别报告正确的缺失项。
- `required_when` 的 `all/any` 和五种操作符行为确定。
- 不支持条件、低置信度字段 ID、必填字段缺少自动化映射会阻止发布。
- 发布新版本不会改变旧刊登副本和旧自动化任务的 hash。
- 模板升级只保留字段 ID 和类型均兼容的值。
- JSON 与 Postgres JSONB 兼容存储包含全部新集合。
- 采集解析使用保存的真实页面 fixture 回放，不依赖实时页面运行单元测试。
- 自动化遇到新必填字段或不存在的选项时安全停止。

### 实地验收

1. 在真实马帮页面采集成人男装 T 恤，人工发布候选版本。
2. 采集第二个差异类目，确认字段差异可见且各自校验正确。
3. 分别创建模板和刊登副本，验证动态表单、缺失提示和快照。
4. 对两个类目各运行一次安全填写，核对属性、变体和 SKU，停在人工复核。
5. 修改并发布其中一个类目的新候选版本，确认旧任务 payload 和 hash 不变。

验收证据包括采集原始 JSON、版本差异、API 校验结果、自动化日志和最终人工复核截图。

## 12. 实施顺序

1. 先建立规则模型、纯校验器和存储兼容测试。
2. 再迁移现有静态档案，切换模板与刊登副本到版本引用和快照。
3. 然后实现采集、规范化、差异与审核发布 API。
4. 接着实现动态表单和规则审核界面。
5. 最后改造自动化为 schema 驱动，并执行两个真实类目的实地验收。

每一步都保留旧路径回退；只有新路径通过回归测试和实地验收后，才删除固定五字段逻辑及浏览器 payload 中的重复硬编码。
