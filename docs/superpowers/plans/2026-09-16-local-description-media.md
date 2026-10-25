# 本土描述末尾插图（尺码表 + 产品图 1-5）实现计划

> **变更（2026-09-19）：** 「产品图只插前 5 张」已取消，改为**全部产品图都插满**。见文末「2026-09-19 变更」。

**目标：** 本土（site）自动化在填完商品描述文字、且产品图上传完成后，于「商品描述」正文**最末尾**依次追加 ① 尺码表图 ② 产品图第 1-5 张。跨境 CNSC 零改动。

**架构：** 新增一个执行器阶段 `append_description_media`，挂在 `upload_product_images` 之后（引用产品图弹窗列的就是刚上传的图）。插图全部走马帮页面自己的交互（引用产品图弹窗 / 网络图片按 URL 插入），由马帮前端写进描述模型，我们不直接改 DOM。阶段失败降级为警告，绝不阻断保存草稿。

**技术栈：** Playwright（CDP 9223）、Node `node:test`、pytest 静态契约测试。

---

## 现场勘查结论（2026-09-16，真实本土草稿）

- 「商品描述」表单项里是 **wangEditor**：正文容器 `div#textDescription.wangEditor-txt[contenteditable=true]`，旁边一个隐藏的 `textarea.code-textarea`（源码模式）。不是纯 textarea，可以插图。
- 工具栏两个插图入口（id 前缀 = 编辑器 id）：
  - `#textDescription_product_img` →「**引用产品图**」弹窗，列表就是**当前商品已上传的产品图**，顺序与「产品图」区一致；每项 `label.el-checkbox` 勾选，底部「确 定」。这就是"必须等产品图上传完毕"的原因。
  - `#textDescription_space_img` →「图片空间」弹窗（账号图库，实测 26064 张、分组懒加载，**不适合按 URL 找图**）。
- 「图片」菜单（`i.wangeditor-menu-img-picture`）打开下拉面板，含「上传图片 / 网络图片」两个页签；**网络图片**可填 URL 直接插入（尺码表图没有引用入口，走这条）。
- 插入产物：`<p><img src="URL" style="max-width:100%;"></p>`，落在**光标处**。
- 详情页是异步渲染的：编辑器容器与描述正文、尺码图表预览都会晚于表单项出现；内容为空时落光标等于落在位置 0，会把图插到正文最前面（实测踩到过）。
- 证据文件：`.scratch/description-area-capture.json`、`description-picker-capture.json`、`description-insert-capture.json`、`description-urltab-capture.json`、`description-insert-modes.json`。

## 实现

**修改：`outputs/ai-listing-system/scripts/mabang_playwright_executor.cjs`**

- [x] 新增文案常量 `productImageMenu/networkImage/submitImage/referenceProductImage`。
- [x] `collectProductImageUrls(frame, limit=5)`：产品图区（拥有 `input[type=file][multiple]` 的块）按页面顺序取前 5 张已上传图 URL。
- [x] `collectSizeChartPreviewUrl(frame)` / `waitForSizeChartPreviewUrl`：尺码图表 `img.img` 预览地址（上传图与模板预览图都能用）。
- [x] `buildDescriptionMediaPlan`（尺码表在前、产品图 ≤5）与 `missingDescriptionMedia`（幂等）。
- [x] `descriptionMediaAppendedAtTail(html, inserted, existing)`：位置校验——本批地址按序出现、图与图之间及最后一张之后只有空段落/换行、原有图仍在本批之前。
- [x] `descriptionEditorContext`：等表单项 + **等 wangEditor 容器**出现（后渲染）。
- [x] `waitForDescriptionContentReady`：等正文出现并连续两次读数一致，避免空内容落光标。
- [x] `focusDescriptionEnd`：click + Control+End，校验 DOM 选区贴在最后一个文本节点；键盘不生效时退回 `Range.collapse(false)` 直接落光标。
- [x] `insertDescriptionImageByUrl`（图片→网络图片）、`insertDescriptionProductImages`（产品图片→引用产品图→勾前 N 张→确定）。
- [x] `insertDescriptionMediaBatch` + 重试循环（`DESCRIPTION_MEDIA_ATTEMPTS = 3`）：插完验位置，不在最下面就用 `Ctrl+Z` 撤掉这批重来；三轮都失败才降级为警告（`description_media_position_not_at_tail`）。
- [x] `appendDescriptionMedia(frame, task)`：跨境 `cnsc_global` 直接 `{skipped:true}`；其余失败一律 emit `{stage, status:'warning', code}`，不阻断草稿。
- [x] 加入 `PRE_SAVE_STAGES`：紧跟 `upload_product_images` 之后。

