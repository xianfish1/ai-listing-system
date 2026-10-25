# 店铺刊登副本标题与图片差异化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 同一基础商品复制到不同店铺模板时，固定主图和核心标题，仅使用店铺模板稳定种子重排第 2 至第 5 张商品图及显式配置的卖点短语。

**架构：** 新建纯函数模块 `services/api/listing_copy_variation.py`，集中处理标题字段校验、稳定 SHA-256 排列和图片范围重排。存储层保存结构化标题字段，并且只在 `_build_store_listing_copy()` 创建新副本时调用差异化函数；前端基础商品维护弹窗只负责录入和回显，不在浏览器中自行随机。

**技术栈：** Python 3 标准库（`hashlib`、`json`）、现有 JSON/PostgreSQL 存储抽象、原生 JavaScript/HTML/CSS、pytest、Playwright CLI。

---

## 文件结构

- 创建 `services/api/listing_copy_variation.py`：标题卖点规范化、字段长度校验、SHA-256 稳定排列、标题生成、图片第 2 至第 5 张重排。
- 创建 `tests/test_listing_copy_variation.py`：纯函数单元测试，不依赖存储或 HTTP 服务。
- 修改 `services/api/storage.py`：基础商品字段持久化与校验；新建副本时调用差异化模块；保留旧数据回退行为。
- 修改 `services/api/main.py`：将基础商品字段校验错误转换为 HTTP 422。
- 修改 `tests/test_product_line_flow.py`：覆盖多模板复制、已有副本不变及自动化载荷使用最终快照。
- 修改 `tests/test_store_template_workflow_api.py`：覆盖基础商品 API 保存、回显和非法输入。
- 修改 `apps/web/index.html`：基础商品维护弹窗增加核心标题与卖点输入，并更新资源版本。
- 修改 `apps/web/app.js`：打开、收集和保存两个新字段。
- 修改 `apps/web/styles.css`：为卖点多行输入提供稳定高度，保持移动端单列布局。
- 修改 `tests/test_store_template_workflow_ui.py`：锁定新控件、字段绑定和资源版本。

### 任务 1：实现并验证纯函数差异化模块

**文件：**

- 创建：`services/api/listing_copy_variation.py`
- 创建：`tests/test_listing_copy_variation.py`

- [ ] **步骤 1：编写标题卖点规范化失败测试**

创建 `tests/test_listing_copy_variation.py`：

```python
import pytest

from services.api.listing_copy_variation import normalize_title_core, normalize_title_selling_points


def test_title_fields_are_trimmed_deduplicated_and_keep_phrase_boundaries():
    assert normalize_title_core("  Hatsune Miku Pixel Style T-shirt  ") == "Hatsune Miku Pixel Style T-shirt"
    assert normalize_title_selling_points("Pure Cotton\nPrinted\npure cotton\nLoose Fit\n") == [
        "Pure Cotton",
        "Printed",
        "Loose Fit",
    ]


def test_title_fields_reject_configured_limits():
    with pytest.raises(ValueError, match="核心标题不能超过 300 个字符"):
        normalize_title_core("x" * 301)
    with pytest.raises(ValueError, match="卖点短语不能超过 12 个"):
        normalize_title_selling_points([f"point-{index}" for index in range(13)])
    with pytest.raises(ValueError, match="每个卖点短语不能超过 80 个字符"):
        normalize_title_selling_points(["x" * 81])
```

- [ ] **步骤 2：运行测试并确认红灯**

运行：

```powershell
python -m pytest -q tests/test_listing_copy_variation.py
```

预期：测试收集失败，提示 `services.api.listing_copy_variation` 不存在。

- [ ] **步骤 3：实现最小字段规范化函数**

创建 `services/api/listing_copy_variation.py`，提供公开接口：

