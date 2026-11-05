"""Static UI contract: 切换页面时商品勾选要自动取消。

勾选（多选 + 当前单选卡片）属于「商品维护」这一页：离开时清掉，回来时不会带着
上一次的勾选去点「批量删除 / 重新识别」。复位只改已渲染的勾选框，不整表重画。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"


def test_switching_view_clears_base_product_selection() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "function clearBaseProductSelection()" in script
    # showView 里离开「商品维护」就清（和提示条同一条规则）。
    assert 'if (id !== "product-maintenance") clearBaseProductSelection();' in script
    assert "state.selectedBaseProducts.clear();" in script
    assert 'state.selectedBaseProductId = "";' in script
    # 直接复位已渲染的勾选框/卡片高亮，不为了清勾选重画整张列表。
    assert 'document.querySelectorAll("[data-base-product-select]")' in script
    assert 'classList.remove("checked", "selected")' in script
    assert "renderBaseProductBatchBar();" in script


def test_switching_filter_clears_base_product_selection() -> None:
    script = APP.read_text(encoding="utf-8")

    # 换分类 / 换时间窗 = 换一批可见商品，勾选一律重来：
    # 否则「已选 N 个」里会挂着当前列表根本看不到的商品，批量删除/重新识别会误伤。
    assert "function clearBaseProductSelectionForFilter()" in script
    assert script.count("clearBaseProductSelectionForFilter();") >= 2  # 时间 + 分类两个入口
    time_fn = script[script.index("function setBaseTimeFilter("):]
    time_fn = time_fn[:time_fn.index("\n}")]
    assert "clearBaseProductSelectionForFilter();" in time_fn
    category_fn = script[script.index("function toggleBaseCategoryFilter("):]
    category_fn = category_fn[:category_fn.index("\n}")]
    assert "clearBaseProductSelectionForFilter();" in category_fn


def test_copying_to_templates_clears_the_selection() -> None:
    script = APP.read_text(encoding="utf-8")

    copy_fn = script[script.index("async function copySelectedBaseProductToTemplates("):]
    copy_fn = copy_fn[:copy_fn.index("\n  await Promise.all(")]
    assert '"/api/store-listing-copies/from-base"' in copy_fn
    # 复制成功就清勾选（不再依赖「跳去刊登副本页」顺手清）。
    assert "clearBaseProductSelection();" in copy_fn


def test_index_ships_the_selection_clear_asset() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "base-selection-clear-on-nav-v1" in html
    assert html.count("base-selection-clear-on-filter-v1") == 1  # 本轮只改 app.js
