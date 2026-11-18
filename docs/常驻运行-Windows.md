# Windows 常驻运行说明

目标：工作台 API（端口 8788）**登录后自动启动**，并且**挂了自动重启**，日常不用手动开。

## 当前采用的方案（无需管理员权限）

| 组成 | 位置 | 作用 |
|---|---|---|
| 自启项 | 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 键名 `MabangListingApiSupervisor` | 用户登录时拉起看护进程 |
| 看护脚本 | `run_api_supervisor.pyw` | 每 60 秒探 `/api/health`，连续 2 次不通就重启 API |
| API 启动器 | `run_api_silent.pyw`（pythonw，无控制台） | 真正跑 `services.api.main` |

自启项实际值：

```text
"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe" "C:\Users\Administrator\Desktop\马帮自动化\run_api_supervisor.pyw"
```

## 为什么不用 Windows 服务 / 计划任务

- **Windows 服务**会把进程放进会话 0，采集用的 Chrome 拿不到桌面会话，浏览器相关功能会静默失效。
- **计划任务**需要管理员权限，当前是标准用户（Medium 完整性），`Register-ScheduledTask` 返回"拒绝访问"。
- 所以用「登录自启 + 用户会话内看护」，既满足常驻，又不影响浏览器自动化。

代价：**必须先登录 Windows** 才会自动启动（未登录时不会有进程）。这对需要桌面会话的采集浏览器来说反而是必须的。

## 日志

| 文件 | 内容 |
|---|---|
| `data/api-runtime/supervisor.log` | 看护记录：何时探测失败、何时拉起、是否恢复（1MB 轮转 3 份） |
| `data/api-runtime/api.out.log` / `api.err.log` | API 自身输出（框架自带轮转） |

## 常用操作

```powershell
# 看 API 是否活着
curl http://127.0.0.1:8788/api/health

# 看看护记录
Get-Content "C:\Users\Administrator\Desktop\马帮自动化\data\api-runtime\supervisor.log" -Tail 20

# 临时停掉（先停看护，再停 API，否则会被拉回来）
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'run_api_supervisor' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'run_api_silent' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 重启（推荐）：双击项目根目录的 _restart_supervisor.bat
#   等价于「停看护+API → 看护重新拉起 API」；从自动化/沙箱会话里也用它（用 explorer 打开）

# 彻底取消开机自启
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'MabangListingApiSupervisor'
```

## 注意

1. **不要同时手动双击 `start-api-silent.vbs`**：看护已在跑时再起一个会因端口占用而失败（日志里会多一条 address in use，无害但吵）。需要重启时直接杀掉 API 进程，看护会自动拉起。
2. 看护脚本靠 `data/api-runtime/supervisor.lock` 做单实例锁，重复启动会立刻退出，不会双开。
3. 若哪天把 WorkBuddy 自带 Python 换路径/升级版本，自启项里的 `pythonw.exe` 绝对路径要同步改（依赖装在那份 Python 上）。
4. 以后拿到管理员权限，可以改成计划任务（登录时触发、崩溃重启），但**不要**做成 Windows 服务。
