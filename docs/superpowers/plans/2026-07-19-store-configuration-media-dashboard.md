# 店铺配置、素材自动化与桌面工作台实施计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在保留当前店铺模板和刊登副本成果的基础上，落地准确促销状态、跨店另存、马帮水印目录、MP4 视频、尺码图快照、分阶段自动化结果、桌面首页与设置页。

**架构：** 保持当前 Python 标准库 HTTP API、JSON/PostgreSQL 兼容存储、原生 JavaScript 前端和 Browser-Use/Playwright Worker。将新增业务规则放入小型服务模块，`storage.py` 负责事务组装，`main.py` 负责路由，浏览器脚本只处理真实马帮页面读取与操作。自动化任务消费不可变副本快照，任何执行路径都只保存草稿并禁止发布。

**技术栈：** Python 3、pytest、标准库 `http.server`、原生 JavaScript/CSS、Node.js Playwright、Browser-Use、系统 `ffprobe`（只读取视频元数据，不转码）。

---

## 0. 当前工作树约束

本计划从 `feature/store-template-workflow` 的当前脏工作树继续。以下文件已有用户功能改动，禁止回退：

- `apps/web/app.js`
- `apps/web/index.html`
- `apps/web/styles.css`
- `scripts/mabang_page_import_capture.js`
- `services/api/mabang_import_snapshot.py`
- `services/api/mabang_page_import.py`
- `services/api/main.py`
- `services/api/storage.py`
- 对应的 7 个测试文件和 `services/api/mabang_size_chart_import.py`

`tmp_upload/` 是运行产物，不得提交。开始实现前先完成任务 1，把已有副本编辑、尺码图采集、图片顺序和按尺码价格改动固定为可回归基线。

## 1. 逐文件结构

### 新建文件

- `services/api/promotion_resolution.py`：区分 available/expired/unresolved/unconfigured，并构造执行期匹配结果。
- `services/api/store_watermarks.py`：规范化、替换和查询按店铺隔离的马帮水印目录。
- `services/api/video_assets.py`：MP4 签名、大小和 `ffprobe` 元数据校验；不包含转码。
- `services/api/runtime_configuration.py`：非敏感运行配置默认值、校验、热更新分类和重启标记。
- `services/api/operations_dashboard.py`：从存储快照计算首页指标、近期事件和动态下一步动作。
- `tests/test_promotion_resolution.py`：促销状态和执行期解析单元测试。
- `tests/test_store_watermarks.py`：水印目录隔离、替换和匹配测试。
- `tests/test_video_assets.py`：视频边界与 `ffprobe` 错误测试。
- `tests/test_runtime_configuration.py`：设置校验、热更新和安全策略测试。
- `tests/test_operations_dashboard.py`：首页聚合和“继续工作”优先级测试。
- `tests/fixtures/mabang_page_capture_watermarks.json`：脱敏的真实马帮水印模板采集结构。
- `scripts/migrate_store_configuration_media.py`：旧模板、旧促销警告、视频和尺码图字段幂等迁移。
- `tests/test_store_configuration_media_migration.py`：迁移 dry-run、幂等和数据保留测试。

### 修改文件

- `services/api/store_promotions.py`：目录缺失项由 `disabled` 改为可解释的 `unresolved`，只以明确证据判定过期。
- `services/api/template_readiness.py`：促销警告非阻断，素材警告与业务阻断分离。
- `services/api/mabang_import_snapshot.py`：规范化完整水印目录及所选尺码图来源。
- `services/api/mabang_page_import.py`：确认导入时原子写入促销、水印和尺码图资产。
- `services/api/storage.py`：持久化新增集合、另存模板、视频/尺码图快照、自动化阶段结果和终态。
- `services/api/main.py`：新增另存、水印、视频、首页和设置 API。
- `scripts/mabang_page_import_capture.js`：只读采集当前店铺全部水印模板和可验证店铺身份。
- `scripts/mabang_playwright_executor.cjs`：主图/产品图水印、视频上传和阶段结果；保持发布保护。
- `scripts/browser_use_runner.py`：将视频与两类水印快照编入任务计划，准备 ASCII 上传路径。
- `scripts/automation_worker.py`：识别 `saved_with_warnings`，保留保存证据后继续下一任务。
- `apps/web/index.html`：顶部导航、独立页面、另存弹窗、水印/视频/尺码图控件、设置页。
- `apps/web/app.js`：新增页面加载器、编辑动作、首页聚合、设置保存和采集后导航。
- `apps/web/styles.css`：桌面工作台视觉、固定顶部导航和 1440/1920 布局。
- `services/api/db_store.py`：新增集合纳入 PostgreSQL JSON 兼容存储。
- `services/api/settings.py`：提供运行配置初始默认值，不把敏感值发给前端。
- `requirements.txt`：不新增转码库；增加注释说明系统运行需可执行的 `ffprobe`。
- 现有测试：扩展 API、存储、浏览器任务、Worker、迁移和 UI 静态契约覆盖。

---

### 任务 1：固定当前未提交的副本编辑与尺码图基线

