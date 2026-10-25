# 马帮当前页采集快照重构实施计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:executing-plans` 逐任务实施此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。当前 worktree 已包含未提交改动；禁止 reset、checkout 或覆盖这些改动，每次只暂存本任务明确列出的文件。

**目标：** 将马帮当前页导入改为“只读采集快照、操作员预览、确认后原子写入”，并修复当前 POD菲律宾 / 100244 的污染数据。

**架构：** Browser-Use 授权会话在当前马帮编辑 frame 内执行共享采集脚本，只输出页面原始事实；Python 快照模块负责规范化、覆盖检查和业务映射。`/collect` 只持久化快照，`/confirm` 才在单个存储事务内更新店铺目录、类目版本、活动目录和店铺模板。前端导入弹窗负责预览，不再把采集成功等同于模板已保存。

**技术栈：** Python 3.14、标准库 HTTP API、JSON/PostgreSQL 存储抽象、Browser-Use 授权会话、共享页面采集 JavaScript、Node.js + Playwright CDP 诊断入口、原生 HTML/CSS/JavaScript、pytest。

---

## 文件结构

**创建：**

- `services/api/mabang_import_snapshot.py`：快照契约、规范化、覆盖检查、交易信息一致性和模板映射。
- `services/api/mabang_page_import_repair.py`：定向、幂等的污染数据修复函数。
- `scripts/repair_mabang_page_import_pollution.py`：修复脚本的 dry-run/apply 命令入口。
- `scripts/mabang_page_import_capture.js`：Browser-Use 和显式 CDP 诊断入口共用的只读页面事实采集器。
- `tests/fixtures/mabang_page_capture_pod_ph.json`：脱敏后的真实页面采集 fixture。
- `tests/test_mabang_import_snapshot.py`：快照规范化单元测试。
- `tests/test_mabang_page_import_repair.py`：污染修复范围和幂等性测试。

**修改：**

- `scripts/mabang_page_import_executor.cjs`：作为显式 CDP 诊断入口复用共享采集器，不再维护第二套页面解析逻辑。
- `scripts/browser_use_runner.py`：通过 Browser-Use 授权会话执行共享采集器并返回结构化错误，不做模板映射，也不依赖固定远程调试端口。
- `services/api/mabang_page_import.py`：拆分 stage/confirm，确认阶段原子写入业务集合。
- `services/api/storage.py`：持久化快照、确认导入、幂等确认和店铺目录 upsert。
- `services/api/main.py`：增加 `/confirm`，让 `/collect` 和 `/complete` 只进入 staging。
- `apps/web/index.html`：把导入弹窗改为采集和预览确认两阶段。
- `apps/web/app.js`：渲染快照、确认导入、刷新店铺/模板/类目/活动并打开新模板。
- `apps/web/styles.css`：增加紧凑预览分组和状态样式。
- `tests/test_mabang_page_import_api.py`：两阶段 API、原子性和幂等性集成测试。
- `tests/test_mabang_page_import.py`：stage/confirm 纯函数、失败不修改输入数据和活动不回退测试。
- `tests/test_browser_use_runner.py`：执行器命令、结构化错误和只读安全测试。
- `tests/test_store_template_workflow_ui.py`：预览、确认按钮、回填和文案测试。
- `tests/test_store_template_category_binding.py`：导入后店铺选项目录测试。
- `tests/test_template_readiness.py`：导入后缺失属性、交易信息和活动的模板阻断测试。
- `tests/test_product_line_flow.py`：不完整导入模板生成的刊登副本不能创建 ready 自动化任务。
- `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`：更新操作流程和故障提示。
- `CONTEXT.md`：更新 Mabang page import 术语，明确 collect 不写模板。

## 任务 1：建立快照契约和覆盖规则

**文件：**

- 创建：`services/api/mabang_import_snapshot.py`
- 创建：`tests/fixtures/mabang_page_capture_pod_ph.json`
- 创建：`tests/test_mabang_import_snapshot.py`

- [ ] **步骤 1：添加脱敏 fixture**

fixture 固定包含：店铺 `2022625115 / POD菲律宾`、类目 `100244`、四个已选属性、一个未选必填属性、`black x S/M/L/XL/2XL` 五个 SKU、库存 `999`、空重量和尺寸、无真实活动 ID。不得包含 token、cKey、cookie 或完整敏感 URL。

- [ ] **步骤 2：编写失败的快照规范化测试**

