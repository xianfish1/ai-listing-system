# 本地文件夹批量导入商品实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 选择一次本地上级目录后，把其中多个一级商品目录串行上传并分别创建为基础商品，同时保持现有单商品目录导入兼容。

**架构：** 新增一个无 DOM 依赖的浏览器/Node 双用辅助模块，负责目录树分组和失败隔离的串行批处理；现有 `app.js` 继续负责上传会话、进度文案、AI 创建以及最终页面刷新。后端单商品上传接口保持不变，确保 NAS 与本地创建最终仍走同一基础商品创建逻辑。

**技术栈：** 原生 JavaScript、CommonJS/浏览器 UMD、Node `node:test`、Python `pytest`、现有 HTML/CSS/REST API。

---

## 文件结构

- 创建：`apps/web/local-folder-batch.js` — 纯函数目录分组及串行批处理，不访问 DOM。
- 创建：`tests/local_folder_batch.test.cjs` — 覆盖批量识别、单商品兼容、根文件忽略、嵌套路径和失败隔离。
- 修改：`apps/web/index.html` — 在 `app.js` 前加载辅助模块，并让按钮文案明确支持批量。
- 修改：`apps/web/app.js` — 将现有单次上传拆成可复用的单商品上传函数，并接入批量进度、确认和汇总。
- 修改：`tests/test_store_template_workflow_ui.py` — 验证页面正确接入新模块和批量文案。

### 任务 1：目录分组与串行批处理核心

**文件：**
- 创建：`apps/web/local-folder-batch.js`
- 创建：`tests/local_folder_batch.test.cjs`

- [ ] **步骤 1：编写失败的目录分组测试**

创建测试中的轻量文件对象，并断言：

```javascript
const files = [
  file('批次/AL0001/main.jpg', 3),
  file('批次/AL0001/颜色/black.jpg', 4),
  file('批次/AL0002/main.png', 5),
  file('批次/readme.jpg', 6),
];
const plan = batch.planLocalFolderImport(files, SUPPORTED);
assert.equal(plan.mode, 'batch');
assert.deepEqual(plan.groups.map((group) => group.folderName), ['AL0001', 'AL0002']);
assert.deepEqual(plan.groups[0].manifest.map((item) => item.path), ['main.jpg', '颜色/black.jpg']);
assert.equal(plan.ignoredRootFileCount, 1);
```

另加单商品断言：只有一个有效一级子目录，或根目录自身就是商品目录时，`mode === 'single'`，清单路径继续相对所选根目录。

- [ ] **步骤 2：运行测试并确认失败**

运行：`node --test tests/local_folder_batch.test.cjs`

预期：FAIL，提示找不到 `../apps/web/local-folder-batch.js`。

- [ ] **步骤 3：实现最小目录分组模块**

实现并导出以下稳定接口：

```javascript
function planLocalFolderImport(files, supportedExtensions) {
  // 统一斜杠、过滤隐藏路径与不支持扩展名；以 webkitRelativePath
  // 的第一段作为所选根目录、第二段作为一级商品候选。
  // 两个及以上有效一级候选 => batch；否则 => single。
  return { mode, rootName, groups, ignoredRootFileCount, filteredFileCount };
}

async function runSequentialImport(groups, worker, onProgress = () => {}) {
  const successes = [];
  const failures = [];
  for (let index = 0; index < groups.length; index += 1) {
    onProgress({ phase: 'start', index, total: groups.length, group: groups[index], successes, failures });
    try {
      successes.push(await worker(groups[index], index));
    } catch (error) {
      failures.push({ folderName: groups[index].folderName, error });
    }
    onProgress({ phase: 'finish', index, total: groups.length, group: groups[index], successes, failures });
  }
  return { successes, failures };
}
```

浏览器挂载到 `window.LocalFolderBatch`，Node 环境使用 `module.exports`。

- [ ] **步骤 4：补充并运行失败隔离测试**

```javascript
const result = await batch.runSequentialImport(groups, async (group) => {
  if (group.folderName === 'AL0002') throw new Error('upload failed');
  return { id: group.folderName };
});
assert.deepEqual(result.successes, [{ id: 'AL0001' }, { id: 'AL0003' }]);
assert.deepEqual(result.failures.map((item) => item.folderName), ['AL0002']);
```

运行：`node --test tests/local_folder_batch.test.cjs`

预期：所有测试 PASS。

- [ ] **步骤 5：提交核心模块**

```bash
git add apps/web/local-folder-batch.js tests/local_folder_batch.test.cjs
git commit -m "feat: plan local product folder batches"
```

### 任务 2：接入本地创建界面与上传流程

**文件：**
- 修改：`apps/web/index.html:169-176`
- 修改：`apps/web/app.js:5423-5528`
- 修改：`tests/test_store_template_workflow_ui.py:33-43`

