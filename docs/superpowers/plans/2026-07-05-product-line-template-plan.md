# Product Line and Store Template Implementation Plan

> **For AI agents:** Required sub-skill: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox syntax (`- [ ]`) to track progress.

**Goal:** Build the first product-line workflow: create base products from NAS folders, maintain store template variants, and copy one base product to multiple store-specific listing copies.

**Architecture:** Extend the existing JSON/Postgres-backed store with explicit product-line collections while preserving existing `products`, `skus`, and `prices` behavior. Add API operations for base product creation and multi-template copy, then update the static web console to expose base products, store templates, and listing copies as separate working areas.

**Tech Stack:** Python standard-library HTTP API, JSON/Postgres store abstraction in `services/api/storage.py`, NAS scanning in `workers/asset_scanner/scanner.py`, static HTML/CSS/JS in `apps/web`, pytest regression tests.

**Boundary:** Do not build Browser-Use, Playwright, or Mabang browser automation in this plan. Browser-Use belongs to a later plan after store listing copies are stable.

---

## Files

- Modify `workers/asset_scanner/scanner.py`: strengthen flat Chinese naming recognition for main, model, variants, size chart, video, and source files.
- Modify `services/api/storage.py`: add `base_products`, `store_templates`, `store_listing_copies`, `listing_skus`, and `listing_prices`; add functions for base product creation, store template CRUD, multi-template copy, and readiness summaries.
- Modify `services/api/main.py`: expose API routes for the new product-line operations.
- Modify `apps/web/index.html`: split the product page into base products, store templates, and store listing copies.
- Modify `apps/web/app.js`: load and render the new resources, create base products from NAS paths, edit templates, and copy to multiple templates.
- Modify `apps/web/styles.css`: add compact operational layout styles that match the current console.
- Create `tests/test_product_line_flow.py`: cover scanner recognition, base product creation, template creation, multi-template copy, and readiness.
- Modify `tests/test_mabang_export.py` only if export helpers need to accept store listing copies in addition to legacy product bundles.
- Modify `README.md` and `docs/M1_ARCHITECTURE.md`: describe the product-line-first workflow and browser automation as a later phase.

## Task 1: Strengthen Flat NAS Asset Recognition

**Files:**
- Modify: `workers/asset_scanner/scanner.py`
- Create: `tests/test_product_line_flow.py`

- [ ] **Step 1: Write failing scanner tests**

Add tests that create a temporary one-product folder with these files:

```text
DL0001超天酱/
  主图.jpg
  模特图1.jpg
  白色.jpg
  黑色.jpg
  尺码表.png
  视频.mp4
  源文件.psd
```

Expected assertions:

```python
from pathlib import Path

from workers.asset_scanner.scanner import scan_asset_root


def test_flat_chinese_product_folder_recognizes_asset_roles(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "模特图1.jpg", "白色.jpg", "黑色.jpg", "尺码表.png", "视频.mp4", "源文件.psd"]:
        (folder / name).write_bytes(b"not-a-real-image")

    result = scan_asset_root(folder, category_override="POD 男装T恤", mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["主图.jpg"]["asset_type"] == "main"
    assert by_name["模特图1.jpg"]["asset_type"] == "model"
    assert by_name["白色.jpg"]["asset_type"] == "variant"
    assert by_name["白色.jpg"]["sku_option"] == "white"
    assert by_name["黑色.jpg"]["asset_type"] == "variant"
    assert by_name["黑色.jpg"]["sku_option"] == "black"
    assert by_name["尺码表.png"]["asset_type"] == "size_chart"
    assert by_name["视频.mp4"]["asset_type"] == "video"
    assert by_name["源文件.psd"]["asset_type"] == "source"
    assert {item["group_id"] for item in result["assets"]} == {"DL0001"}
```

- [ ] **Step 2: Run the scanner test and confirm it fails**

Run: `python -m pytest tests/test_product_line_flow.py::test_flat_chinese_product_folder_recognizes_asset_roles -q`

Expected: FAIL because current scanner misses at least some Chinese role names or source naming variants.

- [ ] **Step 3: Implement scanner recognition**

Update `workers/asset_scanner/scanner.py`:

```python
COLOR_WORDS = {
    "白色": "white",
    "黑色": "black",
    "灰色": "gray",
    "粉色": "pink",
    "紫色": "purple",
    "红色": "red",
    "蓝色": "blue",
    "绿色": "green",
    "黄色": "yellow",
    "米色": "beige",
}

MODEL_WORDS = {"模特图", "模特", "model"}
MAIN_WORDS = {"主图", "main"}
SIZE_CHART_WORDS = {"尺码表", "尺码", "size"}
SOURCE_WORDS = {"源文件", "source"}
```

Then use these sets inside `_detect_asset_type()` before the color fallback. Preserve existing English directory recognition and source extension handling.

- [ ] **Step 4: Run scanner tests**

Run: `python -m pytest tests/test_product_line_flow.py::test_flat_chinese_product_folder_recognizes_asset_roles -q`

Expected: PASS.

## Task 2: Add Product-Line Store Collections

**Files:**
- Modify: `services/api/storage.py`
- Modify: `tests/test_product_line_flow.py`

- [ ] **Step 1: Write failing storage tests**

Add tests for the target storage functions:

```python
from services.api import storage


def test_store_templates_can_be_listed_and_created() -> None:
    template = storage.create_store_template({
        "store_name": "Shopee-MY-A",
        "platform": "shopee",
        "country": "MY",
        "currency": "MYR",
        "template_name": "Mens Tee",
        "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "normal_price": 89,
        "sale_price": 69,
        "discount_label": "Default 7.7",
        "sizes": ["S", "M", "L"],
        "title_suffix": "Cotton T-Shirt Men Casual Tee",
        "description": "Store-specific description.",
    })

    assert template["id"]
    assert template["store_name"] == "Shopee-MY-A"
    assert template["sizes"] == ["S", "M", "L"]
    assert storage.list_store_templates()[0]["template_name"] == "Mens Tee"
```

- [ ] **Step 2: Run the storage template test and confirm it fails**

Run: `python -m pytest tests/test_product_line_flow.py::test_store_templates_can_be_listed_and_created -q`

Expected: FAIL because `create_store_template` and `list_store_templates` do not exist.

- [ ] **Step 3: Extend the empty store**

In `_empty_store()`, add:

```python
"base_products": [],
"store_templates": [],
"store_listing_copies": [],
"listing_skus": [],
"listing_prices": [],
```

Ensure `load_store()` still backfills these collections for existing JSON stores.

- [ ] **Step 4: Implement store template functions**

Add focused functions:

```python
def list_store_templates() -> list[dict[str, Any]]:
    ...

def create_store_template(payload: dict[str, Any]) -> dict[str, Any]:
    ...

def update_store_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    ...
```

Normalize `sizes` from either a list or comma-separated string. Keep price fields numeric through `_float_or_empty`.

- [ ] **Step 5: Run template storage tests**

Run: `python -m pytest tests/test_product_line_flow.py::test_store_templates_can_be_listed_and_created -q`

Expected: PASS.

## Task 3: Create Base Products From One NAS Folder

**Files:**
- Modify: `services/api/storage.py`
- Modify: `services/api/main.py`
- Modify: `tests/test_product_line_flow.py`

- [ ] **Step 1: Write failing base product tests**

Add a storage-level test:

```python
def test_create_base_product_from_assets_records_shared_assets(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    product = storage.create_base_product_from_folder({
        "root_path": str(folder),
        "category": "POD 男装T恤",
    })

    assert product["group_id"] == "DL0001"
    assert product["asset_root"] == str(folder)
    assert product["main_image"] == "主图.jpg"
    assert product["recognized_colors"] == ["black", "white"]
    assert product["status"] == "base_ready"
```

- [ ] **Step 2: Run the base product test and confirm it fails**

Run: `python -m pytest tests/test_product_line_flow.py::test_create_base_product_from_assets_records_shared_assets -q`

Expected: FAIL because `create_base_product_from_folder` does not exist.

- [ ] **Step 3: Implement `create_base_product_from_folder`**

In `services/api/storage.py`, call `scan_asset_root(root_path, category_override=..., mode="recursive")`, persist recognized assets with `replace_assets()` or a new merge helper, then create one record in `base_products`.

Base product fields:

```python
{
    "id": str(uuid.uuid4()),
    "listing_kind": "base_product",
    "category": category,
    "group_id": group_id,
    "product_name_cn": derived_name,
    "base_name": derived_name,
    "asset_root": common_root,
    "main_image": main_file_name,
    "recognized_colors": sorted(colors),
    "variant_count": variant_count,
    "status": "base_ready" or "needs_check",
    "created_at": now,
    "updated_at": now,
}
```

