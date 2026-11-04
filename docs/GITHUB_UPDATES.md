# GitHub 版本发布与在线更新

适用版本：带 `VERSION` 文件的 `ai-listing-system`（1.0.0 起）。
相关代码：`services/api/github_updates.py`、`scripts/publish_release.py`、`scripts/apply_update.py`。

一句话：给工作台接一条 GitHub 版本通道——在设置页一键发布新版本（打包、打 tag、建 Release），也可以在同一页检查并一键更新到最新版本。更新前自动备份，失败自动回滚，且不会碰 `data/`、`storage/` 和 `.env`。

## 能力与边界

| 想做的事 | 在哪做 |
| --- | --- |
| 看当前版本、有没有新版本 | 设置 → 版本更新 |
| 有新版本时收到提醒（任何页面） | 顶栏「发现新版本 vX · 立即更新」按钮 |
| 发布新版本（界面） | 设置 → 版本更新 → 发布版本 |
| 发布新版本（命令行、无人值守） | `python scripts/publish_release.py ...` |
| 安装更新 | 设置 → 版本更新 → 立即更新（或直接点顶栏提醒按钮） |
| 回滚 | 失败自动回滚；手动步骤见「回滚与故障排查」 |

不在范围内：

- 不做代码签名、公证，不做增量补丁（每次整包覆盖），一次只对接一个仓库。
- 更新只覆盖代码目录和启动文件清单，**不覆盖运行数据**。
- 自动重启固定使用 `python -m services.api.main`；自定义启动参数请写进环境变量（见 3.5）。

## 首次接入清单

1. 把 `ai-listing-system` 目录放进一个 Git 仓库（建议单独建仓库，不要混在上级目录里），推送到 GitHub；私有仓库即可。
2. 在 GitHub 建 Token：Fine-grained → 只选该仓库 → `Contents: Read and write`（见 1.2）。
3. 启动 API，打开 设置 → 版本更新，填 `owner/repo` 与 Token，点「保存」。
4. 确认「当前版本」显示 `VERSION` 的内容，且 `GET /api/health` 的 `version` 一致。
5. 点「检查更新」确认能连上 GitHub；没有 Release 时提示没有新版本也算连通成功。
6. 先跑 `python -m pytest -q`，再用 `python scripts/publish_release.py --bump patch --dry-run` 验证产物，然后去掉 `--dry-run` 正式发布。
7. 到 GitHub 确认 Release 下有两个资产：`ai-listing-system-X.Y.Z.zip` 与同名 `.sha256`。
8. 在旧版本的机器上验证「检查更新 → 立即更新 → 页面自动刷新」，并确认 `data/`、`storage/` 内容未变。

## 1. 配置

### 1.1 设置页填写

设置 → 版本更新：

| 字段 | 说明 |
| --- | --- |
| GitHub 仓库 | `owner/repo`；也可粘贴 `https://github.com/owner/repo` 或 `.git` 结尾的地址 |
| GitHub Token | 只写一次；保存后加密存储、不回显；留空表示「不修改」，已配置时输入框提示「已配置（留空不修改）」 |
| GitHub API 地址 | 默认 `https://api.github.com`；GitHub Enterprise 填 `https://<host>/api/v3` |
| 包含预发布版本 | 「把预发布版本也算作更新」：勾选后 `-beta` 这类 Release 也参与比较 |
| 自动检查更新 | 「打开系统时自动检查更新」：API 启动时后台检查一次并缓存到 `storage/updates/check.json`，失败只记录状态、不弹错 |

保存按钮是「保存更新配置」。保存后接口只回 `token_configured: true / false`，不会回 Token 明文；面板会立刻回读一次状态，所以仓库归一化结果与「当前版本」以服务端为准。同一面板还能看到最新版本、发布时间与该版本的发布说明。

### 1.2 Token 需要什么权限

- **Fine-grained personal access token**（推荐）：Repository access 只勾这一个仓库；Permissions → Repository permissions → **Contents: Read and write**。创建 Release、上传 zip 资产、推送 tag 都属于 Contents 写权限；`Metadata: Read-only` 会自动带上。私有仓库必须能被该 Token 读到，否则会报 404。
- **Classic token**：私有仓库勾 `repo`；公开仓库至少 `public_repo`。发布要推 tag，必须是有写权限的 scope。
- 只用于「检查 + 安装更新」的机器，可以给只读 Token（Contents: Read-only）；要在这台机器上发布才需要写权限。

### 1.3 环境变量覆盖

设置页里的值可以在启动时被环境变量覆盖，适合无人值守或多机部署：

