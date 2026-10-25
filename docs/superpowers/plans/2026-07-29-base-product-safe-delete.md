# 基础商品安全删除实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让运营人员可从基础商品卡片安全删除不再需要的商品及其可再生成的刊登数据。

**架构：** `storage` 在单个锁与单次保存中删除基础商品、关联副本、关联 SKU/价格及非终态自动化任务；终态自动化任务保留，但以其冻结的执行快照继续提供审计。`main` 暴露 DELETE 路由，Web 卡片在确认后调用并刷新相关视图。

**技术栈：** Python 标准库 HTTP API、JSON/PostgreSQL 兼容存储、原生浏览器 JavaScript。

---

### 任务 1：存储与 API 删除契约

**文件：**
- 修改：`services/api/storage.py`
- 修改：`services/api/main.py`
- 测试：`tests/test_product_line_flow.py`

- [x] **步骤 1：编写失败的存储/API 测试**

```python
deleted = storage.delete_base_product(base_product["id"])
assert deleted["deleted_listing_copy_count"] == 1
assert storage.get_item("base_products", base_product["id"]) is None
assert storage.get_item("store_listing_copies", listing_copy["id"]) is None
assert storage.get_item("automation_jobs", saved_job["id"])["listing_copy_id"] == listing_copy["id"]
```

- [x] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_product_line_flow.py::test_delete_base_product_removes_regenerable_data_and_retains_terminal_jobs -q`

预期：FAIL，缺少 `delete_base_product`。

- [x] **步骤 3：编写最少实现代码**

```python
def delete_base_product(base_product_id: str) -> dict[str, Any] | None:
    # 删除 base_products、store_listing_copies、listing_skus、listing_prices。
    # 仅删除 ready/claimed/running/needs_manual 等非终态任务；saved/failed 等终态任务保留。
```

并在 `_handle_delete` 中将 `DELETE /api/base-products/{id}` 映射至此函数，找不到时返回 404。

- [x] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_product_line_flow.py::test_delete_base_product_removes_regenerable_data_and_retains_terminal_jobs -q`

预期：PASS。

### 任务 2：基础商品卡片删除操作

**文件：**
- 修改：`apps/web/app.js`
- 测试：`tests/test_store_template_workflow_ui.py`

- [x] **步骤 1：编写失败的 UI 契约测试**

```python
assert 'data-delete-base-product' in script
assert 'deleteBaseProduct' in script
assert '/api/base-products/${encodeURIComponent(productId)}' in script
```

- [x] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_store_template_workflow_ui.py::test_base_product_cards_offer_safe_delete -q`

预期：FAIL，尚未提供删除操作。

- [x] **步骤 3：编写最少实现代码**

```js
async function deleteBaseProduct(productId) {
  if (!window.confirm("删除商品…")) return;
  await api(`/api/base-products/${encodeURIComponent(productId)}`, { method: "DELETE" });
  await Promise.all([loadBaseProducts(), loadStoreListingCopies(), loadAutomationJobs()]);
}
```

在每张基础商品卡片增加 `data-delete-base-product` 按钮；确认文字说明关联副本与非终态任务会被删除、历史执行记录保留。

- [x] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_store_template_workflow_ui.py::test_base_product_cards_offer_safe_delete -q`

预期：PASS。

### 任务 3：回归验证

**文件：**
- 测试：`tests/test_product_line_flow.py`
- 测试：`tests/test_store_template_workflow_ui.py`

- [x] **步骤 1：执行目标测试与全量回归**

运行：`python -m pytest tests/test_product_line_flow.py tests/test_store_template_workflow_ui.py -q`，然后 `python -m pytest -q`。

预期：全部通过。

- [x] **步骤 2：检查语法与工作区差异**

运行：`node --check apps/web/app.js` 与 `git diff --check`。

预期：命令以 0 退出。
