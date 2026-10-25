# 马帮尺码图与水印采集实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将马帮编辑页的已上传尺码图、尺码图模板目录和水印目录可靠导入店铺模板，编辑模板后不会丢失这些数据。

**架构：** 采集脚本只返回页面数据；快照层标准化两类尺码图；确认导入将上传图片下载为资产、按店铺保存尺码图和水印目录。模板 UI 只有上传/模板两种来源，自动化据此上传图片或按稳定 ID 选择马帮模板。

**技术栈：** Python 3、JSON 存储、原生 JavaScript、Browser-Use/CDP、pytest。

---

## 文件与职责

- `scripts/mabang_page_import_capture.js`：读取当前上传尺码图、尺码图模板列表和水印菜单。
- `scripts/browser_use_runner.py`：只读采集时临时展开菜单；刊登时按来源应用尺码图。
- `services/api/mabang_import_snapshot.py`：标准化采集快照。
- `services/api/mabang_page_import.py`：确认导入并原子写入资产和目录。
- `services/api/storage.py`：模板、刊登副本、浏览器输入的数据持久化。
- `apps/web/index.html`、`apps/web/app.js`：两种来源切换、缩略图、导入预览。
- `tests/test_browser_use_runner.py`、`tests/test_mabang_import_snapshot.py`、`tests/test_mabang_page_import.py`、`tests/test_store_template_workflow_ui.py`、`tests/test_store_template_workflow_api.py`、`tests/test_product_line_flow.py`：行为回归测试。

### 任务 1：持久化已上传尺码图，防止重新保存丢失

**文件：** `tests/test_mabang_page_import.py`、`tests/test_store_template_workflow_api.py`、`services/api/mabang_page_import.py`、`services/api/storage.py`。

- [ ] **步骤 1：写失败测试**

```python
def test_update_store_template_preserves_imported_size_chart_media_when_payload_omits_it():
    created = storage.create_store_template({
        **_template_payload(),
        "size_chart_source": "upload",
        "size_chart_asset_id": "chart-1",
        "size_chart_preview_url": "/api/uploads/size-chart/chart-1",
        "default_size_chart_path": "C:/charts/chart-1.png",
    })
    updated = storage.update_store_template(created["id"], {"template_name": "已编辑"})
    assert updated["size_chart_asset_id"] == "chart-1"
    assert updated["size_chart_preview_url"] == "/api/uploads/size-chart/chart-1"
```

在已有 `test_confirm_page_import_imports_a_captured_size_chart` 追加：`size_chart_source == "upload"`、预览 URL 不为空、模板 ID 为空。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_mabang_page_import.py::test_confirm_page_import_imports_a_captured_size_chart tests/test_store_template_workflow_api.py::test_update_store_template_preserves_imported_size_chart_media_when_payload_omits_it -v`

预期：FAIL，字段未持久化或更新时被清空。

- [ ] **步骤 3：实现最小持久化改动**

确认导入上传图片时写入：

```python
payload.update({
    "size_chart_source": "upload",
    "size_chart_asset_id": size_chart["id"],
    "size_chart_preview_url": size_chart["preview_url"],
    "default_size_chart_path": size_chart["file_path"],
    "mabang_size_chart_template_id": "",
    "mabang_size_chart_template_name": "",
})
```

在 `_normalize_store_template_payload()` 保留 `size_chart_preview_url`、`mabang_size_chart_template_id`、`mabang_size_chart_template_name`。在 `update_store_template()` 把调用方未提交的媒体字段从旧模板合并进 `normalize_payload`，不得使用空字符串覆盖。

- [ ] **步骤 4：运行并提交**

运行：`pytest tests/test_mabang_page_import.py::test_confirm_page_import_imports_a_captured_size_chart tests/test_store_template_workflow_api.py::test_update_store_template_preserves_imported_size_chart_media_when_payload_omits_it -v`

预期：PASS。

提交：`git add services/api/mabang_page_import.py services/api/storage.py tests/test_mabang_page_import.py tests/test_store_template_workflow_api.py`，随后 `git commit -m "fix: 保留马帮导入的尺码图"`。

### 任务 2：采集真实的尺码图模板和水印菜单

**文件：** `tests/test_browser_use_runner.py`、`scripts/mabang_page_import_capture.js`、`scripts/browser_use_runner.py`。

- [ ] **步骤 1：写失败测试**

```python
def test_mabang_capture_reads_watermark_menu_options_and_thumbnails():
    capture = _run_mabang_capture({"watermarkMenu": {"options": [
        {"watermarkId": "wm-main", "watermarkName": "主图角标", "imgUrl": "https://cdn.example/main.png"},
        {"watermarkId": "wm-detail", "watermarkName": "产品图角标", "imgUrl": "https://cdn.example/detail.png"},
    ]}})
    assert capture["watermark_catalog_loaded"] is True
    assert capture["watermark_templates"] == [
        {"template_id": "wm-main", "name": "主图角标", "preview_url": "https://cdn.example/main.png"},
        {"template_id": "wm-detail", "name": "产品图角标", "preview_url": "https://cdn.example/detail.png"},
    ]
