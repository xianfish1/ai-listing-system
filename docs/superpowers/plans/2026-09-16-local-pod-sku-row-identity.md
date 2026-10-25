# 本土 POD SKU 行身份校验实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 本土 POD 自动化只能把 SKU 写入马帮中颜色和尺码均匹配的行，避免颜色顺序变化导致错位保存。

**架构：** 在 Playwright 执行器中从 SKU 网格实际行提取颜色和尺码，构造 `颜色\u0000尺码` 身份键；计划 SKU 也构造同样的键。填写时按键查找计划行而不是数组下标，并对重复、缺失和多余行返回明确阻断错误。写完后在保存前回读每行 SKU 并核验。

**技术栈：** Node.js 内置测试运行器、Playwright 页面求值、马帮 VXE 表格。

---

### 任务 1：锁定行身份匹配的回归行为

**文件：**
- 修改：`tests/mabang_playwright_executor.test.cjs`
- 修改：`scripts/mabang_playwright_executor.cjs`

- [x] **步骤 1：编写失败的测试**

```js
test('本土 SKU 行按颜色和尺码匹配，不能按表格顺序错位', () => {
  const plan = [
    { color_code: 'Red', size: '100cm', sku_code: 'AL0471_Red||100cm' },
    { color_code: 'PureWhite', size: '100cm', sku_code: 'AL9999-PureWhite-100cm' },
  ];
  const pageRows = [
    { color: 'PureWhite', size: '100cm', sku: '' },
    { color: 'Red', size: '100cm', sku: '' },
  ];
  assert.deepEqual(executor.matchSiteSkuRows(plan, pageRows), [
    { row_index: 0, sku_code: 'AL9999-PureWhite-100cm' },
    { row_index: 1, sku_code: 'AL0471_Red||100cm' },
  ]);
});
```

- [x] **步骤 2：运行测试验证失败**

运行：`node --test tests/mabang_playwright_executor.test.cjs`

预期：FAIL，`executor.matchSiteSkuRows is not a function`。

- [x] **步骤 3：编写最少实现代码**

```js
function matchSiteSkuRows(plannedSkus, pageRows) {
  // 以规范化的 color + size 唯一键配对；重复、缺失和意外行抛 BlockedError。
}
```

`fillSkuGrid` 在本土模式调用该函数，以匹配结果写 SKU；填写后读取 VXE 行的 SKU 字段并验证每一行。

- [x] **步骤 4：运行测试验证通过**

运行：`node --test tests/mabang_playwright_executor.test.cjs`

预期：PASS，全部子测试通过。

- [x] **步骤 5：执行范围验证**

运行：`node --check scripts/mabang_playwright_executor.cjs` 与 `node --test tests/mabang_playwright_executor.test.cjs`

预期：两个命令退出码均为 0。
