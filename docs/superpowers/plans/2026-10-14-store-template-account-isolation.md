# 店铺模板按马帮账号隔离实现计划

> **面向 AI 代理的工作者：** 逐任务实现此计划，步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 模板记住 `account_id`，列表/店铺下拉/复制选择器按当前马帮账号隔离。

**架构：** 存储层新增唯一的归属推导函数（显式账号 → 店铺唯一命中 → 默认账号），创建/更新/导入/复制/同店复制都经过它；接口层支持 `account_id` 过滤；前端在店铺模板页加账号切换并把店铺下拉收窄到该账号。

**技术栈：** Python 3（services/api + pytest）、原生 JS（apps/web）、PowerShell 静态断言。

---

## 文件结构

- 修改：`services/api/storage.py`——`_normalize_store_template_payload` 写入 `account_id`；新增 `_resolve_store_template_account`、`_migrate_store_template_accounts`；`list_store_templates(account_id)`；创建/更新/复制/同店复制接线。
- 修改：`services/api/mabang_page_import.py`——导入创建的模板带上采集账号。
- 修改：`services/api/main.py`——`GET /api/store-templates` 支持 `account_id` 过滤。
- 修改：`apps/web/index.html`——模板页「马帮账号」下拉 + 资源缓存版本。
- 修改：`apps/web/app.js`——账号态、账号下拉渲染、切换账号、店铺下拉收窄、卡片账号标注、保存带账号。
- 修改：`apps/web/styles.css`——账号字段与卡片账号样式。
- 新增：`tests/test_store_template_account_isolation.py`。
- 修改：`tests/test_store_template_workflow_api.py`、`tests/test_store_template_workflow_ui.py`。

### 任务 1：归属推导与存储层

- [x] **步骤 1：写失败用例**

新增 `tests/test_store_template_account_isolation.py`：显式账号写入、按店铺推导、跨账号被拒、同名店铺不猜、列表按账号过滤、换店铺跟随账号、复制跟随目标账号、同店复制保留账号、回填与幂等。

运行：

```powershell
python -m pytest tests/test_store_template_account_isolation.py -q
```

预期：失败（函数不存在）。

- [x] **步骤 2：实现**

`_normalize_store_template_payload` 增加 `account_id`；实现 `_resolve_store_template_account` 与 `_migrate_store_template_accounts`，并在 `create_store_template`、`update_store_template`、`_copy_store_template_payload`、`duplicate_store_template`、`_normalize_legacy_store_shape` 接线。

- [x] **步骤 3：用例转绿**

运行步骤 1 的命令，预期全部通过。

### 任务 2：接口与导入

- [x] **步骤 1：写失败用例**

在 `tests/test_store_template_workflow_api.py` 增加 `GET /api/store-templates?account_id=` 过滤与不过滤两种结果。

- [x] **步骤 2：实现**

`main.py` 的 GET 分支读取 `query["account_id"]`；`mabang_page_import._upsert_template` 接收并写入账号；`storage.confirm_mabang_page_import` 用 run 上记录的账号而不是默认账号。

- [x] **步骤 3：用例转绿**

运行 `python -m pytest tests/test_store_template_workflow_api.py -q`，预期全部通过。

### 任务 3：页面隔离

- [x] **步骤 1：写失败断言**

在 `tests/test_store_template_workflow_ui.py` 增加：`store-template-account`、`currentStoreTemplateAccountId`、`switchStoreTemplateAccount`、`storeTemplateScopedStoreOptions`、`storeTemplatesAll`、请求带 `account_id`、卡片账号标注与样式。

- [x] **步骤 2：实现**

`index.html` 加下拉；`app.js` 加账号态与切换、店铺下拉按账号收窄、保存带账号、卡片标注账号、跨账号打开模板时自动切账号；`styles.css` 加样式。

- [x] **步骤 3：用例转绿**

运行 `python -m pytest tests/test_store_template_workflow_ui.py -q`，预期新增用例通过（已有的样式类断言在本分支为历史遗留失败，见验证记录）。

### 任务 4：全量验证

- [x] **步骤 1：语法与全量测试**

```powershell
node --check apps/web/app.js
python -m pytest tests -q
```

- [x] **步骤 2：真实数据回填确认**

用只读脚本确认 `data/store.json` 的 3 个在用模板都拿到账号，归档模板未变，二次运行不再写入。

- [x] **步骤 3：页面验证**

重启本地 API 后刷新页面：切账号只看到该账号模板；店铺下拉只列该账号店铺；卡片显示账号名；复制到店铺模板选择器只列当前账号模板。