```text
GITHUB_UPDATE_REPO=owner/repo
GITHUB_UPDATE_TOKEN=github_pat_xxx
GITHUB_API_URL=https://api.github.com
```

- 优先级：环境变量 > 设置页运行时配置。
- `GITHUB_API_URL` 默认 `https://api.github.com`；GitHub Enterprise 填 `https://<host>/api/v3`。
- 环境变量可以写进项目根 `.env`（API、更新器与发布脚本启动时统一读取，真实环境变量优先，见 README「配置」）；`.env` 已被忽略不会提交。

**分流代理（特殊网络需要）**：有的网络里 `github.com` 直连被墙必须走本地代理，而 `api.github.com` 直连可用、走共享代理出口反而被 GitHub 按 IP 拒（403）。此时在 `.env` 配：

```text
GITHUB_UPDATE_PROXY=http://127.0.0.1:7897
GITHUB_UPDATE_DIRECT_HOSTS=api.github.com   # 默认值，可省略
```

效果：检查更新（api.github.com）直连；下载安装包（github.com / 对象存储）与发布时的 git 推送走代理。改完重启一次 API 生效。

### 1.4 验证配置

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/health
Invoke-RestMethod http://127.0.0.1:8787/api/updates/status
```

重点看 `configured`、`token_configured`、`current_version`、`error`。也可以直接点设置页的「检查更新」。

## 2. 发布版本

### 2.1 VERSION、tag、Release 的关系

```text
VERSION（项目根，内容如 1.2.0）        ← 唯一版本来源
   │ 发布时写回并提交 chore(release): v1.2.0
   ▼
git tag v1.2.0
   │ 推送当前分支 + tag
   ▼
GitHub Release  tag = v1.2.0  name = v1.2.0
   资产：dist/ai-listing-system-1.2.0.zip
         dist/ai-listing-system-1.2.0.zip.sha256（同目录，整包校验值）
```

- 版本号按语义化递增：`patch`（修 bug）、`minor`（加功能）、`major`（不兼容）。`1.2.3-beta.1` 视为预发布，低于同号正式版。
- `VERSION`、tag `v<版本>`、Release 三者必须同名；`/api/health` 与 `/api/updates/status` 都读 `VERSION`。
- tag 已存在时发布会直接报错，**不会覆盖**；要重发请换版本号。
- 安装包顶部有一份 `manifest.json`（版本 + 每个文件的 sha256 与大小），更新器按它逐文件校验。

### 2.2 设置页发布

1. 设置 → 版本更新 → 发布版本。
2. 选递增方式 `patch / minor / major`，新版本号会自动填好；也可以直接手填。
3. 填发布说明；留空时的取值顺序是 `docs/releases/v<版本>.md` → 「发布 v<版本>」。模板见 `docs/releases/TEMPLATE.md`。
4. 需要 beta 就先勾「预发布」。
5. 先点「构建安装包」：固定按 dry-run 执行，只在 `dist/` 产出 zip 与 `.sha256`，不写 `VERSION`、不提交、不打 tag、不推送。
6. 确认无误再点「发布版本」。如果这次只想 dry-run，也可以勾选「只构建安装包（dry-run，不提交/不推送）」再点「发布版本」。确认对话框后后台执行，页面每 2 秒拉一次任务状态，底部日志显示尾部内容（最多保留尾部 300 行）。

发布说明最长 60 KB；仓库没配置时「发布版本」按钮是禁用的。

> 发布是**会推仓库**的操作：会提交一个 `chore(release): v<版本>` commit、打 tag 并推送当前分支。发布前建议在干净工作区操作——未提交的改动也会被打进安装包。

### 2.3 命令行发布

```powershell
# 1) 只构建，不改仓库：先验证产物
python scripts/publish_release.py --bump patch --dry-run

# 2) 递增补丁号并正式发布（Token 取自 GITHUB_UPDATE_TOKEN 或设置页）
python scripts/publish_release.py --bump patch --notes "修复批量导入标题被截断"

# 3) 指定版本号，发布说明放文件
python scripts/publish_release.py --version 1.2.0 --notes-file docs/releases/v1.2.0.md

# 4) 发布预发布
python scripts/publish_release.py --bump minor --prerelease --notes "1.2 beta：新导入流程"

# 5) 只写 VERSION、提交、打 tag，不推送（离线准备，之后再 push）
python scripts/publish_release.py --version 1.2.0 --no-push

