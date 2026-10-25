"""店铺刊登副本列表的交互约束：全选只勾新建、已完成按时间分桶可折叠。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_select_all_only_selects_new_copies() -> None:
    """现场反馈：全选把「已完成」的一起勾上，批量生成任务时容易误操作。
    现在全选只覆盖「新建」区（没有作业、还没跑过的副本）。"""

    source = APP.read_text(encoding="utf-8")
    assert "function selectableListingCopies()" in source
    helper = source[source.index("function selectableListingCopies()"):]
    helper = helper[: helper.index("\n}\n")]
    assert 'listingCopyTaskSection(jobs.get(copy.id)) === "new"' in helper, "只挑新建区"

    toggle = source[source.index("function toggleSelectAllListingCopies()"):]
    toggle = toggle[: toggle.index("\n}\n")]
    assert "const targets = selectableListingCopies();" in toggle, "全选走新建范围"
    assert "state.storeListingCopies" not in toggle, "不再对全量副本勾选"

    label = source[source.index("function updateListingCopySelectAllButton()"):]
    label = label[: label.index("\n}\n")]
    assert "全选新建" in label and "取消全选新建" in label, "按钮文案点明范围"


def test_completed_copies_are_bucketed_by_time_and_collapsible() -> None:
    """已完成区按完成时间分桶（今天/昨天/近 7 天/近 30 天/更早），每桶与整区都能折叠隐藏。"""

    source = APP.read_text(encoding="utf-8")
    for label in ["今天", "昨天", "近 7 天", "近 30 天", "更早"]:
        assert f'label: "{label}"' in source, f"缺少时间桶 {label}"
    bucket_fn = source[source.index("function completedBucketFor("):]
    bucket_fn = bucket_fn[: bucket_fn.index("\n}\n")]
    assert 'return "older"' in bucket_fn, "时间戳解析不了时归入更早（不误判成今天）"

    render_fn = source[source.index("function renderStoreListingCopies()"):]
    render_fn = render_fn[: render_fn.index("\n}\n")]
    # 分桶抽成了通用函数（自动化任务列表复用），副本列表用 completed: 前缀
    assert "bucketByTime(" in render_fn and '"completed",' in render_fn, "已完成区走分桶"
    assert "copySectionCollapsed(section.id)" in render_fn, "整区可折叠"
    assert "copySectionCollapsed(group.key)" in render_fn, "分桶可折叠"
    assert "appendCopyBucketRow(tbody, group, 11, groupCollapsed)" in render_fn

    # 折叠状态持久化 + 点击委托 + 键盘可达
    assert 'COPY_SECTION_COLLAPSED_KEY = "ai_listing.store_copies_collapsed"' in source
    assert "window.localStorage.setItem(COPY_SECTION_COLLAPSED_KEY" in source
    assert 'target.closest("[data-copy-section-collapse], [data-copy-collapse]")' in source
    keydown = source[source.index('document.addEventListener("keydown", (event) => {\n  if (event.key === "Escape"'):]
    assert "data-copy-section-collapse" in keydown[:1400], "Enter/空格 也能折叠"


def test_copies_panel_copy_and_assets_updated() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "「全选新建」只勾选还没跑过的副本" in html
    assert "已完成区按完成时间分组，点分组标题可折叠隐藏" in html
    # app.js 是静态文件直出，改动必须带新的缓存参数，否则操作员浏览器仍加载旧脚本。
    assert "feature=copy-list-new-only-v1" in html

    styles = STYLES.read_text(encoding="utf-8")
    assert "#automation .table-wrap .copy-bucket-row" in styles
    assert "copy-bucket-caret" in styles


def test_local_folder_picking_avoids_browser_upload_confirm() -> None:
    """本地添加商品不再弹窗：改用 File System Access 选目录，且去掉自己的批量确认框。"""

    script = APP.read_text(encoding="utf-8")
    planner = (ROOT / "apps" / "web" / "local-folder-batch.js").read_text(encoding="utf-8")

    assert "async function pickLocalProductFolder()" in script
    assert 'typeof window.showDirectoryPicker === "function"' in script
    assert 'async function collectDirectoryHandleFiles(directoryHandle, prefix = "")' in script
    assert 'collectDirectoryHandleFiles(handle, String(handle.name || ""))' in script
    assert 'Object.defineProperty(file, "__relativePath", { value: path, enumerable: false })' in script
    # 按钮走新路径；只有回退时才 click 老 input
    assert 'if (target.id === "create-local-base-product-btn") {' in script
    assert "pickLocalProductFolder().catch(" in script
    # 批量导入不再 window.confirm（直接入队 + 提示）
    batch_block = script[script.index("if (plan.mode === \"batch\")"): script.index("const category = categoryProfile(", script.index("if (plan.mode === \"batch\")"))]
    assert "window.confirm(" not in batch_block
    assert "showOperationFeedback(" in batch_block
    # 规划器认 __relativePath
    assert "file?.__relativePath || file?.webkitRelativePath" in planner


def test_local_import_supports_multiple_folders_batch() -> None:
    """批量导入多条路径：选父文件夹（一次多个）、连续点按钮累加、拖多个文件夹。"""

    script = APP.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 拖多个文件夹：递归读目录条目 + 每个顶层文件夹单独入队 + 汇总一条提示
    assert "async function importDroppedFolders(dataTransfer)" in script
    assert "async function collectFileSystemEntries(directoryEntry, prefix = \"\")" in script
    assert 'collectFileSystemEntries(entry, String(entry.name || ""))' in script
    assert "function setupLocalFolderDropZone()" in script
    assert 'zone.dataset.dropZoneReady === "1"' in script
    assert "bootstrapLocalFolderDropZone();" in script
    assert "已把 ${queued} 个文件夹加入导入队列" in script

    # 乱序/静默：拖拽时不要每条都弹提示
    assert "async function createBaseProductFromLocalFiles(allFiles, options = {})" in script
    assert "if (options.quiet !== true) {" in script

    # 提示与样式
    assert "多个商品文件夹一起拖进基础商品面板" in html
    assert "#product-maintenance.is-drop-target" in styles
