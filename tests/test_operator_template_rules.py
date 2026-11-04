from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib import error, request

import pytest
from cryptography.fernet import Fernet

from services.api import main, settings, storage, title_selection, title_skill_presets, vision_listing
from tests.test_api_boundaries import _start_api
from tests.test_vision_listing import _StubGateway, _write_image


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")
    monkeypatch.setattr(settings, "AI_CREDENTIALS_MASTER_KEY", Fernet.generate_key().decode("ascii"))


def _call(method: str, url: str, payload: dict | None = None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=raw, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _preset_rule_payload(**overrides: object) -> dict:
    preset = title_skill_presets.list_presets()[0]
    return {**preset["rule"], **overrides}


def _men_product() -> dict:
    return {
        "id": "bp-men",
        "recognized_attributes": {"Item Type": "T-Shirts", "Gender": "Men"},
        "marketplace_category_path": "Men's Clothing > Tops",
    }


class TestOperatorTemplateStorage:
    def test_roundtrip_reload_and_update(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        assert skill["generation_mode"] == "operator_template"
        template = skill["operator_template"]
        assert template["core_term"] == "tshirt for men"
        assert template["style_words"] == ["Anime", "Cartoon", "Retro", "Streetwear"]
        assert template["trim_order"] == ["Ootd", "100% Cotton", "Streetwear"]
        assert template["patterns"][0] == "{theme} {character} {core}"
        assert skill["fixed_terms"] == ["tshirt for men"]
        assert skill["operator_allowances"] == ["100% Cotton"]

        reloaded = storage.get_title_skill(skill["id"])
        assert reloaded["operator_template"] == template  # 保存后能完整重新加载

        updated = storage.update_title_skill(
            skill["id"], {"operator_template": {**template, "core_term": "graphic tee for men"}}
        )
        assert updated["operator_template"]["core_term"] == "graphic tee for men"
        assert updated["version"] == skill["version"] + 1

    def test_old_rules_default_to_standard(self) -> None:
        skill = storage.create_title_skill({"name": "旧规则"})
        assert skill["generation_mode"] == "standard"
        assert skill["operator_template"] is None

    def test_switching_back_to_standard_drops_template(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        updated = storage.update_title_skill(skill["id"], {"generation_mode": "standard"})
        assert updated["generation_mode"] == "standard"
        assert updated["operator_template"] is None

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"generation_mode": "operator_template", "operator_template": None}, "必须提供模板配置"),
            ({"operator_template": {"core_term": " "}}, "核心词"),
            ({"operator_template": {"core_term": "tee", "patterns": []}}, "排列方式"),
            ({"operator_template": {"core_term": "tee", "patterns": ["a"], "style_words": "Anime"}}, "文本列表"),
            ({"generation_mode": "magic"}, "生成模式"),
        ],
    )
    def test_invalid_template_configs_are_rejected(self, changes: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            storage.create_title_skill(_preset_rule_payload(**changes))


class TestOperatorTemplatePrompt:
    def _context(self, product: dict, skills: list[dict]) -> dict:
        return title_selection.build_context(product, skills, [], lambda batch_id: [])

    def test_template_section_enters_prompt(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        context = self._context(_men_product(), [skill])
        prompt = title_selection.prompt_context(context)
        assert "OPERATOR TEMPLATE MODE" in prompt
        assert "tshirt for men" in prompt
        assert "Anime, Cartoon, Retro, Streetwear" in prompt
        assert "Print, Short Sleeve, Ootd" in prompt
        assert "Ootd, 100% Cotton, Streetwear" in prompt
        assert "NEVER cut a word in half" in prompt
        assert "NOT confirmed product facts" in prompt  # 模板风格与商品事实的边界
        assert "Faux Embroidery" in prompt
        assert '"generation_mode": "operator_template"' in prompt
        # 主题名+角色名由 AI 从商品图识别，识别不出不得编造 IP 名。
        assert "{theme}=the theme/franchise/motif name" in prompt
        assert "{character}=the character or role name" in prompt
        assert "Identify {theme} and {character} from the product photos" in prompt
        assert "NEVER invent or guess a franchise or character name" in prompt

    def test_standard_rule_prompt_has_no_template_section(self) -> None:
        skill = storage.create_title_skill({"name": "标准规则"})
        context = self._context({"id": "p"}, [skill])
        assert "OPERATOR TEMPLATE MODE" not in title_selection.prompt_context(context)

    def test_template_actually_reaches_vision_request(self, tmp_path: Path) -> None:
        """模型桩捕获请求：模板规则确实进入视觉生成的系统提示（端到端）。"""
        skill = storage.create_title_skill(_preset_rule_payload())
        context = self._context(_men_product(), [skill])
        image = _write_image(tmp_path)
        gateway = _StubGateway('{"title": "Naruto Sasuke tshirt for men", "description": "d"}')
        result = vision_listing.generate_listing_from_images(
            gateway=gateway,
            connection={"id": "c"},
            base_product={"main_image": image.name, "recognized_attributes": _men_product()["recognized_attributes"]},
            assets=[{"file_name": image.name, "file_path": str(image), "asset_type": "main"}],
            want_title=True,
            want_description=False,
            title_skill=context["skill"],
            title_context=context,
        )
        assert result.ok
        assert result.title == "Naruto Sasuke tshirt for men"  # 主题名+角色名+核心词
        assert gateway.last_request is not None
        assert "OPERATOR TEMPLATE MODE" in gateway.last_request.system
        assert "tshirt for men" in gateway.last_request.system
        assert "Identify {theme} and {character}" in gateway.last_request.system

    def test_men_template_isolated_from_kids_but_used_for_unknown_scope(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        kids = {"id": "bp-kids", "recognized_attributes": {"Gender": "Girls"}}
        # 已知是童装 → 绝不套男装模板
        assert title_selection.resolve_skill(kids, [skill])["id"] == "builtin"
        # 范围未确认（导入瞬间没有属性）→ 用启用中的默认规则兜底
        unknown = {"id": "bp-unknown"}
        assert title_selection.resolve_skill(unknown, [skill])["id"] == skill["id"]
        assert title_selection.resolve_skill(_men_product(), [skill])["id"] == skill["id"]
        # 明确指定但不匹配范围 → 报错，而不是悄悄回退
        with pytest.raises(ValueError, match="不匹配"):
            title_selection.resolve_skill(kids, [skill], {"skill_id": skill["id"]})

    def test_cotton_allowance_is_warning_not_error(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        context = self._context(_men_product(), [skill])
        audit = title_selection.validate_title(
            "Anime Graphic Print Tshirt for Men Short Sleeve Ootd 100% Cotton", context
        )
        assert audit["errors"] == []
        assert any("100% Cotton" in text and "待核对" in text for text in audit["warnings"])

    def test_missing_fixed_term_blocks_title(self) -> None:
        skill = storage.create_title_skill(_preset_rule_payload())
        context = self._context(_men_product(), [skill])
        audit = title_selection.validate_title("Cool Graphic Tee for Guys Daily Wear", context)
        assert any("固定词" in text for text in audit["errors"])


class TestOperatorTemplateApi:
    def test_preset_endpoint_returns_readonly_snapshot(self) -> None:
        server, base = _start_api()
        try:
            status, data = _call("GET", base + "/api/title-skill-presets")
            assert status == 200
            preset = data["items"][0]
            assert preset["id"] == "ph-men-print-tshirt"
            assert preset["version"] == 2
            assert preset["scope"] == {"market": "PH", "category": "tshirt", "audience": "men"}
            rule = preset["rule"]
            assert rule["generation_mode"] == "operator_template"
            assert rule["operator_template"]["core_term"] == "tshirt for men"
            assert rule["operator_template"]["patterns"][0] == "{theme} {character} {core}"
            assert rule["operator_allowances"] == ["100% Cotton"]
            assert "tshirt for men" in rule["examples"]
            status, _ = _call("POST", base + "/api/title-skill-presets", {})
            assert status == 404  # 预设只读，创建仍走 /api/title-skills
        finally:
            server.shutdown()
            server.server_close()

    def test_create_from_preset_over_http_and_preview_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server, base = _start_api()
        try:
            with storage.locked_store() as (data, save):
                data.setdefault("base_products", []).append({
                    "id": "bp-1", "group_id": "G1", "main_sku": "AL0001", "base_name": "男装印花 T 恤",
                    "recognized_attributes": {"Item Type": "T-Shirts", "Gender": "Men"},
                    "updated_at": "2026-10-25T00:00:00+00:00",
                })
                save(data)
            status, preset_list = _call("GET", base + "/api/title-skill-presets")
            assert status == 200
            status, created = _call("POST", base + "/api/title-skills", preset_list["items"][0]["rule"])
            assert status == 201
            assert created["generation_mode"] == "operator_template"
            assert created["operator_template"]["core_term"] == "tshirt for men"

            monkeypatch.setattr(
                storage, "get_default_ai_model_connection_private",
                lambda: {"id": "conn", "protocol": "anthropic_messages", "model_id": "test"},
            )
            monkeypatch.setattr(
                vision_listing, "generate_listing_from_images",
                lambda **kwargs: SimpleNamespace(
                    title_candidates=[
                        {"title": "Anime Graphic Print Tshirt for Men Short Sleeve Ootd 100% Cotton",
                         "chars": 63, "status": "REVIEW", "warnings": ["运营例外用词：100% Cotton（待核对）"],
                         "errors": [], "accepted_terms": []}
                    ],
                    error="", ok=True,
                ),
            )
            status, preview = _call("POST", base + "/api/title-workbench/preview", {"product_id": "bp-1"})
            assert status == 200
            assert preview["skill"]["generation_mode"] == "operator_template"
            assert preview["skill"]["operator_template"]["core_term"] == "tshirt for men"
            assert preview["context"]["scope"]["audience"] == "men"

            status, history = _call("GET", base + "/api/title-workbench/history?product_id=bp-1")
            assert status == 200
            record = history["items"][0]
            assert record["skill"]["generation_mode"] == "operator_template"
            assert record["skill"]["operator_template"]["trim_order"] == ["Ootd", "100% Cotton", "Streetwear"]
        finally:
            server.shutdown()
            server.server_close()


def _scoped_skill(skill_id: str, *, audience: str, is_default: bool = True, enabled: bool = True) -> dict:
    return {
        "id": skill_id,
        "name": f"{audience} 规则",
        "version": 1,
        "market": "PH",
        "category": "tshirt",
        "audience": audience,
        "library_id": "ph-keywords",
        "enabled": enabled,
        "is_default": is_default,
        "hot_word_policy": "reference",
        "max_age_days": 30,
        "title_min_chars": 40,
        "title_max_chars": 100,
        "structure": ["theme", "core", "attributes", "audience"],
        "generation_mode": "operator_template",
        "operator_template": {
            "core_term": f"tshirt for {audience}",
            "style_words": [],
            "companion_words": [],
            "patterns": [],
            "trim_order": [],
        },
    }


def _all_scope_batch() -> dict:
    return {
        "id": "b1",
        "library_id": "ph-keywords",
        "data_date": "2026-10-20",
        "uploaded_at": "2026-10-20",
        "enabled": True,
        "source": "export.xlsx",
        "source_type": "measured",
        "scope_categories": ["all"],
        "scope_audiences": ["all"],
    }


def _scope_rows() -> list[dict]:
    return [
        {"id": "1", "word": "tshirt for kids boy", "category": "all", "audience": "kids", "status": "enabled", "search_volume": 900},
        {"id": "2", "word": "tshirt", "category": "all", "audience": "all", "status": "enabled", "search_volume": 100},
        {"id": "3", "word": "tshirt for men", "category": "all", "audience": "men", "status": "enabled", "search_volume": 5000},
    ]


class TestTitleRuleFallback:
    """导入瞬间还没有属性：按启用中的默认规则（当前是童装）生成，而不是内置通用标题。"""

    def test_unknown_product_uses_default_rule_and_its_scope(self) -> None:
        context = title_selection.build_context(
            {"id": "bp-new"}, [_scoped_skill("kids-default", audience="kids")], [_all_scope_batch()], lambda _: _scope_rows()
        )
        assert context["skill"]["id"] == "kids-default"
        assert context["scope"] == {"market": "PH", "category": "tshirt", "audience": "kids"}
        assert [row["word"] for row in context["candidates"]] == ["tshirt for kids boy", "tshirt"]
        assert any("默认规则" in text for text in context["warnings"])

    def test_known_audience_never_borrows_another_audience_default(self) -> None:
        girls = {"id": "bp-kids", "recognized_attributes": {"Gender": "Girls"}}
        rules = [_scoped_skill("men-default", audience="men")]
        assert title_selection.resolve_skill(girls, rules)["id"] == "builtin"

    def test_disabled_or_non_default_rules_are_never_used(self) -> None:
        unknown = {"id": "bp-unknown"}
        assert title_selection.resolve_skill(unknown, [_scoped_skill("kids-off", audience="kids", enabled=False)])["id"] == "builtin"
        assert title_selection.resolve_skill(unknown, [_scoped_skill("kids-plain", audience="kids", is_default=False)])["id"] == "builtin"

    def test_kids_default_wins_when_both_audience_defaults_exist(self) -> None:
        # 男装规则即使更新得更晚，范围未确认的商品仍按「先默认童装」生成。
        men = _scoped_skill("men-default", audience="men")
        men["created_at"] = men["updated_at"] = "2099-01-01T00:00:00+00:00"
        kids = _scoped_skill("kids-default", audience="kids")
        kids["created_at"] = kids["updated_at"] = "2026-10-26T17:51:57+00:00"
        assert title_selection.resolve_skill({"id": "p"}, [men, kids])["id"] == "kids-default"

    def test_forced_skill_overrides_scope_for_title_regeneration(self) -> None:
        rules = [_scoped_skill("kids-default", audience="kids")]
        with pytest.raises(ValueError, match="不匹配"):
            title_selection.resolve_skill(_men_product(), rules, {"skill_id": "kids-default"})
        forced = title_selection.resolve_skill(_men_product(), rules, {"skill_id": "kids-default", "force_skill": True})
        assert forced["id"] == "kids-default"
        context = title_selection.build_context(
            _men_product(),
            rules,
            [_all_scope_batch()],
            lambda _: _scope_rows(),
            overrides={"skill_id": "kids-default", "force_skill": True},
        )
        assert context["scope"]["audience"] == "kids"
        assert "tshirt for men" not in [row["word"] for row in context["candidates"]]
        assert any("指定规则" in text for text in context["warnings"])
