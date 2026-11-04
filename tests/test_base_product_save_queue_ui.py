"""Static UI contract for the base-product maintenance save queue.

点「保存维护内容」不再同步等一串列表重载：表单快照入队，PUT 在后台串行跑；
保存成功后只更新本地那一条商品，绝不因为保存而把弹窗重新弹出来（操作员可能
已经在看别的商品了）。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"


def _save_region(script: str) -> str:
    start = script.index("// ===== 基础商品「保存维护内容」队列 =====")
    end = script.index("// AI 识别失败后的重试", start)
    return script[start:end]


def test_save_is_queued_with_a_form_snapshot() -> None:
    script = APP.read_text(encoding="utf-8")
    region = _save_region(script)

    assert "const baseProductSaveQueue = [];" in region
    assert "function collectBaseProductMaintenancePayload()" in region
    assert "async function drainBaseProductSaveQueue()" in region
    # 快照要脱离编辑态：入队后继续改表单不能影响已排队的写入。
    assert "JSON.parse(JSON.stringify(collectBaseProductMaintenancePayload()))" in region
    # 同一商品连点只保留最新快照。
    assert "baseProductSaveQueue[existing] = task;" in region
    assert "function saveBaseImageOrder()" in script


def test_save_no_longer_blocks_or_reopens_the_modal() -> None:
    script = APP.read_text(encoding="utf-8")
    region = _save_region(script)

    # 保存后不再整表重拉（副本 4.5MB + 自动化 2.9MB 正是点击后卡顿的来源）。
    assert "await Promise.all([loadBaseProducts(), loadStoreListingCopies(), loadAutomationJobs()]);" not in region
    assert "scheduleBaseProductRefresh(" in region
    # 保存成功不重开弹窗：只在弹窗仍停在这一条商品时才更新可见内容。
    assert "openBaseProductMaintenance(product.id)" not in region
    assert "if (state.editingBaseProductId === product.id) {" in region
    assert "renderBaseProducts();" in region


def test_save_merges_put_response_with_cached_list_fields() -> None:
    script = APP.read_text(encoding="utf-8")
    region = _save_region(script)
    # PUT 只回商品本体，副本数/生命周期/自动化图标来自列表接口，先沿用旧值。
    assert "const merged = before ? { ...before, ...product } : product;" in region
    # 后台只补拉商品列表；副本/自动化两张表各几 MB，保存后不再拉。
    assert "function scheduleBaseProductRefresh()" in region
    assert "loadBaseProducts().catch(() => {});" in region
    assert "loadStoreListingCopies()" not in region
    assert "loadAutomationJobs()" not in region


def test_index_ships_the_save_queue_assets() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "base-save-queue-v1" in html