```python
from __future__ import annotations

import hashlib
import re
from typing import Any, TypeVar


T = TypeVar("T")
TITLE_CORE_MAX_LENGTH = 300
TITLE_SELLING_POINT_MAX_LENGTH = 80
TITLE_SELLING_POINT_LIMIT = 12


def normalize_title_core(value: Any) -> str:
    core = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(core) > TITLE_CORE_MAX_LENGTH:
        raise ValueError("核心标题不能超过 300 个字符")
    return core


def normalize_title_selling_points(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.splitlines()
    elif isinstance(value, list):
        raw_items = value
    elif value in (None, ""):
        raw_items = []
    else:
        raise ValueError("可重排卖点必须是数组或每行一个短语的文本")
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        item = re.sub(r"\s+", " ", str(raw_item or "")).strip()
        if not item or item.casefold() in seen:
            continue
        if len(item) > TITLE_SELLING_POINT_MAX_LENGTH:
            raise ValueError("每个卖点短语不能超过 80 个字符")
        seen.add(item.casefold())
        result.append(item)
    if len(result) > TITLE_SELLING_POINT_LIMIT:
        raise ValueError("卖点短语不能超过 12 个")
    return result
```

- [ ] **步骤 4：补充稳定标题和图片重排失败测试**

在同一测试文件追加：

```python
from services.api.listing_copy_variation import build_varied_title, vary_product_images


def test_varied_title_keeps_core_and_complete_phrases():
    points = ["Pure Cotton", "Printed", "Loose Fit", "Casual"]
    first = build_varied_title(
        title_core="Hatsune Miku Pixel Style T-shirt",
        selling_points=points,
        fallback_title="fallback",
        base_product_id="base-1",
        template_id="store-a",
    )
    repeated = build_varied_title(
        title_core="Hatsune Miku Pixel Style T-shirt",
        selling_points=points,
        fallback_title="fallback",
        base_product_id="base-1",
        template_id="store-a",
    )
    assert first == repeated
    assert first.startswith("Hatsune Miku Pixel Style T-shirt ")
    assert all(point in first for point in points)


def test_varied_title_falls_back_without_both_structured_fields():
    assert build_varied_title(
        title_core="Hatsune Miku T-shirt",
        selling_points=[],
        fallback_title="Original full title",
        base_product_id="base-1",
        template_id="store-a",
    ) == "Original full title"


def test_product_images_keep_main_and_tail_while_reordering_two_through_five():
    images = ["main.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg", "7.jpg"]
    varied = vary_product_images(images, base_product_id="base-1", template_id="store-a")
    repeated = vary_product_images(images, base_product_id="base-1", template_id="store-a")
    assert varied == repeated
    assert varied[0] == "main.jpg"
    assert set(varied[1:5]) == {"2.jpg", "3.jpg", "4.jpg", "5.jpg"}
    assert varied[5:] == ["6.jpg", "7.jpg"]


def test_fixed_store_ids_produce_different_stable_variants():
    points = ["Pure Cotton", "Printed", "Loose Fit", "Casual"]
    title_a = build_varied_title(
        title_core="Hatsune Miku Pixel Style T-shirt", selling_points=points,
        fallback_title="fallback", base_product_id="base-1", template_id="store-a",
    )
    title_b = build_varied_title(
        title_core="Hatsune Miku Pixel Style T-shirt", selling_points=points,
        fallback_title="fallback", base_product_id="base-1", template_id="store-b",
    )
    images = ["main.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg"]
    assert title_a != title_b
    assert vary_product_images(images, base_product_id="base-1", template_id="store-a") != vary_product_images(
        images, base_product_id="base-1", template_id="store-b"
    )
```

- [ ] **步骤 5：实现 SHA-256 稳定排列和两个领域函数**

在新模块中追加：

```python
def _stable_permutation(items: list[T], *, seed: str, namespace: str) -> list[T]:
    weighted = []
    for index, item in enumerate(items):
        digest = hashlib.sha256(
            f"{namespace}\0{seed}\0{index}\0{item}".encode("utf-8")
        ).digest()
        weighted.append((digest, index, item))
    return [item for _, _, item in sorted(weighted)]


def build_varied_title(
    *,
    title_core: Any,
    selling_points: Any,
    fallback_title: str,
    base_product_id: str,
    template_id: str,
) -> str:
    core = normalize_title_core(title_core)
    points = normalize_title_selling_points(selling_points)
    if not core or not points:
        return str(fallback_title or "").strip()
    ordered = _stable_permutation(
        points,
        seed=f"{base_product_id}\0{template_id}",
        namespace="listing-title-selling-points-v1",
    )
    return " ".join([core, *ordered])


def vary_product_images(
    images: list[str], *, base_product_id: str, template_id: str
) -> list[str]:
    normalized = [str(item or "").strip() for item in images if str(item or "").strip()]
    if len(normalized) < 3:
        return normalized
    movable_end = min(5, len(normalized))
    movable = _stable_permutation(
        normalized[1:movable_end],
        seed=f"{base_product_id}\0{template_id}",
        namespace="listing-product-images-v1",
    )
    return [normalized[0], *movable, *normalized[movable_end:]]
```

