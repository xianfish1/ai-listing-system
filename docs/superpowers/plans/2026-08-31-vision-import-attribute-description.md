# 视觉导入属性详情描述实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 NAS 文件夹导入和本地文件夹上传在启用详情生成时，将产品图识别出的属性以 ERP `Key : Value` 格式作为唯一详情描述，并保留内部 `recognized_attributes` 兼容字段。

**架构：** 保持现有 `_enrich_with_vision_listing` 和 `vision_listing.generate_listing_from_images` 入口不变，只调整结果组装策略。模型仍返回标题、文案和属性，服务端在属性填充开启且存在属性时用格式化属性块覆盖描述；两条导入 API 无需分叉。

**技术栈：** Python 3、内置 JSON/pytest、现有视觉模型网关和 HTTP API。

---

### 任务 1：为属性-only 描述建立失败测试

**文件：**
- 修改：`tests/test_vision_listing.py`

- [x] **步骤 1：编写失败测试**

在 `test_attributes_parsed_and_appended_to_description` 中将期望改为：结果描述等于由属性渲染的完整块，且不包含 `Soft polyester hoodie for daily wear.`；在 `test_attributes_fill_description_when_prose_is_empty` 中保留属性-only 期望，确保空文案路径仍成立。

```python
assert result.description == (
    "Applicable Season : Four Seasons\n\n"
    "Applicable Scene : Daily\n\n"
    "Detachable Part : NONE\n\n"
    "Material : Polyester\n\n"
    "Style : Casual\n\n"
    "Sleeve Length(cm) : Full\n\n"
    "Item Type : Hoodies"
)
assert "Soft polyester hoodie for daily wear." not in result.description
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_vision_listing.py::test_attributes_parsed_and_appended_to_description -q`

预期：FAIL，因为当前实现把模型文案和属性块拼接在一起。

### 任务 2：实现属性-only 描述组装

**文件：**
- 修改：`services/api/vision_listing.py:generate_listing_from_images`
- 测试：`tests/test_vision_listing.py`

- [x] **步骤 1：编写最少实现代码**

将描述组装从：

```python
if want_description and fill_attributes_into_description:
    description = with_attributes_block(description, attributes)
```

改为：

```python
if want_description and fill_attributes_into_description and attributes:
    description = format_attributes_block(attributes)
```

这样保留 `result.attributes` 和标题，同时在属性存在时完全覆盖 prose；属性为空时沿用模型描述，兼容无属性结果和已有错误处理。

- [x] **步骤 2：运行视觉测试确认通过**

运行：`pytest tests/test_vision_listing.py -q`

预期：PASS，包含属性格式、关闭开关、空属性、无图片和网关失败等测试。

- [x] **步骤 3：Commit**

```bash
git add services/api/vision_listing.py tests/test_vision_listing.py
git commit -m "feat: use recognized attributes as import description"
```

### 任务 3：验证 NAS 与本地导入入口的兼容行为

**文件：**
- 修改：无（仅在已有 API 测试中验证）
- 测试：`tests/test_local_folder_uploads.py`、`tests/test_product_line_flow.py`、`tests/test_api_boundaries.py`

- [x] **步骤 1：运行本地上传和基础商品导入测试**

运行：`pytest tests/test_local_folder_uploads.py tests/test_product_line_flow.py tests/test_api_boundaries.py -q`

预期：PASS；两条入口仍创建商品，AI 失败仍不阻断创建。

- [x] **步骤 2：检查工作区差异**

运行：`git diff --check; git status --short`

预期：无空白错误；只出现本计划涉及的视觉模块、测试和计划文件变更，保留用户已有的其他修改。

### 任务 4：回归验证并完成交接

**文件：**
- 测试：`tests/test_vision_listing.py`、`tests/test_local_folder_uploads.py`、`tests/test_product_line_flow.py`、`tests/test_description_generation.py`

- [x] **步骤 1：运行聚焦回归套件**

运行：`pytest tests/test_vision_listing.py tests/test_local_folder_uploads.py tests/test_product_line_flow.py tests/test_description_generation.py -q`

预期：全部 PASS；若仓库已有无关失败，记录具体测试名和输出，不扩大修改范围。

- [x] **步骤 2：检查描述格式实现**

运行：`rg -n "with_attributes_block|format_attributes_block|recognized_attributes" services/api/vision_listing.py services/api/main.py tests/test_vision_listing.py`

预期：属性格式化函数仍可复用，导入路径保存 `recognized_attributes`，描述生成分支在属性填充开启时使用属性-only 块。

- [x] **步骤 3：Commit（如任务 2 后仍有测试或计划状态变更）**

```bash
git add services/api/vision_listing.py tests/test_vision_listing.py docs/superpowers/plans/2026-08-31-vision-import-attribute-description.md
git commit -m "test: verify vision import description behavior"
```
