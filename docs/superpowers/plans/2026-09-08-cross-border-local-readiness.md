# 跨境与本土 readiness 区分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 CNSC 跨境与 site 本土副本的 readiness 缺项、促销提醒、尺码图校验和类目属性展示严格隔离。

**架构：** 保留现有 `_check_automation_readiness` 与 `_runtime_configuration_missing` 作为后端事实来源，以 `listing_mode == "cnsc_global"` 分支公共字段。前端继续渲染后端字段，不新增名称推断。类目属性错误保持 `category_attributes.<field_id>` 形状。

**技术栈：** Python、pytest、现有静态前端契约测试。

---

### 任务 1：补充模式隔离的失败回归测试

**文件：**
- 修改：`outputs/ai-listing-system/tests/test_automation_readiness.py`
- 修改：`outputs/ai-listing-system/tests/test_product_line_flow.py`
- 修改：`outputs/ai-listing-system/tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：编写失败测试**

在 `test_automation_readiness.py` 增加两个最小副本：本土副本带 CNSC 字段但不应产生 `target_site_stores`、`site_price_matrix`、`warehouse_stock_defaults`；跨境副本缺少尺码图且带普通促销字段，不应产生 `size_chart_image` 或普通促销缺项。

在 `test_product_line_flow.py` 增加类目属性 ID 回归，断言 `category_attributes.100022`、`category_attributes.100134`、`category_attributes.100168` 能进入 readiness 缺项。

在 UI 契约测试中断言跨境促销解析文案和属性 ID 映射存在。

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
python -m pytest outputs/ai-listing-system/tests/test_automation_readiness.py outputs/ai-listing-system/tests/test_product_line_flow.py::test_category_required_field_is_reported_by_automation_readiness outputs/ai-listing-system/tests/test_store_template_workflow_ui.py -q
```

预期：新增模式隔离断言失败，现有测试错误应保持为功能断言失败而非导入错误。

### 任务 2：按 listing_mode 隔离后端 readiness

**文件：**
- 修改：`outputs/ai-listing-system/services/api/storage.py`
- 测试：`outputs/ai-listing-system/tests/test_automation_readiness.py`

- [ ] **步骤 1：调整公共字段集合**

在 `_check_automation_readiness` 中先计算 `is_cnsc = listing_copy.get("listing_mode") == "cnsc_global"`。仅在 `not is_cnsc` 时加入 `size_chart_image` 与普通 `mabang_discount_id`；仅在 `is_cnsc` 时运行成本价、仓库库存和 `_cnsc_configuration_problems` 分支。保留基础资料、SKU、物流和快照类目属性的公共校验。

- [ ] **步骤 2：隔离运行时促销状态**

在 `_runtime_configuration_missing` 保持 CNSC 早退语义，并补充本土分支只读取 `promotion_activity_id` / `mabang_discount_id` 与本土店铺活动目录，确保 CNSC 不生成 `promotion_unresolved`、`promotion_unconfigured` 等普通活动字段。

- [ ] **步骤 3：运行测试确认通过**

运行：

```powershell
python -m pytest outputs/ai-listing-system/tests/test_automation_readiness.py outputs/ai-listing-system/tests/test_product_line_flow.py::test_category_required_field_is_reported_by_automation_readiness outputs/ai-listing-system/tests/test_cnsc_copy_flow.py -q
```

预期：全部通过；CNSC 现有站点活动/价格矩阵测试保持绿色。

### 任务 3：锁定前端文案与属性 ID 展示

**文件：**
- 修改：`outputs/ai-listing-system/apps/web/app.js`
- 修改：`outputs/ai-listing-system/tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：补充文案契约测试**

断言 `promotion_unresolved` 的完整中文文案仍包含“促销目录中未找到该活动”和“执行时可能失败”，并断言 `listingCopyReadinessLabel` 对 `category_attributes.100022` 输出具体 ID。

- [ ] **步骤 2：实现最小前端调整**

只在后端字段缺少旧映射时补充稳定的 `category_attributes.<id>` 文案映射，保留现有 CNSC 专用字段分区；不根据店铺名称或国家增加前端判定。

- [ ] **步骤 3：运行 UI 静态测试**

运行：

```powershell
python -m pytest outputs/ai-listing-system/tests/test_store_template_workflow_ui.py -q
```

预期：全部通过。

### 任务 4：完整验证与收尾

**文件：**
- 验证：`outputs/ai-listing-system/services/api/storage.py`
- 验证：`outputs/ai-listing-system/apps/web/app.js`

- [ ] **步骤 1：运行相关完整测试**

```powershell
python -m pytest outputs/ai-listing-system/tests/test_automation_readiness.py outputs/ai-listing-system/tests/test_template_readiness.py outputs/ai-listing-system/tests/test_category_profile_integration.py outputs/ai-listing-system/tests/test_cnsc_copy_flow.py outputs/ai-listing-system/tests/test_product_line_flow.py outputs/ai-listing-system/tests/test_store_template_workflow_ui.py -q
```

- [ ] **步骤 2：检查变更范围**

```powershell
git diff --check
git status --short
```

确认只包含本计划涉及的实现、测试和文档变更，保留工作区中其他既有修改。

- [ ] **步骤 3：提交实现**

```powershell
git add outputs/ai-listing-system/services/api/storage.py outputs/ai-listing-system/apps/web/app.js outputs/ai-listing-system/tests/test_automation_readiness.py outputs/ai-listing-system/tests/test_product_line_flow.py outputs/ai-listing-system/tests/test_store_template_workflow_ui.py
git commit -m "fix: 区分跨境与本土 readiness 校验"
```