**文件：**
- 修改：当前已修改的应用、服务、脚本和测试文件
- 排除：`tmp_upload/`
- 测试：`tests/test_mabang_import_snapshot.py`
- 测试：`tests/test_mabang_page_import.py`
- 测试：`tests/test_product_line_flow.py`
- 测试：`tests/test_store_template_workflow_api.py`
- 测试：`tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：记录当前差异，不清理用户改动**

运行：

```powershell
git status --short
git diff --check
git diff -- apps/web/app.js services/api/storage.py scripts/mabang_page_import_capture.js
```

预期：只看到当前工作树已有改动；`git diff --check` 无空白错误。不得运行 `git reset`、`git checkout --` 或删除 `tmp_upload`。

- [ ] **步骤 2：运行当前改动的专项测试**

运行：

```powershell
python -m pytest tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py -q
```

预期：全部 PASS。若失败，只修复当前副本编辑、尺码图、图片顺序或尺码价格范围内的回归。

- [ ] **步骤 3：运行 Browser-Use 任务契约测试**

运行：

```powershell
python -m pytest tests/test_browser_schema_payload.py tests/test_browser_use_runner.py -q
node --test tests/mabang_playwright_executor.test.cjs
```

预期：全部 PASS，执行器仍证明 `publish_clicked=false` 且保存后关闭页面。

- [ ] **步骤 4：提交已有成果作为基线**

```powershell
git add apps/web/app.js apps/web/index.html apps/web/styles.css scripts/mabang_page_import_capture.js services/api/mabang_import_snapshot.py services/api/mabang_page_import.py services/api/mabang_size_chart_import.py services/api/main.py services/api/storage.py tests/test_browser_schema_payload.py tests/test_browser_use_runner.py tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py docs/superpowers/plans/2026-07-18-listing-copy-editor.md docs/superpowers/specs/2026-07-18-listing-copy-editor-design.md
git commit -m "feat: complete listing copy maintenance baseline"
```

预期：提交不包含 `tmp_upload/`。

---

### 任务 2：拆分促销状态并修正错误提示

**文件：**
- 创建：`services/api/promotion_resolution.py`
- 创建：`tests/test_promotion_resolution.py`
- 修改：`services/api/store_promotions.py`
- 修改：`services/api/template_readiness.py`
- 修改：`services/api/storage.py`
- 测试：`tests/test_store_promotions.py`
- 测试：`tests/test_template_readiness.py`
- 测试：`tests/test_product_line_flow.py`

- [ ] **步骤 1：编写失败的促销状态测试**

在 `tests/test_promotion_resolution.py` 添加：

```python
from services.api.promotion_resolution import promotion_state, resolve_execution_promotion


def test_absent_catalog_entry_is_unresolved_not_expired():
    assert promotion_state("activity-1", None, now="2026-07-19T00:00:00Z") == "unresolved"


def test_explicit_end_time_is_expired():
    promotion = {"activity_id": "activity-1", "status": "available", "end_at": "2026-07-18T23:59:59Z"}
    assert promotion_state("activity-1", promotion, now="2026-07-19T00:00:00Z") == "expired"


def test_execution_resolution_requires_unique_verified_identity():
    result = resolve_execution_promotion(
        {"activity_id": "missing", "name": "六折活动", "sale_ratio": "0.6", "end_at": "2026-08-01"},
        [{"activity_id": "new-id", "name": "六折活动", "sale_ratio": "0.6", "end_at": "2026-08-01"}],
        now="2026-07-19T00:00:00Z",
    )
    assert result == {"status": "resolved", "activity_id": "new-id", "matched_by": "verified_identity"}
```

同时把 `tests/test_store_promotions.py::test_refresh_preserves_other_stores_and_disables_missing_activity` 改为断言缺失项状态是 `unresolved`。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_promotion_resolution.py tests/test_store_promotions.py -q
```

预期：FAIL，模块不存在或旧状态仍为 `disabled`。

- [ ] **步骤 3：实现纯促销解析模块**

`promotion_resolution.py` 提供稳定接口：

```python
def promotion_state(activity_id: str, promotion: dict[str, Any] | None, *, now: str) -> str:
    if not str(activity_id or "").strip():
        return "unconfigured"
    if promotion is None:
        return "unresolved"
    if explicitly_expired(promotion, now=now):
        return "expired"
    return "available" if promotion.get("status") == "available" else "unresolved"


def resolve_execution_promotion(snapshot: dict[str, Any], catalog: list[dict[str, Any]], *, now: str) -> dict[str, str]:
    wanted_id = str(snapshot.get("activity_id") or "").strip()
    by_id = [item for item in catalog if str(item.get("activity_id") or "").strip() == wanted_id]
    if len(by_id) == 1 and promotion_state(wanted_id, by_id[0], now=now) == "available":
        return {"status": "resolved", "activity_id": wanted_id, "matched_by": "activity_id"}
    wanted_identity = promotion_identity(snapshot)
    by_identity = [item for item in catalog if promotion_identity(item) == wanted_identity]
    if len(by_identity) == 1 and promotion_state(str(by_identity[0].get("activity_id") or ""), by_identity[0], now=now) == "available":
        return {"status": "resolved", "activity_id": str(by_identity[0]["activity_id"]), "matched_by": "verified_identity"}
    return {"status": "needs_confirmation", "activity_id": "", "matched_by": ""}
```

实现中可展开最后一行，但不得使用模糊名称包含、相似度或同折扣自动替换。

- [ ] **步骤 4：让目录刷新保留 unresolved 历史项**

修改 `replace_promotion_catalog()`：新目录未包含的旧活动仅在已有明确结束时间/状态时保持 `expired`，否则标记 `unresolved`。在 `template_readiness.py` 中将 `expired`、`unresolved`、`unconfigured` 转成 warnings，不加入 errors。

警告代码固定为：

```python
promotion_expired
promotion_unresolved
promotion_unconfigured
```

- [ ] **步骤 5：修正执行前缺口检查**

修改 `storage._runtime_configuration_missing()`：只有执行期解析返回 `needs_confirmation` 时阻断；不得再把目录缺失统一映射为 `promotion_activity_expired`。

- [ ] **步骤 6：运行专项与回归测试**

```powershell
python -m pytest tests/test_promotion_resolution.py tests/test_store_promotions.py tests/test_template_readiness.py tests/test_product_line_flow.py -q
```

预期：全部 PASS；断言 UI/API 不再把 unresolved 显示成 expired。

- [ ] **步骤 7：提交**

```powershell
git add services/api/promotion_resolution.py services/api/store_promotions.py services/api/template_readiness.py services/api/storage.py tests/test_promotion_resolution.py tests/test_store_promotions.py tests/test_template_readiness.py tests/test_product_line_flow.py
git commit -m "fix: distinguish unresolved store promotions"
```

---

### 任务 3：实现店铺模板另存和目标店铺配置入口

**文件：**
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_store_template_workflow_api.py`
- 测试：`tests/test_store_template_workflow_ui.py`
- 测试：`tests/test_store_template_category_binding.py`

- [ ] **步骤 1：编写同站点/跨站点复制矩阵测试**

在 `tests/test_store_template_workflow_api.py` 添加：

```python
def test_save_as_template_clears_store_bound_values_same_site(isolated_store):
    source = create_complete_template(store_id="store-a", country="PH")
    copied = storage.copy_store_template(source["id"], {"mabang_store_id": "store-b", "store_name": "B", "country": "PH", "template_name": "B template"})
    assert copied["mabang_category_id"] == source["mabang_category_id"]
    assert copied["promotion_activity_id"] == ""
    assert copied["main_image_watermark"] == {}
    assert copied["product_image_watermark"] == {}


