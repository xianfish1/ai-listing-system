# Product Maintenance Reorganization Implementation Plan

> **For AI agents:** Required sub-skill: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox syntax (`- [ ]`) to track progress.

**Goal:** Reorganize the console so product maintenance uses base product cards and store template maintenance, automation owns store listing copies and jobs, file management replaces the asset table, and store templates own variation and SKU generation rules.

**Architecture:** Preserve the current Python HTTP API and static web app. Add small backend fields and routes for folder selection, `main_sku`, variation rules, and SKU templates; then reshape `apps/web/index.html`, `apps/web/app.js`, and `apps/web/styles.css` around the new page boundaries.

**Tech Stack:** Python standard-library HTTP API in `services/api/main.py`, JSON/Postgres store abstraction in `services/api/storage.py`, NAS scanning in `workers/asset_scanner/scanner.py`, static HTML/CSS/JS in `apps/web`, pytest regression tests.

---

## Files

- Modify `services/api/storage.py`: add `main_sku` recognition/defaulting, store template `variation_rule` and `sku_template`, and SKU generation from templates.
- Modify `services/api/main.py`: add a local folder picker endpoint for `从 NAS 创建`.
- Modify `workers/asset_scanner/scanner.py` only if folder name parsing cannot already derive the code used by storage.
- Modify `apps/web/index.html`: replace the single product page with `商品维护`, `自动化`, and `文件管理` views.
- Modify `apps/web/app.js`: render base products as cards, open the maintenance dialog, drive folder selection, move listing copies to automation, and render file management.
- Modify `apps/web/styles.css`: add product card grid, modal, animated image sorting, tab bar, and file manager styles.
- Modify `tests/test_product_line_flow.py`: cover `main_sku`, SKU template generation, and store template defaults.
- Create `tests/test_product_maintenance_reorg.py`: cover folder picker route behavior where it can be tested without opening a UI.
- Modify `README.md`: update the operator workflow names.
- Modify `CONTEXT.md`: keep the domain vocabulary aligned with the implementation.

## Task 1: Add Main SKU and Store Template Rule Defaults

**Files:**
- Modify: `services/api/storage.py`
- Modify: `tests/test_product_line_flow.py`

- [ ] **Step 1: Add failing tests for main SKU recognition**

Append this test near the existing base product creation tests:

```python
def test_create_base_product_recognizes_main_sku_from_folder_name(tmp_path: Path) -> None:
    folder = tmp_path / "AL0002 我的世界"
    folder.mkdir()
    for name in ["main.jpg", "white.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder(
        {"root_path": str(folder), "category": "POD Mens T-Shirt"}
    )

    assert product["group_id"] == "AL0002"
    assert product["main_sku"] == "AL0002"
    assert product["product_name_cn"] == "我的世界"
    assert product["product_images"][0] == "main.jpg"
```

- [ ] **Step 2: Add failing tests for template SKU rules**

Add this test near `test_copy_base_product_to_multiple_store_templates_creates_listing_copies`:

```python
def test_store_template_sku_template_generates_listing_skus(tmp_path: Path) -> None:
    folder = tmp_path / "YSC008P 童装"
    folder.mkdir()
    for name in ["main.jpg", "White.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Kids T-Shirt"})
    template = storage.create_store_template(
        {
            "store_name": "POD PH",
            "template_name": "Kids Tee",
            "product_line": "POD Kids T-Shirt",
            "marketplace_category_path": "Baby & Kids Fashion > Tops > T-Shirts",
            "normal_price": 89,
            "sale_price": 69,
            "sizes": ["110"],
            "variation_rule": "color_size",
            "sku_template": "{main_sku}_{color}||{size}",
        }
    )

    result = storage.copy_base_product_to_store_templates(base["id"], [template["id"]])
    skus = storage.list_listing_skus(result["created"][0]["id"])

    assert skus[0]["sku_code"] == "YSC008P_White||110"
```

- [ ] **Step 3: Run the new tests and confirm they fail**

Run:

```powershell
python -m pytest tests/test_product_line_flow.py::test_create_base_product_recognizes_main_sku_from_folder_name tests/test_product_line_flow.py::test_store_template_sku_template_generates_listing_skus -q
```

Expected: both tests fail because `main_sku`, `variation_rule`, and `sku_template` are not fully implemented.

- [ ] **Step 4: Implement folder-name SKU parsing**

In `services/api/storage.py`, add helpers near the existing `_derive_group_from_path` helpers:

```python
def _derive_main_sku_from_folder_name(value: str) -> str:
    name = Path(str(value or "")).name
    match = re.match(r"^([A-Za-z]{1,8}\d{2,}[A-Za-z]?)\b", name.strip())
    return match.group(1).upper() if match else ""


def _derive_product_name_from_folder_name(value: str, main_sku: str) -> str:
    name = Path(str(value or "")).name.strip()
    if main_sku and name.upper().startswith(main_sku.upper()):
        return re.sub(r"^\S+\s*", "", name).strip() or main_sku
    return name
```

Update `create_base_product_from_folder()` so it sets:

```python
main_sku = _derive_main_sku_from_folder_name(str(root_path)) or group_id
base_name = _derive_product_name_from_folder_name(str(root_path), main_sku) or _derive_product_name(group_id, assets)
base_product["main_sku"] = main_sku
```

- [ ] **Step 5: Normalize old base products**

In `list_base_products_with_summary()`, ensure old records have a display-safe value:

```python
product["main_sku"] = product.get("main_sku") or product.get("group_id") or ""
```

In `update_base_product()`, add `"main_sku"` to `editable_fields` and include it in `changed_sync`.

- [ ] **Step 6: Add store template rule defaults**

In `create_store_template()` and `update_store_template()`, normalize:

```python
template["variation_rule"] = str(payload.get("variation_rule") or template.get("variation_rule") or "color_size").strip()
template["sku_template"] = str(payload.get("sku_template") or template.get("sku_template") or "{main_sku}_{color}||{size}").strip()
```

In `list_store_templates()`, ensure returned templates also include defaults for old data.

- [ ] **Step 7: Generate SKU codes from the template**

Replace the hard-coded SKU code assembly in `_generate_listing_skus()` with a helper:

```python
def _format_listing_sku(template: dict[str, Any], base: dict[str, Any], color: str, size: str) -> str:
    sku_template = str(template.get("sku_template") or "{main_sku}_{color}||{size}")
    values = {
        "main_sku": base.get("main_sku") or base.get("group_id") or "",
        "color": color,
        "size": size,
    }
    return sku_template.format(**values)
```

Then call:

```python
sku_code = _format_listing_sku(template, base, color, size)
```

- [ ] **Step 8: Run the focused product-line tests**

Run:

```powershell
python -m pytest tests/test_product_line_flow.py -q
```

Expected: PASS.

## Task 2: Add a Local Folder Picker Route

**Files:**
- Modify: `services/api/main.py`
- Create: `tests/test_product_maintenance_reorg.py`

- [ ] **Step 1: Add a route-shape regression test**

Create `tests/test_product_maintenance_reorg.py` with:

```python
from __future__ import annotations

import json
from urllib import request

from tests.test_api_boundaries import _start_api


def test_folder_picker_route_can_return_unavailable_without_breaking_api() -> None:
    server, base_url = _start_api()
    try:
        req = request.Request(
            f"{base_url}/api/system/folder-picker",
            method="POST",
            data=json.dumps({"dry_run": True}).encode("utf-8"),
        )
        req.add_header("Content-Type", "application/json")
        with request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert "ok" in payload
    assert "path" in payload
```

- [ ] **Step 2: Run the new route test and confirm it fails**

Run:

```powershell
python -m pytest tests/test_product_maintenance_reorg.py::test_folder_picker_route_can_return_unavailable_without_breaking_api -q
```

Expected: FAIL with route not found.

- [ ] **Step 3: Implement the route**

In `services/api/main.py`, add this import:

```python
import tkinter
from tkinter import filedialog
```

If importing tkinter at module import time is risky in the test environment, import it inside the handler instead.