- [ ] **步骤 6：运行纯函数测试并确认绿灯**

运行：

```powershell
python -m pytest -q tests/test_listing_copy_variation.py
```

预期：全部通过。

- [ ] **步骤 7：提交纯函数模块**

```powershell
git add services/api/listing_copy_variation.py tests/test_listing_copy_variation.py
git commit -m "feat: add stable listing copy variation rules"
```

### 任务 2：保存并校验基础商品结构化标题字段

**文件：**

- 修改：`services/api/storage.py:1230-1350`
- 修改：`services/api/main.py:780-793`
- 修改：`tests/test_store_template_workflow_api.py`

- [ ] **步骤 1：编写基础商品 API 保存与回显失败测试**

在 `tests/test_store_template_workflow_api.py` 增加测试，先向隔离存储写入 `base-1`，然后调用 PUT：

```python
def test_base_product_api_saves_structured_title_fields_and_rejects_limits() -> None:
    data = storage.load_store()
    data["base_products"] = [{
        "id": "base-1", "group_id": "EL0001", "main_sku": "EL0001",
        "product_images": ["main.jpg", "2.jpg"], "created_at": "2026-01-01T00:00:00Z",
    }]
    storage.save_store(data)
    server, base = _start_api()
    try:
        status, updated = _call("PUT", base + "/api/base-products/base-1", {
            "title_core": "  Hatsune Miku Pixel Style T-shirt  ",
            "title_selling_points": ["Pure Cotton", "Printed", "pure cotton"],
        })
        assert status == 200
        assert updated["title_core"] == "Hatsune Miku Pixel Style T-shirt"
        assert updated["title_selling_points"] == ["Pure Cotton", "Printed"]

        status, invalid = _call("PUT", base + "/api/base-products/base-1", {
            "title_selling_points": ["x" * 81],
        })
        assert status == 422
        assert invalid["error"] == "每个卖点短语不能超过 80 个字符"
    finally:
        server.shutdown(); server.server_close()
```

- [ ] **步骤 2：运行 API 测试并确认失败**

运行：

```powershell
python -m pytest -q tests/test_store_template_workflow_api.py -k structured_title_fields
```

预期：字段未保存，或者非法输入没有返回 422。

- [ ] **步骤 3：接入存储字段规范化**

在 `services/api/storage.py` 导入：

```python
from services.api.listing_copy_variation import (
    normalize_title_core,
    normalize_title_selling_points,
)
```

在 `create_base_product_from_folder()` 创建的新记录中加入兼容默认值：

```python
"title_core": "",
"title_selling_points": [],
```

在 `update_base_product()` 的 `editable_fields` 中加入两个字段，并在字段循环中使用专门分支：

```python
elif key == "title_core":
    base[key] = normalize_title_core(payload.get(key))
elif key == "title_selling_points":
    base[key] = normalize_title_selling_points(payload.get(key))
```

不要把这两个基础商品配置字段同步到 `store_listing_copies`；已有副本必须保持快照不变。

- [ ] **步骤 4：将校验错误映射为 HTTP 422**

修改 `services/api/main.py` 的基础商品 PUT 路由：

```python
try:
    product = storage.update_base_product(base_product_id, payload)
except ValueError as exc:
    raise ApiRequestError(str(exc), 422) from exc
```

- [ ] **步骤 5：运行基础商品 API 回归测试**

运行：

```powershell
python -m pytest -q tests/test_store_template_workflow_api.py
```

预期：该文件全部测试通过。

- [ ] **步骤 6：提交基础商品字段支持**

```powershell
git add services/api/storage.py services/api/main.py tests/test_store_template_workflow_api.py
git commit -m "feat: store structured listing title fields"
```

### 任务 3：在新副本创建流程中应用稳定差异化

**文件：**

- 修改：`services/api/storage.py:2988-3040`
- 修改：`tests/test_product_line_flow.py`
- 修改：`tests/test_browser_schema_payload.py`

- [ ] **步骤 1：编写多店铺模板复制失败测试**

在 `tests/test_product_line_flow.py` 增加一个包含 7 张商品图的基础商品，保存结构化标题字段，创建两个不同模板，再一次复制：