```python
def test_snapshot_keeps_only_selected_attributes_and_reports_missing_required():
    snapshot = normalize_mabang_capture(_fixture())
    assert {
        field_id: field["value"]
        for field_id, field in snapshot["selected_attributes"].items()
    } == {
        "100134": ["1149"],
        "100162": ["1486"],
        "100168": "1500",
        "101004": "5487",
    }
    assert snapshot["missing_required_attributes"] == [
        {"field_id": "required-empty", "label": "未选择必填项"}
    ]
    assert "尺码图表:上传图片选择模板" not in snapshot["selected_attributes"]
    assert "变种属性:" not in snapshot["selected_attributes"]


def test_snapshot_derives_sizes_and_preserves_empty_dimensions():
    snapshot = normalize_mabang_capture(_fixture())
    assert snapshot["variation"]["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert snapshot["transaction_defaults"] == {
        "stock": {"status": "complete", "value": 999},
        "weight_kg": {"status": "missing", "value": None},
        "package_length_cm": {"status": "missing", "value": None},
        "package_width_cm": {"status": "missing", "value": None},
        "package_height_cm": {"status": "missing", "value": None},
    }


def test_snapshot_never_fabricates_a_promotion():
    snapshot = normalize_mabang_capture(_fixture())
    assert snapshot["promotion_catalog"] == []
    assert snapshot["selected_promotion_id"] == ""
    assert snapshot["coverage"]["promotions"]["status"] == "missing"
```

- [ ] **步骤 3：运行测试确认红灯**

运行：

```powershell
python -m pytest tests/test_mabang_import_snapshot.py -q
```

预期：FAIL，`mabang_import_snapshot` 尚不存在。

- [ ] **步骤 4：实现快照规范化 API**

实现 `MabangImportSnapshotError`、`normalize_mabang_capture(raw)`、`collapse_uniform_sku_value(rows, key)` 和 `snapshot_template_payload(snapshot)` 四个公开接口。`collapse_uniform_sku_value` 只返回 `{"status": "complete|missing|conflict", "value": value_or_none}`；冲突时 `value` 必须为 `None`，不得取第一行。

`normalize_mabang_capture()` 的固定输出结构为：

```json
{
  "schema_version": 1,
  "store": {"id": "2022625115", "name": "POD菲律宾", "platform": "shopee", "country": "PH"},
  "category": {"id": "100244", "path": "男装(Men Clothes)>上衣(Tops)>T恤(T-Shirts)", "id_path": ["100011", "100054", "100244"]},
  "category_fields": [
    {"field_id": "100134", "label": "材料", "type": "multi_select", "required": true, "options": [{"value": "1149", "label": "Cotton"}], "selected_value": ["1149"]},
    {"field_id": "100162", "label": "图案", "type": "multi_select", "required": true, "options": [{"value": "1486", "label": "Print"}], "selected_value": ["1486"]},
    {"field_id": "100168", "label": "袖长", "type": "single_select", "required": true, "options": [{"value": "1500", "label": "Short Sleeves"}], "selected_value": "1500"},
    {"field_id": "101004", "label": "加大码", "type": "single_select", "required": true, "options": [{"value": "5487", "label": "Yes"}], "selected_value": "5487"},
    {"field_id": "required-empty", "label": "未选择必填项", "type": "single_select", "required": true, "options": [], "selected_value": ""}
  ],
  "selected_attributes": {
    "100134": {"field_id": "100134", "label": "材料", "type": "multi_select", "required": true, "value": ["1149"], "value_labels": ["Cotton"]},
    "100162": {"field_id": "100162", "label": "图案", "type": "multi_select", "required": true, "value": ["1486"], "value_labels": ["Print"]},
    "100168": {"field_id": "100168", "label": "袖长", "type": "single_select", "required": true, "value": "1500", "value_labels": ["Short Sleeves"]},
    "101004": {"field_id": "101004", "label": "加大码", "type": "single_select", "required": true, "value": "5487", "value_labels": ["Yes"]}
  },
  "missing_required_attributes": [{"field_id": "required-empty", "label": "未选择必填项"}],
  "variation": {
    "colors": ["black"],
    "sizes": ["S", "M", "L", "XL", "2XL"],
    "sku_count": 5,
    "axes": [{"name": "color", "values": ["black"]}, {"name": "size", "values": ["S", "M", "L", "XL", "2XL"]}]
  },
  "transaction_defaults": {
    "stock": {"status": "complete", "value": 999},
    "weight_kg": {"status": "missing", "value": null},
    "package_length_cm": {"status": "missing", "value": null},
    "package_width_cm": {"status": "missing", "value": null},
    "package_height_cm": {"status": "missing", "value": null}
  },
  "promotion_catalog": [],
  "selected_promotion_id": "",
  "coverage": {
    "complete": false,
    "store_category": {"status": "complete"},
    "attributes": {"status": "missing"},
    "variation": {"status": "complete"},
    "transaction": {"status": "missing"},
    "promotions": {"status": "missing"}
  },
  "provenance": {
    "store_category": "vue:$refs.storeCategory",
    "category_attributes": "vue:ProductAttributeInfo.attributesNew",
    "sku_rows": "vue:vxe-table.tableData",
    "promotions": "vue:transaction-information"
  }
}
```

