# POD 男装 T 恤素材规则

## 1. 当前真实目录

样例目录：

```text
\\Juns\产品编辑部\一步履\POD款式\DL  23#成人纯棉\DL0001超天酱\超天酱-1
```

当前是平铺结构：

```text
主图1.jpg
模特图1.jpg
模特图2.jpg
模特图3.jpg
白色超天酱1.jpg
黑色超天酱1.jpg
灰色超天酱1.jpg
粉色超天酱1.jpg
紫色超天酱1.jpg
POD成人尺码表.png
1超天酱.psd
```

## 2. 系统内部归类

为了兼容当前素材包，扫描器先按文件名识别：

```text
主图*               -> main
模特图*             -> model
白色/黑色/灰色/...  -> variant
尺码表/size/sheet   -> size_chart
psd/ai/cdr/pdf      -> source
mp4/mov/avi/mkv     -> video
印花/透明图/print   -> print
```

`print` 是生产用印刷图（`前印花.png`、`后印花.png`、`前胸印花图.png`、`后胸印花图.png`、
`前胸透明图.png`、`袖口透明图.png`、`后背透明图.png`、`主透明图.png`…，2026-10-09 起）：
它们只用于打样和生产，**不进商品图、不当主图、也不进颜色图和颜色识别**。文件名里的颜色词
指的是印花色而不是衣服颜色（`后印花 (黑色衣服).png` 的「黑色」），所以这类文件的
`sku_option` 一并清空——少了这条，黑色印花图会被当成黑色衣服的颜色图。文件名自己声明
主图的（`主图-印花细节.jpg`）仍然是主图，显式主图优先于印刷图判定。

默认商品图顺序（2026-10-09 起）：**主图 → 模特图 → 颜色图**，各自按文件名自然序，
最多 9 张。颜色图不再需要每件手工加进「产品图顺序」：产品文件夹里只有颜色图
（`A白色.jpg`、`A黑色.jpg`…）时，主图也按 `模特图 > 颜色图 > 详情图` 回退到第一张颜色图。
尺码表/源图/视频/印刷图永远不进这套默认顺序。

颜色图顺序可以跟随这套产品图顺序（2026-10-12 起，商品维护「颜色图」区的开关
**颜色跟随产品图顺序**，默认开、按商品保存）：打开时拖动「产品图顺序」里的图会带动
颜色顺序，拖动颜色行也会把它的颜色图挪到对应位置（主图/模特图的位置不动）。颜色还没选
颜色图、或者那张图不在产品图顺序里时，该颜色保持原有相对顺序排在最后。关掉开关就回到
「颜色自己排、产品图自己排」的老行为。开关只影响排序交互，颜色顺序最终仍写进
`recognized_colors`。

AI 生图工具（新生图skill）产出的本地商品文件夹按 `<SKU>-<序号>-<颜色>.png`、
`<SKU>-sheet.png`、`<SKU>-source.png` 命名，同样按上表识别：颜色图进
`variant_image_map`，`-sheet` 进 `size_chart_image`，`-source` 只留档参考，
两者都不会被当成商品图或主图。没有 `主图-` 文件时，主图按
`模特图 > 颜色图 > 详情图` 回退。

整套没有任何角色线索的图片（`1 (1).png`…`1 (10).png`、`服装布面颜色更换 (1).png`
这类下载文件名）按「未命名颜色图」处理：整组进颜色图，由视觉识别命名颜色
（AI 不可用时兜底 款式A/B/C），不再整批涌进商品图、也不会被商品图 9 张上限截断。
文件夹里只要出现显式主图（`主图-`/`main-`）或已识别的颜色词，就保持原判定——
那些无信息文件是多角度商品图，不是颜色图。

同一个 SKU 文件夹重新导入时，商品图/颜色图只取本次导入目录（`asset_root`）下的素材，
上一次导入留下的图片不会混进商品、颜色识别或导出。

商品维护「产品图顺序 → 可选产品图」支持操作员自己上传图片（PNG/JPEG/WebP，单张
≤10 MiB，可多选）：文件存到 `storage/uploads/product-images/`，同时登记为该商品的
素材（`origin="manual_upload"`，素材角色 main）并追加到商品图末尾——主图不变，
保存维护内容后在商品图选择器、颜色图、马帮导出里都能用。

颜色会写入 `sku_option`，中文文件名也按同一套规范英文名落库（首字母大写、
深浅前缀合成 CamelCase），不用等视觉模型翻译：

```text
白色 -> White          杏色   -> Apricot
黑色 -> Black          棕色   -> Brown
灰色 -> Gray           深蓝色 -> DarkBlue
粉色 -> Pink           浅蓝色 -> LightBlue
紫色 -> Purple         咖啡色 -> Brown
蓝色 -> Blue           藏青色 -> Navy
```

裸颜色字（`白.jpg`）只有自成一段才认；`奶油白`、`米白`这类词表没有的自造色名
保持原样，仍交给视觉模型翻译。