```

保留现有“模板无预览 URL 仍采集 ID/名称”的测试。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_browser_use_runner.py -k "watermark or size_chart" -v`

预期：新水印测试 FAIL，已有测试 PASS。

- [ ] **步骤 3：实现只读多来源读取器**

在 `mabang_page_import_capture.js` 统一水印字段：

```javascript
const normalizeWatermark = (item) => ({
  template_id: clean(item?.template_id ?? item?.templateId ?? item?.watermark_id ?? item?.watermarkId ?? item?.id ?? item?.value),
  name: clean(item?.name ?? item?.template_name ?? item?.templateName ?? item?.watermark_name ?? item?.watermarkName ?? item?.label),
  preview_url: clean(item?.preview_url ?? item?.previewUrl ?? item?.image_url ?? item?.imageUrl ?? item?.img_url ?? item?.imgUrl ?? item?.url),
});
```

读取菜单组件 `options`、`watermarkMenu.options`、`watermarkList` 和可见菜单 DOM，并按 `template_id` 去重。仅有有效项时设置 `watermark_catalog_loaded: true`。

在 `browser_use_runner.py` 保留现有临时展开/恢复逻辑，等待“选项出现或明确暂无内容”。采集脚本不得新增保存、发布、刊登、提交或网络写入。

- [ ] **步骤 4：运行并提交**

运行：`pytest tests/test_browser_use_runner.py -k "watermark or size_chart or page_import_runner" -v`

预期：PASS。

提交：`git add scripts/mabang_page_import_capture.js scripts/browser_use_runner.py tests/test_browser_use_runner.py`，随后 `git commit -m "fix: 采集马帮水印与尺码图模板目录"`。

### 任务 3：确认导入目录，并保留模板引用

**文件：** `tests/test_mabang_import_snapshot.py`、`tests/test_mabang_page_import.py`、`services/api/mabang_import_snapshot.py`、`services/api/mabang_page_import.py`。

- [ ] **步骤 1：写失败测试**

```python
def test_confirm_page_import_does_not_replace_catalogs_when_mabang_did_not_load_them():
    data = _store_with_existing_catalogs()
    stage_page_import(data, "run-1", _capture_without_catalogs(), now="2026-07-29T00:01:00Z")
    confirm_page_import(data, "run-1", now="2026-07-29T00:02:00Z")
    assert _catalog_ids(data) == {"watermark": ["old-wm"], "size_chart": ["old-chart"]}
```

同时断言 `snapshot_template_payload()` 对有上传图的快照返回 `size_chart_source == "upload"`。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py -k "size_chart or watermark" -v`

预期：新测试 FAIL。

- [ ] **步骤 3：实现目录写入边界**

在 `snapshot_template_payload()` 添加：

```python
"size_chart_source": "upload" if _text((snapshot.get("size_chart") or {}).get("url")) else "template",
"mabang_size_chart_template_id": "",
"mabang_size_chart_template_name": "",
```

`confirm_page_import()` 仅在 `*_catalog_loaded is True` 时替换对应店铺目录；无预览的尺码图模板保持 `metadata_only`。`_confirmed_result()` 返回 `watermarks` 与 `size_chart_templates`，供前端显示真实保存状态。

- [ ] **步骤 4：运行并提交**

运行：`pytest tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py -k "size_chart or watermark" -v`

预期：PASS。

提交：`git add services/api/mabang_import_snapshot.py services/api/mabang_page_import.py tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py`，随后 `git commit -m "feat: 保存马帮媒体目录"`。

### 任务 4：模板页面仅显示上传图片和选择模板

**文件：** `tests/test_store_template_workflow_ui.py`、`apps/web/index.html`、`apps/web/app.js`。

- [ ] **步骤 1：写失败 UI 契约**

```python
def test_store_template_has_two_size_chart_sources_and_store_scoped_catalogs():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert 'value="upload"' in html
    assert 'value="template"' in html
    assert 'value="mabang_capture"' not in html
    assert "mabang_size_chart_template_id" in script
    assert "size_chart_preview_url" in script
```

再断言导入预览存在 `mabang-import-size-chart-upload`、`mabang-import-size-chart-templates`、`mabang-import-watermarks`。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_store_template_workflow_ui.py -k "size_chart or watermark or mabang" -v`

预期：FAIL，遗留第三来源仍在。

- [ ] **步骤 3：实现切换、缩略图与回填**

从 `index.html` 删除 `<option value="mabang_capture">马帮采集</option>`。`renderSizeChartSource()` 只接受 `upload`、`template`；上传模式只显示上传控件/已导入缩略图，模板模式只显示下拉/模板缩略图。

