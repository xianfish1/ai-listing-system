# 完整运营版设置中心设计

## 目标

将设置页从“展示少量运行参数”升级为可维护、可验证、可审计的运营控制台。设置保存后必须明确区分“立即生效”和“重启后生效”，并真实驱动 AI 描述生成、马帮采集、NAS 扫描与 Browser-Use Worker。系统安全边界固定为只保存马帮草稿，禁止刊登、发布和提交审核。

## 范围

### AI 模型连接

支持两种协议：`anthropic_messages` 和 `openai_chat_completions`。每个连接保存名称、协议、Base URL、模型 ID、API 版本（Anthropic）、超时和加密 API Key。页面支持新增、编辑基础信息、轮换密钥、测试连通性和设为默认连接；读取接口只返回脱敏状态，不返回密钥。

### 描述生成方案

方案独立于模型连接，保存 `combined`（固定模板 + AI 商品段）或 `full`（完整生成）模式、默认连接、语言、温度、最大 Token、固定开场、固定售后内容和版本号。生成结果保存连接版本、方案版本和模型 ID；失败时副本保持“描述待生成”，不允许生成自动化任务。

### 马帮连接

保存工作台 URL、CDP URL、最后测试时间、连接状态和脱敏错误。测试只读验证当前地址与 CDP 可达性，失败不得覆盖上一次有效配置。页面提供重新检查入口，不保存会话 Cookie 或完整敏感 URL。

### Worker 控制

保存开机自动启动、领取间隔、任务租约、最大重试和 CDP 地址。API 进程启动后按开关启动单个 Worker；设置页提供启动、暂停、继续、重启和查看日志。Worker 状态包括 `offline`、`starting`、`running`、`paused`、`stopping`、`error`；配置变化设置 `restart_required=true`，重启成功后清除。

### NAS 素材

保存素材根目录、扫描模式和单次最大文件数。目录检测返回可访问性、权限和最近检测时间；检测失败阻止新的扫描，但不影响已有基础商品和刊登副本。扫描使用当前保存配置并记录扫描批次。

### 店铺采集

支持手动立即采集和定时自动采集。只处理已配置且启用的店铺。每次采集分别保存平台类目树、当前页必填项、促销活动、主图水印、产品图水印和尺码图快照，并记录来源页面、店铺 ID、采集时间、状态和错误摘要。单店登录过期、页面结构变化或采集失败时仅暂停该店，其他店铺继续；上一次有效快照不被失败结果覆盖。

### 安全策略

`save_draft_only=true`、`publish_allowed=false` 和策略不可编辑。前端禁用发布开关，后端拒绝任何修改发布权限的请求。Worker 只识别并点击保存草稿动作，禁止刊登、发布、保存并刊登和提交审核。

## 数据与生效规则

设置写入运行配置版本，敏感字段使用现有加密存储。每次变更记录配置分组、字段、操作者、时间、旧值摘要、新值摘要和生效范围；API Key、Cookie、完整 CDP 参数不进入审计正文。AI、采集调度和 Worker 控制读取当前有效配置；已经创建的刊登副本和自动化任务继续使用其创建时的快照。

| 配置 | 生效时间 | 失败行为 |
| --- | --- | --- |
| AI 连接、描述方案 | 保存后 | 测试失败不设为默认，生成失败保留待生成 |
| 马帮/NAS 地址 | 保存后用于下一次检测 | 不覆盖上次有效配置 |
| Worker 间隔、租约、CDP | Worker 重启后 | 标记需重启，当前任务不强制中断 |
| 店铺采集周期 | 保存后下一调度周期 | 单店暂停并保留旧快照 |
| 安全策略 | 永久固定 | 后端拒绝修改 |

## API 边界

- `GET/PUT /api/settings`
- `GET/POST/PUT/DELETE /api/ai/model-connections`
- `POST /api/ai/model-connections/{id}/test`
- `POST /api/ai/model-connections/{id}/rotate-key`
- `GET/POST/PUT /api/description-generation/profiles`
- `POST /api/settings/mabang/test`、`POST /api/settings/nas/test`
- `GET /api/worker/status`、`POST /api/worker/start`、`/pause`、`/resume`、`/restart`、`/stop`
- `GET/PUT /api/store-collection/settings`
- `POST /api/store-collection/{store_id}/collect`
- `GET /api/store-collection/runs`、`GET /api/store-collection/runs/{id}`

所有写接口必须返回版本号、生效状态和可读中文错误；读取接口不得泄露密钥、Cookie 或未经脱敏的内部堆栈。

## 验收标准

1. 页面可以新增两种协议的 AI 连接，密钥只显示“已配置”，测试成功后可切换默认模型。
2. 描述方案可以保存两个生成模式和版本，副本生成失败不会产生自动化任务。
3. Worker 可从设置页启动、暂停、继续、重启，状态和日志可刷新看到。
4. 马帮与 NAS 检测失败不会覆盖有效配置；Worker 配置变更显示需重启。
5. 店铺采集支持手动和定时；单店失败不影响其他店，失败结果不覆盖旧快照。
6. 任何发布策略修改请求均被后端拒绝，自动化回归测试确认未出现发布动作。
7. 前端、API、存储、Worker 和采集器均有回归测试，服务启动后设置页可通过浏览器完成上述流程。