**修改：`scripts/browser_use_runner.py`**：任务计划 actions 在 `upload_product_images` 后补 `append_description_media`（仅本土；跨境不追加）。

**修改：`apps/web/app.js`**：`AUTOMATION_STAGE_LABELS` 增加 `append_description_media: "正在追加描述图片（尺码表 + 产品图）"`；`apps/web/index.html` 同步 bump `app.js?v=`。

## 验证

- Node：`NODE_PATH=node_modules/.pnpm/node_modules node --test tests/mabang_playwright_executor.test.cjs`（62 项通过，含阶段顺序、跨境短路、取前 5/去重、位置校验、失败降级）。
- Python：`python -m pytest tests/test_browser_use_runner.py tests/test_automation_status_ui.py -q`（88 项通过）；全量 `python -m pytest -q` → 731 passed / 14 failed（14 项为工作区既有红项，与本改动无关）。
- 现场（CDP 9223，真实本土草稿，`.scratch/verify_description_media_stage.cjs`）：
  - 冷启动 3 次连续通过，结果均为 `size_chart:true, product_images:5, inserted:6`，图片全部落在正文末尾（tailGap≈148 字符的空段落），顺序 = 尺码表 → 产品图 1-5；其中一次在第一轮插错位置后由重试（round 2，Range 落光标）纠正。
  - 幂等：同页连跑第二次返回 `{already:true, method:'already_present'}`，图片数仍为 6。
  - 每次验证后都把描述还原为原样（不保存草稿）。

## 影响与回滚

- 只影响本土；跨境 CNSC 阶段短路、任务计划不含该动作。
- 描述插图失败只警告，草稿照常保存；位置不对时先撤销再报警告，不会留下半截描述。
- 系统里存的 `description` 仍是纯文字，插图只发生在马帮页面上（自动化运行时）。
- 回滚：删除 `PRE_SAVE_STAGES` 中 `append_description_media` 一行 + 前端标签 + 任务计划动作项。

## 2026-09-19 变更：产品图全插（不再截前 5 张）

**原因：** 现场描述末尾只有 6 张图（尺码表 1 + 产品图前 5），而本商品实际上传了 8 张产品图
（`.scratch/description-insert-capture.json` 里「引用产品图」弹窗 `dialogOrder.count = 8`）。操作员要求补满。

- `scripts/mabang_playwright_executor.cjs`：
  - `DESCRIPTION_MEDIA_LIMIT = 5` → `DESCRIPTION_MEDIA_MAX_IMAGES = 30`（只是页面结构异常时的护栏；
    商品图本身有 9 张上限，见 `storage.PRODUCT_IMAGE_LIMIT`）。`collectProductImageUrls` /
    `buildDescriptionMediaPlan` 的默认上限都用它，产品图有多少插多少。
  - `insertDescriptionProductImages(frame, editorId, wantedUrls)`：参数从「前 N 张」改成**待插地址集合**，
    读弹窗里每个 `.images-item` 的 `img.src`，只勾地址命中的那些。重跑（`Ctrl+Z` 重试、
    `--resume-from-stage`）时描述里已有的图不会被再插一遍，也不会因为「按前 N 项」而漏掉后面的图。
  - 点 `label.el-checkbox` 是**切换**语义，弹窗若带着上次的勾选状态重开，再点一次会把已勾的取消掉：
    勾之前先读一遍各行的 `input[type=checkbox].checked`，只点还没勾上的。
  - 弹窗里一张都匹配不上 → 抛 `description_product_images_not_listed` 并点「取 消」关掉弹窗
    （取消按钮拿不到就点标题栏的关闭按钮），调用方按既有逻辑退回「网络图片按 URL 逐张插入」。
- `scripts/browser_use_runner.py`：动作文案 `在商品描述末尾追加尺码表与产品图 1-5` → `…与全部产品图`。
- 跨境 CNSC 依旧整段跳过，未改动。
- 测试：
  - `tests/mabang_playwright_executor.test.cjs`：计划断言改成 7 张全进计划；新增「按地址勾选」
    用例（8 张全勾、子集只勾缺的、弹窗预勾选时不重复点、全不匹配时报错关弹窗）。Node 执行器 72 项通过。
  - 离线真机 DOM 集成：`.scratch/integration_check_description_picker.cjs` 用 `.scratch/description-insert-capture.json`
    里采集的真实 `.images-item`/label/按钮 DOM（8 个真实图 URL）在本地 Chromium 复刻弹窗，实跑该函数：
    `all=8`、子集只勾 `[2..7]`、预勾选 `[0,1]` 时 confirmed 仍是 `[0..7]`、匹配不上时报
    `description_product_images_not_listed` 且弹窗已关。不连马帮、不碰真实草稿。

