# 完整运营版设置中心实施计划

> 面向 AI 代理的工作计划：在 feature/store-template-workflow worktree 中执行；每个任务按“先写失败测试 -> 运行确认失败 -> 最小实现 -> 回归测试 -> 提交”推进。

目标：将设置中心升级为真实可运营的控制台，支持两种 AI 协议、AI 描述方案、马帮/NAS 检测、单 Worker 生命周期控制、店铺定时采集和可审计配置生效，同时永久禁止发布。

架构：继续使用 Python http.server API、JSON/PostgreSQL 兼容存储、原生 JavaScript/CSS 前端、现有 Browser-Use Worker；新增协议适配器、Worker supervisor、采集调度模块，设置 API 只负责配置与操作命令。

## 任务 1：运行配置模型与审计快照

文件：
- 修改 services/api/runtime_configuration.py
- 修改 services/api/storage.py
- 修改 services/api/main.py
- 新建 tests/test_runtime_configuration_complete.py
- 新建 tests/test_runtime_settings_audit.py

- [ ] 写失败测试：AI 默认参数、Worker 自动启动/重启标记、采集周期、最大重试、字段白名单、不可修改安全策略和审计脱敏。
- [ ] 运行 python -m pytest tests/test_runtime_configuration_complete.py tests/test_runtime_settings_audit.py -q，确认新增接口/字段尚不存在。
- [ ] 扩展完整 schema；所有更新严格拒绝未知字段和 publish_allowed=true。
- [ ] 增加配置版本与审计记录，过滤 API Key、Cookie、CDP 敏感参数。
- [ ] 增加 GET/PUT /api/settings 的版本、生效范围和重启标记返回值。
- [ ] 重跑测试并提交 feat: 完善运行配置与审计。

## 任务 2：AI 双协议适配与连接管理

文件：
- 新建 services/api/openai_chat_completions_adapter.py
- 修改 services/api/model_gateway.py
- 修改 services/api/storage.py
- 修改 services/api/main.py
- 修改 services/api/anthropic_messages_adapter.py
- 新建 tests/test_openai_chat_completions_adapter.py
- 修改 tests/test_model_gateway.py
- 修改 tests/test_credential_crypto.py

- [ ] 写失败测试：OpenAI Chat Completions 请求体、Bearer 鉴权、响应文本/usage 解析、HTTP/超时错误和模型 ID 回写。
- [ ] 运行 python -m pytest tests/test_openai_chat_completions_adapter.py tests/test_model_gateway.py -q，确认协议适配器缺失时失败。
- [ ] 实现 /v1/chat/completions URL 规范化和 OpenAI 兼容适配器；保留 Anthropic Messages 行为不变。
- [ ] 放开连接 CRUD 协议白名单为两种协议，统一返回脱敏连接；支持编辑、删除非默认连接、设默认、轮换密钥和测试。
- [ ] 重跑 AI 与加密测试并提交 feat: 支持 Anthropic 与 OpenAI 兼容模型。

## 任务 3：描述生成方案配置

文件：
- 修改 services/api/description_generation.py
- 修改 services/api/storage.py
- 修改 services/api/main.py
- 修改 tests/test_description_generation.py
- 新建 tests/test_description_profile_api.py

- [ ] 写失败测试：combined/full、连接选择、温度/Token/语言边界、固定内容注入、版本快照和生成失败状态。
- [ ] 运行 python -m pytest tests/test_description_generation.py tests/test_description_profile_api.py -q，确认新字段和路由缺失。
- [ ] 实现描述方案 CRUD、启用/停用、版本快照和默认方案；生成请求读取方案快照。
- [ ] 保证 AI 失败只返回描述待生成，不允许创建自动化任务；成功结果记录模型连接版本与方案版本。
- [ ] 重跑测试并提交 feat: 完善描述生成方案。

## 任务 4：Worker supervisor 与生命周期 API

文件：
- 新建 services/api/worker_supervisor.py
- 修改 services/api/main.py
- 修改 scripts/automation_worker.py
- 修改 services/api/runtime_configuration.py
- 新建 tests/test_worker_supervisor.py
- 修改 tests/test_automation_worker.py

