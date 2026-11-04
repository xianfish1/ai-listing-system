# AI Listing System MVP

## Category Rules and Template Validation

Store templates reference a published, versioned marketplace category profile. Profiles are collected from the real Mabang product editor in read-only mode, reviewed by an operator, and published only after field identity and automation mappings pass validation.

The published version owns field types, options, required rules, conditional requirements, and variation axes. A store template owns only default/override values. A store listing copy freezes the resolved profile snapshot and hash, so later profile changes do not mutate queued automation jobs.

After creating a collection run through the API, collect a profile with:

```powershell
python scripts/collect_mabang_category_profile.py --country PH --category-id 100244 --output data/category-100244.json --run-id <run-id>
```

The collector installs a save/publish guard and stops before any product save. Existing static profiles can be migrated with `python scripts/migrate_category_profiles.py --data-dir data --dry-run`, then applied with `--apply` after reviewing the report and backup.

跨境电商 AI 上架资料工作台。当前版本专注一条主线：

```text
文件管理
-> 商品维护 / 基础商品
-> 商品维护 / 店铺模板
-> 自动化 / 店铺刊登副本
-> 自动化 / 浏览器自动化任务
-> 马帮导出预览 / Excel 下载
```

产品线优先把一个 NAS 商品文件夹变成“基础商品”，再按多个店铺模板复制成店铺刊登副本。每个店铺模板可以维护自己的平台类目、售价、折扣、尺码、标题后缀和描述。系统仍保留马帮产品导入 `.xlsx` 作为 fallback 和审计出口，由运营人员在马帮侧手动导入。

Browser-Use / 浏览器自动化执行器属于后续阶段。当前系统已经可以从店铺刊登副本生成自动化任务和填单数据包；等需要真实打开马帮后台时，再安装 Browser-Use 并用这些任务中的店铺、类目、价格、折扣和 SKU 数据驱动浏览器填写页面。

## 快速启动

```powershell
cd C:\Users\Administrator\Documents\Codex\2026-06-16\files-mentioned-by-the-user-batch-2\outputs\ai-listing-system
python -m services.api.main
```

打开：

```text
http://127.0.0.1:8787
```

macOS / Linux 上用 `bash start-api.sh`（等价于 `python3 -m services.api.main`），
完整步骤见 `docs/DEPLOY_NEW_MACOS.md`。

### 局域网访问（多台设备）

打开「设置 → 局域网访问」，勾选「允许局域网设备访问」后点「应用网络设置」：

- 服务会在当前进程内切换到 `0.0.0.0` 监听，不需要手动重启 API；关闭开关会切回仅本机。
- 页面会列出本机所有可用网卡的 IPv4 地址（物理网卡优先），每台设备用任意一个地址打开即可。
- Windows 防火墙未放行时，可点「开放防火墙端口」自动添加规则（需要管理员权限）；规则默认只允许专用/域网络和本地子网。
- 局域网访问没有登录校验，请只在可信网络开启。
- 旧的启动方式 `API_HOST=0.0.0.0` 仍然有效；一旦用启动参数指定了非回环地址，设置页会显示「由启动参数指定」并禁止用开关覆盖。

多台电脑同时使用时会共享 HTTP/1.1 连接复用、READ 缓存与 GET 响应短期缓存；存储缓存重建是单飞的，避免多客户端同时触发整库解析而卡顿。

## 配置

复制 `.env.example` 为本地 `.env` 后按需设置环境变量（API 启动、更新器与发布脚本都会读取；真实环境变量优先于 `.env`）。常用项：

```text
API_PORT=8788
DATA_DIR=./data
STORAGE_DIR=./storage
NAS_ASSET_ROOT=./sample_assets
MABANG_TEMPLATE_PATH=./storage/samples/mabang-template.xlsx
```

注意两点：默认端口 8787 在 Windows 属于被排除端口段，本机固定用 8788；不要在 `.env` 写 `API_HOST`——设置页的「局域网访问」开关靠监听地址切换，写死会被锁定为「由启动参数指定」。