**上线方式：** 执行器是每个任务新起的 node 子进程，**不用重启 worker/API** 即生效；
`browser_use_runner.py` 的动作文案要重启 worker 才会变（纯文案）。

**待现场复验：** 下次真机跑本土任务时确认描述末尾图片数 = 尺码表 1 + 该商品产品图全量，
且顺序与「产品图」区一致（`.scratch/verify_description_media_stage.cjs` 可直接复用）。

**诊断依据（2026-09-19 生产任务）：** 任务 `74282690` 的 `apply_product_image_watermark`
`image_positions = [2,3,4,5,6,7,8,9]`（8 张产品图），而描述里只插了 6 张（尺码表 1 + 前 5）。


## 2026-09-23 变更：修「滚到底」误判、清掉幽灵图地址、选择模板时用本地上传的尺码图

**现场症状（HN_POD，店铺模板 `814c2428` 的尺码图表切到「选择模板」之后）：**
2026-09-21 15:48 起的 7 条本土作业，`append_description_media` 全部降级为警告
`description_caret_not_at_end`，描述里**一张图都没插进去**。再往前的上传模式作业（09-20 12:xx）
是另一个失败 `description_media_not_verified`。

**根因 1：`descriptionScrolledToBottom` 把页面主滚动容器算进来，判定必然为假。**

`focusDescriptionEnd` 要求「光标在正文末尾」+「滚到底」两条同时成立。原实现从编辑器一路往上，
要求每个可滚祖先（含 `document.scrollingElement`）都到底。现场埋点（`.scratch/caret_diag.jsonl`，
`node .scratch/analyze_caret_diag.py` 汇总）：

- 光标判定 18 次尝试全部为真（`anchorNode === editor`、`anchorOffset === childNodes.length`）；
- `main.el-main`（el-container 的主滚动区）内容 4051px、可视 785px，`scrollTop` 停在 510：
  描述表单项下面还有变体、SKU 网格、物流信息等一大票表单项，**它永远到不了底**；
- `scrollDescriptionToBottom` 先把祖先滚到底，随后 `editor.scrollIntoView({ block: 'end' })`
  又把页面滚回去让编辑器停在视口底部——于是判定恒假，三轮全败、整批回滚。

修法（只动执行器）：

- `scrollDescriptionToBottom`：只把**编辑器自己的滚动盒子**（`.wangEditor-txt`，自带 `overflow:auto`）
  贴到底，再用 `scrollIntoView({ block: 'nearest' })` 把编辑器滚进视野；不再要求外层容器到底。
- 新增 `scrollDescriptionEditorBoxToBottom`：落完光标后浏览器可能把盒子滚回顶部
  （现场第一次尝试 `scrollTop=0`，后两次才是 277），落光标后再钉一次；滚动不移动 DOM 光标。
- `descriptionScrolledToBottom`：只看编辑器自己的盒子（4px 容差）。
  `descriptionCaretAtEnd`（DOM 判定）仍是硬条件，插错位置还有插后位置校验 + `Ctrl+Z` 回滚兜底。

**根因 2：产品图区里 `<img src="">` 的占位图混进插图计划。**

`collectProductImageUrls` 读的是 `image.src`（解析后的属性）：src 为空时浏览器把它解析成
当前页面地址。现场产品图区里 `<div class="img-content"><img src=""></div>` 让
`https://publish.mabangerp.com/publish-ui/` 进了计划——它既插不进描述、也不在「引用产品图」
弹窗里，校验时永远缺一张，整批插图被判「没插全」后回滚（09-20/09-22 上传模式那批
`description_media_not_verified` 是同一类幽灵地址，当时形态是空串）。

修法：改读 `image.getAttribute('src')`，空的/相对的直接跳过。

**根因 3：选择模板时页面上没有可引用的尺码图地址。**

「尺码图表」选「选择模板」后，表单项里只有「上传图片 / 选择模板 / 同步」三个控件，
**没有 `img`**（现场 `collectSizeChartPreviewUrl` 返回空），描述末尾那张尺码表图取不到来源。
操作员的做法是「选模板 + 再把尺码图上传一份，用这张图插详情」。为此：

