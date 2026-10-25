# AI Listing · 马帮采集器（Chrome 扩展）

这是内网自用扩展，让运营在自己的 Chrome 里采集马帮商品编辑页数据（店铺、类目、促销、水印、尺码图，以及 CNSC 全球价、三仓库存、目标店铺、站点价格和折扣活动），直接推送到内网 AI Listing 服务，不再依赖服务器本机的 Chrome。

## 安装（sideload unpacked）

1. 打开 Chrome → `chrome://extensions`
2. 右上角打开"开发者模式"
3. 点击"加载已解压的扩展程序"，选中本目录（`browser_extension/`）
4. 记录扩展的 ID（在扩展卡片下）

## 使用方式一 · Web UI 触发（推荐）

1. 在 AI Listing web UI（如 `http://10.144.0.3:8787`）点击"从马帮当前页导入"
2. Web UI 会在新 Tab 中打开马帮的新建刊登页，URL 尾部带上 hash `#ai_listing_run_id=xxx&api=http://10.144.0.3:8787`
3. 扩展 content script 读到 hash → 存到 chrome.storage → 页面右下角出现橙色悬浮球
4. 运营正常在马帮页面里操作（选店铺、类目、必填项、促销、尺码图、水印）
5. 数据填齐后点击悬浮球：
   - 采集中… → 推送到服务器… → **√ 已推送**（2 秒后悬浮球自动消失）
6. 回到 Web UI 点"确认导入"完成入库

## 使用方式二 · 手动激活（备用）

1. 点击工具栏扩展图标 → popup
2. 从 Web UI 拷贝 Run ID 和 API 地址（如 `http://10.144.0.3:8787`）
3. 点"激活采集会话"
4. 打开马帮的商品编辑页 → 悬浮球出现 → 走上面第 4 步

## 数据流

```
运营 Chrome (自己电脑)                    AI Listing 服务器
──────────────────────                    ─────────────────
[马帮编辑页]                              /api/mabang-page-imports
  content.js (isolated world, 每帧)          POST /complete
    ↓ 注入 <script> 到本帧 MAIN world
  page_capture.js (MAIN world, 每帧)
    ↓ 扫本帧 Vue __vue__
  {raw_capture}    ── fetch POST ──────►  stage_mabang_page_import
                                              ↓ confirm 时
                                          落库 (store/category/watermark/...)
```

- 马帮商品编辑表单在**跨域 iframe**（`publish.mabangerp.com`）里，不在 `www.mabangerp.com` 顶层页面。扩展以 `all_frames: true` 在**每个 frame** 注入 content script；点悬浮球后，捕获请求从顶层向下扩散到所有子 frame，每个 frame 在自己的文档里跑 `page_capture.js`，结果汇总回顶层，取「能读到店铺编号」的那一份推送。服务端 Browser-Use 路径也是同样的逻辑（用 CDP 直接定位 `publish.mabangerp.com` frame 执行）。
- 只**读取**页面 Vue 数据。CNSC 采集会临时打开店铺和折扣活动选择器，读取后用“取消”或关闭按钮退出；不会点击马帮的保存/发布/提交按钮。
- 采集脚本与服务器端 `scripts/mabang_page_import_capture.js` 是同一份逻辑（本目录里的 `page_capture.js` 是它的 MAIN-world 包装）。有页面结构变化时两处都要同步更新。
- 修改采集核心后运行 `node scripts/sync_browser_extension_capture.cjs` 重新生成扩展包装文件；测试会阻止两份逻辑漂移。
- 采集结果带 `capture_debug`（是否找到编辑表单、storeCategory 引用、`store_info_keys`/`module_keys`、frame 地址），店铺编号识别失败时后端会在 422 错误里带上这些诊断。

## 平时不启动

- 没有活跃会话（chrome.storage 里没 `aiListingSession`）时，**不显示悬浮球**，不干扰运营在马帮的正常操作。
- 会话通过 URL hash 或 popup 显式激活，1 小时后失效需重激活。
- 推送成功后自动清除会话、隐藏悬浮球。

## 权限说明

- `host_permissions`：`https://*.mabangerp.com/*`（采集所在页），以及 `http://*/*` `https://*/*`（内网 API 可能是 http，需要允许 fetch）
- `permissions`：`storage`（保存会话）、`activeTab`（无实际用，兼容旧行为）
- 不上传任何数据到第三方，只 POST 到 popup 里填的 API 地址

## 不做的事

- 不带自动更新（内网 sideload；改代码后需要在 `chrome://extensions` 点"重新加载"）
- 不做 Chrome Web Store 发布
- 不做多店铺并行会话（单个会话，简单）
- 不做数据加密（内网信任模型，run_id 就是凭证）