In `_handle_post()`, before unknown-route handling, add:

```python
if path == "/api/system/folder-picker":
    self._json(self._pick_local_folder(payload))
    return
```

Add a helper method on `ApiHandler`:

```python
def _pick_local_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("dry_run") is True:
        return {"ok": False, "path": "", "mode": "dry_run"}
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        selected = filedialog.askdirectory(
            title=str(payload.get("title") or "选择 NAS 商品文件夹"),
            initialdir=str(payload.get("initial_dir") or ""),
        )
        root.destroy()
        return {"ok": bool(selected), "path": selected or ""}
    except Exception as exc:
        return {"ok": False, "path": "", "error": str(exc), "mode": "unavailable"}
```

- [ ] **Step 4: Run the route test**

Run:

```powershell
python -m pytest tests/test_product_maintenance_reorg.py::test_folder_picker_route_can_return_unavailable_without_breaking_api -q
```

Expected: PASS.

## Task 3: Rebuild Page Navigation and Move Views

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Change left navigation**

In `apps/web/index.html`, replace the nav buttons with:

```html
<button class="nav-item active" data-view="dashboard">总览</button>
<button class="nav-item" data-view="product-maintenance">商品维护</button>
<button class="nav-item" data-view="automation">自动化</button>
<button class="nav-item" data-view="file-management">文件管理</button>
<button class="nav-item" data-view="plan">计划</button>
```

- [ ] **Step 2: Rename the product view container**

Rename:

```html
<section id="products" class="view">
```

to:

```html
<section id="product-maintenance" class="view">
```

Create a tab bar at the top of that section:

```html
<div class="subnav" role="tablist" aria-label="商品维护">
  <button class="subnav-item active" data-maintenance-tab="base-products">基础商品</button>
  <button class="subnav-item" data-maintenance-tab="store-templates">店铺模板</button>
</div>
```

- [ ] **Step 3: Split product maintenance content into tabs**

Wrap base product content in:

```html
<div id="base-products-tab" class="maintenance-tab active"></div>
```

Wrap store template content in:

```html
<div id="store-templates-tab" class="maintenance-tab"></div>
```

- [ ] **Step 4: Create automation section**

Move the store listing copy panel and browser automation job panel into:

```html
<section id="automation" class="view"></section>
```

Keep these table body IDs unchanged:

```html
<tbody id="store-listing-copy-table"></tbody>
<tbody id="automation-job-table"></tbody>
```

- [ ] **Step 5: Rename asset section**

Rename:

```html
<section id="assets" class="view">
```

to:

```html
<section id="file-management" class="view">
```

Change visible headings from `素材` to `文件管理`.

- [ ] **Step 6: Remove the legacy product list panel from visible UI**

Delete the panel containing:

```html
<tbody id="product-table"></tbody>
```

Keep the old `products` API and JS functions until all tests pass. They can remain unused.

- [ ] **Step 7: Update navigation helpers**

In `app.js`, change `newProduct()` so it no longer navigates to `products`. If the function remains for compatibility, make it show `product-maintenance`:

```javascript
showView("product-maintenance");
```

Add tab switching:

```javascript
function showMaintenanceTab(id) {
  document.querySelectorAll(".maintenance-tab").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".subnav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.maintenanceTab === id);
  });
  $(`${id}-tab`)?.classList.add("active");
}
```

In the click handler:

```javascript
const tab = target.closest("[data-maintenance-tab]");
if (tab instanceof HTMLElement && tab.dataset.maintenanceTab) {
  showMaintenanceTab(tab.dataset.maintenanceTab);
}
```

- [ ] **Step 8: Add CSS for tabs**

Add:

```css
.subnav {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 16px;
}

.subnav-item {
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  padding: 10px 12px;
}

.subnav-item.active {
  border-bottom-color: var(--blue);
  color: var(--blue);
}

.maintenance-tab {
  display: none;
}

.maintenance-tab.active {
  display: grid;
  gap: 16px;
}
```

## Task 4: Replace Base Product Table with Product Cards

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Replace the base product table container**

In the base product panel, replace the table wrapper with:

```html
<div id="base-product-cards" class="product-card-grid"></div>
```

Keep `base-product-table` removed from visible HTML.

- [ ] **Step 2: Render cards from base products**

Replace `renderBaseProducts()` with card rendering:

```javascript
function renderBaseProducts() {
  const target = $("base-product-cards");
  if (!target) return;
  target.innerHTML = "";
  if (!state.baseProducts.length) {
    target.innerHTML = `<div class="empty-state">暂无基础商品。点击“从 NAS 创建”选择一个商品文件夹。</div>`;
    return;
  }
  for (const product of state.baseProducts) {
    const card = document.createElement("article");
    card.className = "product-card";
    const mainAsset = assetsForBaseProduct(product).find((asset) => asset.file_name === product.main_image);
    const imageSrc = mainAsset ? `/api/assets/${encodeURIComponent(mainAsset.id || "")}/file` : "";
    card.innerHTML = `
      <div class="product-card-thumb">${imageSrc ? `<img src="${imageSrc}" alt="${escapeHtml(product.product_name_cn || "")}" />` : ""}</div>
      <div class="product-card-body">
        <strong>${escapeHtml(product.product_name_cn || product.base_name || product.main_sku || "")}</strong>
        <span>主 SKU：${escapeHtml(product.main_sku || product.group_id || "")}</span>
        <span>颜色：${escapeHtml((product.recognized_colors || []).length)} / 图片：${escapeHtml((product.product_images || []).length)}</span>
        <span>店铺副本：${escapeHtml(product.listing_copy_count || 0)}</span>
      </div>
      <div class="product-card-actions">
        <span class="status ${escapeHtml(product.status || "")}">${escapeHtml(product.status || "")}</span>
        <button data-maintain-base-product="${escapeHtml(product.id)}">维护</button>
      </div>
    `;
    target.appendChild(card);
  }
}
```

- [ ] **Step 3: Update base product selection behavior**

When a card is maintained, also set:

```javascript
state.selectedBaseProductId = productId;
```

Keep store template selection as the source for copying to templates.

- [ ] **Step 4: Add card CSS**

Add:

```css
.product-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.product-card {
  display: grid;
  grid-template-rows: 150px minmax(112px, auto) auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.product-card-thumb {
  background: #f3f5f8;
}

.product-card-thumb img {
  width: 100%;
  height: 150px;
  object-fit: cover;
  display: block;
}

.product-card-body {
  display: grid;
  gap: 5px;
  padding: 10px;
}

.product-card-body span {
  color: var(--muted);
  font-size: 12px;
}

.product-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  border-top: 1px solid var(--line);
}

.empty-state {
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 18px;
  color: var(--muted);
  background: #fbfcfe;
}
```

## Task 5: Build the Base Product Maintenance Dialog

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Add dialog markup**

Add this near the end of `<body>` before the script tag:

```html
<div id="base-maintenance-modal" class="modal-backdrop" hidden>
  <section class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="base-maintenance-title">
    <div class="panel-head">
      <h2 id="base-maintenance-title">基础商品维护</h2>
      <button id="close-base-maintenance-btn">关闭</button>
    </div>
    <div class="form-grid compact-form">
      <label>商品名<input id="base-maintenance-name" /></label>
      <label>主 SKU<input id="base-maintenance-main-sku" /></label>
      <label>状态<input id="base-maintenance-status" /></label>
      <label>尺码图<input id="base-size-chart-image" /></label>
    </div>
    <h3>产品图顺序</h3>
    <div id="base-image-order-list" class="image-order-list animated"></div>
    <h3>颜色图</h3>
    <div id="base-variant-image-map" class="variant-image-map"></div>
    <p id="base-image-editor-result" class="muted"></p>
    <div class="actions">
      <button id="save-base-image-order-btn">保存维护内容</button>
    </div>
  </section>
</div>
```

Remove or hide the old inline `base-image-editor` block so the IDs are not duplicated.

- [ ] **Step 2: Replace edit function with modal open function**

Rename `editBaseProductImages(productId)` to `openBaseProductMaintenance(productId)` and set the form fields:

```javascript
function openBaseProductMaintenance(productId) {
  const product = state.baseProducts.find((item) => item.id === productId);
  if (!product) return;
  state.selectedBaseProductId = productId;
  state.editingBaseProductId = productId;
  state.editingBaseProductImages = productImageAssets(product).map((asset) => asset.file_name);
  state.editingBaseVariantImageMap = { ...(product.variant_image_map || {}) };
  $("base-maintenance-name").value = product.product_name_cn || product.base_name || "";
  $("base-maintenance-main-sku").value = product.main_sku || product.group_id || "";
  $("base-maintenance-status").value = product.status || "base_ready";
  $("base-size-chart-image").value = product.size_chart_image || "";
  $("base-image-editor-result").textContent = "";
  $("base-maintenance-modal").hidden = false;
  renderBaseImageOrder();
  renderBaseVariantImageMap();
}
```

- [ ] **Step 3: Save all base maintenance fields**

Update `saveBaseImageOrder()` payload:

```javascript
body: JSON.stringify({
  product_name_cn: $("base-maintenance-name").value.trim(),
  base_name: $("base-maintenance-name").value.trim(),
  main_sku: $("base-maintenance-main-sku").value.trim(),
  status: $("base-maintenance-status").value.trim(),
  product_images: state.editingBaseProductImages,
  main_image: state.editingBaseProductImages[0] || "",
  size_chart_image: $("base-size-chart-image").value.trim(),
  variant_image_map: state.editingBaseVariantImageMap,
})
```

- [ ] **Step 4: Wire dialog click handlers**

In the click handler:

```javascript
const maintainButton = target.closest("[data-maintain-base-product]");
if (maintainButton instanceof HTMLElement && maintainButton.dataset.maintainBaseProduct) {
  openBaseProductMaintenance(maintainButton.dataset.maintainBaseProduct);
}
if (target.id === "close-base-maintenance-btn") closeBaseImageEditor();
```

Update `closeBaseImageEditor()`:

```javascript
$("base-maintenance-modal").hidden = true;
```

- [ ] **Step 5: Add modal CSS**

Add:

```css
.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(16, 24, 32, 0.42);
}

.modal-backdrop[hidden] {
  display: none;
}

.modal-panel {
  width: min(980px, 100%);
  max-height: min(860px, 92vh);
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  padding: 16px;
  box-shadow: 0 18px 60px rgba(16, 24, 32, 0.24);
}
```

## Task 6: Animate Product Image Sorting

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Add drag state classes**

In `dragstart`, after finding `item`, add:

```javascript
item.classList.add("dragging");
```

Add a `dragend` listener:

```javascript
document.addEventListener("dragend", () => {
  document.querySelectorAll(".image-order-item").forEach((item) => item.classList.remove("dragging", "drag-over"));
  state.draggingBaseImageIndex = -1;
});
```

- [ ] **Step 2: Mark the current drop target**

In `dragover`, add:

```javascript
document.querySelectorAll(".image-order-item.drag-over").forEach((node) => node.classList.remove("drag-over"));
const item = target.closest("[data-image-order-index]");
if (item instanceof HTMLElement) item.classList.add("drag-over");
```

- [ ] **Step 3: Add animation CSS**

Add:

```css
.image-order-list.animated .image-order-item,
.image-order-item {
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.image-order-item.dragging {
  opacity: 0.72;
  transform: scale(1.03);
  box-shadow: 0 12px 28px rgba(16, 24, 32, 0.2);
}

.image-order-item.drag-over {
  border-color: var(--blue);
  transform: translateY(-3px);
}
```

- [ ] **Step 4: Verify sorting still saves only on explicit save**

Run the app, drag images in the modal, refresh before saving, and confirm the persisted order is unchanged. Then drag again, click `保存维护内容`, refresh, and confirm the saved order remains.