# 6) 临时指定仓库与 Token（不写设置页）
python scripts/publish_release.py --bump patch --repo owner/repo --token github_pat_xxx
```

| 参数 | 说明 |
| --- | --- |
| `--bump patch\|minor\|major` | 在当前 `VERSION` 上递增 |
| `--version X.Y.Z` | 直接指定版本（与 `--bump` 二选一） |
| `--notes "..."` / `--notes-file 文件` | 发布说明；都为空时用 `docs/releases/v<版本>.md` |
| `--prerelease` | 标记预发布 |
| `--dry-run` | 只构建安装包，不改仓库 |
| `--no-push` | 提交 + 打 tag，但不推送 |
| `--repo` / `--token` / `--api-url` | 覆盖设置页与环境变量 |

> `--token` 会把 Token 写进命令历史和进程列表，只建议临时排障；日常用设置页保存或设 `GITHUB_UPDATE_TOKEN`。

发布内部顺序：解析版本 → 检查 Git 仓库 → 生成说明 → 构建 `dist/` 产物 → 写 `VERSION` 并提交 → 打 tag → 推送 → 创建 Release → 上传 zip 与 `.sha256`。中间任一步失败都会尽量恢复 `VERSION`、删除本地 tag，并报出失败步骤。

出现「未配置 GitHub Token」：没有 Token 且远端不是本地路径时发布会拒绝推送——先在设置页填 Token，或设 `GITHUB_UPDATE_TOKEN`。

### 2.4 发布产物

```text
dist/
  ai-listing-system-1.2.0.zip          安装包（代码 + manifest.json + VERSION）
  ai-listing-system-1.2.0.zip.sha256   整包校验值
```

- `dist/` 已被 `.gitignore` 忽略，不要提交。
- 安装包只含发布白名单（见 3.3），不含 `data/`、`storage/`、`.env`、`.venv*`、`node_modules`、`vendor/` 等。
- 安装包体积上限 200 MB；超过时更新端会拒绝下载与校验。

## 3. 检查与安装更新

### 3.1 检查更新

设置 → 版本更新 → 「检查更新」，等同于：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/api/updates/check `
  -ContentType "application/json" -Body "{}"
```

返回里有 `current_version`、`latest`（版本 / tag / 说明 / 时间 / 资产）、`update_available`、`error`。检查失败不返回 HTTP 错误，错误写在 `error` 字段里（401 / 403 / 404 见 4.4）。没配仓库时检查按钮禁用。

### 3.1.1 顶栏全局提醒

打开工作台后，任何页面（首页 / 商品 / 模板 / 副本 / 自动化 / 设置…）的右上角都会自动读一次 `/api/updates/status`：

- 有更新时出现「**发现新版本 vX.Y.Z · 立即更新**」按钮，点一下就是「确认 → 下载校验 → 重启」，和设置页的「立即更新」同一条链路。
- 安装包已经下载并校验、只差重启时，按钮变成「**vX.Y.Z 已下载 · 重启安装**」。
- 没有更新、未配置仓库或检查失败时按钮隐藏，不打扰操作员。

读的是 API 启动时后台检查写入的缓存；缓存超过 30 分钟（或从未检查过）时，打开页面会静默补查一次，页面长期开着时每小时再读一次。所以新版本发布后，其它电脑刷新一下页面就能看到提醒，不需要先进设置页。

### 3.2 一键更新的时序

1. 点「立即更新」→ 确认对话框 → `POST /api/updates/apply`，请求体是 `{"version":"1.2.0","restart":true}`。
2. 下载与校验在返回 `202` 之前完成（通常几秒，取决于网速和包大小）：选 zip 资产 → 下载到 `storage/updates/downloads/` → 校验 sha256 与 `manifest.json` → 解压到 `storage/updates/staged/<版本>/` → 写 `pending.json`，返回 `202`。
3. 约 1.2 秒后：停 Worker → 关闭 HTTP 服务 → API 进程退出。
4. 独立更新器 `scripts/apply_update.py` 接管：等端口释放 → 把旧文件备份到 `storage/updates/backups/<时间>-from-<旧版本>/` → 逐文件原子替换 → 写 `state.json` → 用 `python -m services.api.main` 重启 API。
5. 页面显示「正在重启…」，每 2 秒轮询 `/api/health`，最多 120 秒。必须先看到一次失败、再看到恢复，页面才刷新——否则可能刷回还没退出的旧进程。
6. 失败时更新器回滚已替换文件、记录失败原因，然后用旧代码重启 API。

重启期间页面打不开是正常现象；只要 `/api/health` 回来，页面会自动刷新。

### 3.3 更新会动与不会动的文件

会被替换（新包里有才替换，不会删除多余文件）：

```text
目录：apps/ services/ scripts/ workers/ browser_extension/ docs/
文件：VERSION requirements.txt README.md AGENTS.md start-api.bat
      start-api-silent.vbs run_api_silent.pyw preview_launch.py
