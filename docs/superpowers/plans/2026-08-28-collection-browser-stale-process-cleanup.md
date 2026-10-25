# 采集浏览器残留进程自动清理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 启动采集浏览器前自动关闭同一采集 profile 或 CDP 端口对应的残留 Chrome 实例，避免 profile 锁和残留窗口导致 Chrome 退出码 0。

**架构：** 在现有 `mabang_browser.launch_mabang_browser` 启动前增加 Windows 进程枚举清理。通过 WMI 查询 Chrome 命令行，仅匹配规范化的 `--user-data-dir` 或 `--remote-debugging-port`，先调用 terminate，超时后 kill；查询/清理失败不阻断后续启动。非 Windows 平台跳过该清理。

**技术栈：** Python 标准库、Windows `wmic`/PowerShell 进程查询、pytest。

---

### 任务 1：为残留进程清理定义回归测试

**文件：**
- 修改：`outputs/ai-listing-system/tests/test_mabang_browser.py`
- 修改：`outputs/ai-listing-system/services/api/mabang_browser.py`

- [x] **步骤 1：编写失败的测试**
  覆盖命令行匹配、非目标进程保留，以及 `launch_mabang_browser` 在 `Popen` 前调用清理。

- [x] **步骤 2：运行测试验证失败**
  运行 `pytest outputs/ai-listing-system/tests/test_mabang_browser.py -q`，预期新增测试因清理函数不存在或未调用而失败。

- [x] **步骤 3：编写最少实现代码**
  增加 Windows 进程查询/匹配/终止辅助函数，并在现有启动流程创建 profile 后、`Popen` 前调用。

- [x] **步骤 4：运行测试验证通过**
  运行同一 pytest 命令，确认该文件全部通过。

- [x] **步骤 5：运行相关 API 回归测试**
  运行 `pytest outputs/ai-listing-system/tests/test_mabang_page_import_api.py -q`，确认启动 API 的既有契约不变。