模板查找顺序：

1. `MABANG_TEMPLATE_PATH` 指向的文件。
2. `storage/samples/mabang-template.xlsx` 本地 fallback。

## 核心接口

```text
GET  /api/health
GET  /api/project-plan
GET  /api/deployment/readiness
GET  /api/assets
POST /api/assets/scan
GET  /api/base-products
POST /api/base-products/from-folder
POST /api/system/folder-picker
GET  /api/store-templates
POST /api/store-templates
GET  /api/store-listing-copies
POST /api/store-listing-copies/from-base
GET  /api/automation/jobs
POST /api/automation/jobs/from-listing-copies
GET  /api/automation/jobs/{job_id}/payload
GET  /api/products
POST /api/products
PUT  /api/products/{product_id}
GET  /api/product-templates
GET  /api/mabang/export/preview
POST /api/mabang/export
GET  /api/mabang/template
```

## 扫描素材

网页里可以直接输入 NAS 或本地目录并点击扫描。也可以用命令行：

```powershell
python -m workers.asset_scanner.cli --root "\\NAS\ecom-assets" --out data\asset-scan.json
```

推荐目录结构：

```text
NAS/ecom-assets/
  tablet-case/
    PA0001/
      samsung/
        main/
        variants/
        video/
```


当前控制台按运营动作拆分为 `商品维护`、`自动化` 和 `文件管理`。`商品维护 / 基础商品` 使用商品卡和维护弹窗；`商品维护 / 店铺模板` 维护马帮店铺编号、马帮类目编号、马帮折扣编号、颜色 x 尺码规则和 SKU 模板；`自动化` 承接店铺刊登副本和浏览器自动化任务。
## 商品维护

控制台支持：

- 从一个 NAS 商品文件夹自动创建基础商品，识别 `主图.jpg`、`模特图1.jpg`、`白色.jpg`、`黑色.jpg`、`尺码表.png`、`视频.mp4`、`源文件.psd` 等平铺命名。
- 按店铺维护模板：店铺名、平台类目、售价、折扣、尺码、标题后缀和描述。
- 选择一个基础商品，并多选店铺模板，批量生成店铺刊登副本。
- 店铺刊登副本会按“识别颜色 x 模板尺码”生成最终 SKU 和价格行。
- 从店铺刊登副本生成浏览器自动化任务，检查马帮店铺 ID、类目 ID、折扣 ID 等缺口，并输出 Browser-Use 后续可消费的填单数据包。
- 从素材分组批量创建商品。
- 维护商品标题、重量、尺寸、主图、描述。
- 维护 SKU 库存、重量、变体图和价格。
- 预览马帮导出缺失字段与阻塞项。
- 下载马帮导入 Excel，并记录导出审计。

## 自动化 Worker 与并行上架

自动上架由 Worker 进程驱动：`每账号一个 Worker`；账号内部再按「并行槽位」拆：**一个槽位 = 一只独立的
采集浏览器（独立 CDP 端口 + 独立 Chrome Profile）+ 一个 Worker 进程**。所以同一个马帮账号下的十几个
店铺不再只能排队，可以同时上架多单（ADR `docs/adr/0003-mabang-account-worker-slots.md`）。

- 槽位数在「设置 → 自动化 Worker → 每账号并行槽位」维护（`worker.slots_per_account`，1~4，默认 1），
  **保存后立即生效**：调大就给新槽位开浏览器 + 自动登录 + 启动它的 Worker；调小就停掉多余 Worker 并
  关掉它们那只浏览器。
- **只有名下有店铺的账号**才拿额外槽位——没店铺的账号不产生上架任务，不为它白开浏览器。
- 槽位号决定端口与 Profile：`slot 0` = 账号原有那只浏览器（端口 = 账号端口、Profile = 账号 Profile）；
  `slot k` = 端口 `账号端口 + k*100`、Profile `<账号 Profile>-slot{k+1}`（例如 9223 → 9323、
  `storage/browser-use-profile` → `storage/browser-use-profile-slot2`）。
