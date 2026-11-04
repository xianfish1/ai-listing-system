# 新 macOS 电脑部署指南

本指南覆盖当前版本在 macOS 上从源码运行的部署方式。Windows 部署仍以
`docs/DEPLOY_NEW_WINDOWS.md` 为准，两边的 Windows 行为没有变化。

## 0. 前置条件

| 组件 | 要求 | 说明 |
|---|---|---|
| macOS | 12 及以上（Apple Silicon / Intel 均可） | |
| Python | 3.11 – 3.14 | 建议 `brew install python@3.12`；需要文件夹选择框时另装 `python-tk` |
| Node.js | ≥ 18 LTS | 仅供自动化执行器用（CDP 连接） |
| Chrome | 建议安装 Google Chrome | 放在 `/Applications` 即可；也可用 `CHROME_EXECUTABLE` 指定 |
| ffprobe | 可选 | `brew install ffmpeg`；缺少时只是少视频元数据 |

## 1. 获取代码

把仓库放到一个**固定不移动**的目录，例如：

```bash
git clone <repo-url> ~/ai-listing-system
cd ~/ai-listing-system
```

> 如果解压的是 Windows 部署包：先删掉包内的 `vendor/chrome/`。那是 win64 的
> `chrome.exe`，在 macOS 上不可执行（代码会跳过并报错回退，但删掉最省事）。

## 2. 安装依赖

```bash
python3 -m pip install -r requirements.txt
npm install
```

`requirements.txt` 只有 openpyxl / pytest / cryptography / Pillow（PostgreSQL 驱动可选）。
执行器通过 CDP 连外部 Chrome，不需要 `npx playwright install`。

## 3. 配置 .env

```bash
cp .env.example .env
```

默认 `API_PORT=8788`，一般不用改。需要多台设备访问时**不要**写死 `API_HOST`，
启动后在设置页开「允许局域网设备访问」。

## 4. 启动

```bash
bash start-api.sh
# 等价于：python3 -m services.api.main
```

浏览器打开：

```text
http://127.0.0.1:8788
```

日志直接输出到终端；需要后台常驻可用 `nohup bash start-api.sh > /tmp/ai-listing.log 2>&1 &`，
或用 `launchd` 配置登录自启（对应 Windows 的任务计划程序）。

## 5. 采集浏览器

默认启动顺序：项目内置 Chrome → `CHROME_EXECUTABLE` → 系统安装路径。macOS 的
系统候选是：

```text
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Chromium.app/Contents/MacOS/Chromium
```

指定其它浏览器：

```bash
export CHROME_EXECUTABLE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

也可以把 Playwright 缓存里的 Chromium 收进项目：

```bash
python3 -m scripts.bundle_chrome
```

## 6. NAS 与素材路径

- 在 Finder 里挂载 SMB 共享（例如 `smb://Juns/产品编辑部`），会出现在 `/Volumes/...`。
- 把设置里的 NAS 素材根目录指向挂载点（或在 `.env` 设 `NAS_ASSET_ROOT`）。
- **从 Windows 迁移数据时**：`data/store.json` 里的素材路径是 Windows 绝对路径
  （`D:\...`、`\\Juns\...`），macOS 上需要重新指向 `/Volumes/...`，否则素材、
  打开文件、本地导入都会报路径不存在。

## 7. 与 Windows 的差异

- 「开放防火墙端口」按钮仅 Windows 可用；macOS 首次监听非回环地址时由系统弹窗授权。
- 自动更新流程有 POSIX 分支，但发布产物目前按 Windows 打包；macOS 建议直接 git pull。
- 采集浏览器的停止、无头/窗口模式切换、页面导入的“先开可见窗口”在 macOS 上
  使用 `ps` + `SIGTERM/SIGKILL`，行为与 Windows 的 `taskkill` 对应。
- 打开素材/所在文件夹使用 `open` / `open -R`（Windows 仍用 `explorer`）。