`openStoreTemplateEditor()` 需初始化 `state.sizeChartAsset`：

```javascript
state.sizeChartAsset = template.size_chart_asset_id ? {
  id: template.size_chart_asset_id,
  preview_url: template.size_chart_preview_url || `/api/uploads/size-chart/${encodeURIComponent(template.size_chart_asset_id)}`,
} : null;
```

`renderSizeChartTemplateOptions()` 列出该店铺全部采集模板；无预览项标注“仅马帮模板”且可选择。保存时上传来源提交资产；模板来源提交模板 ID、名称、可用资产和预览。

- [ ] **步骤 4：运行并提交**

运行：`pytest tests/test_store_template_workflow_ui.py -k "size_chart or watermark or mabang" -v`

预期：PASS。

提交：`git add apps/web/index.html apps/web/app.js tests/test_store_template_workflow_ui.py`，随后 `git commit -m "feat: 支持马帮尺码图模板选择"`。

### 任务 5：将模板选择传递到刊登副本和浏览器自动化

**文件：** `tests/test_product_line_flow.py`、`tests/test_browser_use_runner.py`、`services/api/storage.py`、`scripts/browser_use_runner.py`。

- [ ] **步骤 1：写失败测试**

```python
def test_listing_copy_snapshots_a_selected_mabang_size_chart_template(tmp_path: Path) -> None:
    copy = _create_listing_copy(tmp_path, {
        "size_chart_source": "template",
        "mabang_size_chart_template_id": "106239228",
        "mabang_size_chart_template_name": "男装T恤",
    })
    job = storage.create_automation_job_from_listing_copy(copy["id"])
    payload = storage.build_automation_payload(job["id"])
    assert payload["browser_inputs"]["size_chart"] == {
        "source": "template", "template_id": "106239228", "template_name": "男装T恤", "image_path": ""
    }
```

再写任务计划测试：模板来源没有上传文件时仍可构建任务，且 `task["form"]["size_chart"]["template_id"] == "106239228"`。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_product_line_flow.py::test_listing_copy_snapshots_a_selected_mabang_size_chart_template tests/test_browser_use_runner.py -k "size_chart_template" -v`

预期：FAIL，统一 `size_chart` 输入尚不存在。

- [ ] **步骤 3：实现来源感知输入与填写**

在 `storage.py` 的刊登副本与 `_build_browser_inputs()` 传递：

```python
"size_chart": {
    "source": _normalize_size_chart_source(listing_copy.get("size_chart_snapshot", {}).get("source")),
    "template_id": str(listing_copy.get("mabang_size_chart_template_id") or ""),
    "template_name": str(listing_copy.get("mabang_size_chart_template_name") or ""),
    "image_path": size_chart_image_path,
},
```

在 `browser_use_runner.py` 中，只有 `upload` 校验/上传图片；`template` 切换马帮“选择模板”并以稳定 ID 匹配。ID 不存在时返回 `mabang_size_chart_template_unresolved` 并停止，禁止点击保存；成功后沿用既有仅保存草稿流程。

- [ ] **步骤 4：运行并提交**

运行：`pytest tests/test_product_line_flow.py::test_listing_copy_snapshots_a_selected_mabang_size_chart_template tests/test_browser_use_runner.py -k "size_chart_template" -v`

预期：PASS。

提交：`git add services/api/storage.py scripts/browser_use_runner.py tests/test_product_line_flow.py tests/test_browser_use_runner.py`，随后 `git commit -m "feat: 自动化选择马帮尺码图模板"`。

### 任务 6：端到端验证与交付

**文件：** 无生产代码修改。

- [ ] **步骤 1：运行受影响测试和全量回归**

运行：`pytest tests/test_mabang_import_snapshot.py tests/test_mabang_page_import.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py tests/test_browser_use_runner.py tests/test_product_line_flow.py -q`，然后运行 `pytest -q`。

预期：两次均全部 PASS。

- [ ] **步骤 2：进行真实页面只读验收**

保留一个已登录马帮商品编辑页；采集并确认导入；检查预览的上传尺码图、模板目录、水印目录状态；检查模板页的两个来源和两个水印缩略图；刷新、再次保存；创建刊登副本运行一次模板选择路径。

预期：不点击马帮保存、保存并刊登、刊登、发布或提交。

- [ ] **步骤 3：重启服务并确认测试地址**

运行项目既有启动命令；执行 `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/api/health`。

预期：响应 JSON 含 `"ok": true`，服务地址为 `http://127.0.0.1:8787/`。

## 计划自检

- 规格中的上传尺码图、模板目录、水印目录、保存不丢失、自动化选择、只读安全均有测试和实现任务。
- 没有 TODO、待定或“适当处理”类占位语。
- 跨层字段固定为 `size_chart_source`、`size_chart_asset_id`、`size_chart_preview_url`、`mabang_size_chart_template_id`、`mabang_size_chart_template_name`。
