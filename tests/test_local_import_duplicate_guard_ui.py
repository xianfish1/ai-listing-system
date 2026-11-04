"""Static UI contract for the local-import duplicate guard.

批量把多个商品文件夹拖进「基础商品」面板时，同一个商品目录只能入队一次：
重复的目录（同一批拖重了、队列/本次会话已导入、拖了父目录又拖子目录）会被
createBaseProductFromLocalFiles 跳过，并在提示里点名。这里锁住接线与文案，
纯逻辑由 tests/local_folder_batch.test.cjs 覆盖。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
PLANNER = ROOT / "apps" / "web" / "local-folder-batch.js"


def test_planner_exposes_duplicate_helpers() -> None:
    planner = PLANNER.read_text(encoding="utf-8")
    for helper in [
        "function importGroupSignature(",
        "function groupContainsGroup(",
        "function splitDuplicateLocalImportGroups(",
        "function dropContainedLocalImportBatches(",
        "function folderSkuToken(",
    ]:
        assert helper in planner
    # 导出给 app.js 用；前端也在本地导入前做重复拦截。
    assert "splitDuplicateLocalImportGroups," in planner
    assert "dropContainedLocalImportBatches," in planner
    assert "folderSkuToken," in planner


def test_drop_import_skips_duplicates_and_reports_them() -> None:
    script = APP.read_text(encoding="utf-8")

    # 拖拽入口先按「父目录包含子目录」过滤顶层文件夹，再逐个规划入队。
    assert "const dedupedBatches = dropContainedLocalImportBatches(batches);" in script
    assert "const batchesToImport = dedupedBatches.batches;" in script
    assert 'reason: "nested"' in script
    # 汇总提示只该出现一次（重复/残留的提示块会在运行时报未定义变量）。
    assert script.count("已把 ${queued} 个文件夹加入导入队列") == 1

    # 入队前用「队列 + 本次会话已成功」的项查重，重复的直接跳过（不再重复创建）。
    assert "function knownLocalImportGroups(snapshot)" in script
    # 商品被删掉后，同一目录要能重新导入。
    assert "liveIds.size && !liveIds.has(productId)" in script
    assert "splitDuplicateLocalImportGroups(" in script
    assert "if (groups.length) {" in script
    assert "queue.enqueue(groups.map((group) => ({" in script

    # 提示里点名跳过了哪些目录、为什么；单目录导入只有出现重复/覆盖风险才提示。
    assert "const LOCAL_IMPORT_DUPLICATE_REASONS = {" in script
    assert "已在队列或本次会话导入" in script
    assert "本次拖入里重复" in script
    assert "已包含在本次拖入的上级目录里" in script
    assert "function localImportDuplicateHint(duplicates)" in script
    assert "跳过 ${duplicates.length} 个重复导入" in script
    assert 'if (options.quiet !== true && (plan.mode === "batch" || duplicates.length || conflictNames.length)) {' in script
    assert "没有新商品加入队列" in script

    # 商品库已有同名主 SKU：后端按 group_id 覆盖旧记录，导入前必须提醒。
    assert "function localImportLibraryConflicts(groups)" in script
    assert "在商品库里已有同名主 SKU" in script
    # 同一批里多个目录对应同一主 SKU（内容不同）：后导入的会覆盖先导入的。
    assert "function localImportSameSkuHint(groups)" in script
    assert "后导入的会覆盖先导入的那个" in script


def test_index_ships_the_duplicate_guard_assets() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "local-import-duplicate-guard-v1" in html