## Task 7: Move Folder Selection into From NAS Creation

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`

- [ ] **Step 1: Keep button text and add fallback input**

In the base product header, keep:

```html
<button id="create-base-product-btn">从 NAS 创建</button>
```

Keep `base-product-folder` as a fallback input and make its placeholder clear:

```html
<input id="base-product-folder" placeholder="无法打开文件夹选择器时，在这里粘贴 NAS 商品文件夹路径" />
```

- [ ] **Step 2: Add folder picker helper**

Add:

```javascript
async function pickNasProductFolder() {
  try {
    const data = await api("/api/system/folder-picker", {
      method: "POST",
      body: JSON.stringify({ title: "选择 NAS 商品文件夹" }),
    });
    return data.path || "";
  } catch (err) {
    return "";
  }
}
```

- [ ] **Step 3: Use the picker before reading fallback input**

Update `createBaseProductFromFolder()`:

```javascript
let rootPath = await pickNasProductFolder();
if (!rootPath) rootPath = $("base-product-folder").value.trim();
if (rootPath) $("base-product-folder").value = rootPath;
```

Keep the existing API call to `/api/base-products/from-folder`.

## Task 8: Put Publishing IDs, Variation, and SKU Template in Store Templates

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`

- [ ] **Step 1: Rename labels in the store template form**

Use:

```html
<label>马帮店铺编号<select id="store-template-store"></select></label>
<label>马帮类目编号<input id="store-template-category-id" readonly /></label>
<label>马帮折扣编号<input id="store-template-discount-id" readonly /></label>
```

- [ ] **Step 2: Add variation and SKU template controls**

Add:

```html
<label>变体规则<select id="store-template-variation-rule">
  <option value="color_size">颜色 x 尺码</option>
</select></label>
<label class="wide">SKU 模板<input id="store-template-sku-template" value="{main_sku}_{color}||{size}" /></label>
```

- [ ] **Step 3: Submit new fields**

In `createStoreTemplate()`, add:

```javascript
variation_rule: $("store-template-variation-rule")?.value || "color_size",
sku_template: $("store-template-sku-template")?.value.trim() || "{main_sku}_{color}||{size}",
```

- [ ] **Step 4: Render Chinese publishing ID table controls**

In `renderStoreTemplates()`, replace placeholders and button text:

```javascript
<input data-template-id-field="${escapeHtml(template.id)}" data-field="mabang_store_id" placeholder="马帮店铺编号" value="${escapeHtml(template.mabang_store_id || "")}" />
<input data-template-id-field="${escapeHtml(template.id)}" data-field="mabang_category_id" placeholder="马帮类目编号" value="${escapeHtml(template.mabang_category_id || "")}" />
<input data-template-id-field="${escapeHtml(template.id)}" data-field="mabang_discount_id" placeholder="马帮折扣编号" value="${escapeHtml(template.mabang_discount_id || "")}" />
<button data-save-template-ids="${escapeHtml(template.id)}">保存编号</button>
```

- [ ] **Step 5: Show variation and SKU template in the table**

Add table columns for `变体规则` and `SKU 模板`, rendering:

```javascript
<td>${escapeHtml(template.variation_rule === "color_size" ? "颜色 x 尺码" : template.variation_rule || "")}</td>
<td>${escapeHtml(template.sku_template || "{main_sku}_{color}||{size}")}</td>
```