Do not generate final SKU rows here. Store listing copies will do that from template sizes.

- [ ] **Step 4: Add API route**

In `services/api/main.py`, add:

```python
if path == "/api/base-products/from-folder":
    product = storage.create_base_product_from_folder(payload)
    if not product:
        self._json({"error": "No assets found for folder"}, status=404)
        return
    self._json(product, status=201)
    return
```

Also add `GET /api/base-products` returning `storage.list_base_products_with_summary()`.

- [ ] **Step 5: Run base product tests**

Run: `python -m pytest tests/test_product_line_flow.py::test_create_base_product_from_assets_records_shared_assets -q`

Expected: PASS.

## Task 4: Copy Base Product To Multiple Store Templates

**Files:**
- Modify: `services/api/storage.py`
- Modify: `services/api/main.py`
- Modify: `tests/test_product_line_flow.py`

- [ ] **Step 1: Write failing multi-template copy test**

Add:

```python
def test_copy_base_product_to_multiple_store_templates_creates_listing_copies(tmp_path: Path) -> None:
    folder = tmp_path / "DL0001超天酱"
    folder.mkdir()
    for name in ["主图.jpg", "白色.jpg", "黑色.jpg"]:
        (folder / name).write_bytes(b"image")

    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD 男装T恤"})
    mens = storage.create_store_template({
        "store_name": "Shopee-MY-A",
        "platform": "shopee",
        "country": "MY",
        "currency": "MYR",
        "template_name": "Mens Tee",
        "product_line": "POD 男装T恤",
        "marketplace_category_path": "Men Clothes > Tops > T-shirts",
        "normal_price": 89,
        "sale_price": 69,
        "sizes": ["S", "M"],
        "title_suffix": "Cotton T-Shirt Men",
        "description": "Mens tee description.",
    })
    kids = storage.create_store_template({
        "store_name": "Shopee-MY-A",
        "platform": "shopee",
        "country": "MY",
        "currency": "MYR",
        "template_name": "Kids Tee",
        "product_line": "POD 童装T恤",
        "marketplace_category_path": "Baby & Kids Fashion > Tops > T-shirts",
        "normal_price": 79,
        "sale_price": 59,
        "sizes": ["100", "110"],
        "title_suffix": "Kids Cotton T-Shirt",
        "description": "Kids tee description.",
    })

    result = storage.copy_base_product_to_store_templates(base["id"], [mens["id"], kids["id"]])

    assert result["created_count"] == 2
    copies = storage.list_store_listing_copies()
    assert {copy["template_id"] for copy in copies} == {mens["id"], kids["id"]}
    assert all(copy["base_product_id"] == base["id"] for copy in copies)
    assert len(storage.list_listing_skus(copies[0]["id"])) == 4
    assert len(storage.list_listing_prices(copies[0]["id"])) == 4
```

- [ ] **Step 2: Run the copy test and confirm it fails**

Run: `python -m pytest tests/test_product_line_flow.py::test_copy_base_product_to_multiple_store_templates_creates_listing_copies -q`

Expected: FAIL because copy functions do not exist.

- [ ] **Step 3: Implement copy functions**

Add:

```python
def list_store_listing_copies() -> list[dict[str, Any]]:
    ...

def list_listing_skus(listing_copy_id: str) -> list[dict[str, Any]]:
    ...

def list_listing_prices(listing_copy_id: str) -> list[dict[str, Any]]:
    ...

def copy_base_product_to_store_templates(base_product_id: str, template_ids: list[str]) -> dict[str, Any]:
    ...
```

Generate one listing copy per template. Generate `listing_skus` from base product recognized colors and template sizes. Generate `listing_prices` from template price fields. Avoid duplicate copies for the same `base_product_id + template_id`; return skipped entries if they already exist.

- [ ] **Step 4: Add API route**

In `services/api/main.py`, add:

```python
if path == "/api/store-listing-copies/from-base":
    result = storage.copy_base_product_to_store_templates(
        payload.get("base_product_id") or "",
        payload.get("template_ids") or [],
    )
    self._json(result, status=201)
    return
```

Add `GET /api/store-templates`, `POST /api/store-templates`, and `GET /api/store-listing-copies`.

- [ ] **Step 5: Run copy tests**

Run: `python -m pytest tests/test_product_line_flow.py::test_copy_base_product_to_multiple_store_templates_creates_listing_copies -q`

