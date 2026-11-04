# 新 Windows 电脑部署指南

本部署包是「开箱即用」形态：代码、马帮采集用的 Chrome（`vendor\chrome`）、导出模板
（`storage\samples\mabang-template.xlsx`）、启动脚本全部在内。照着下面 6 步走即可。

## 0. 前置条件

| 组件 | 要求 | 说明 |
|---|---|---|
| Windows | 10 / 11 x64 | |
| Python | 3.11 – 3.14 | 安装时勾选 **Add python.exe to PATH** |
| Node.js | ≥ 18 LTS | 仅供自动化执行器用（CDP 连接，不下载浏览器） |
| Chrome | **不需要单独装** | 包内自带 `vendor\chrome` |
| NAS | 可达 `\\Juns\产品编辑部` | 导出模板与素材默认从这里取；不可达也能跑，只是模板要另配 |

## 1. 解压

把 zip 解压到一个**固定不移動**的目录，例如：

```text
C:\ai-listing-system
```

## 2. 安装 Python 依赖

```powershell
cd C:\ai-listing-system
python -m pip install -r requirements.txt
```

`requirements.txt` 只有 openpyxl / pytest / cryptography / Pillow 四项（PostgreSQL 驱动可选）。

## 3. 安装 Node 依赖

```powershell
cd C:\ai-listing-system
npm install
```

只装 `playwright` 这一个包（执行器通过 CDP 连包内 Chrome，**不要**执行
`npx playwright install`，那是给无头浏览器下载用的，这里用不到）。

## 4. 配置 .env

```powershell
copy .env.example .env
```

默认就是生产配置（API_PORT=8788，8787 在 Windows 是被排除的端口段），一般不用改。
需要局域网访问时**不要**写死 `API_HOST`，启动后在设置页开「允许局域网设备访问」。

## 5. 启动

双击：

```text
start-api-silent.vbs
```

（pythonw 无控制台窗口，日志在 `data\api-runtime\api.out.log` / `api.err.log`。）

想开机自启：任务计划程序 → 创建任务 → 触发器「登录时」→ 操作
`wscript.exe "C:\ai-listing-system\start-api-silent.vbs"`（务必勾选「只在用户登录时运行」，
不要用 S4U/不登录就跑——那会把 API 拉进会话 0，界面相关功能会静默失效）。

## 6. 验证

浏览器打开：

```text
http://127.0.0.1:8788
```

命令行核验：

```powershell
python scripts\verify_deployment_readiness.py
```

## 马帮采集浏览器

- 无需安装 Chrome：`vendor\chrome` 里的便携版会被自动找到（Playwright 目录布局，
  按 `chrome.exe` 逐层 glob）。
- 首次采集前在 **设置 → 马帮连接** 里绑定账号、按需勾选「无头启动」。

## 数据迁移（可选，从旧电脑搬数据）

新机默认是空库。要沿用旧机数据时，把旧机项目下这两个目录**整体**复制过来（API 停止时拷）：

```text
data\      ← 全部业务数据 + data\ai_credentials_master.key（马帮密码加密主密钥，务必一起拷）
storage\   ← 截图 / 导出 / 更新缓存等运行时文件
```

漏拷 `ai_credentials_master.key` 的话，已保存的马帮登录密码将无法解密（需要重新绑定）。

注意：`data\store.json` 在 2026-11-03 之后是分片布局——业务集合放在
`data\base_products.json`、`data\listing_skus.json` 等独立文件里，**整个 `data\` 目录
一起拷就是完整数据**，不要只拷 `store.json` 一个文件。

## 常见问题

- **端口 8788 被占用**：改 `.env` 的 `API_PORT` 后重启，或 `netstat -ano | findstr 8788`
  找到占用进程。
- **打不开页面**：确认 `start-api-silent.vbs` 已运行；看 `data\api-runtime\api.err.log`。
- **导出报模板缺失**：确认 NAS `\\Juns\产品编辑部\宁\产品导入示例模板 (1).xlsx` 可达，
  或把模板路径写进 `.env` 的 `MABANG_TEMPLATE_PATH`。
- **更新**：更新通道（GitHub Release）需要在设置页配 Token；没有配置时手动用新部署包
  解压覆盖即可（不要覆盖 `data\` 与 `storage\`）。