视觉模型给颜色图命名时只允许用这 9 个固定颜色（2026-09-21 起）：
`Apricot`、`Purple`、`Blue`、`Red`、`Black`、`White`、`Pink`、`Brown`、`Green`。
模型要选最接近的一个，不许自造 `LightPurple`、`Cream White`、`Dark Blue` 这类名字，
也不许用中文回答；提示词里直接给出色板，返回结果再按色板归一化一次（`Light Purple`
→ `Purple`、`navy` → `Blue`、`Cream White` → `White`）。
色板里没有的颜色（灰/黄/橙/卡其）以及套装标签（`Costume`/`Wig`/`Costume + Wig`）
保持原样，等操作员在商品维护里手动改，不会被悄悄替换成别的颜色。
文件名里已经认出来的颜色（第 2 节的中英词表）不受这条限制——那是确定性映射，不是 AI 判断。

## 3. 推荐长期目录

不建议立即强制整理历史素材。建议“新旧并行”：

```text
NAS/POD男装T恤/
  DL0001/
    超天酱-1/
      main/
        DL0001-main-01.jpg
      model/
        DL0001-model-01.jpg
        DL0001-model-02.jpg
        DL0001-model-03.jpg
      variants/
        DL0001-white-01.jpg
        DL0001-black-01.jpg
        DL0001-gray-01.jpg
        DL0001-pink-01.jpg
        DL0001-purple-01.jpg
      size_chart/
        DL0001-size-chart.png
      video/
        DL0001-video-01.mp4
      source/
        DL0001-source.psd
      meta.json
```

## 4. 推荐上架图片顺序

POD 男装 T 恤建议：

```text
1. main：主图
2. model：模特图 1
3. model：模特图 2
4. model：模特图 3
5. variant：热卖颜色图，例如黑色/白色
6. size_chart：尺码表
```

变体图用于 SKU 颜色：

```text
white  -> 白色图
black  -> 黑色图
gray   -> 灰色图
pink   -> 粉色图
purple -> 紫色图
```

### 4.1 本土店铺：产品图最后一张放尺码表（2026-10-01）

本土店铺模板上的开关「产品图最后一张放尺码表」（默认开；跨境没有这条规则，保存时强制关闭）
决定**上传到马帮「产品图」区的那一份列表**：

```text
产品图正好 9 张   -> 第 9 张被模板尺码图替换（总数仍是 9，末位是尺码图）
产品图不足 9 张   -> 尺码图追加到末尾（不替换任何产品图）
```

- 副本自带的 `product_images`（商品维护「产品图顺序」）始终是真实产品图，不动；
  只有任务里的 `images.product_image_form_paths` 才带那张尺码图，上传按它逐张进行。
- 描述（详情）里**不再重复插尺码表**：尺码图本来就已经插在描述开头，产品图区末位那张会被
  跳过（执行器 `descriptionProductImageUrls`），步骤结果里 `size_chart_excluded` 报出跳过张数。
- 产品图水印槽位按真实产品图张数算（默认 2..N），尺码图那张不会被水印盖到。
- 尺码图没配或本地文件掉线时**不做替换**（否则上传阶段会 `upload_file_missing` 把整单
  blocked），任务里的 `size_chart_slot_skipped` 会写明原因：`size_chart_missing` /
  `size_chart_file_missing` / `product_images_missing`。
- 开关粒度：模板上是默认值，复制出的副本可以单独再关/开（刊登副本「检查」→「尺码图」）。

## 5. 视频建议

当前样例目录没有视频。建议视频不是必填，但系统应显示：

```text
video: missing
```

后续如果有视频，建议统一放：

```text
video/DL0001-video-01.mp4
```

## 6. 完整度规则

MVP 阶段建议：

```text
main >= 1       必须
variant >= 1    必须
model >= 1      建议
size_chart >= 1 建议
video >= 0      可选
source >= 0     可选
```

如果后续平台要求更严格，再把 model/size_chart 调整为必须。

## 7. 产品维护入口

POD 男装 T 恤不再要求先维护 Excel。推荐流程：

```text
1. 素材库扫描 NAS 目录
2. 商品管理中新建款式
3. 产品维护中编辑标题、重量、长宽高、主图、描述
4. 从产品维护页创建上架任务
5. 执行器按产品库参数执行
```

Excel 只作为批量导入/导出工具，不作为日常上架配置主入口。

## 8. SKU 和价格维护

产品维护页支持按素材颜色生成 SKU：

```text
颜色来源：variant 图片的 sku_option
尺码默认：S,M,L,XL,2XL
SKU 格式：DL0001-COLOR-SIZE
变体名：颜色中文/尺码
```

价格可以按 SKU 批量生成测试值，再逐行编辑：

```text
普通尺码：normal_price 89, sale_price 69
大码 XL/2XL：normal_price 95, sale_price 75
```