def test_save_as_template_clears_site_bound_values_cross_site(isolated_store):
    source = create_complete_template(store_id="store-a", country="PH")
    copied = storage.copy_store_template(source["id"], {"mabang_store_id": "store-sg", "store_name": "SG", "country": "SG", "template_name": "SG template"})
    assert copied["mabang_category_id"] == ""
    assert copied["normal_price"] == ""
    assert copied["attribute_values"] == {}
    assert copied["sizes"] == source["sizes"]
    assert copied["weight_kg"] == source["weight_kg"]
```

在同一测试文件增加 `create_complete_template(store_id: str, country: str)` helper：调用现有 `storage.create_store_template()`，固定填入已发布测试类目版本、`normal_price="100"`、标准尺码、描述版本、库存/重量/包裹尺寸和测试尺码图；返回创建后的模板。不得从其他测试模块导入 helper。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_store_template_workflow_api.py -k "save_as_template" -q
```

预期：FAIL，`copy_store_template` 尚不存在。

- [ ] **步骤 3：实现事务化另存**

在 `storage.py` 增加：

```python
def copy_store_template(template_id: str, target: dict[str, Any]) -> dict[str, Any]:
    with locked_store() as (data, save):
        source = _require_store_template(data, template_id)
        copied = _copy_store_template_payload(source, target, now=utc_now())
        data.setdefault("store_templates", []).append(copied)
        save(data)
        return deepcopy(copied)
```

`_copy_store_template_payload()` 按规格中的同站点/跨站点矩阵显式列出保留字段，不使用 `{**source, **target}` 盲拷贝。

- [ ] **步骤 4：增加 API 路由**

在 `main.py` 增加：

```text
POST /api/store-templates/{template_id}/copy
```

请求体必须包含真实目标 `mabang_store_id`、`country` 和 `template_name`；未知店铺返回 400，源模板不存在返回 404。

- [ ] **步骤 5：增加“另存为模板”弹窗和目标配置按钮**

`index.html` 增加 `save-as-store-template-dialog`，字段只有目标店铺、模板名、复制摘要；编辑态按钮为 `另存为模板` 和 `获取目标店铺配置`。`app.js` 增加：

```javascript
async function saveStoreTemplateAsCopy() {
  return api(`/api/store-templates/${encodeURIComponent(state.editingStoreTemplateId)}/copy`, {
    method: "POST",
    body: JSON.stringify(readSaveAsTarget()),
  });
}
```

创建成功后调用现有模板编辑加载函数打开新模板，不修改源模板。

- [ ] **步骤 6：把目标配置采集绑定到所选店铺**

创建 Mabang import run 时写入 `expected_store_id`。确认导入前校验采集店铺 ID；不一致时显示“当前马帮页面店铺与目标店铺不一致”，保持弹窗打开且不写目录。

- [ ] **步骤 7：运行 API 和 UI 测试**

```powershell
python -m pytest tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py tests/test_store_template_category_binding.py tests/test_mabang_page_import_api.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```powershell
git add services/api/storage.py services/api/main.py apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py tests/test_store_template_category_binding.py tests/test_mabang_page_import_api.py
git commit -m "feat: save store templates for target stores"
```

---

### 任务 4：采集并维护马帮原生水印目录

**文件：**
- 创建：`services/api/store_watermarks.py`
- 创建：`tests/test_store_watermarks.py`
- 创建：`tests/fixtures/mabang_page_capture_watermarks.json`
- 修改：`scripts/mabang_page_import_capture.js`
- 修改：`services/api/mabang_import_snapshot.py`
- 修改：`services/api/mabang_page_import.py`
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 测试：`tests/test_browser_use_runner.py`
- 测试：`tests/test_mabang_import_snapshot.py`
- 测试：`tests/test_mabang_page_import.py`

- [ ] **步骤 1：建立脱敏真实夹具**

夹具至少包含两个模板，保留真实结构字段但替换店铺 token 和 URL 查询参数：

```json
{
  "store_category_state": {"store": {"id": "store-ph", "name": "PH 测试店铺"}},
  "watermark_templates": [
    {"template_id": "wm-main", "name": "主图水印", "preview_url": "https://publish-cos.mabangerp.com/sanitized/main.png"},
    {"template_id": "wm-product", "name": "podQ-7", "preview_url": "https://publish-cos.mabangerp.com/sanitized/product.png"}
  ]
}
```

- [ ] **步骤 2：编写目录隔离和原子替换失败测试**

```python
def test_watermark_catalog_is_store_scoped():
    data = {"store_watermark_templates": []}
    replace_watermark_catalog(data, store_id="a", raw_items=[{"template_id": "1", "name": "A"}], now=NOW)
    replace_watermark_catalog(data, store_id="b", raw_items=[{"template_id": "1", "name": "B"}], now=NOW)
    assert [item["name"] for item in list_watermarks(data, "a")] == ["A"]


def test_empty_failed_capture_does_not_replace_last_valid_catalog():
    data = existing_catalog()
    with pytest.raises(ValueError, match="水印模板目录为空"):
        replace_watermark_catalog(data, store_id="a", raw_items=[], now=NOW)
    assert data == existing_catalog()
