# 店铺模板同店复制实现计划

> **面向 AI 代理的工作者：** 逐任务实现此计划，步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 店铺模板卡片增加「复制」，同店生成 `<模板名> - 副本` 并打开编辑器。

**架构：** 新增独立的存储层函数与 POST 路由，避免复用「另存为模板」的换店清空逻辑；前端只加一个卡片按钮和一个事件分支。

**技术栈：** Python 3（services/api + pytest）、原生 JS（apps/web/app.js）、PowerShell 静态断言。

---

## 文件结构

- 修改：`outputs/ai-listing-system/services/api/storage.py`——新增 `duplicate_store_template` 与同店重名序号助手。
- 修改：`outputs/ai-listing-system/services/api/main.py`——新增 `POST /api/store-templates/{id}/duplicate` 路由。
- 修改：`outputs/ai-listing-system/apps/web/app.js`——卡片「复制」按钮、`copyStoreTemplate`、点击委托分支。
- 修改：`outputs/ai-listing-system/apps/web/index.html`——app.js 缓存版本追加 `feature=store-template-duplicate-v1`。
- 修改：`outputs/ai-listing-system/CONTEXT.md`——在 `Store template copy` 条目补同店复制语义。
- 修改：`outputs/ai-listing-system/tests/test_store_template_workflow_api.py`——存储层与接口层用例。
- 修改：`outputs/ai-listing-system/tests/test_store_template_workflow_ui.py`——前端接线断言。

### 任务 1：存储层复制

- [x] **步骤 1：写失败用例**

在 `tests/test_store_template_workflow_api.py` 增加：复制同店模板后 `id` / `current_version_id` 变化、`template_name` 为 `Source - 副本`、促销活动与两个水印照抄、`promotion_activity_id` 不变、源模板未被修改、`store_template_versions` 新增一条；连续复制第二次得到 `Source - 副本 (2)`。

运行：

```powershell
python -m pytest tests/test_store_template_workflow_api.py -k duplicate -q
```

预期：失败（`duplicate_store_template` 不存在）。

- [x] **步骤 2：实现 `duplicate_store_template`**

在 `copy_store_template` 之后实现：`locked_store(split_keys=())`、源模板必须存在且未归档、深拷贝后重设身份与时间戳、CNSC 模板重算 `missing_configuration` / `status`、追加 `store_template_versions`。

- [x] **步骤 3：用例转绿**

运行步骤 1 的命令，预期全部通过。

### 任务 2：接口层

- [x] **步骤 1：写失败用例**

在同一个测试文件增加接口用例：`POST /api/store-templates/template-source/duplicate` 返回 201 且同店同站点；未知模板返回 404。

- [x] **步骤 2：新增路由**

在 `main.py` 的 `POST /api/store-templates/{id}/copy` 之后加 `duplicate` 分支，映射 `LookupError → 404`、`ValueError → 422`。

- [x] **步骤 3：用例转绿**

运行 `python -m pytest tests/test_store_template_workflow_api.py -q`，预期全部通过。

### 任务 3：前端入口

- [x] **步骤 1：写失败断言**

在 `tests/test_store_template_workflow_ui.py` 增加断言：`data-copy-store-template`、`copyStoreTemplate`、`/duplicate` 出现在 `app.js`；`index.html` 的 app.js 版本串包含 `store-template-duplicate-v1`。

- [x] **步骤 2：实现按钮与处理器**

卡片操作区加入 `<button type="button" data-copy-store-template="…">复制</button>`；新增 `copyStoreTemplate(templateId)`：调用 `/duplicate`、`loadStoreTemplates()`、`openStoreTemplateEditor(copied.id)`、成功提示；在点击委托里按 `data-edit-store-template` / `data-delete-store-template` 之间的位置接上 `data-copy-store-template` 分支。

- [x] **步骤 3：用例转绿**

运行 `python -m pytest tests/test_store_template_workflow_ui.py -q`，预期全部通过。

### 任务 4：文档与全量验证

- [x] **步骤 1：更新词表**

`CONTEXT.md` 的 `Store template copy` 条目补一句：同店复制保留全部店铺绑定值（类目、价格、必填项、活动、水印），只有换站点时才清空。

- [x] **步骤 2：语法与全量测试**

运行：

```powershell
node --check apps/web/app.js
python -m pytest tests -q
```

预期：JS 语法通过；pytest 全绿。

- [x] **步骤 3：页面验证**

重启本地 API 后刷新页面，确认卡片出现「复制」，点击后列表新增 `- 副本` 且编辑器打开，源模板配置未变。