- 每个槽位都是一只 Chromium + 一个 node 执行器：开 3 槽就是三份内存/CPU 与三个马帮会话。建议先 2 槽
  试跑，确认平台不限制同账号多会话后再加。
- 「从马帮当前页导入」只占用 1 号槽位的可见窗口；导入期间只有那个槽位的 Worker 让路，其他槽位照常跑。
- 排障：设置页的 Worker 列表按「账号 · 并行槽位」逐行列出（每行一个「日志」按钮）；
  `GET /api/worker/status` 的 `workers[]` 每行带 `key`、`account_id`、`slot`；
  `GET /api/worker/logs?account_id=…&slot=1` 读指定槽位的日志。
- 手工起一个槽位 Worker（排障用）：

```powershell
python -m scripts.automation_worker --api-url http://127.0.0.1:8788 --account-id <马帮账号ID> `
  --worker-slot 1 --cdp-url http://127.0.0.1:9323 `
  --worker-id mabang-<账号ID前8位>-s2 `
  --pending-completion-file tmp_upload\automation-jobs\pending-completion-slot2.json `
  --lock-file data\worker-runtime\accounts\<账号ID>\slot-2\automation-worker.lock
```

## AI 辅助

AI 只提供建议，不自动保存商品资料，也不自动导出文件：

- 标题建议：`POST /api/products/{product_id}/ai/title-suggestion`
- 属性建议：`POST /api/products/{product_id}/ai/attribute-suggestion`
- 历史建议：`GET /api/products/{product_id}/ai/outputs`

默认使用本地规则 fallback。只有显式配置 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_ENABLE_LIVE=1` 时才会调用 DeepSeek。

## 存储

本地开发默认使用：

```text
data/store.json
```

大集合（`automation_jobs` 任务、`marketplace_categories` 类目目录、`ai_outputs`、
`listing_copy_revisions`、`mabang_page_import_runs`）拆在同目录的独立 `<集合名>.json` 里，
读的时候合并成一份、对代码透明。这样只改任务的保存（心跳/进度/建任务）不必重写整份主库。

**备份要连 split 文件一起**，只拷 `store.json` 会丢掉任务与类目目录：

```powershell
Copy-Item data\store.json, data\automation_jobs.json, data\marketplace_categories.json, `
  data\ai_outputs.json, data\listing_copy_revisions.json, data\mabang_page_import_runs.json `
  <备份目录>\
```

任务列表接口默认不返回 `execution_snapshot`（它占任务体积的绝大部分，界面上用不到）；
需要快照的调用方走 `build_automation_payload`。

如需多人共享同一资料库，可以切换 PostgreSQL：

```powershell
python scripts\verify_postgres_config.py --database-url "postgresql://USER:PASSWORD@HOST:5432/ai_listing"
python scripts\verify_postgres_config.py --database-url "postgresql://USER:PASSWORD@HOST:5432/ai_listing" --write-smoke
.\scripts\run_api.ps1 -HostName "0.0.0.0" -Port 8787 -StorageBackend "postgres" -DatabaseUrl "postgresql://USER:PASSWORD@HOST:5432/ai_listing"
```

迁移当前 JSON 数据：

```powershell
python scripts\migrate_json_to_db.py --dry-run
python scripts\migrate_json_to_db.py
```

## 项目结构

```text
apps/web/                 静态 Web 控制台
database/schema.sql       PostgreSQL 未来正式 schema 草稿
docs/                     架构、部署、素材和 AI 说明
services/api/             零依赖 HTTP API 原型
workers/asset_scanner/    NAS 素材扫描器
workers/executor/         AI 建议适配器
data/                     本地 JSON 数据
storage/                  模板、导出、截图和临时文件
tests/                    回归测试
```

## 验证