- 执行器 `descriptionSizeChartSource(task)`：来源是模板且任务带着本地 `image_path`（文件存在）时
  走 `file`，否则走原来的 `url`（预览地址）。上传模式完全不变。
- 新增 `insertDescriptionImageByFile`：走描述编辑器「图片 → 上传图片」页签。这一页签点开的是
  系统文件对话框（面板里没有 file input，只有 `.upload-icon-container` 图标），用 Playwright
  的 `filechooser` 拦截后 `setFiles`；wangEditor 自己 POST 到马帮、把返回的 COS 地址插到光标处。
- 新增 `uploadDescriptionSizeChartFile`：先 `focusDescriptionEnd` 钉光标 → 选文件 → 等新图落到正文
  （`DESCRIPTION_SIZE_CHART_UPLOAD_TIMEOUT = 60s`）→ 返回马帮给的地址。拿到的地址并进 `existing`
  后走**同一套**计划/去重/顺序/位置校验；回滚基线也跟着带上它（不会把已插好的尺码图撤掉）。
  上传失败只记 `description_size_chart_upload_failed` 警告，产品图照插。
- `services/api/storage.py`：`_build_store_listing_copy` 不再在「选择模板」时清空本地尺码图字段，
  模板上 `default_size_chart_path` / `size_chart_asset_id` 原样带给副本（没上传时仍是空路径，
  行为与改动前一致）。
- `apps/web/app.js`：店铺模板保存时 `size_chart_asset_id` / `size_chart_preview_url` /
  `default_size_chart_path` **优先取操作员上传的那张图**（模板自带预览图次之），
  `mabang_size_chart_template_id/name` 照旧；「选择图片」入口两种模式都保留，选择模板时提示
  「这张图会插到商品描述末尾」。`styles.css` 补 `.size-chart-picker[hidden]`（子元素 display 会盖过 hidden）。
- 新增错误码文案 `description_size_chart_upload_failed: "本地尺码图没传进描述，只插了产品图（请检查店铺模板里上传的尺码图）"`。

**测试：**

- `tests/mabang_playwright_executor.test.cjs`：改写「滚到底」两条用例（只看编辑器自己的盒子 +
  现场数值 4050px/785px 的说明）、产品图收集改成读原始属性并覆盖 `src=""` 幽灵图、
  新增 `descriptionSizeChartSource` 四种组合与「上传图片」链路契约；83 项通过。
- `tests/test_product_line_flow.py`：`test_listing_copy_snapshots_a_selected_mabang_size_chart_template`
  改为断言模板模式**保留**本地尺码图路径；新增「只选模板、没传图 → 没有本地图路径」用例。
- `tests/test_store_template_workflow_ui.py`：补上传图优先 + 入口常显的源码断言。

**复验方式（真机，CDP 9223，跑到保存前停下）：**

```
python .scratch/diag_description_caret.py <job_id>            # 用冻结任务重放，--stop-before-save
DIAG_SIZE_CHART_IMAGE=<本地尺码图> python .scratch/diag_description_caret.py <job_id>   # 复现「选模板 + 一并上传图」
node .scratch/analyze_caret_diag.py                            # 汇总 .scratch/caret_diag.jsonl 里两个子条件的真假
```

**现场复验（2026-09-23，CDP 9223，冻结任务 `34101810`，`--stop-before-save`）：**

- 修前：`description_caret_not_at_end`（三轮全败、整批回滚，描述 0 张图）；
- 修后（只修根因 1+2）：`description_media_not_verified`，`missing: ["product_image:publish-ui"]`
  —— 9 张产品图其实已经插进去了，被幽灵地址判掉；
- 修后（+根因 3，注入本地尺码图）：`{"stage":"append_description_media","ok":true,"size_chart":true,
  "product_images":9,"inserted":9,"caret":"range","round":1,"method":"size_chart:upload+product_images:reference"}`，
  描述末尾 10 张图 = 本地上传的尺码图（马帮回 `https://publish-cos.mabangerp.com/product/publish/...`）+ 9 张产品图，
  顺序正确、光标一次到位（`round: 1`）。

**上线方式：** 执行器改动（根因 1/2/3 的页面链路）随每个任务新起的 node 子进程立即生效；
`services/api/storage.py` 的副本构建改动要**重启 API**（worker 通过 HTTP 取任务快照，不用重启），
前端改动刷新页面即可。

**已知取舍：** 本地文件那条路靠「上传后读回地址」，重跑同一页面（`--resume-from-stage`）时无法按
地址去重，会再插一张；正常一个任务只在新建的空白表单上跑一次，跨尝试会重新开页面，不受影响。