`category_fields` 保留 Vue 返回的全部真实平台属性元数据；`selected_attributes` 以 `field_id` 为键，每项只包含 `field_id/label/type/required/value/value_labels`，且只保留非空值。交易字段优先折叠全部 SKU 行；所有 SKU 都为空时才读取 `transaction_information` 的单一页面值。`snapshot_template_payload()` 只从 `selected_attributes` 生成模板 `attribute_values`；从 `transaction_defaults` 仅提升 `status=complete` 的统一值，missing/conflict 均映射为空字符串。无真实活动 ID 时活动为空并标记未配置。

- [ ] **步骤 5：运行单元测试确认绿灯**

运行：`python -m pytest tests/test_mabang_import_snapshot.py -q`

预期：PASS。

- [ ] **步骤 6：提交任务 1**

```powershell
git add services/api/mabang_import_snapshot.py tests/fixtures/mabang_page_capture_pod_ph.json tests/test_mabang_import_snapshot.py
git commit -m "feat: define Mabang import snapshot contract"
```

## 任务 2：让 Browser-Use 会话只采集原始事实

**文件：**

- 修改：`scripts/mabang_page_import_executor.cjs`
- 修改：`scripts/browser_use_runner.py`
- 创建：`scripts/mabang_page_import_capture.js`
- 修改：`tests/test_browser_use_runner.py`
- 测试：`tests/fixtures/mabang_page_capture_pod_ph.json`

- [ ] **步骤 1：编写执行器契约失败测试**

新增测试断言：执行器结果包含 `store_category_state`、`category_attributes`、`sku_rows`、`transaction_information`、`promotion_options` 和 `provenance`；不包含 `attribute_values`、`sizes` 或模板字段；源码不再使用全页 `.el-form-item`/`.ant-form-item` 扫描生成分类属性，也不再用第一行 SKU 代表所有行。

```python
def test_mabang_executor_contract_has_raw_sources_only():
    source = browser_use_runner.MABANG_PAGE_IMPORT_CAPTURE_PATH.read_text(encoding="utf-8")
    assert "store_category_state" in source
    assert "category_attributes" in source
    assert "sku_rows" in source
    assert "transaction_information" in source
    assert "promotion_options" in source
    assert "document.querySelectorAll('.el-form-item,.ant-form-item,.form-item,tr')" not in source
    assert "const firstSku = skuRows[0]" not in source
```

- [ ] **步骤 2：运行测试确认红灯**

运行：`python -m pytest tests/test_browser_use_runner.py -k mabang_executor_contract -q`

预期：FAIL，执行器仍混合业务映射。

- [ ] **步骤 3：重构执行器数据读取边界**

共享页面采集脚本只读取：

```javascript
return {
  source: 'mabang_current_page',
  page: { url: location.origin + location.pathname, platform, country },
  store_category_state: {
    store: { id: '2022625115', name: 'POD菲律宾' },
    category: { id: '100244', path: categoryPath, id_path: categoryIdPath, is_leaf: true },
  },
  category_attributes: readCategoryAttributes(detailVm), // 每项含 field_id/label/type/required/options/selected_value
  sku_rows: readSkuRows(detailFrame), // 每行分别保留 color/size/stock/weight_kg/包裹长宽高
  transaction_information: readTransactionInformation(detailVm), // 从 transactionInformationModuleRef 读取页面统一值
  promotion_options: readPromotionOptions(detailVm), // 只保留有真实 activity_id 和 name 的项，并标记 selected
  provenance: {
    store_category: 'vue:$refs.storeCategory',
    sku_rows: 'vue:vxe-table.tableData',
    transaction_information: 'vue:$refs.transactionInformationModuleRef',
    promotions: 'vue:$refs.transactionInformationModuleRef',
  },
};
```

`readCategoryAttributes()` 必须遍历 `$refs.storeCategory.ProductAttributeInfo.attributesNew` 的全部真实属性：未选择必填项仍保留元数据并让 `selected_value` 为空，但不得从通用 DOM 构造新 field ID。`readSkuRows()` 必须逐行读取 `.vxe-table` 的 `tableData`。`readTransactionInformation()` 和 `readPromotionOptions()` 只能读取 `$refs.transactionInformationModuleRef` 的现有模型/选项；DOM 只可回退读取可见名称，不可生成内部 ID。生产 `/collect` 必须在 Browser-Use 已授权会话中定位详情 frame 并执行共享采集脚本，不再探测或依赖单独的硬编码 CDP 端口。CJS 入口仅保留为显式 CDP 诊断工具，且复用同一采集脚本。所有入口都必须无点击、无导航、无保存、无发布，执行结束不得关闭用户 Chrome。

