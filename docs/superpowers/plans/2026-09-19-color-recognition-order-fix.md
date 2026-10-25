# 颜色识别错位修复（数字文件名 + 颜色名归一化）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**症状：** 本地/NAS 导入后颜色图和颜色对不上——「红色配到别的颜色」，严重时整批颜色错位一格，个别颜色只有名字没有图（AL0508、AL0507，2026-09-18 导入）。

**结论：** 两处都是我们自己管线里的确定性缺陷，不是模型看错图。

---

## 根因 1：图片顺序 ≠ 提示顺序

`storage.assets_for_group` 按文件名**字典序**排：`1 (1).png, 1 (10).png, 1 (2).png, …`。
提示里的文件名清单和实际发给模型的图片都按这个顺序，但**人和模型都按数字读文件名**——
模型把第 N 个提示文件名配到第 N 张图时，从出现 `1 (10).png` 的位置开始整体错位一格。

用 AL0508 的真实数据复现（模型按数字序回答、图片按字典序发），产出的
`recognized_colors` 与库里存的一字不差：

```
复现 colors: ['DarkGreen','LightPurple','LightBlue','Brown','White','Pink','Royal Blue','Red','Beige','Black']
库里 colors: ['DarkGreen','LightPurple','LightBlue','Brown','White','Pink','Royal Blue','Red','Beige','Black']
库里 map:    Brown→1 (3)（真值 Brown→1 (2)）… Red→1 (7)（真值 Red→1 (6)）
```

## 根因 2：颜色名归一化只做了一半

模型把复合色写成 `Light Purple`（带空格）时：

- `recognized_colors` 经 `_title_case_english` 归一化成 `LightPurple`；
- `variant_image_map` 的键仍是模型原样的 `Light Purple`；
- `_variant_image_map_for_colors` 用颜色名去查映射，键对不上 → **整条映射被静默丢掉**。

AL0507 存的 `colors`（9 个）与 `map`（7 条）正是这个形状：`LightPurple`、`DarkGreen` 只有名字没有图。

## 修复

- `storage.natural_name_key()` / `_natural_parts()`：文件名数字段按数值比较（`1 (2)` < `1 (10)`）；
  `_default_variant_image_map` 也改用自然序。
- `vision_listing.ordered_unnamed_variant_assets()`：未命名 SKU 图统一按自然序，
  **提示清单、发给模型的图片、写回映射的顺序都取自它**。
- `_pick_image_paths(color_only=True)`：颜色那一趟只发未命名 SKU 图，不再掺入商品上已有的
  颜色图映射（那份映射在重试时可能正是错的，会把图片顺序打乱）。
- 提示改成带编号的清单（`#1 1 (1).png` …），并且**只列真正读进来的图片**（坏图不会再让编号错位）。
- `storage.normalize_color_label()`：颜色列表和映射键用同一套写法，`Light Purple` → `LightPurple` 两边一致。
- `_variant_image_map_for_colors`：匹配时忽略大小写与空格，历史数据里已经对不上的映射在下次保存时自动接回。
- 裸数字键的匹配优先级：整文件名 → 主干 → **该文件独有的**数字段 → 提示里的编号 `#N`。
  `1 (3).png` 里前导的 `1` 每个文件都有，拿它当键会把一堆文件配到同一个颜色上。
- 重试（「重试 AI 识别」）以这次识别为准：未命名文件上残留的旧绑定不再带进来，
  同名的“有颜色名没图”的半截数据这次补上绑定，所以重试能真正修好既有错配。

## 验证

- `python -m pytest tests/test_vision_listing.py -q` → 40 passed，新增 5 项：
  自然序提示/图片顺序、复合色名保留映射、忽略空格的颜色匹配、共用数字段不当键、重试替换旧绑定。
- 用 AL0507/AL0508 的存量数据离线跑通「重试」：AL0508 修复后 10 个颜色 / 10 张图全对，
  AL0507 补回 `DarkGreen`、`LightPurple` 两条绑定。
- 存量受影响商品只有 AL0508（10 张图但只有 9 个颜色）需要重试一次；AL0507 已被操作员重试修好。

## 注意

- 改的是 `services/api/*.py`，**必须重启 API**（正在跑的进程仍是旧代码）。
- 前端无需改动。