```python
def test_copy_to_different_store_templates_varies_title_and_images_stably(tmp_path: Path) -> None:
    folder = tmp_path / "EL0001 Hatsune Miku"
    folder.mkdir()
    for name in ["main.jpg", "model2.jpg", "model3.jpg", "model4.jpg", "model5.jpg", "model6.jpg", "model7.jpg"]:
        (folder / name).write_bytes(b"image")
    base = storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})
    data = storage.load_store()
    stored_base = next(item for item in data["base_products"] if item["id"] == base["id"])
    stored_base["id"] = "base-1"
    storage.save_store(data)
    base = storage.get_item("base_products", "base-1")
    base = storage.update_base_product(base["id"], {
        "listing_title": "Original full title",
        "title_core": "Hatsune Miku Pixel Style T-shirt",
        "title_selling_points": ["Pure Cotton", "Printed", "Loose Fit", "Casual"],
        "product_images": ["main.jpg", "model2.jpg", "model3.jpg", "model4.jpg", "model5.jpg", "model6.jpg", "model7.jpg"],
    })
    template_a = storage.create_store_template({"id": "store-a", "store_name": "Store A", "template_name": "A"})
    template_b = storage.create_store_template({"id": "store-b", "store_name": "Store B", "template_name": "B"})

    result = storage.copy_base_product_to_store_templates(base["id"], [template_a["id"], template_b["id"]])
    by_template = {item["template_id"]: item for item in result["created"]}
    copy_a = by_template[template_a["id"]]
    copy_b = by_template[template_b["id"]]

    for copy in [copy_a, copy_b]:
        assert copy["main_image"] == "main.jpg"
        assert copy["product_images"][0] == "main.jpg"
        assert set(copy["product_images"][1:5]) == {"model2.jpg", "model3.jpg", "model4.jpg", "model5.jpg"}
        assert copy["product_images"][5:] == ["model6.jpg", "model7.jpg"]
        assert copy["listing_title"].startswith("Hatsune Miku Pixel Style T-shirt ")

    assert copy_a["listing_title"] != copy_b["listing_title"] or copy_a["product_images"] != copy_b["product_images"]
```

固定 ID `base-1`、`store-a`、`store-b` 对应的 SHA-256 排列已在纯函数测试中锁定：标题和图片排列都不同。不要改用随机 UUID，也不要在生产代码中加入运行时碰撞修正。

- [ ] **步骤 2：运行复制测试并确认失败**

运行：

```powershell
python -m pytest -q tests/test_product_line_flow.py -k varies_title_and_images_stably
```

预期：两个副本仍沿用相同标题和图片顺序。

- [ ] **步骤 3：在 `_build_store_listing_copy()` 中生成最终快照**

在 `services/api/storage.py` 导入：

```python
from services.api.listing_copy_variation import build_varied_title, vary_product_images
```

在 `_build_store_listing_copy()` 中先计算回退标题，再生成最终标题和图片：

```python
fallback_title = str(base.get("listing_title") or "").strip()
if not fallback_title:
    fallback_title = " ".join(
        part for part in [base_name, template.get("title_suffix") or "", base.get("group_id") or ""]
        if str(part or "").strip()
    ).strip()
title = build_varied_title(
    title_core=base.get("title_core"),
    selling_points=base.get("title_selling_points"),
    fallback_title=fallback_title,
    base_product_id=str(base.get("id") or ""),
    template_id=str(template.get("id") or ""),
)
product_images = vary_product_images(
    _normalize_product_images(base.get("product_images") or []),
    base_product_id=str(base.get("id") or ""),
    template_id=str(template.get("id") or ""),
)
```

副本字段使用：

```python
"listing_title": title,
"main_image": product_images[0] if product_images else str(base.get("main_image") or ""),
"product_images": product_images,
```

- [ ] **步骤 4：增加旧数据与已有副本兼容测试**

在 `tests/test_product_line_flow.py` 增加：

```python
def test_copy_without_structured_title_keeps_existing_full_title(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path)
    base = storage.get_item("base_products", listing_copy["base_product_id"])
    assert listing_copy["listing_title"] == base["listing_title"]


def test_editing_base_title_variation_fields_does_not_change_existing_copy(tmp_path: Path) -> None:
    listing_copy = _create_listing_copy(tmp_path)
    original = storage.get_item("store_listing_copies", listing_copy["id"])
    storage.update_base_product(listing_copy["base_product_id"], {
        "title_core": "New core",
        "title_selling_points": ["New point"],
    })
    assert storage.get_item("store_listing_copies", listing_copy["id"]) == original
```