```powershell
python -m pytest -q
python -m compileall -q services workers scripts tests
python scripts\verify_project_plan.py
python scripts\verify_deployment_readiness.py
```

马帮导出专项回归：

```powershell
python -m pytest tests/test_mabang_export.py tests/test_mabang_export_audit.py tests/test_mabang_export_audit_resilience.py tests/test_mabang_template_fallback.py -q
```

## Browser-Use local environment

Browser-Use is installed in an isolated local environment:

```powershell
python -m uv venv .venv-browser-use --python 3.12
python -m uv pip install browser-use --python .venv-browser-use\Scripts\python.exe
.venv-browser-use\Scripts\browser-use.exe --doctor
```

Current verified install:

```text
Python: 3.12.13
browser-use package: 0.13.3
browser-use CLI: .venv-browser-use\Scripts\browser-use.exe
```

The first browser connection currently requires Chrome remote debugging permission. Open `chrome://inspect/#remote-debugging`, enable remote debugging for the browser instance, and allow the prompt before running the future Mabang automation runner.

Run a safe automation dry-run for an existing `automation_jobs` id:

```powershell
python scripts\browser_use_runner.py --job-id <automation_job_id>
python scripts\browser_use_runner.py --job-id <automation_job_id> --doctor
```

The runner summarizes the Browser-Use fill plan and can also run a guarded safe-fill script:

```powershell
python scripts\browser_use_runner.py --job-id <automation_job_id> --execute-safe-fill
```

Safe-fill mode refuses any plan that allows submit/publish, installs a page-level click guard for `保存` and `保存并刊登`, fills only supported page data, and stops for operator review. Store templates can be updated with the Chinese-readable `马帮店铺编号`, `马帮类目编号`, and `马帮折扣编号` fields through `PUT /api/store-templates/{template_id}` or the matching web console inputs; the underlying values are synced to existing listing copies for the same template.

The live Shopee save-only workflow is recorded in `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`.

Mabang store options captured from the browser can be saved and reused by the product page:

```text
GET  /api/mabang/store-options
POST /api/mabang/store-options
```

The store template form uses these saved options as a dropdown and submits both the display store name and `mabang_store_id`.

## Guarded self-run commands

Use the PowerShell wrapper for the AL0071 / POD Philippines guarded workflow:

```powershell
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode health
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode audit
```

`health` checks the local API, Chrome CDP on `127.0.0.1:9223`, and Browser-Use doctor.
`audit` runs health checks, dry-run readiness, safe navigation placeholders, and the guarded iframe audit path.
`fill` is reserved for guarded fill after audit passes. It must still stop for operator review and must not save or publish.

## 版本发布与在线更新

工作台自带一条 GitHub 版本通道：`VERSION` 文件是唯一版本来源；发布时把代码打成 `dist/ai-listing-system-<版本>.zip`（同目录带 `.sha256`）、提交版本号、打 `v<版本>` tag、推送并创建 GitHub Release。设置页「版本更新」可以检查最新 Release，一键下载、校验、备份、替换并自动重启；更新只覆盖代码目录和启动文件，**不会动 `data/`、`storage/` 和 `.env`**，失败自动回滚。

完整操作手册（Token 权限、发布步骤、更新时序、回滚、错误码、安全边界）：[docs/GITHUB_UPDATES.md](docs/GITHUB_UPDATES.md)。

常用命令：

```powershell
# 只构建安装包，不改仓库
python scripts/publish_release.py --bump patch --dry-run

# 发布补丁版本
python scripts/publish_release.py --bump patch --notes "修复批量导入标题被截断"

# 指定版本号，发布说明取自文件
python scripts/publish_release.py --version 1.2.0 --notes-file docs/releases/v1.2.0.md
```

发布说明模板在 `docs/releases/TEMPLATE.md`；按 `docs/releases/v<版本>.md` 存放会自动作为默认发布说明。发布前建议先跑 `python -m pytest -q`。