Expected: PASS.

## Task 5: Update Product Page UI For The New Line

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`

- [ ] **Step 1: Add page structure**

In `apps/web/index.html`, replace the single product-maintenance block with three sections inside the `products` view:

```html
<div class="panel">
  <div class="panel-head">
    <h2>基础商品</h2>
    <div class="actions compact">
      <button id="create-base-product-btn">从 NAS 创建</button>
      <button id="copy-to-store-templates-btn">复制到店铺模板</button>
    </div>
  </div>
  <div class="inline-form">
    <input id="base-product-folder" placeholder="输入 NAS 商品文件夹路径" />
    <input id="base-product-category" value="POD 男装T恤" />
  </div>
  <p id="base-product-result" class="muted"></p>
  <div class="table-wrap">
    <table>
      <thead><tr><th></th><th>Group</th><th>商品名</th><th>主图</th><th>颜色</th><th>状态</th></tr></thead>
      <tbody id="base-product-table"></tbody>
    </table>
  </div>
</div>
```

Add similar compact panels for `store-template-table` and `store-listing-copy-table`.

- [ ] **Step 2: Wire state and loaders**

In `apps/web/app.js`, add state keys:

```javascript
baseProducts: [],
storeTemplates: [],
selectedStoreTemplates: new Set(),
storeListingCopies: [],
selectedBaseProductId: "",
```

Add loaders for `/api/base-products`, `/api/store-templates`, and `/api/store-listing-copies`. Include them in `refreshAll()`.

- [ ] **Step 3: Implement UI actions**

Add:

```javascript
async function createBaseProductFromFolder() { ... }
async function createStoreTemplate() { ... }
async function copySelectedBaseProductToTemplates() { ... }
```

Use `POST /api/base-products/from-folder`, `POST /api/store-templates`, and `POST /api/store-listing-copies/from-base`.

- [ ] **Step 4: Render operational tables**

Render base products, templates, and listing copies with stable table columns. Keep dense console styling consistent with the existing app. Avoid large decorative blocks.

- [ ] **Step 5: Run a static smoke check**

Run: `python -m compileall -q services workers scripts tests`

Expected: PASS.

## Task 6: Align Export And Documentation Boundaries

**Files:**
- Modify: `services/api/mabang_export.py` if needed
- Modify: `README.md`
- Modify: `docs/M1_ARCHITECTURE.md`
- Modify: `tests/test_mabang_export.py` if needed

- [ ] **Step 1: Decide export source compatibility**

If the UI still exports legacy `products`, leave existing export behavior unchanged in this task. If the UI moves export buttons to store listing copies, add an adapter that builds the existing Mabang bundle shape from one store listing copy.

- [ ] **Step 2: Add focused export test only if adapter is added**

Use an existing `_sample_bundle()` style fixture and assert store listing copy fields map to product, SKU, and price bundle fields before workbook generation.

- [ ] **Step 3: Update docs**

In `README.md`, describe:

- Base product from NAS folder
- Store template variants
- Multi-template copy
- Excel export as fallback/audit
- Browser automation as later phase

In `docs/M1_ARCHITECTURE.md`, update runtime flow from product library directly to workbook export into product-line store listing copies first.

- [ ] **Step 4: Run documentation and regression verification**

Run:

```powershell
python -m pytest tests/test_product_line_flow.py -q
python -m pytest tests/test_mabang_export.py tests/test_mabang_template_fallback.py -q
python -m compileall -q services workers scripts tests
```

Expected: all pass.

## Task 7: Full Regression Gate

**Files:**
- No required edits unless failures expose gaps.

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run project verification scripts**

Run:

```powershell
python scripts\verify_deployment_readiness.py
python scripts\verify_project_plan.py
python scripts\smoke_test.py
```

Expected:

```text
deployment_readiness_ok
project_plan_ok
scanner_ok ...
```

- [ ] **Step 3: Manual browser smoke**

Start the API with:

```powershell
.\scripts\run_api.ps1 -HostName "127.0.0.1" -Port 8787
```

Open `http://127.0.0.1:8787/`, go to the product page, and verify:

- Base product can be created from a sample folder path.
- Store template can be created.
- Multiple templates can be selected.
- Copy operation creates listing copies.
- Existing asset scan and Excel export entry points still render.

- [ ] **Step 4: Record verification**

Update `progress.md` with the exact commands and observed results.