如果 `_create_listing_copy()` 生成的基础商品没有 `listing_title`，测试先通过 `update_base_product()` 设置明确的 `listing_title`，再创建副本。

- [ ] **步骤 5：验证 Browser-Use 使用副本最终标题和顺序**

在 `tests/test_browser_schema_payload.py` 增加：

```python
def test_browser_payload_uses_varied_listing_copy_snapshot(tmp_path: Path):
    listing_copy = _listing_copy(tmp_path)
    varied_images = list(reversed(listing_copy["product_images"]))
    updated = storage.update_store_listing_copy(listing_copy["id"], {
        "title": "Hatsune Miku Pixel Style T-shirt Printed Pure Cotton",
        "product_images": varied_images,
        "main_image": varied_images[0],
    })
    job = storage.create_automation_job_from_listing_copy(updated["id"])
    payload = storage.build_automation_payload(job["id"])
    assert payload["browser_inputs"]["title"] == updated["listing_title"]
    assert payload["browser_inputs"]["main_image"] == updated["product_images"][0]
    assert payload["browser_inputs"]["product_images"] == updated["product_images"]
```

沿用现有 `_listing_copy()` helper 和 readiness 门禁；不要通过删除门禁来让测试通过。

- [ ] **步骤 6：运行复制与载荷测试**

运行：

```powershell
python -m pytest -q tests/test_product_line_flow.py tests/test_browser_schema_payload.py
```

预期：两个文件全部通过。

- [ ] **步骤 7：提交新副本差异化流程**

```powershell
git add services/api/storage.py tests/test_product_line_flow.py tests/test_browser_schema_payload.py
git commit -m "feat: vary new store listing copy titles and images"
```

### 任务 4：在基础商品维护弹窗中编辑标题结构

**文件：**

- 修改：`apps/web/index.html:232-246`
- 修改：`apps/web/app.js:980-1005`
- 修改：`apps/web/app.js:1115-1150`
- 修改：`apps/web/styles.css`
- 修改：`tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：编写 UI 结构与绑定失败测试**

在 `tests/test_store_template_workflow_ui.py` 增加：

```python
def test_base_product_maintenance_edits_structured_title_fields():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in ["base-maintenance-title-core", "base-maintenance-title-selling-points"]:
        assert f'id="{control}"' in html
    assert '$("base-maintenance-title-core").value = product.title_core || ""' in script
    assert '(product.title_selling_points || []).join("\\n")' in script
    assert 'title_core: $("base-maintenance-title-core").value.trim()' in script
    assert 'title_selling_points: $("base-maintenance-title-selling-points").value.split("\\n")' in script
    assert 'app.js?v=store-copy-variation-v1' in html
```

- [ ] **步骤 2：运行 UI 测试并确认失败**

运行：

```powershell
python -m pytest -q tests/test_store_template_workflow_ui.py -k structured_title_fields
```

预期：缺少两个控件和 JavaScript 字段绑定。

- [ ] **步骤 3：添加基础商品维护控件**

在 `apps/web/index.html` 的现有“标题”后增加：

```html
<label class="wide">核心标题<input id="base-maintenance-title-core" maxlength="300" /></label>
<label class="wide">可重排卖点<textarea id="base-maintenance-title-selling-points" rows="5" maxlength="971"></textarea></label>
```

`maxlength="971"` 只用于提前减少明显超长输入；最终的 12 项和单项 80 字符限制仍由后端校验。

- [ ] **步骤 4：实现打开回显与保存提交**

在 `openBaseProductMaintenance()` 中加入：

```javascript
$("base-maintenance-title-core").value = product.title_core || "";
$("base-maintenance-title-selling-points").value = (product.title_selling_points || []).join("\n");
```

在 `saveBaseImageOrder()` 的 payload 中加入：

```javascript
title_core: $("base-maintenance-title-core").value.trim(),
title_selling_points: $("base-maintenance-title-selling-points").value
  .split("\n")
  .map((item) => item.trim())
  .filter(Boolean),
