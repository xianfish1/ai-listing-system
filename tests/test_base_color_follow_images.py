"""商品维护「颜色图顺序跟随产品图顺序」开关（2026-10-10）。

开关本身只影响前端排序交互，颜色顺序最终还是落到 ``recognized_colors``，所以这里
钉的是三件事：

1. 前端必须把开关状态按商品存下来（``color_order_follows_images``），旧商品缺字段
   时读取端默认「打开」；
2. 新导入的基础商品默认打开联动；
3. 前后端契约（HTML 控件、app.js 双向同步函数、样式、静态资源版本）齐全——改完
   app.js/styles.css 不 bump ``?v=``，旧标签页会继续跑旧代码。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import request

import pytest

from services.api import settings, storage
from tests.test_api_boundaries import _start_api


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _call(method: str, url: str, payload: dict | None = None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode())


def _make_base(tmp_path: Path, folder_name: str = "AL0602 Color Follow Tee") -> dict:
    folder = tmp_path / folder_name
    folder.mkdir()
    for name in ["主图1.jpg", "White.jpg", "Black.jpg"]:
        (folder / name).write_bytes(b"image")
    return storage.create_base_product_from_folder({"root_path": str(folder), "category": "POD Mens T-Shirt"})


def test_fresh_import_opens_with_the_link_switched_on(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    assert base["color_order_follows_images"] is True


def test_switch_is_saved_per_product(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    off = storage.update_base_product(base["id"], {"color_order_follows_images": False}, mark_edited=True)
    assert off["color_order_follows_images"] is False

    on = storage.update_base_product(base["id"], {"color_order_follows_images": True}, mark_edited=True)
    assert on["color_order_follows_images"] is True


def test_switch_accepts_string_values_from_older_clients(tmp_path: Path) -> None:
    base = _make_base(tmp_path)

    assert storage.update_base_product(base["id"], {"color_order_follows_images": "0"})["color_order_follows_images"] is False
    assert storage.update_base_product(base["id"], {"color_order_follows_images": "off"})["color_order_follows_images"] is False
    assert storage.update_base_product(base["id"], {"color_order_follows_images": "true"})["color_order_follows_images"] is True


def test_missing_switch_value_keeps_the_link_on() -> None:
    """旧商品 / 旧客户端没有这个字段：不能把它当成 False。"""

    assert storage._normalize_flag(None) is True
    assert storage._normalize_flag("") is True
    assert storage._normalize_flag(False) is False


def test_switch_does_not_rewrite_color_membership(tmp_path: Path) -> None:
    """开关只管排序交互：颜色名单本身按操作员保存的内容原样落库。"""

    base = _make_base(tmp_path)
    storage.update_base_product(base["id"], {"recognized_colors": ["White", "Black"]})

    updated = storage.update_base_product(
        base["id"],
        {"color_order_follows_images": False, "recognized_colors": ["White", "Black"]},
        mark_edited=True,
    )

    assert updated["recognized_colors"] == ["White", "Black"]
    assert updated["color_order_follows_images"] is False


def test_switch_alone_does_not_mark_the_product_edited(tmp_path: Path) -> None:
    """开关只是界面偏好：单为它保存一次，不该把商品从「已完成」打回「未复制」。"""

    base = _make_base(tmp_path)
    storage.update_base_product(base["id"], {"listing_title": "Tee"}, mark_edited=True)
    edited_at = {item["id"]: item for item in storage.list_base_products_with_summary()}[base["id"]]["last_edited_at"]
    assert edited_at

    storage.update_base_product(base["id"], {"color_order_follows_images": False}, mark_edited=True)

    product = {item["id"]: item for item in storage.list_base_products_with_summary()}[base["id"]]
    assert product["last_edited_at"] == edited_at
    assert product["color_order_follows_images"] is False


def test_real_content_change_still_marks_the_product_edited(tmp_path: Path) -> None:
    """同一份载荷里既有开关又有真实内容改动时，编辑标记照旧。"""

    base = _make_base(tmp_path)

    storage.update_base_product(
        base["id"],
        {"color_order_follows_images": False, "listing_title": "新标题"},
        mark_edited=True,
    )

    product = {item["id"]: item for item in storage.list_base_products_with_summary()}[base["id"]]
    assert product["last_edited_at"]
    assert product["lifecycle_stage"] == "uncopied"


def test_put_endpoint_round_trips_the_switch(tmp_path: Path) -> None:
    base = _make_base(tmp_path)
    server, base_url = _start_api()
    try:
        status, payload = _call("PUT", f"{base_url}/api/base-products/{base['id']}", {"color_order_follows_images": False})
        assert status == 200
        assert payload["color_order_follows_images"] is False
    finally:
        server.shutdown()


def test_html_carries_the_switch_next_to_the_color_images() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="base-color-follow-images"' in html
    assert 'id="base-color-follow-hint"' in html
    assert "maintenance-follow-toggle" in html
    assert "颜色跟随产品图顺序" in html
    # 开关必须排在「添加颜色」之前、并和颜色图区在同一个 section 里。
    colors_section = html.split('id="maintenance-section-colors"', 1)[1].split("</section>", 1)[0]
    assert 'id="base-color-follow-images"' in colors_section
    assert colors_section.index("base-color-follow-images") < colors_section.index("add-base-color-btn")
    # 静态资源版本两处都要带上这个 feature（styles.css + app.js）。
    assert html.count("base-color-follow-images-v1") == 2


def test_app_js_syncs_both_directions() -> None:
    script = APP.read_text(encoding="utf-8")

    for name in (
        "function baseColorFollowEnabled(",
        "function baseColorImageSlots(",
        "function syncBaseColorsToImageOrder(",
        "function syncBaseImageOrderToColors(",
        "function applyBaseColorFollowFromImages(",
        "function applyBaseColorFollowFromColors(",
        "function renderBaseColorFollowToggle(",
    ):
        assert name in script
    # 拖动产品图 → 收颜色顺序；拖动颜色 → 挪颜色图再收顺序。
    assert "applyBaseColorFollowFromImages();" in script
    assert "applyBaseColorFollowFromColors();" in script
    # 开关随商品保存。
    assert "color_order_follows_images: baseColorFollowEnabled()" in script
    assert "state.editingBaseColorOrderFollowsImages = target.checked;" in script
    # 旧商品缺字段时默认打开：不能写成 `=== true`。
    assert "product.color_order_follows_images !== false" in script


def test_follow_switch_styles_exist() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    assert ".maintenance-section-head .maintenance-follow-toggle" in styles
    assert ".base-color-follow-hint" in styles
    assert ".variant-image-map.is-follow-images .variant-image-row" in styles


def test_quick_color_pick_present_and_wired() -> None:
    """颜色图「常用颜色」快捷添加（2026-10-25）：点一下常用色块即以该颜色名新增一行。

    颜色名与 AI 识别的 9 个固定色同源（Black/Apricot/Blue/Red/Green/Brown/Pink/
    Purple/White）；已添加的置灰防重复；HTML/app.js/styles 三处契约齐全并 bump 缓存版本。
    """
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 容器在颜色图区、紧挨「添加颜色」。
    colors_section = html.split('id="maintenance-section-colors"', 1)[1].split("</section>", 1)[0]
    assert 'id="base-color-quick-pick" class="color-quick-pick"' in colors_section

    # 9 个固定色与对应色块。
    assert "const BASE_QUICK_COLORS = [" in script
    for name in ("Black", "Apricot", "Blue", "Red", "Green", "Brown", "Pink", "Purple", "White"):
        assert f'{{ name: "{name}", swatch:' in script, f"缺少常用颜色 {name}"

    # 渲染、按名新增、防重复（大小写不敏感）、点击接线、随颜色行增删刷新。
    assert "function renderBaseColorQuickPick()" in script
    assert "function addBaseColorWithName(" in script
    assert 'data-quick-color' in script
    assert "renderBaseColorQuickPick();" in script
    quick_fn = script[script.index("function renderBaseColorQuickPick()"):script.index("function addBaseColorWithName(")]
    assert "disabled" in quick_fn, "已添加的颜色要置灰"
    add_fn = script[script.index("function addBaseColorWithName("):script.index("function syncBaseColorInputs(")]
    assert "toLowerCase()" in add_fn, "防重复比对要大小写不敏感"

    # 样式：色块 chip + 颜色圆点 + 置灰态。
    assert ".color-quick-chip" in styles
    assert ".color-quick-dot" in styles
    assert ".color-quick-chip:disabled" in styles

    # 静态资源版本：app.js 与 styles.css 的缓存参数都带上这个 feature。
    assert "app.js?v=color-quick-pick-v1" in html
    assert "styles.css?v=color-quick-pick-v1" in html
    assert "feature=color-quick-pick-v1" in html