- [ ] **步骤 1：扩展 UI 接线测试并确认失败**

在 `test_base_product_maintenance_wires_local_folder_upload` 中增加：

```python
assert 'local-folder-batch.js' in html
assert '从本地批量创建' in html
assert 'planLocalFolderImport' in script
assert 'runSequentialImport' in script
assert '批量导入完成' in script
assert '失败' in script
```

运行：`python -m pytest tests/test_store_template_workflow_ui.py::test_base_product_maintenance_wires_local_folder_upload -q`

预期：FAIL，因为 HTML 与 `app.js` 尚未接入批量模块。

- [ ] **步骤 2：加载模块并更新入口文案**

在 `app.js` 前加载：

```html
<script src="/local-folder-batch.js?v=1"></script>
<script src="/app.js?...existing query..."></script>
```

按钮改为“从本地批量创建”，文件输入继续使用 `webkitdirectory directory multiple`。

- [ ] **步骤 3：抽取单商品上传函数**

将现有上传主体抽为：

```javascript
async function uploadLocalProductGroup(group, onProgress) {
  let uploadId = '';
  try {
    const session = await api('/api/local-folder-uploads', {
      method: 'POST',
      body: JSON.stringify({ folder_name: group.folderName, files: group.manifest }),
    });
    uploadId = session.upload_id;
    // 按 group.files 顺序上传，并通过 onProgress 汇报字节进度。
    return await api(`/api/local-folder-uploads/${encodeURIComponent(uploadId)}/complete`, {
      method: 'POST',
      body: JSON.stringify({ category }),
    });
  } catch (error) {
    if (uploadId) await api(`/api/local-folder-uploads/${encodeURIComponent(uploadId)}`, { method: 'DELETE' }).catch(() => {});
    throw error;
  }
}
```

清单和上传文件必须来自同一个 `group`，保留组内嵌套相对路径。

- [ ] **步骤 4：接入批量确认、进度和汇总**

`createBaseProductFromLocalFolder` 调用 `planLocalFolderImport`。批量模式使用确认文案：

```javascript
const ignoredHint = plan.ignoredRootFileCount ? `，根目录 ${plan.ignoredRootFileCount} 个素材将忽略` : '';
if (plan.mode === 'batch' && !window.confirm(`识别到 ${plan.groups.length} 个商品文件夹${ignoredHint}。开始批量导入？`)) return;
```

随后用 `runSequentialImport` 串行处理；进度至少显示当前序号、文件夹名、成功数和失败数。结束文案格式：

```javascript
`批量导入完成：成功 ${successes.length} 个，失败 ${failures.length} 个${failureNames}`
```

至少一个成功时只在全部处理完后执行一次 `Promise.all([loadBaseProducts(), loadAssets(), loadReadiness()])`；全部失败时抛出包含失败文件夹名的错误。单商品模式保持无需确认并显示原有成功文案。

- [ ] **步骤 5：运行前端接线和核心测试**

运行：

```bash
node --test tests/local_folder_batch.test.cjs
python -m pytest tests/test_store_template_workflow_ui.py::test_base_product_maintenance_wires_local_folder_upload -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交 UI 集成**

```bash
git add apps/web/index.html apps/web/app.js tests/test_store_template_workflow_ui.py
git commit -m "feat: batch import local product folders"
```

### 任务 3：回归验证与交付

**文件：**
- 验证：`tests/test_local_folder_uploads.py`
- 验证：`tests/local_folder_batch.test.cjs`
- 验证：`tests/test_store_template_workflow_ui.py`

- [ ] **步骤 1：运行本地上传后端回归**

运行：`python -m pytest tests/test_local_folder_uploads.py -q`

预期：全部 PASS，证明单商品上传、安全路径校验和完成接口未回归。

- [ ] **步骤 2：运行完整相关测试集**

运行：

```bash
node --test tests/local_folder_batch.test.cjs
python -m pytest tests/test_local_folder_uploads.py tests/test_store_template_workflow_ui.py -q
```

预期：全部 PASS，无 warning 导致失败。

- [ ] **步骤 3：执行静态检查**

运行：

```bash
node --check apps/web/local-folder-batch.js
node --check apps/web/app.js
git diff --check
```

预期：命令退出码均为 0，无语法错误和空白错误。

- [ ] **步骤 4：检查改动范围并提交遗漏的验证修正**

运行：`git status --short` 和 `git diff -- apps/web/local-folder-batch.js apps/web/app.js apps/web/index.html tests/local_folder_batch.test.cjs tests/test_store_template_workflow_ui.py`

预期：只审阅本功能涉及文件；不改动或提交工作区中原有的其他用户变更。如验证过程产生必要修正，仅提交上述功能文件，提交信息使用 `fix: finalize local folder batch import`。