```

在同一文件定义 `existing_catalog()`，每次返回新的字典：`{"store_watermark_templates": [{"store_id": "a", "template_id": "old", "name": "旧模板", "collected_at": NOW}]}`，避免测试之间共享可变对象。

- [ ] **步骤 3：运行测试确认失败**

```powershell
python -m pytest tests/test_store_watermarks.py tests/test_mabang_import_snapshot.py -q
```

预期：FAIL，新模块和 snapshot 字段尚不存在。

- [ ] **步骤 4：实现水印目录服务**

`store_watermarks.py` 公开：

```python
normalize_watermark_catalog(store_id, raw_items, now)
replace_watermark_catalog(data, store_id, raw_items, now)
list_watermarks(data, store_id)
resolve_watermark_reference(reference, catalog)
```

`resolve_watermark_reference()` 先匹配 ID，再校验名称；多个同名结果必须返回 unresolved，禁止按数组索引。

- [ ] **步骤 5：扩展只读采集脚本**

在 `mabang_page_import_capture.js` 增加 `readWatermarkTemplates(detailVm)`，优先读取打开水印弹窗后 Vue 实例/状态中的完整列表。只读取数据，不点击保存模板、不应用水印、不修改商品图片。返回字段固定为 `watermark_templates`。

- [ ] **步骤 6：扩展 snapshot 与确认导入事务**

`normalize_mabang_import_snapshot()` 返回 `watermark_catalog`；`confirm_page_import()` 在同一 `locked_store()` 事务中写入目标店铺目录。空列表只有在采集结果明确标记 `catalog_loaded=true` 时允许表示“该店铺无水印”；页面解析失败不得清空旧目录。

- [ ] **步骤 7：增加查询 API**

```text
GET /api/store-watermarks?store_id={id}
```

响应只返回模板 ID、名称、本地/去敏预览地址、状态和采集时间。

- [ ] **步骤 8：运行专项测试**

```powershell
python -m pytest tests/test_store_watermarks.py tests/test_browser_use_runner.py tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py tests/test_mabang_page_import_api.py -q
```

预期：全部 PASS；共享采集实现测试仍通过。

- [ ] **步骤 9：提交**

```powershell
git add services/api/store_watermarks.py services/api/mabang_import_snapshot.py services/api/mabang_page_import.py services/api/storage.py services/api/main.py scripts/mabang_page_import_capture.js tests/test_store_watermarks.py tests/fixtures/mabang_page_capture_watermarks.json tests/test_browser_use_runner.py tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py tests/test_mabang_page_import_api.py
git commit -m "feat: collect store-scoped Mabang watermarks"
```

---

### 任务 5：完成尺码图来源和主图/产品图水印选择界面

**文件：**
- 修改：`services/api/storage.py`
- 修改：`services/api/template_readiness.py`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_product_line_flow.py`
- 测试：`tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：编写快照和水印选择失败测试**

在 `tests/test_product_line_flow.py` 增加：

```python
def test_listing_copy_snapshots_size_chart_source_and_two_watermark_roles(tmp_path):
    template = storage.create_store_template(complete_template_payload(
        size_chart_source="mabang_capture",
        size_chart_asset_id="chart-1",
        main_image_watermark={"template_id": "wm-main", "name": "主图水印"},
        product_image_watermark={"template_id": "wm-product", "name": "产品图水印"},
    ))
    base = storage.create_base_product_from_folder(str(make_product_folder(tmp_path)))
    copy = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])["items"][0]
    assert copy["size_chart_snapshot"]["source"] == "mabang_capture"
    assert copy["main_image_watermark"]["template_id"] == "wm-main"
    assert copy["product_image_watermark"]["template_id"] == "wm-product"
```

`complete_template_payload(**overrides)` 和 `make_product_folder(tmp_path)` 作为本文件本地 helper：前者返回现有完整模板测试使用的必填字典并应用 overrides；后者创建含 `main.jpg` 和颜色图的最小商品目录。复用本文件现有 fixture 形状，不导入别的测试模块。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_product_line_flow.py -k "snapshots_size_chart_source" -q
```

预期：FAIL，新增快照字段尚未构造。

- [ ] **步骤 3：规范化模板与副本快照字段**

`_normalize_store_template_payload()` 接受 `size_chart_source`、`main_image_watermark`、`product_image_watermark`。创建副本时深拷贝三项；更新模板不得回写已有副本。

- [ ] **步骤 4：重做尺码图控件为明确来源选择**

`index.html` 使用分段控件：`上传图片`、`选择模板`、`马帮采集`。增加已有尺码图模板选择弹窗和缩略图。不得保留一个无法区分来源的裸文件输入。

- [ ] **步骤 5：增加两类水印下拉框**

店铺模板编辑器显示：

```html
<label>主图水印<select id="store-template-main-watermark"></select></label>
<label>产品图水印<select id="store-template-product-watermark"></select></label>
```

每个下拉第一项为“关闭”，其余项按当前店铺 `/api/store-watermarks` 填充，显示名称和缩略图。切换店铺时立即清空旧选择，再加载新目录。

- [ ] **步骤 6：确认采集后定位缺失项**

更新 `confirmMabangPageImport()`：关闭导入弹窗，切换顶部/当前店铺模板视图，调用模板编辑加载函数，并按 API readiness 的 `missing_fields[0]` 映射到元素 ID 后 `scrollIntoView({block: "center"})` 与 `focus()`。

- [ ] **步骤 7：运行 UI 与快照测试**

```powershell
python -m pytest tests/test_product_line_flow.py tests/test_store_template_workflow_ui.py tests/test_mabang_page_import.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```powershell
git add services/api/storage.py services/api/template_readiness.py apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_product_line_flow.py tests/test_store_template_workflow_ui.py tests/test_mabang_page_import.py
git commit -m "feat: configure listing size charts and watermarks"
```

---

### 任务 6：实现 MP4 视频资产、校验和副本维护

**文件：**
- 创建：`services/api/video_assets.py`
- 创建：`tests/test_video_assets.py`
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 修改：`services/api/settings.py`
- 修改：`requirements.txt`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_product_line_flow.py`
- 测试：`tests/test_store_template_workflow_api.py`

- [ ] **步骤 1：编写视频边界测试**

使用 monkeypatch 替换 `subprocess.run`，不在单元测试中依赖真实 ffprobe：

```python
@pytest.mark.parametrize("duration,width,height,size_bytes", [
    (10, 1280, 1280, 30 * 1024 * 1024),
    (60, 720, 1280, 1),
])
def test_mp4_boundary_values_are_valid(tmp_path, monkeypatch, duration, width, height, size_bytes):
    video = make_fake_mp4(tmp_path, size_bytes=size_bytes)
    fake_ffprobe(monkeypatch, duration=duration, width=width, height=height)
    assert validate_video(video)["validation_status"] == "valid"


def test_non_mp4_and_missing_ffprobe_return_readable_errors(tmp_path, monkeypatch):
    video = tmp_path / "movie.mov"
    video.write_bytes(b"not-mp4")
    result = validate_video(video)
    assert "video_format_unsupported" in result["validation_errors"]
```