```

保存成功提示增加卖点数量：

```javascript
result.textContent = `已保存 ${product.product_images?.length || 0} 张产品图、${product.title_selling_points?.length || 0} 个标题卖点、${product.recognized_colors?.length || 0} 个颜色。`;
```

- [ ] **步骤 5：补充样式和缓存版本**

在 `apps/web/styles.css` 增加：

```css
#base-maintenance-title-selling-points {
  min-height: 116px;
  resize: vertical;
}
```

保持现有 `.form-grid` 和移动端单列规则，不新增嵌套卡片。将 `apps/web/index.html` 的脚本资源改为：

```html
<script src="/app.js?v=store-copy-variation-v1"></script>
```

- [ ] **步骤 6：运行 UI 与语法测试**

运行：

```powershell
python -m pytest -q tests/test_store_template_workflow_ui.py
node --check apps/web/app.js
```

预期：UI 测试全部通过，Node 退出码为 0。

- [ ] **步骤 7：提交基础商品维护界面**

```powershell
git add apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_store_template_workflow_ui.py
git commit -m "feat: edit structured titles in product maintenance"
```

### 任务 5：全链路验证与浏览器验收

**文件：**

- 验证：`services/api/listing_copy_variation.py`
- 验证：`services/api/storage.py`
- 验证：`apps/web/index.html`
- 验证：`apps/web/app.js`
- 验证：`apps/web/styles.css`
- 验证：所有相关测试

- [ ] **步骤 1：运行目标测试集合**

```powershell
python -m pytest -q tests/test_listing_copy_variation.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py tests/test_browser_schema_payload.py
```

预期：全部通过，无 skipped 或 failed。

- [ ] **步骤 2：运行全量测试和静态检查**

```powershell
python -m pytest -q
node --check apps/web/app.js
git diff --check
```

预期：pytest 退出码为 0；Node 退出码为 0；`git diff --check` 无空白错误。Windows 的 LF/CRLF 提示不属于失败。

- [ ] **步骤 3：重启本地服务并检查 API**

停止当前监听 `8793` 的项目 Python 进程，使用以下环境启动：

```powershell
$env:API_PORT='8793'
Start-Process -FilePath 'python' -ArgumentList '-m','services.api.main' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8793/api/health
```

预期：返回对象的 `ok` 为 `true`。

- [ ] **步骤 4：使用 Playwright 验收基础商品维护弹窗**

使用 Playwright CLI：

1. 打开 `http://127.0.0.1:8793/`。
2. 进入“商品维护”，打开一个基础商品的“维护”。
3. 确认核心标题和卖点输入可见，移动端 `390 × 844` 下不重叠。
4. 输入核心标题及四个卖点，保存后关闭并重新打开。
5. 确认字段逐字回显。

截图保存到：

```text
output/playwright/store-copy-variation-product-maintenance.png
```

- [ ] **步骤 5：实际复制到两个测试模板并核对结果**

只在可安全删除或明确用于验收的测试数据上执行：

1. 选择同一基础商品及两个不同店铺模板。
2. 执行“复制到店铺模板”。
3. 分别打开两个新副本。
4. 确认两者主图相同且为基础商品第 1 张。
5. 确认只有第 2 至第 5 张的顺序可能不同，第 6 张以后与基础商品一致。
6. 确认核心标题完整固定在开头，卖点以完整短语重排。
7. 不生成自动化任务，不触发马帮保存或发布。

如果现有数据因“基础商品 + 模板”唯一约束不能重复创建，不删除生产副本；使用测试 fixture 或新建专用测试模板完成验收。

- [ ] **步骤 6：最终核对范围与工作树**

```powershell
git status --short
git diff --stat
git log -5 --oneline
```

确认：

- 没有改写已有副本的数据迁移。
- 没有 AI 标题调用。
- 没有发布或保存马帮页面。
- 没有把无关的既有工作树改动混入本功能提交。

若任务 5 产生必要的测试或修复改动，单独提交本计划范围内的文件：

```powershell
git add services/api/listing_copy_variation.py services/api/storage.py services/api/main.py apps/web/index.html apps/web/app.js apps/web/styles.css tests/test_listing_copy_variation.py tests/test_product_line_flow.py tests/test_store_template_workflow_api.py tests/test_store_template_workflow_ui.py tests/test_browser_schema_payload.py
git commit -m "test: verify store copy title and image variation"
```