```

永远不会被更新覆盖：

```text
data/            运行数据（store.json 及拆分集合等）
storage/         模板、导出、日志、浏览器 Profile、更新备份
.env             本机配置与密钥
vendor/ .venv* node_modules/ tmp_upload/
```

所以更新不会丢商品、任务和账号配置；但也不会自动安装新依赖——`requirements.txt` 变了要手动 `pip install -r requirements.txt`。

### 3.4 更新过程中的目录

| 路径 | 内容 |
| --- | --- |
| `storage/updates/downloads/` | 下载的 zip 与 `.sha256` |
| `storage/updates/staged/<版本>/` | 解压校验通过的待安装文件 |
| `storage/updates/backups/<时间>-from-<版本>/` | 更新前文件备份 + `backup.json` |
| `storage/updates/logs/apply-<时间>.log` | 更新器日志（备份 / 替换 / 重启） |
| `storage/updates/state.json` | 最近一次安装结果 |
| `storage/updates/pending.json` | 当前待安装任务 |
| `storage/updates/check.json` | 最近一次检查更新（或启动时自动检查）的结果缓存 |
| `storage/updates/release_jobs/<id>.json` | 设置页发布任务的日志与结果 |

`storage/updates/` 已被 `.gitignore` 忽略。备份会占空间，确认更新稳定后可手动清理旧备份目录。

### 3.5 关于自动重启

更新器固定用 `python -m services.api.main`（工作目录 = 项目根）重启。端口按这个顺序决定：更新器继承 API 进程的环境变量 → 项目根 `.env`（只补缺，见 README「配置」）→ 默认 8787。本机启动器（`start-api-silent.vbs` / `run_api_silent.pyw` / `start-api.bat`）都固定 `API_PORT=8788`，`.env` 里也写了一份 `API_PORT=8788` 兜底，所以更新后会回到同一端口。

两点注意：默认端口 8787 在 Windows 属于被排除端口段，别用；不要在 `.env` 写 `API_HOST`——设置页的「局域网访问」开关会被锁定为「由启动参数指定」。更新后确认一次 `/api/health` 的地址与端口即可。

## 4. 回滚与故障排查

### 4.1 自动回滚

替换阶段任何一步失败（写文件失败、磁盘满、权限不足等），更新器会按 `backup.json` 恢复已替换的文件、写 `state.json`（`status: "failed"`）、用旧代码重启，页面刷新后仍是旧版本。日志在 `storage/updates/logs/apply-<时间>.log`。

### 4.2 手动回滚

先停 API（不要开着 API 替换代码），在项目根执行：

```powershell
# 查看最近一次备份目录
python -c "from pathlib import Path; from services.api.github_updates import find_latest_backup; print(find_latest_backup(Path('storage/updates')))"