## Task 9: Reframe Automation Page Around Listing Copies and Jobs

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`

- [ ] **Step 1: Keep listing copy selection in automation**

Ensure the store listing copy table remains selectable with:

```html
<input type="checkbox" data-listing-copy-select="...">
```

The `生成自动化任务` button should live in the automation page header or listing copy panel.

- [ ] **Step 2: Make copy creation route users to automation**

After `copySelectedBaseProductToTemplates()` succeeds, add:

```javascript
showView("automation");
```

Then run:

```javascript
renderStoreListingCopies();
```

- [ ] **Step 3: Remove automation job panel from product maintenance**

Confirm no `automation-job-table` panel remains inside `product-maintenance`.

## Task 10: Turn Assets into File Management

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Replace asset table with file manager containers**

In `file-management`, replace the table with:

```html
<div id="file-group-list" class="file-group-list"></div>
<div id="file-grid" class="file-grid"></div>
```

- [ ] **Step 2: Track selected group**

Add to `state`:

```javascript
selectedFileGroup: "",
```

- [ ] **Step 3: Render grouped folders and files**

Replace `renderAssets()` with grouped rendering:

```javascript
function renderAssets() {
  const groupsTarget = $("file-group-list");
  const filesTarget = $("file-grid");
  if (!groupsTarget || !filesTarget) return;
  const groups = [...new Set(state.assets.map((asset) => asset.group_id || "未分组"))].sort();
  if (!state.selectedFileGroup && groups.length) state.selectedFileGroup = groups[0];
  groupsTarget.innerHTML = groups.map((group) => `
    <button class="file-group ${group === state.selectedFileGroup ? "active" : ""}" data-file-group="${escapeHtml(group)}">${escapeHtml(group)}</button>
  `).join("");
  const files = state.assets.filter((asset) => (asset.group_id || "未分组") === state.selectedFileGroup);
  filesTarget.innerHTML = files.map((asset) => {
    const previewable = ["main", "variant", "model", "detail", "size_chart"].includes(asset.asset_type || "");
    const image = previewable ? `<img src="/api/assets/${encodeURIComponent(asset.id || "")}/file" alt="${escapeHtml(asset.file_name || "")}" />` : "";
    return `
      <article class="file-card">
        <div class="file-card-preview">${image}</div>
        <strong>${escapeHtml(asset.file_name || "")}</strong>
        <span>${escapeHtml(asset.asset_type || "")}</span>
      </article>
    `;
  }).join("");
}
```

- [ ] **Step 4: Wire group click**

In the click handler:

```javascript
const fileGroup = target.closest("[data-file-group]");
if (fileGroup instanceof HTMLElement && fileGroup.dataset.fileGroup) {
  state.selectedFileGroup = fileGroup.dataset.fileGroup;
  renderAssets();
}
```

- [ ] **Step 5: Add file manager CSS**

Add:

```css
.file-group-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}

.file-group.active {
  border-color: var(--blue);
  color: var(--blue);
  background: #eff6ff;
}

.file-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.file-card {
  display: grid;
  gap: 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px;
  background: #fff;
}

.file-card-preview {
  height: 112px;
  border-radius: 6px;
  background: #f3f5f8;
  overflow: hidden;
}

.file-card-preview img {
  width: 100%;
  height: 112px;
  object-fit: cover;
  display: block;
}

.file-card span {
  color: var(--muted);
  font-size: 12px;
}
```

## Task 11: Update Documentation and Run Verification

**Files:**
- Modify: `README.md`
- Modify: `CONTEXT.md`

- [ ] **Step 1: Update README workflow names**

In `README.md`, update the console workflow summary to:

```text
文件管理
-> 商品维护 / 基础商品
-> 商品维护 / 店铺模板
-> 自动化 / 店铺刊登副本
-> 自动化 / 浏览器自动化任务
```

- [ ] **Step 2: Confirm domain vocabulary**

Ensure `CONTEXT.md` includes:

```text
Product maintenance
Product card
File management
Main SKU
SKU template
Mabang publishing identifiers
```

- [ ] **Step 3: Run Python tests**

Run:

```powershell
python -m pytest tests/test_product_line_flow.py tests/test_product_maintenance_reorg.py -q
```

Expected: PASS.

- [ ] **Step 4: Run compile check**

Run:

```powershell
python -m compileall -q services workers scripts tests
```

Expected: exit code 0.

- [ ] **Step 5: Run the app and visually inspect**

Run:

```powershell
python -m services.api.main
```

Open:

```text
http://127.0.0.1:8787
```

Verify:

- left nav shows `总览 / 商品维护 / 自动化 / 文件管理 / 计划`
- `商品维护` defaults to `基础商品`
- base products render as cards
- `维护` opens a dialog
- image sorting animates and saves only after clicking save
- `店铺模板` contains Chinese Mabang publishing labels, variation rule, and SKU template
- copying a base product to templates moves the operator to `自动化`
- `文件管理` presents grouped folders and file cards
