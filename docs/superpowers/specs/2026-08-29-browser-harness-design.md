# Browser Harness 受限接管设计

日期：2026-08-29  
状态：待实现

## 1. 背景与目标

当前自动化以 Playwright 为唯一执行器，失败后只调用 AI 做文字诊断。CNSC 店铺选择场景中，`#shops-wap` 初始只显示“所选店铺 0 个”触发器，店铺列表在展开和异步渲染后才出现，容易造成定位超时并返回 `cnsc_target_site_not_found`。

本设计增加一个受限 Browser Harness：Playwright 遇到可恢复的页面结构或时序错误时，AI 复用当前 CDP 浏览器和当前详情页补完失败步骤，然后回到现有的确定性校验流程。目标是提高恢复能力，同时保持“只保存草稿、绝不发布”的安全边界。

## 2. 方案与范围

采用方案 B：当前页面接管并补完失败步骤。Harness 只执行本次任务已经声明的目标，不重新登录、不新建无关页面、不重跑完整任务。每个任务最多触发一次接管；接管失败即阻止任务。

触发错误包括 `cnsc_target_site_not_found`、`cnsc_site_picker_not_ready`、Playwright 定位超时、iframe/弹窗未就绪、类目或交易信息控件结构变化等页面结构类错误。登录、验证码、权限、发布安全拦截和业务数据校验错误不触发自动接管。

## 3. 组件职责

### 3.1 HybridAutomationExecutor

- 先调用现有 Playwright 执行器。
- 根据错误白名单决定是否调用 Harness。
- 将任务计划、当前阶段、错误和页面上下文传给 Harness。
- Harness 成功后重新执行必要的确定性验证或保存步骤。
- 最终仍原样交给 Worker，由真实浏览器证据决定 `saved`、`blocked` 或 `needs_confirmation`。

### 3.2 Browser Harness

- 连接已有 CDP 会话，复用当前 page/frame。
- 收集当前 URL、frame、可见文本、目标区域 DOM 快照和错误上下文。
- 调用项目配置的 AI 模型生成结构化动作计划。
- 校验并逐步执行动作，记录每一步结果。
- 不产生“已保存”结论；只返回动作执行结果和诊断轨迹。

### 3.3 Action Validator / Safety Guard

- 只接受版本化 JSON 动作计划。
- 允许动作：`wait_for`、安全区域内 `click`、允许字段 `fill`、`select_shop`、`save_draft`、`verify`。
- 禁止任意 JavaScript、`eval`、网络请求、打开新页、登录和文件系统操作。
- 对按钮文本、ARIA 标签、选择器和动作参数执行发布词拦截：`保存并刊登`、`刊登`、`发布`、`提交审核` 等一律拒绝。
- 限制动作数量、单步超时和总接管时间；默认每次接管最多 20 个动作、120 秒。
- 只允许操作原任务页面、原站点和已知详情 iframe。

## 4. CNSC 店铺恢复流程

1. 使用 `#shops-wap .shop-box` 点击下拉触发器。
2. 按条件等待 `.feat-content` 和 `.site` 实际可见，不能使用固定 sleep 代替状态等待。
3. 以站点代码、店铺名称、店铺 ID 组合匹配目标，例如 `TW / 海宁TW / 59760739`。
4. 处理 Vue 重绘：点击后重新获取 locator，不复用旧节点。
5. 通过 Vue `shops[]` 状态优先、DOM checkbox 状态兜底，验证目标店铺确实被选中。
6. 校验失败时返回原始错误、picker DOM 快照和 Harness 轨迹，不宣称成功。

Harness 不得绕过现有“每个目标站点必须恰好一个店铺”的完整性规则，也不得接受仅凭可见文本猜测出的店铺。

## 5. 保存草稿与结果判定

Harness 可以点击“保存”但不能点击任何发布或审核动作。保存后必须调用现有 `verifySavedDraft()`，并由 Worker 的 `is_proven_saved_execution()` 检查真实页面证据。AI 输出的 `ok`、自然语言或动作完成数都不能单独证明保存成功。

接管成功但保存证据不足时返回 `needs_confirmation`；接管超时、动作被安全策略拒绝或目标仍未找到时返回 `harness_blocked`。结果保留原始 Playwright 错误，便于诊断和后续修复选择器。

## 6. 日志与成本控制

每次接管记录任务 ID、触发错误、页面 URL、动作计划摘要、每步状态、耗时、模型 ID、token/成本和最终结果。DOM 快照需脱敏，禁止记录 Cookie、凭据和完整隐私字段。单任务最多一次 AI 调用，沿用现有最大成本配置；超过成本或超时立即阻止。

## 7. 测试与验收

- 模拟店铺下拉延迟渲染，Harness 能展开并选择 `TW / 海宁TW / 59760739`。
- 模拟 Vue 重绘，Harness 重新采集节点后仍能完成选择并验证状态。
- AI 输出点击发布按钮的动作计划时，Validator 拒绝且页面无发布副作用。
- AI 输出非法 JSON、未知动作、缺少目标或越权 iframe 时，任务进入 `harness_blocked`。
- Harness 超时或 AI 服务失败时，保留原始 Playwright 错误，不伪造成功。
- 保存草稿后必须通过 `verifySavedDraft()` 和 Worker 证据校验。
- Worker 正确区分 `saved`、`blocked`、`needs_confirmation`。

## 8. 非目标

- 不构建全自动开放式浏览器代理。
- 不让 AI 修改任务计划、商品内容或发布策略。
- 不替换现有 Playwright CNSC 选择逻辑；Harness 是一次性受限恢复层。
- 不在本次设计中增加人工远程控制台或新的登录流程。