- [ ] **步骤 4：收紧 Python runner 结果解析**

`run_mabang_page_import_audit()` 通过 Browser-Use 标准输入执行采集脚本，只校验结构化 marker、执行状态和原始 capture 类型。采集失败时返回精简中文错误，不暴露 Browser-Use 内部堆栈。

- [ ] **步骤 5：运行执行器测试**

运行：

```powershell
python -m pytest tests/test_browser_use_runner.py -q
```

预期：PASS。

- [ ] **步骤 6：提交任务 2**

```powershell
git add scripts/mabang_page_import_executor.cjs scripts/browser_use_runner.py tests/test_browser_use_runner.py
git commit -m "refactor: collect raw Mabang page facts"
```

## 任务 3：将导入任务改为只保存快照

**文件：**

- 修改：`services/api/storage.py:789-835`
- 修改：`services/api/main.py:165-172,471-512`
- 修改：`services/api/mabang_page_import.py`
- 修改：`tests/test_mabang_page_import_api.py`
- 修改：`tests/test_mabang_page_import.py`

- [ ] **步骤 1：编写 `/collect` 不写业务数据的失败测试**

```python
def test_collect_stages_snapshot_without_writing_template(monkeypatch):
    monkeypatch.setattr(
        "services.api.main.run_mabang_page_import_audit",
        lambda plan: {"ok": True, "capture": _raw_capture_fixture()},
    )
    created = post("/api/mabang-page-imports", _plan())
    collected = post(f"/api/mabang-page-imports/{created['id']}/collect", {})
    assert collected["status"] == "collected_with_warnings"
    assert collected["snapshot"]["store"]["id"] == "2022625115"
    assert get("/api/store-templates")["items"] == []
    assert get("/api/mabang/store-options?platform=shopee")["items"] == []
```

同时添加测试：`/complete` 接收外部 raw capture 时也只 stage；已采集 run 可重新采集并替换快照但业务集合仍不变；首次采集失败保持 `pending` 并记录错误；重新采集失败保留上一份有效快照；快照中不得持久化敏感 URL 参数。将 `tests/test_mabang_page_import.py` 的旧 `import_current_page()` 测试迁移为 stage/confirm 单元测试，不保留直接采集即写模板的断言。

- [ ] **步骤 2：运行 API 测试确认红灯**

运行：`python -m pytest tests/test_mabang_page_import_api.py -q`

预期：FAIL，当前 `/collect` 直接写模板。

- [ ] **步骤 3：实现 staging 存储函数**

在 `services/api/mabang_page_import.py` 提供 `stage_page_import(data, run_id, raw_capture, *, now)`：先在 `deepcopy(data)` 上查找 run、规范化 raw capture、更新 run，成功后再一次替换原 data 并返回 run 的副本。允许状态为 `pending`、`collected`、`collected_with_warnings`；`completed` 禁止重新采集。状态更新规则固定为：

```python
run.update({
    "status": "collected" if snapshot["coverage"]["complete"] else "collected_with_warnings",
    "snapshot": snapshot,
    "last_error": "",
    "collected_at": now,
    "updated_at": now,
})
```

在 `storage.py` 将 `complete_mabang_page_import` 改名/包装为 `stage_mabang_page_import`，只更新目标 run。保留兼容函数时必须调用 stage，不得调用确认写入。采集器执行失败由 storage/API 的 `record_mabang_page_import_error()` 只更新 `last_error/updated_at`；已有有效 snapshot 和状态不得被清空。

- [ ] **步骤 4：更新 API 路由**

`/collect`：运行 CDP、stage、返回 run。`/complete`：stage 提交的 raw capture。GET 返回完整 run 与 snapshot。两条路由均允许重新采集未确认的 run。任何路径都不得在此阶段调用 `create_candidate_from_capture`、`replace_promotion_catalog` 或 `_upsert_template`。

- [ ] **步骤 5：运行 API 测试确认绿灯**

运行：`python -m pytest tests/test_mabang_page_import_api.py -q`

预期：PASS。

- [ ] **步骤 6：提交任务 3**

```powershell
git add services/api/mabang_page_import.py services/api/storage.py services/api/main.py tests/test_mabang_page_import_api.py tests/test_mabang_page_import.py
git commit -m "feat: stage Mabang import snapshots"
```

