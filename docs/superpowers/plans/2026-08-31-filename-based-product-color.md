# 文件名主图与颜色识别实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 NAS 和本地文件夹导入依据 `主图-`/`Main-` 与 `SKU-颜色-` 文件名稳定生成主图、产品图和英文颜色候选。

**架构：** 在 `workers/asset_scanner/scanner.py` 的文件名解析层增加前缀优先级和 SKU 颜色提取，保持现有资产字段和存储 API 不变。扫描器生成的 `sku_option` 会继续被 `storage.create_base_product_from_folder` 汇总为颜色列表和变体映射；未知中文颜色由视觉生成结果提供英文映射，并在基础商品更新时同步颜色列表和对应图片键。

**技术栈：** Python 3、pytest、现有 asset scanner/storage pipeline。

---

### 任务 1：补充文件名规则失败测试

**文件：**
- 修改：`tests/test_product_line_flow.py`

- [ ] **步骤 1：编写失败测试**

新增直接扫描断言，覆盖前缀和颜色候选：

```python
def test_filename_prefixes_classify_main_and_sku_color(tmp_path: Path) -> None:
    folder = tmp_path / "AL0099测试"
    folder.mkdir()
    for name in ["主图-正面.jpg", "Main-侧面.jpg", "SKU-红色-01.jpg", "sku-奶油白.jpg"]:
        (folder / name).write_bytes(b"image")

    result = scan_asset_root(folder, mode="recursive")
    by_name = {item["file_name"]: item for item in result["assets"]}

    assert by_name["主图-正面.jpg"]["asset_type"] == "main"
    assert by_name["Main-侧面.jpg"]["asset_type"] == "main"
    assert by_name["SKU-红色-01.jpg"]["asset_type"] == "variant"
    assert by_name["SKU-红色-01.jpg"]["sku_option"] == "Red"
    assert by_name["sku-奶油白.jpg"]["asset_type"] == "variant"
    assert by_name["sku-奶油白.jpg"]["sku_option"] == "奶油白"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`$env:PYTHONPATH='.'; pytest tests/test_product_line_flow.py::test_filename_prefixes_classify_main_and_sku_color -q`

预期：FAIL，当前 `SKU-红色-01.jpg` 不会从前缀提取颜色，`sku-奶油白.jpg` 也不会稳定归类为变体。

### 任务 2：实现前缀与 SKU 颜色解析

**文件：**
- 修改：`workers/asset_scanner/scanner.py:_detect_asset_type`、`_detect_sku_option`
- 测试：`tests/test_product_line_flow.py`

- [ ] **步骤 1：实现最少解析逻辑**

在 `_detect_asset_type` 的详情/目录回退前增加：

```python
if _is_main_filename(stem):
    return "main"
if _sku_color_candidate(stem):
    return "variant"
```

并增加确定性辅助函数：

```python
def _is_main_filename(stem: str) -> bool:
    value = str(stem or "").strip().lower()
    return value.startswith("main-") or value.startswith("main_") or value.startswith("主图-") or value.startswith("主图_")


def _sku_color_candidate(stem: str) -> str:
    value = str(stem or "").strip()
    prefix = value[:4].lower()
    if prefix != "sku-" and not value[:4].lower().startswith("sku_"):
        return ""
    remainder = value[4:]
    return remainder.split("-", 1)[0].split("_", 1)[0].strip()
```

`_detect_sku_option` 先读取 `_sku_color_candidate`，再通过现有中文/英文颜色映射；未知候选原样返回。解析顺序必须保证 `SKU-` 无颜色段时继续走原有颜色词回退。

- [ ] **步骤 2：运行规则测试确认通过**

运行：`$env:PYTHONPATH='.'; pytest tests/test_product_line_flow.py::test_filename_prefixes_classify_main_and_sku_color -q`

预期：PASS。

- [ ] **步骤 3：Commit**

```bash
git add workers/asset_scanner/scanner.py tests/test_product_line_flow.py
git commit -m "feat: parse product image prefixes and sku colors"
```

### 任务 3：验证颜色列表、变体映射与导入兼容性

**文件：**
- 修改：无
- 测试：`tests/test_product_line_flow.py`、`tests/test_local_folder_uploads.py`、`tests/test_vision_listing.py`

- [ ] **步骤 1：运行基础商品与上传回归**

运行：`$env:PYTHONPATH='.'; pytest tests/test_product_line_flow.py tests/test_local_folder_uploads.py tests/test_vision_listing.py -q`

预期：新增规则通过，`recognized_colors` 和 `variant_image_map` 能消费 `sku_option`，视觉详情描述行为保持通过；仅报告与本次无关的既有失败。

- [ ] **步骤 2：检查导入字段**

运行：`$env:PYTHONPATH='.'; python -c "from pathlib import Path; from workers.asset_scanner.scanner import scan_asset_root; print('scanner import ok')"`

预期：输出 `scanner import ok`，无导入错误。

- [ ] **步骤 3：检查差异**

运行：`git diff --check; git status --short`

预期：无空白错误；只新增本计划、扫描器和测试相关变更，保留其他工作区修改。

### 任务 4：补充视觉颜色英文归一化

**文件：**
- 修改：`services/api/vision_listing.py`、`services/api/main.py`
- 测试：`tests/test_vision_listing.py`

- [ ] **步骤 1：编写失败测试**

让模型返回 `colors: {"奶油白": "Cream White"}`，断言解析结果暴露颜色映射，并验证 AI enrichment 将基础商品的 `recognized_colors` 和 `variant_image_map` 的中文键更新为英文键。

- [ ] **步骤 2：运行测试确认失败**

运行：`$env:PYTHONPATH='.'; pytest tests/test_vision_listing.py -k color -q`

预期：FAIL，因为当前视觉结果协议没有 `colors` 字段，AI enrichment 也不会更新颜色字段。

- [ ] **步骤 3：实现最少颜色映射代码**

扩展视觉结果为受限的 `colors` 映射，提示模型只翻译输入的 `recognized_colors`，并在 `_enrich_with_vision_listing` 中按大小写不敏感原键替换 `recognized_colors` 与 `variant_image_map`；未知或无效翻译保留原值。

- [ ] **步骤 4：运行视觉与导入测试确认通过**

运行：`$env:PYTHONPATH='.'; pytest tests/test_vision_listing.py tests/test_product_line_flow.py tests/test_local_folder_uploads.py -q`

预期：新增颜色翻译测试通过，既有导入和视觉测试保持通过。

- [ ] **步骤 5：Commit**

```bash
git add services/api/vision_listing.py services/api/main.py tests/test_vision_listing.py docs/superpowers/specs/2026-08-31-filename-based-product-color-design.md docs/superpowers/plans/2026-08-31-filename-based-product-color.md
git commit -m "feat: normalize imported colors with vision hints"
```
