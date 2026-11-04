"""Shopee 标题工作台模板编辑器的静态契约。

2026-10-27 修：多行字段（风格词/搭配词/排列方式/删词顺序/附加规则）的
``<textarea>`` 模板里多了一个引号，导致保存后每组列表的最后一行被追加 ``">``
（男装/童装规则都中招）。这里守住「文本框内容 = 数据」以及版本号。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
WORKBENCH = ROOT / "apps" / "web" / "title-workbench.js"


def test_template_textareas_do_not_capture_a_stray_quote() -> None:
    script = WORKBENCH.read_text(encoding="utf-8")

    assert '}"></textarea>' not in script
    for field in ("style_words", "companion_words", "patterns", "trim_order", "rules"):
        assert f'${{esc((tpl.{field} || []).join("\\n"))}}</textarea>' in script


def test_workbench_script_version_is_bumped() -> None:
    import re

    html = INDEX.read_text(encoding="utf-8")

    # 不钉具体版本号：并行改动会继续 bump 缓存参数，只要带 v= 数字即可。
    assert re.search(r"/title-workbench\.js\?v=\d+", html)


def test_audience_options_are_boys_girls_and_men() -> None:
    script = WORKBENCH.read_text(encoding="utf-8")

    assert 'const AUDIENCE_OPTIONS = ["boys", "girls", "men"];' in script
    assert 'boys: "男童", girls: "女童", men: "男装"' in script
    # 旧值只用于显示历史数据，不能再回到规则的人员下拉里。
    assert '"kids", "men", "women", "adult"' not in script


def test_base_product_modal_has_ai_title_suggestions() -> None:
    html = INDEX.read_text(encoding="utf-8")

    assert 'id="base-maintenance-audience"' in html
    assert 'id="base-maintenance-ai-title"' in html
    assert 'id="base-maintenance-title-suggestions"' in html