在 `tests/test_video_assets.py` 定义：`make_fake_mp4(tmp_path, size_bytes)` 写入 `b"\x00\x00\x00\x18ftypisom"` 并补齐指定大小；`fake_ffprobe(monkeypatch, duration, width, height)` 替换 `services.api.video_assets.subprocess.run`，返回 `returncode=0` 且 stdout 为对应 ffprobe JSON。

另测 9.99 秒、60.01 秒、1281 像素和 30 MB + 1 byte 均 invalid。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_video_assets.py -q
```

预期：FAIL，`video_assets` 不存在。

- [ ] **步骤 3：实现只读视频校验**

`video_assets.py` 定义常量和接口：

```python
MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 60
MAX_DIMENSION_PIXELS = 1280
MAX_SIZE_BYTES = 30 * 1024 * 1024

def validate_video(path: Path, *, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    errors = validate_mp4_container_and_size(path)
    metadata = probe_video_metadata(path, ffprobe_path=ffprobe_path)
    errors.extend(validate_video_metadata(metadata))
    return {
        "mime_type": "video/mp4",
        "size_bytes": path.stat().st_size,
        "duration_seconds": metadata.get("duration_seconds", 0),
        "width": metadata.get("width", 0),
        "height": metadata.get("height", 0),
        "validation_status": "invalid" if errors else "valid",
        "validation_errors": errors,
    }
```

调用参数固定为 `ffprobe -v error -show_entries stream=width,height:format=duration -of json VIDEO_PATH`（代码中 `VIDEO_PATH` 由参数数组传入，不经过 shell 拼接），设置超时 15 秒。找不到 ffprobe 返回 `video_metadata_unavailable`，不得执行转码命令。

- [ ] **步骤 4：实现视频上传/替换/删除 API**

提供：

```text
POST /api/base-products/{id}/video
POST /api/store-listing-copies/{id}/video
DELETE /api/store-listing-copies/{id}/video
```

沿用当前 base64 JSON 上传模式和最大请求限制；服务端保存到 `storage/videos/{entity-id}/`，文件名使用 UUID ASCII 名。响应返回校验元数据，不返回未去敏 NAS 路径。

- [ ] **步骤 5：让 NAS 视频进入基础商品和副本快照**

从已识别 `asset_type == "video"` 的资产中只选择第一项，创建 `video` 字段；多于一个时增加 `multiple_product_videos` 提醒。副本创建时深拷贝视频元数据和路径，不在任务执行时重新扫描 NAS。

- [ ] **步骤 6：增加基础商品和副本视频控件**

显示 MP4 文件名、大小、时长、分辨率、原生 `<video controls>` 预览、替换和删除按钮。文件输入固定 `accept="video/mp4,.mp4"`。无视频显示黄色“视频缺失，不影响保存”；invalid 显示逐条中文原因。

- [ ] **步骤 7：运行专项测试**

```powershell
python -m pytest tests/test_video_assets.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```powershell
git add services/api/video_assets.py services/api/storage.py services/api/main.py services/api/settings.py requirements.txt apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_video_assets.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py
git commit -m "feat: validate and maintain MP4 listing videos"
```

---

### 任务 7：把视频和两类水印加入自动化任务

**文件：**
- 修改：`services/api/storage.py`
- 修改：`scripts/browser_use_runner.py`
- 修改：`scripts/mabang_playwright_executor.cjs`
- 修改：`tests/test_browser_schema_payload.py`
- 修改：`tests/test_browser_use_runner.py`
- 修改：`tests/mabang_playwright_executor.test.cjs`

- [ ] **步骤 1：编写任务快照失败测试**

```python
def test_browser_task_includes_video_and_two_watermark_roles(tmp_path):
    payload = ready_payload_with_media(tmp_path)
    plan = build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)
    assert plan["video"]["validation_status"] == "valid"
    assert plan["watermarks"]["main_image"]["template_id"] == "wm-main"
    assert plan["watermarks"]["product_images"]["image_positions"] == list(range(2, 10))
```

在 `tests/test_browser_use_runner.py` 增加 `ready_payload_with_media(tmp_path)`：从该文件现有 `_ready_payload()` 深拷贝，创建一个真实临时 MP4 路径，并写入 `browser_inputs.video`、`main_image_watermark` 和 `product_image_watermark`。不得修改共享 `_ready_payload()` 返回对象。

并断言 invalid 视频仍返回 `ok=True`，action 标记为 skipped，而不是把任务整体 blocked。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_browser_schema_payload.py tests/test_browser_use_runner.py -k "video or watermark" -q
```

预期：FAIL，计划中没有对应字段和 action。

- [ ] **步骤 3：扩展不可变 payload**

`build_automation_payload()` 从刊登副本快照输出：

```python
"video": deepcopy(listing_copy.get("video") or {"validation_status": "missing"}),
"watermarks": {
    "main_image": deepcopy(listing_copy.get("main_image_watermark") or {}),
    "product_images": deepcopy(listing_copy.get("product_image_watermark") or {}),
},
```

execution snapshot hash 必须包含这些字段，模板后续变更不能改变已生成任务。

- [ ] **步骤 4：准备 ASCII 视频上传副本**

在 `browser_use_runner.py` 增加 `prepare_video_upload()`，仅当 validation_status 为 valid 且源文件存在时复制到 `tmp_upload/automation-jobs/{job-id}/video/product.mp4`。无效或缺失返回带中文 warning 的 skipped action。

- [ ] **步骤 5：实现执行器水印阶段**

`mabang_playwright_executor.cjs` 增加：

```javascript
async function applyWatermark(frame, reference, imagePositions, role) { /* ID-first verified selection */ }
```

主图传 `[1]`；产品图使用 `Array.from({length: Math.max(0, productImageCount - 1)}, (_, index) => index + 2)` 生成第 2 张到最后一张的位置。每类函数自己捕获可恢复错误，返回 `{status:'warning', code, role, template_id, template_name, image_positions}`；不得选择其他模板兜底。

- [ ] **步骤 6：实现视频上传阶段**

增加 `uploadProductVideo(frame, task)`：valid 才定位唯一视频 input 并 `setInputFiles`；missing/invalid 返回 skipped；控件或上传失败返回 warning。不得把视频放入商品图片 input。

- [ ] **步骤 7：固定执行顺序和发布保护**

顺序必须为商品图上传 → 主图水印 → 产品图水印 → 视频上传 → 尺码图/SKU/交易信息 → 保存。Node 契约测试继续断言没有任何“保存并刊登/刊登/发布/提交审核”点击路径。

- [ ] **步骤 8：运行 Python 与 Node 测试**

```powershell
python -m pytest tests/test_browser_schema_payload.py tests/test_browser_use_runner.py -q
node --test tests/mabang_playwright_executor.test.cjs
```

预期：全部 PASS。

- [ ] **步骤 9：提交**

```powershell
git add services/api/storage.py scripts/browser_use_runner.py scripts/mabang_playwright_executor.cjs tests/test_browser_schema_payload.py tests/test_browser_use_runner.py tests/mabang_playwright_executor.test.cjs
git commit -m "feat: automate Mabang video and watermark steps"
```

---

### 任务 8：分阶段记录错误并支持警告保存终态

**文件：**
- 修改：`services/api/storage.py`
- 修改：`scripts/automation_worker.py`
- 修改：`services/api/main.py`
- 修改：`apps/web/app.js`
- 修改：`apps/web/index.html`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_automation_worker.py`
- 测试：`tests/test_product_line_flow.py`
- 测试：`tests/test_automation_status_ui.py`

- [ ] **步骤 1：编写 Worker 终态测试**

```python
def test_worker_finishes_saved_with_warnings_when_draft_is_proven_saved():
    execution = proven_saved_execution(stages={
        "apply_main_image_watermark": {"status": "warning", "code": "watermark_not_found"},
        "video_upload": {"status": "skipped", "code": "video_missing"},
    })
    result = run_worker_with_execution(execution)
    assert result["status"] == "saved_with_warnings"
    assert result["job"]["publish_clicked"] is False
```

复用 `tests/test_automation_worker.py` 现有 fake client/executor 结构：增加 `proven_saved_execution(stages)` helper，返回 `ok=true`、`status=saved`、`save_clicked=true`、`publish_clicked=false`、`page_closed=true`、`close_method="playwright_page_close"` 和现有测试要求的两项保存证据；`run_worker_with_execution(execution)` 使用现有 `_client()`/临时 upload root 创建单次 Worker 并返回 `run_once()`。

另测促销 unresolved 返回 `needs_confirmation`，不能保存为 warning 终态。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_automation_worker.py -k "warnings or promotion" -q
```

预期：FAIL，Worker 只有 saved/failed/blocked。

- [ ] **步骤 3：定义阶段结果规范化函数**

在 `storage.py` 增加 `_normalize_execution_stages()`，每项仅保留：

```python
status, started_at, finished_at, code, message, details, evidence
```

去除 token、完整 Mabang URL、API key 和未去敏 traceback。允许状态固定为 `succeeded|skipped|warning|failed|needs_confirmation`。

- [ ] **步骤 4：扩展任务状态机**

终态加入 `saved_with_warnings`。`finish_automation_job()` 只有在 `save_clicked=true`、`publish_clicked=false`、保存证据有效时接受 saved/saved_with_warnings。关闭页面失败记录阶段 warning，但保存 token 不可再次消费。

- [ ] **步骤 5：更新 Worker 判断**

`is_proven_saved_execution(execution)` 保持严格证据检查；若证据成立且阶段含 warning/skipped-media，则 final_status 为 `saved_with_warnings`，否则为 `saved`。两者都清理 pending completion 并继续轮询下一任务。

- [ ] **步骤 6：更新任务结果 UI**

任务详情按规格的 11 个阶段显示。黄色用于 warning/skipped，红色用于 failed/needs_confirmation。显示中文错误点、水印模板或视频文件和受影响图片范围，不直接渲染原始异常。

- [ ] **步骤 7：运行测试**

```powershell
python -m pytest tests/test_automation_worker.py tests/test_product_line_flow.py tests/test_automation_status_ui.py tests/test_store_template_workflow_ui.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```powershell
git add services/api/storage.py services/api/main.py scripts/automation_worker.py apps/web/app.js apps/web/index.html apps/web/styles.css tests/test_automation_worker.py tests/test_product_line_flow.py tests/test_automation_status_ui.py tests/test_store_template_workflow_ui.py
git commit -m "feat: record automation stages and warning saves"
```

---

### 任务 9：实现运行设置服务和设置页

**文件：**
- 创建：`services/api/runtime_configuration.py`
- 创建：`tests/test_runtime_configuration.py`
- 修改：`services/api/settings.py`
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 修改：`services/api/db_store.py`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_api_boundaries.py`
- 测试：`tests/test_store_template_workflow_api.py`

- [ ] **步骤 1：编写配置安全和生效策略测试**

```python
def test_publish_policy_is_read_only():
    config = public_runtime_configuration(default_configuration())
    assert config["safety"] == {"save_draft_only": True, "publish_allowed": False, "editable": False}


def test_worker_connection_change_sets_restart_required():
    result = update_runtime_configuration(default_configuration(), {"worker": {"cdp_url": "http://127.0.0.1:9223"}})
    assert result["worker"]["restart_required"] is True


def test_publish_permission_change_is_rejected():
    with pytest.raises(ValueError, match="禁止修改发布策略"):
        update_runtime_configuration(default_configuration(), {"safety": {"publish_allowed": True}})
```

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_runtime_configuration.py -q
```

预期：FAIL，新模块不存在。

- [ ] **步骤 3：实现配置模型**

配置分组固定为 `ai`、`mabang`、`worker`、`nas`、`store_collection`、`safety`。AI 密钥继续走现有 `ai_model_connections` 加密存储，不进入 runtime config。`settings.py` 环境变量只作为首次默认值。

- [ ] **步骤 4：增加设置 API**

```text
GET  /api/settings
PUT  /api/settings
POST /api/settings/nas/test
POST /api/settings/mabang/test
POST /api/ai/model-connections/{id}/test
```

连接测试返回 `ok`、中文消息和时间，不返回 traceback、会话 token 或密钥。

- [ ] **步骤 5：加入持久化集合**

`storage.default_store()` 和 `db_store.py` 增加 `runtime_configuration`。更新使用 `locked_store()` 原子写入；AI/NAS 后续操作即时读取最新配置，Worker/CDP 修改标记重启但不停止当前进程。

- [ ] **步骤 6：构建设置页**

设置页左侧分组、右侧详情。实现 AI 模型连接列表/测试/设默认、马帮连接状态、Worker 状态、NAS 路径检测、按店铺采集更新时间和不可编辑安全策略。密钥输入保存后立即清空，永不回显。

- [ ] **步骤 7：运行安全与 API 测试**

```powershell
python -m pytest tests/test_runtime_configuration.py tests/test_api_boundaries.py tests/test_store_template_workflow_api.py tests/test_credential_crypto.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```powershell
git add services/api/runtime_configuration.py services/api/settings.py services/api/storage.py services/api/main.py services/api/db_store.py apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_runtime_configuration.py tests/test_api_boundaries.py tests/test_store_template_workflow_api.py
git commit -m "feat: add runtime settings workspace"
```

---

### 任务 10：实现真实数据首页和动态“继续工作”

**文件：**
- 创建：`services/api/operations_dashboard.py`
- 创建：`tests/test_operations_dashboard.py`
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 修改：`apps/web/index.html`
- 修改：`apps/web/app.js`
- 修改：`apps/web/styles.css`
- 测试：`tests/test_store_template_workflow_ui.py`
- 测试：`tests/test_automation_status_ui.py`

- [ ] **步骤 1：编写聚合值和下一步优先级测试**

```python
def test_dashboard_counts_only_persisted_real_records():
    result = build_dashboard(store_with_products_templates_copies_and_jobs(), now=NOW)
    assert result["metrics"]["base_products"] == 2
    assert result["metrics"]["failed_jobs"] == 1
    assert "sales" not in result["metrics"]
    assert "token_balance" not in result["metrics"]


@pytest.mark.parametrize(("state_factory", "action"), [
    (with_needs_confirmation_job, "open_automation_job"),
    (with_unchecked_listing_copy, "open_listing_copy"),
    (with_ready_job_and_offline_worker, "open_automation"),
    (with_base_product_only, "copy_base_product"),
    (empty_store, "create_base_product"),
])
def test_next_work_action_priority(state_factory, action):
    assert build_dashboard(state_factory(), now=NOW)["next_action"]["type"] == action
```

在 `tests/test_operations_dashboard.py` 定义纯字典 helper：`empty_store()` 返回包含所有集合空列表的 `storage.default_store()` 深拷贝；其余五个 helper 只向该字典追加最少的 base product、listing copy、job 和 worker heartbeat 记录。每个 helper 都返回新对象，job 使用固定 `created_at` 以验证排序。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_operations_dashboard.py -q
```

预期：FAIL，新模块不存在。

- [ ] **步骤 3：实现纯聚合服务和 API**

`build_dashboard(data, now)` 不访问网络，只从存储快照计算 metrics、current_batch、connections、recent_events、recent_results、stale_store_catalogs 和 next_action。

增加：

```text
GET /api/dashboard
```

- [ ] **步骤 4：彻底改为顶部导航**

移除 `<aside class="sidebar">`，建立固定顶部导航：`首页`、`商品维护`、`店铺模板`、`刊登副本`、`自动化任务`、`文件管理`、`设置`。将现有组合页面拆成独立 view，但复用现有 DOM ID 和加载函数，避免重写业务逻辑。

- [ ] **步骤 5：实现桌面首页布局**

首页包含顶部真实指标、当前批次与“继续工作”、运行日志、右侧连接/采集状态和最近保存结果。按钮使用 API 返回的 action type/id 导航，不直接调用创建任务或保存 API。

- [ ] **步骤 6：应用视觉系统**

CSS 变量至少包括：

```css
--workspace-bg: #eef3f9;
--surface: #ffffff;
--primary: #3978e6;
--warning: #d97706;
--danger: #c43d3d;
--line: #d9e2ee;
```

页面 `min-width: 1180px`；不添加手机断点，不缩放字体随视口变化。面板圆角不超过 8px，维护页保持紧凑表格和表单。

- [ ] **步骤 7：运行 UI 契约测试**

```powershell
python -m pytest tests/test_operations_dashboard.py tests/test_store_template_workflow_ui.py tests/test_automation_status_ui.py tests/test_product_maintenance_reorg.py -q
```

预期：全部 PASS；新增断言左侧 sidebar 不存在、7 个顶部导航存在、首页不包含销售额或 Token 余额。

- [ ] **步骤 8：启动本地服务并做桌面截图验证**

```powershell
python -m services.api.main
```

使用 Playwright 在 1440×900 和 1920×1080 截图，检查顶部导航、首页右栏、设置页和模板编辑器无重叠、无截断、无空白主区。浏览器控制台不得有错误。

- [ ] **步骤 9：提交**

```powershell
git add services/api/operations_dashboard.py services/api/storage.py services/api/main.py apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_operations_dashboard.py tests/test_store_template_workflow_ui.py tests/test_automation_status_ui.py tests/test_product_maintenance_reorg.py
git commit -m "feat: rebuild desktop listing operations workspace"
```

---

### 任务 11：迁移旧数据并验证幂等性

**文件：**
- 创建：`scripts/migrate_store_configuration_media.py`
- 创建：`tests/test_store_configuration_media_migration.py`
- 修改：`services/api/db_store.py`
- 修改：`tests/test_store_template_workflow_migration.py`

- [ ] **步骤 1：编写迁移 dry-run 和幂等测试**

```python
def test_migration_preserves_unknown_fields_and_does_not_mutate_on_dry_run(tmp_path):
    before = legacy_store_with_unknown_fields()
    result = migrate_store(deepcopy(before), now=NOW, dry_run=True)
    assert before == legacy_store_with_unknown_fields()
    assert result["preview"]["templates_updated"] == 1


def test_migration_is_idempotent():
    once = migrate_store(legacy_store(), now=NOW, dry_run=False)["data"]
    twice = migrate_store(deepcopy(once), now=NOW, dry_run=False)["data"]
    assert twice == once
```

在 `tests/test_store_configuration_media_migration.py` 定义 `legacy_store()`，返回一个含旧模板、旧基础商品、旧副本和历史 saved job 的最小字典；`legacy_store_with_unknown_fields()` 在其顶层及模板中各增加一个 `future_field`，用于证明迁移保留未知数据。

- [ ] **步骤 2：运行测试确认失败**

```powershell
python -m pytest tests/test_store_configuration_media_migration.py -q
```

预期：FAIL，迁移脚本不存在。

- [ ] **步骤 3：实现迁移规则**

迁移必须：

- 为旧模板补两类空水印和 `video_enabled=true`。
- 将旧本地尺码图映射为 `size_chart_source=upload`，不复制或删除文件。
- 为旧基础商品/副本补 `video.validation_status=missing`。
- 重新计算未解析促销，不能继续标记 expired。
- 新增所需集合但保留未知字段和历史任务终态。
- 支持 `--dry-run`、写前备份和幂等执行。

- [ ] **步骤 4：运行迁移测试**

```powershell
python -m pytest tests/test_store_configuration_media_migration.py tests/test_store_template_workflow_migration.py tests/test_category_profile_migration.py -q
```

预期：全部 PASS。

- [ ] **步骤 5：对当前数据只运行 dry-run**

```powershell
python scripts/migrate_store_configuration_media.py --dry-run
```

预期：输出更新对象数量、警告和备份目标；`git status --short data` 不出现数据修改。

- [ ] **步骤 6：提交**

```powershell
git add scripts/migrate_store_configuration_media.py services/api/db_store.py tests/test_store_configuration_media_migration.py tests/test_store_template_workflow_migration.py
git commit -m "feat: migrate store media and configuration data"
```

---

### 任务 12：全量回归、真实马帮草稿试运行和生产门禁

**文件：**
- 修改：仅修复本任务发现的回归文件
- 参考：`docs/superpowers/specs/2026-07-19-store-configuration-media-dashboard-design.md`
- 参考：`docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`

- [ ] **步骤 1：运行完整自动化测试**

```powershell
python -m pytest -q
node --test tests/mabang_playwright_executor.test.cjs
```

预期：全部 PASS，测试数量不少于任务开始时的 254，并包含本计划新增测试。

- [ ] **步骤 2：运行静态安全扫描**

```powershell
rg -n "保存并刊登|提交审核|publish_clicked\s*[:=]\s*true|allow_publish\s*[:=]\s*true" scripts services apps
git diff --check
```

预期：匹配只出现在禁止列表、提示文字或测试断言，不存在可执行发布分支；diff check 通过。

- [ ] **步骤 3：验证桌面 UI**

启动服务后在 1440×900 与 1920×1080 检查：顶部导航、首页、设置、另存模板、采集预览、两类水印、视频和尺码图。保存截图到 `storage/screenshots/qa/`，该目录作为运行证据，不加入 Git。

- [ ] **步骤 4：在真实马帮页面执行只读采集**

选择一个测试店铺，确认采集结果包含：真实店铺 ID、类目、已选必填值、完整促销目录、完整水印目录、交易信息和尺码图。确认采集阶段没有保存、应用水印或发布请求。

- [ ] **步骤 5：执行一个警告路径草稿任务**

使用一个无视频或 invalid 视频、同时配置一类不存在水印的副本。预期：视频和该类水印被记录为 warning/skipped，另一类水印继续，马帮草稿取得保存证据，任务终态为 `saved_with_warnings`，页面关闭并继续轮询。

- [ ] **步骤 6：执行一个促销未解析路径**

使用无法解析的目标店铺活动。预期：任务进入 `needs_confirmation`，不点击保存，不使用替代活动，不发布。

- [ ] **步骤 7：执行一个全成功草稿任务**

使用有效促销、两类有效水印、合规 MP4 和尺码图。核对主图只有主图水印，第 2 张以后只有产品图水印；视频、SKU、价格、库存、重量和包裹尺寸正确。保存成功后 `publish_clicked=false`，页面关闭，Worker 可领取下一任务。

- [ ] **步骤 8：记录生产门禁结论**

在运行日志中记录三条真实任务 ID、保存证据、阶段结果和截图路径。任何以下条件未满足都不得宣布可生产：

- 完整测试通过。
- 三条真实路径行为与预期一致。
- 没有发布点击。
- 保存后页面关闭且 Worker 继续领取。
- 首页和设置页显示真实状态，无模拟数据。

- [ ] **步骤 9：确认回归阶段没有遗留源码改动**

```powershell
git status --short
```

预期：任务 12 没有未提交源码改动。若步骤 1–7 发现回归，立即回到拥有该文件的任务，先增加失败测试、修复、运行该任务完整测试，并使用该任务规定的精确文件清单提交；之后从任务 12 步骤 1 重新运行全量门禁。不得在这里批量暂存未知文件或创建空 commit。

---

## 完成判定

只有以下证据同时存在，本计划才完成：

1. 规格第 4–18 节分别有对应任务和通过测试。
2. `python -m pytest -q` 与 Node 执行器测试通过。
3. 1440×900、1920×1080 桌面页面截图无重叠和截断。
4. 真实马帮完成警告保存、促销人工确认、全成功保存三条路径。
5. 所有保存证据均显示 `publish_clicked=false`。
6. 保存成功后关闭页面并继续下一任务。
7. `git status --short` 中只剩明确的运行产物或用户未要求提交的改动。

## 规格覆盖矩阵

| 规格章节 | 实施任务 | 完成证据 |
| --- | --- | --- |
| 4. 促销活动语义 | 任务 2、7、8、12 | 状态单测、执行解析测试、人工确认实测 |
| 5. 店铺模板另存 | 任务 3、11 | 同站/跨站复制矩阵、迁移测试 |
| 6. 采集后导航 | 任务 5、10 | UI 契约测试、桌面浏览器验证 |
| 7. 视频 | 任务 6、7、8、12 | 边界单测、任务快照、警告保存实测 |
| 8. 尺码图 | 任务 1、5、11 | 资产导入测试、快照不变性测试 |
| 9. 马帮水印 | 任务 4、5、7、12 | 目录夹具、ID 匹配、图片范围实测 |
| 10. 自动化结果 | 任务 7、8、12 | Python/Node 契约、保存证据、终态测试 |
| 11. 桌面工作台 | 任务 10、12 | 1440×900 与 1920×1080 截图验证 |
| 12. 设置页面 | 任务 9、10 | API 安全测试、UI 契约与连接测试 |
| 13–14. 数据/API | 任务 2–11 | 专项 API、存储、迁移测试 |
| 15–16. 错误/兼容 | 任务 2、6、8、11 | 错误矩阵单测、dry-run、幂等测试 |
| 17–18. 测试/验收 | 任务 12 | 全量 pytest、Node 测试、三条真实马帮路径 |