## 任务 4：实现原子、幂等的确认写入

**文件：**

- 修改：`services/api/mabang_page_import.py`
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 修改：`tests/test_mabang_page_import_api.py`
- 修改：`tests/test_mabang_page_import.py`
- 修改：`tests/test_store_template_category_binding.py`

- [ ] **步骤 1：编写确认写入失败测试**

测试必须证明：确认前店铺选项、类目版本、活动目录、模板和模板版本五个业务集合不变；确认后五者同时出现；确认中途失败时五者都不出现；同一个 run 重复确认返回同一个 template，不新增版本；无真实活动 ID 时保持空值，不自动选择目录中的第一项。

```python
def test_confirm_import_writes_store_sizes_selected_values_atomically():
    confirmed = post(f"/api/mabang-page-imports/{run_id}/confirm", {})
    template = confirmed["template"]
    assert template["mabang_store_id"] == "2022625115"
    assert template["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert template["attribute_values"] == {
        "100134": ["1149"],
        "100162": ["1486"],
        "100168": "1500",
        "101004": "5487",
    }
    assert template["weight_kg"] == ""
    assert template["promotion_activity_id"] == ""
    option = get("/api/mabang/store-options?platform=shopee")["items"][0]
    assert {key: option[key] for key in ("mabang_store_id", "store_name", "platform", "country")} == {
        "mabang_store_id": "2022625115",
        "store_name": "POD菲律宾",
        "platform": "shopee",
        "country": "PH",
    }
```

- [ ] **步骤 2：运行测试确认红灯**

运行：

```powershell
python -m pytest tests/test_mabang_page_import_api.py tests/test_store_template_category_binding.py -q
```

预期：FAIL，`/confirm` 尚不存在。

- [ ] **步骤 3：实现确认纯函数**

将现有 `import_current_page` 拆为 `confirm_page_import(data, run_id, *, now)`。它只读取 run 中已经规范化的 snapshot，并在 `deepcopy(data)` 上完成全部修改；所有步骤成功后才 `data.clear(); data.update(staged)`，返回 `run/category_profile/category_version/promotions/template` 的副本。

确认顺序：验证 run 状态仅为 `collected` 或 `collected_with_warnings`；按 `store.id` upsert `mabang_store_options`；用 `category_fields` 创建干净类目版本；用 `promotion_catalog` 替换当前店铺活动目录；调用 `snapshot_template_payload()`；按冲突策略 upsert 模板；追加模板版本；最后写入 run 的 `status=completed/confirmed_at/template_id/category_version_id`。模板必须写入 `sizes`、`variation`、`missing_required_attributes` 和 `import_coverage`。禁止从 `raw_capture` 再次推导业务值。

- [ ] **步骤 4：实现幂等和冲突处理**

`run.status == completed` 时按 run 已记录的 ID 返回同一确认结果。`conflict_action=update` 只更新相同店铺+类目模板；`save_as_new` 新建模板。`selected_promotion_id` 只有在它存在于当前 snapshot 的 `promotion_catalog` 时才写入；为空时保留“活动未配置”，不得选择第一项、不得按折扣率匹配，也不得生成 ID。

- [ ] **步骤 5：增加 `/confirm` 路由**

`POST /api/mabang-page-imports/{id}/confirm` 调用 `storage.confirm_mabang_page_import()`，该函数在一次 `locked_store()` 临界区内执行确认和一次 save。pending run 返回 409；不存在返回 404；无效 snapshot 返回 422；快照警告允许确认，但模板标记不完整，自动化 readiness 阻止任务创建。

- [ ] **步骤 6：运行测试确认绿灯**

运行任务 4 的两个测试文件，预期 PASS。

- [ ] **步骤 7：提交任务 4**

```powershell
git add services/api/mabang_page_import.py services/api/storage.py services/api/main.py tests/test_mabang_page_import_api.py tests/test_mabang_page_import.py tests/test_store_template_category_binding.py
git commit -m "feat: confirm Mabang imports atomically"
```

## 任务 5：实现采集预览与正确回填

**文件：**

