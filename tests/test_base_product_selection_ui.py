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


def test_index_ships_the_selection_clear_asset() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert "base-selection-clear-on-nav-v1" in html