# 用备份回滚（目录名按上一步结果替换）
python -c "from pathlib import Path; from services.api.github_updates import rollback_update; print(rollback_update(project_root=Path('.'), backup_dir=Path('storage/updates/backups/20261024-120000-from-1.1.0')))"
```

回滚完成后重新启动 API（`python -m services.api.main` 或你的启动脚本）。回滚只恢复更新覆盖过的文件，不删除新版本新增的文件；要完全回到旧版本，直接重新安装对应旧 Release 更干净。

### 4.3 状态文件速查

```powershell
Get-Content storage/updates/state.json
Get-Content storage/updates/pending.json
Get-Content storage/updates/logs/apply-*.log -Tail 80
Get-ChildItem storage/updates/release_jobs
```

`state.json` 关键字段：`status`（`success` / `failed`）、`version`、`applied_at`、`backup_dir`、`files`、`error`。

### 4.4 常见错误

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `401 GitHub 认证失败` | Token 无效、过期或填错 | 重新生成 Token，在设置页重填；留空保存不会清掉旧值 |
| `403 权限不足（Token 或限流）` | Token 没有 Contents 写权限，或触到 API 限流 | 换有权限的 Token；等限流窗口过去再试。若配了 `GITHUB_UPDATE_PROXY`，还要确认 `api.github.com` 在 `GITHUB_UPDATE_DIRECT_HOSTS` 里直连（共享代理出口 IP 可能被 GitHub 拒） |
| 检查更新超时 / 连不上，或推送报 `schannel`/TLS 中断 | `github.com` 直连被网络干扰 | 在 `.env` 配 `GITHUB_UPDATE_PROXY`（本地代理），见 1.3；改完重启 API |
| `404 仓库或 Release 不存在` | 仓库名拼错、私有仓库 Token 无权、还没发过 Release | 核对仓库名与 Token 的仓库授权；先发布第一个版本 |
| 检查到更新，「立即更新」却报没有 zip 资产 | Release 只写了说明，没传安装包 | 用 `publish_release.py` 重新发布，它会自动传 zip 与 `.sha256` |
| `已经是最新版本`（409） | 选中的版本不比当前 `VERSION` 新 | 先点「检查更新」，确认版本号后再点「立即更新」 |
| `请先配置 GitHub 仓库与 Token`（422） | 仓库或 Token 没保存成功 | 在「更新配置」里补全并保存，再刷新状态 |
| `sha256 不匹配` | 下载损坏，或资产被替换 | 删掉 `storage/updates/downloads/` 里对应文件，重新检查更新再装 |
| `tag 已存在` | 该版本号已经发过 | 换版本号（如 `--bump patch`），不要复用版本号 |
| 提示端口被占用，或更新后页面打不开 | 旧 API 没退干净，或重启端口与原端口不一致 | 结束残留 `python` 进程后手动启动；检查 `.env` 的 `API_PORT` |
| 设置页显示「需要重启 API」 | 当前进程是老版本，还没有更新接口（刚升级完还没重启） | 重启一次 API 后刷新页面即可 |
| 更新完成但界面还是旧版 | 页面缓存，或服务没有真正重启 | 强制刷新（Ctrl+F5）；看 `/api/health` 的 `version` 与 `state.json` |
| 更新后功能报缺依赖 | `requirements.txt` 新增了依赖 | 执行 `pip install -r requirements.txt` 后重启 |
| 报「文件不在允许目录」或 zip-slip 被拒 | 包内路径越界（`..`、绝对路径、符号链接） | 多为打包环境异常，用官方脚本重新打包，不要手工改 zip |

### 4.5 排障命令

```powershell
# 服务与更新状态
Invoke-RestMethod http://127.0.0.1:8787/api/health
Invoke-RestMethod http://127.0.0.1:8787/api/updates/status

# 手动核对当前版本
Get-Content VERSION

# 最近一次发布任务（设置页发布失败时）
Get-ChildItem storage/updates/release_jobs | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

## 5. 安全边界

- **Token 加密存储**：设置页保存的 Token 与 AI / 马帮密钥使用同一套 Fernet 加密，落库后不进入公开配置；`/api/settings` 只返回 `token_configured`，任何接口都不回显明文。留空保存等于保留旧值。
- **最小权限**：Token 只授权目标仓库；只做更新的机器用只读 Token；换机或人员变动及时吊销。
- **Token 不进推送参数与发布日志**：CLI 推送时 Token 只通过进程环境变量传给 git（`http.extraheader`），不出现在 git 的 argv 和发布日志里；发布日志只记录步骤名。`--token` 参数只建议临时排障，因为它会留在命令历史。
- **路径白名单**：更新只允许写 3.3 的目录与文件清单；包内出现其它路径、`..`、绝对路径、反斜杠、符号链接一律拒绝（zip-slip 防护）。
- **整包校验**：先比整包 sha256（Release 带 `.sha256` 时），再按 `manifest.json` 逐文件比 sha256 与大小，最后才替换。
- **HTTPS**：仓库地址与 `GITHUB_API_URL` 只接受 `http(s)`；公网坚持 `https://api.github.com`，自建 GitHub Enterprise 也建议 HTTPS。
- **运行数据不参与更新**：打包时排除 `data/`、`storage/`、`.env`，既不把开发数据带上 GitHub，也不会覆盖生产数据。
- **发布前跑测试**：发布不会自动跑测试，建议固定执行 `python -m pytest -q`，并确认工作区没有不该提交的改动（打包取的是工作区当前内容）。
- **局域网注意**：开启局域网访问后，同网段设备也能打开设置页；发布能力等同于仓库写权限，不要在不可信网络开启。

## 6. 接口速查

```text
GET  /api/health                    version = 当前 VERSION
GET  /api/updates/status            配置 / 最新版本 / 待安装 / 上次结果
POST /api/updates/check             实时检查 GitHub（失败写在 error）
POST /api/updates/apply             下载 + 校验 + 暂存，202 后重启安装
POST /api/releases/publish          后台发布任务，返回 job_id
GET  /api/releases/publish/status   发布任务状态与日志尾部
```