- [ ] 写失败测试：启动、暂停、继续、停止、重启、心跳、单实例锁、配置变化 restart_required、子进程退出和日志脱敏。
- [ ] 运行 python -m pytest tests/test_worker_supervisor.py tests/test_automation_worker.py -q，确认 supervisor 尚不存在。
- [ ] 实现单 Worker supervisor，使用现有 worker 命令启动子进程，保存 PID/状态/最近错误，不重复启动；API 启动时按 auto_start 启动。
- [ ] 增加 /api/worker/status、start、pause、resume、stop、restart、logs；暂停不打断当前保存。
- [ ] 重跑测试并提交 feat: 增加 Worker 生命周期控制。

## 任务 5：店铺采集调度与失败隔离

文件：
- 新建 services/api/store_collection_scheduler.py
- 修改 services/api/mabang_page_import.py
- 修改 services/api/storage.py
- 修改 services/api/main.py
- 修改 scripts/mabang_page_import_capture.js
- 新建 tests/test_store_collection_scheduler.py
- 修改 tests/test_mabang_page_import_api.py

- [ ] 写失败测试：手动采集、定时采集只选启用店铺、各类快照、单店失败不覆盖旧快照且不阻塞其他店。
- [ ] 运行 python -m pytest tests/test_store_collection_scheduler.py tests/test_mabang_page_import_api.py -q，确认调度路由和失败隔离缺失。
- [ ] 实现店铺级采集配置、采集周期、运行记录、下一次调度时间和单店暂停状态。
- [ ] 将采集结果拆分为独立快照；失败写入错误摘要和需要重新登录/重新采集状态，保留旧有效快照。
- [ ] 增加店铺采集设置、指定店铺采集、运行记录查询接口；采集脚本继续只读并禁止保存/发布。
- [ ] 重跑测试并提交 feat: 增加店铺采集调度与隔离。

## 任务 6：设置中心前端完整控制台

文件：
- 修改 apps/web/index.html
- 修改 apps/web/app.js
- 修改 apps/web/styles.css
- 修改 tests/test_store_template_workflow_ui.py
- 新建 tests/test_runtime_settings_ui.py

- [ ] 写失败测试：AI 连接表单/协议切换、描述方案编辑、Worker 操作按钮、采集周期与店铺状态、保存生效提示和安全策略禁用状态。
- [ ] 运行 python -m pytest tests/test_runtime_settings_ui.py tests/test_store_template_workflow_ui.py -q，确认新控件缺失。
- [ ] 将设置页改为六个可切换分组：AI 连接、描述方案、马帮、Worker、NAS、店铺采集、安全。
- [ ] 增加新增/编辑/轮换/测试 AI 连接弹窗；协议切换动态显示 Anthropic API 版本和 OpenAI 兼容字段。
- [ ] 增加 Worker 启停/暂停/继续/重启与日志刷新；增加店铺采集开关、周期、立即采集和失败店铺重试。
- [ ] 所有保存按钮显示已保存、重启后生效或未生效原因；发布相关控件保持禁用。
- [ ] 重跑前端静态测试和 node --check apps/web/app.js，提交 feat: 完善完整运营版设置中心。

## 任务 7：集成验证与生产启动

文件：
- 修改 README.md
- 修改 scripts/run_api.ps1
- 新建 tests/test_runtime_settings_integration.py
- 修改 progress.md 和 task_plan.md

- [ ] 写集成测试：启动 API，读取设置，新增两种协议连接（mock endpoint），保存方案，检查 Worker 状态和店铺采集失败隔离。
- [ ] 运行 python -m pytest tests/test_runtime_settings_integration.py -q，确认端到端缺口。
- [ ] 更新 Windows 启动脚本，使生产服务默认使用正式代码目录、真实数据目录和 API_PORT=8793，启动前自动备份数据。
- [ ] 运行全量 python -m pytest -q、python -m compileall -q services scripts tests、node --check apps/web/app.js。
- [ ] 用 Playwright 在 1440x900 打开 http://127.0.0.1:8793/，进入设置页完成分组切换、保存和状态刷新，确认无控制台错误。
- [ ] 记录服务 PID、数据备份路径、测试结果和未配置外部凭据项，提交 chore: 验证完整运营版设置中心。

## 计划自检

- 每个设计章节均对应至少一个任务：AI、描述、马帮、Worker、NAS、店铺采集、安全、审计和验收。
- 任务顺序满足依赖：配置模型 -> 协议 -> 描述 -> Worker -> 采集 -> UI -> 集成。
- 所有任务包含具体文件、失败测试、最小实现、通过测试和提交步骤。
- 未包含未定义步骤、模糊实现或允许发布的功能。