- 修改：`apps/web/index.html:132-141`
- 修改：`apps/web/app.js:89-236,693-745,1343-1420`
- 修改：`apps/web/styles.css`
- 修改：`tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：编写 UI 结构失败测试**

断言导入弹窗存在：`collect-mabang-page-import-btn`（文案“采集当前页”）、`mabang-import-preview`、五个预览分组、`confirm-mabang-page-import-btn`（初始禁用）、缺失/冲突状态容器。断言 JS 确认成功后调用 `loadMabangStoreOptions()`、`loadStoreTemplates()`、`loadCategoryProfiles()`、`loadStorePromotions()`、`loadReadiness()` 和 `openStoreTemplateEditor()`。

- [ ] **步骤 2：运行 UI 测试确认红灯**

运行：`python -m pytest tests/test_store_template_workflow_ui.py -q`

预期：FAIL，当前按钮仍是“采集并保存到模板”。

- [ ] **步骤 3：重构弹窗标记**

在 `index.html` 使用不嵌套卡片的紧凑分组：店铺类目、已选属性、颜色尺码、交易信息、促销活动。每组预留状态图标/文本和内容区域。底部仅有取消、重新采集、确认导入三个命令。

- [ ] **步骤 4：实现预览渲染**

新增 `renderMabangImportPreview(run)` 与 `confirmMabangPageImport()`。前者先清空五个固定分组，再用 `textContent`/现有 `escapeHtml()` 渲染 snapshot，最后按 `coverage` 设置状态和确认按钮；不得把页面采集文本直接拼入未转义 `innerHTML`。后者只 POST 当前 run 的 `/confirm`，在请求期间禁用重新采集和确认，失败时保留预览供重试。预览只显示 `selected_attributes`，缺失必填项放在警告区，不渲染为空分类字段。交易信息按完整/缺失/冲突显示。活动没有真实 ID 时固定显示“活动未配置”。确认按钮只在 run 尚未采集或 store/category 身份无效时禁用；`collected_with_warnings` 允许操作员确认并生成“需要补充”的模板。

- [ ] **步骤 5：确认后刷新并打开模板**

确认成功后先并行刷新店铺目录、类目规则、模板和 readiness，再设置店铺 select、刷新该店铺活动目录，最后调用 `openStoreTemplateEditor(template.id)`。`renderSizeOptions(template.sizes)` 必须为 `2XL` 创建自定义复选框并勾选；等待店铺目录完成前不得打开编辑器，避免 select 回填为空。

- [ ] **步骤 6：修正属性控件值绑定**

`renderStoreTemplateCategoryFields` 的 select/multi-select 必须按 option `value`（真实属性值 ID）选择，而不是按 option index 或标签。摘要改为从当前字段和 `attribute_values` 动态生成，删除固定 Brand/Pattern/Material/Sleeve/Plus 英文摘要。

- [ ] **步骤 7：增加响应式样式并运行 UI 测试**

样式要求：预览为全宽分组、8px 以下圆角、桌面两列/移动端单列、状态不遮挡内容、长类目路径可换行。运行 UI 测试，预期 PASS。

- [ ] **步骤 8：提交任务 5**

```powershell
git add apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_store_template_workflow_ui.py
git commit -m "feat: preview Mabang import snapshots"
```

## 任务 6：定向修复已污染数据

**文件：**

- 创建：`services/api/mabang_page_import_repair.py`
- 创建：`scripts/repair_mabang_page_import_pollution.py`
- 创建：`tests/test_mabang_page_import_repair.py`

- [ ] **步骤 1：编写范围和幂等性失败测试**

```python
def test_repair_only_touches_target_store_and_category():
    repaired, report = repair_import_pollution(
        deepcopy(_polluted_store()),
        store_id="2022625115",
        category_id="100244",
        snapshot=_trusted_snapshot(),
    )
    assert report["removed_field_ids"] == ["尺码图表:上传图片选择模板", "变种属性:"]
    assert repaired["store_templates"][0]["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert repaired["store_templates"][1] == _polluted_store()["store_templates"][1]
    assert repair_import_pollution(
        repaired,
        store_id="2022625115",
        category_id="100244",
        snapshot=_trusted_snapshot(),
    )[1]["changed"] is False
```

同时测试：snapshot 的店铺/类目不匹配时抛错且输入数据不变；没有 snapshot 时拒绝执行；其他店铺的活动、模板和类目版本逐字节不变。

- [ ] **步骤 2：运行测试确认红灯**

运行：`python -m pytest tests/test_mabang_page_import_repair.py -q`

预期：FAIL，修复模块尚不存在。

- [ ] **步骤 3：实现定向修复纯函数**

`repair_import_pollution(data, *, store_id, category_id, snapshot)` 只接受与目标 store/category 完全匹配、`schema_version=1` 的可信 snapshot。删除规则和当前模板中的两个污染 field ID；保留 snapshot `category_fields` 中存在的真实数字属性 ID；upsert 店铺目录；只从 snapshot 恢复 sizes 和已选属性；创建一个干净当前类目版本；删除/失效仅含污染字段的重复导入版本；修正 profile active/published 指针；当前模板追加 repair 版本，旧模板版本保持审计只读。没有可信 snapshot 时直接失败，不从标签或旧空控件猜值。

- [ ] **步骤 4：实现 dry-run/apply 脚本**

```powershell
$runId = "test-run-pod-ph"
python scripts/repair_mabang_page_import_pollution.py --store-id 2022625115 --category-id 100244 --snapshot-run-id $runId --dry-run
python scripts/repair_mabang_page_import_pollution.py --store-id 2022625115 --category-id 100244 --snapshot-run-id $runId --apply
```

CLI 从 `mabang_page_import_runs` 读取指定 snapshot 并校验身份。dry-run 输出修改前后计数和目标 ID，不写存储。`--apply` 通过 `storage.locked_store()` 的 JSON/PostgreSQL 统一事务保存；保存前生成 UTC `$timestamp`，把 `storage.load_store()` 导出到未纳入 Git 的 `data/backups/mabang-import-repair-$timestamp.json`。重复运行应报告无变化。

- [ ] **步骤 5：运行修复测试并执行 dry-run**

对 fixture store 运行 dry-run。预期：pytest PASS；dry-run 只列出目标店铺和类目，无其他 ID，且 fixture 文件不变。本任务不对用户实际数据执行 `--apply`。

- [ ] **步骤 6：提交任务 6**

```powershell
git add services/api/mabang_page_import_repair.py scripts/repair_mabang_page_import_pollution.py tests/test_mabang_page_import_repair.py
git commit -m "fix: repair polluted Mabang import data"
```

## 任务 7：更新文档和兼容行为

**文件：**

- 修改：`docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`
- 修改：`CONTEXT.md`
- 修改：`tests/test_mabang_page_import_api.py`
- 修改：`tests/test_template_readiness.py`
- 修改：`tests/test_product_line_flow.py`

- [ ] **步骤 1：增加兼容性测试**

测试 `/complete` 只 stage、不写模板；旧前端若误把 collected 当 completed，不会获得 `template`；明确 409/422 中文错误。在 `tests/test_template_readiness.py` 增加 `test_imported_incomplete_template_is_not_ready`，覆盖未选必填属性、交易默认值缺失和活动未配置。在 `tests/test_product_line_flow.py` 增加 `test_imported_incomplete_template_cannot_create_ready_automation_job`，证明由该模板产生的刊登副本/任务状态为 `needs_manual` 且列出精确缺失字段。

- [ ] **步骤 2：实现兼容状态和文档**

文档明确：采集不保存模板；确认才写入；会话过期处理；活动未配置含义；交易信息冲突处理；生产仍只保存草稿不发布。`CONTEXT.md` 中 Required-field snapshot 更新为“规则元数据与已选模板值分离”。

- [ ] **步骤 3：运行相关测试**

运行：

```powershell
python -m pytest tests/test_mabang_page_import_api.py tests/test_template_readiness.py -q
python -m pytest tests/test_product_line_flow.py -k imported_incomplete_template -q
```

- [ ] **步骤 4：提交任务 7**

```powershell
git add docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md CONTEXT.md tests/test_mabang_page_import_api.py tests/test_template_readiness.py tests/test_product_line_flow.py
git commit -m "docs: update staged Mabang import workflow"
```

## 任务 8：全量验证和真实页面验收

**文件：**

- 验证：上述所有修改文件
- 产物：`artifacts/mabang-import-preview-desktop.png`
- 产物：`artifacts/mabang-import-preview-mobile.png`

- [ ] **步骤 1：运行完整测试集**

```powershell
python -m pytest -q
```

预期：全部 PASS，无未处理 warning/error。

- [ ] **步骤 2：运行静态检查**

```powershell
git diff --check
node --check scripts/mabang_page_import_executor.cjs
```

预期：均返回 0。

- [ ] **步骤 3：重启本地服务**

```powershell
$baseUrl = "http://127.0.0.1:8791"
$health = try { Invoke-RestMethod "$baseUrl/api/health" -TimeoutSec 2 } catch { $null }
if (-not $health -or $health.store_file -notlike "*store-template-workflow*") {
    $baseUrl = "http://127.0.0.1:8792"
    $apiProcess = Start-Process powershell -WindowStyle Hidden -PassThru `
        -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/run_api.ps1", "-Port", "8792", "-NoAutomationWorker"
}
```

轮询 `$baseUrl/api/health` 最多 20 秒，确认返回 `ok=true`，且 `store_file` 指向当前 worktree。若启动了 `$apiProcess`，记录 PID，验收结束后仅停止该进程；不得终止占用 8791 的未知进程。

- [ ] **步骤 4：真实马帮页面采集验收**

使用当前已授权的 Browser-Use Chrome 会话和 POD菲律宾 / 100244 商品详情页：

1. 记录店铺目录、类目版本、活动目录、模板和模板版本五个集合的计数，创建 import run 并保存 `$runId`。
2. 调用 collect 后确认五个计数不变；检查预览店铺、类目、四个已选属性、颜色、五个尺码、当前页实际库存（本次为 300）、空重量和尺寸、活动未配置。
3. 调用 confirm 后检查店铺目录和模板回填，并确认重复 confirm 不新增模板/版本。
4. 使用同一个 `$runId` 执行定向修复 dry-run，核对只包含 `2022625115 / 100244` 后再执行 apply；确认自动生成备份，再次 dry-run 必须返回 `changed=false`。

```powershell
python scripts/repair_mabang_page_import_pollution.py --store-id 2022625115 --category-id 100244 --snapshot-run-id $runId --dry-run
python scripts/repair_mabang_page_import_pollution.py --store-id 2022625115 --category-id 100244 --snapshot-run-id $runId --apply
python scripts/repair_mabang_page_import_pollution.py --store-id 2022625115 --category-id 100244 --snapshot-run-id $runId --dry-run
```

- [ ] **步骤 5：浏览器 UI 验收**

在桌面 `1440x900` 和移动端 `390x844` 打开 `$baseUrl`：

- 导入预览无水平溢出；
- 长类目路径换行；
- 状态和字段不重叠；
- 确认后店铺选中 POD菲律宾；
- 尺码勾选 S/M/L/XL/2XL；
- 属性只显示真实平台字段；
- 活动显示“活动未配置”；
- 不出现“尺码图表”“变种属性”分类字段。

保存截图到两个 artifacts 路径，并检查控制台无 error、图片无破损。

- [ ] **步骤 6：最终工作区检查**

```powershell
git status --short
git log --oneline -10
```

确认只保留用户原有未提交改动或明确说明的运行产物；不得把 `data/store.json`、备份、会话信息、CDP URL token 或截图中的敏感 URL 提交到 Git。

若步骤 3 启动了 `$apiProcess`，在完成截图和控制台检查后执行 `Stop-Process -Id $apiProcess.Id`；若复用了已有 8791 服务则保持运行，并向用户报告实际 URL。

- [ ] **步骤 7：提交最终验证所需的小修正**

仅在步骤 1-6 发现实际问题时提交对应最小修正；若无代码变更，不创建空提交。

---

## 需求覆盖矩阵

| 需求 | 实施任务 | 验收证据 |
|---|---|---|
| 真实店铺和类目 ID/名称 | 1、2、4 | 快照 fixture、店铺目录断言、真实页面验收 |
| 只保存已选非空属性值 | 1、4、5 | `selected_attributes`/`attribute_values` 精确断言 |
| 未选必填项只诊断、不写模板值 | 1、4、7 | missing 列表与 readiness 阻断测试 |
| 颜色、尺码、SKU 库存、重量、包裹尺寸 | 1、2、4 | 全 SKU 折叠测试与真实页面预览 |
| 空交易字段保持空、冲突不取第一行 | 1、2、5 | missing/conflict 单元测试与预览状态 |
| 真实店铺活动、不伪造 ID | 1、2、4、7 | 无活动 ID fixture、无回退断言、活动未配置阻断 |
| 采集后先预览、确认才写入 | 3、4、5 | collect 集合计数不变、confirm 原子写入测试 |
| 可重新采集且失败保留上一快照 | 3、5 | API 状态迁移测试和重新采集按钮 |
| 确认后店铺/活动/尺码/属性正确回填 | 4、5 | 目录刷新和编辑器 UI 测试 |
| 定向修复 POD菲律宾 / 100244 污染 | 6、8 | dry-run 范围、备份、apply 后幂等检查 |
| 采集与自动化永久不发布 | 2、7、8 | 只读执行器检查、文档和浏览器验收 |

---

## 完成标准

- `/collect` 永远不写店铺模板或类目版本。
- `/confirm` 原子、幂等地写入所有业务数据。
- 店铺、尺码、属性、交易信息和活动在预览中有明确来源与覆盖状态。
- 仅非空已选属性进入模板值，未选必填项只形成缺失诊断。
- 空交易信息保持空，SKU 冲突不被静默折叠。
- 没有真实活动 ID 时只显示“活动未配置”。
- 当前污染字段和版本被定向修复，其他店铺/类目不变。
- 真实页面和桌面/移动端 UI 验收通过。
- 采集器不点击保存或发布；后续自动化最多保存马帮草稿，发布动作始终禁用。
