from __future__ import annotations

from services.api.category_profile_service import create_candidate_from_capture


def _capture(fields: list[dict]) -> dict:
    return {
        "platform": "shopee",
        "country": "PH",
        "store_id": "store-1",
        "mabang_store_id": "store-1",
        "marketplace_category_id": "100244",
        "marketplace_category_path": "Men Clothes > Tops > T-Shirts",
        "page_url": "https://publish.mabangerp.com/publish-ui/",
        "fields": fields,
        "variation": {"type": "variant", "axes": ["color", "size"]},
    }


def _populated_field() -> dict:
    return {
        "field_id": "100134",
        "label": "材料",
        "required": True,
        "input_kind": 2,
        "automation": {"page_field_id": "100134", "label": "材料", "strategy": "mabang_category_attribute"},
        "options": [{"value": "1149", "label": "棉花 (Cotton)"}],
    }


def test_empty_capture_reuses_published_version_instead_of_superseding() -> None:
    """空采集（页面没渲染属性）不能发布 0 字段版本顶掉已发布规则：
    2026-09-26 现场两次空确认把 100244 的 16 字段 v18 顶成空 v19/v20，
    副本属性阶段无字段可填，马帮报「2 必填项未填」保存被拦。"""
    data = {"category_profiles": [], "category_profile_versions": []}

    first = create_candidate_from_capture(data, run_id="run-1", raw_capture=_capture([_populated_field()]))
    assert first["version"]["status"] == "active"
    assert len(first["version"]["fields"]) == 1
    assert data["category_profiles"][0]["active_version_id"] == first["version"]["id"]

    again = create_candidate_from_capture(data, run_id="run-2", raw_capture=_capture([]))
    assert again.get("reused") is True
    assert again["version"]["id"] == first["version"]["id"]
    # 没有发布新版本，也没有把现役版本降级
    assert len(data["category_profile_versions"]) == 1
    assert data["category_profile_versions"][0]["status"] == "active"
    assert data["category_profiles"][0]["active_version_id"] == first["version"]["id"]


def test_empty_capture_without_existing_version_still_publishes() -> None:
    """首采就是空表单时维持旧行为（发布空版本），避免没有任何可绑方案。"""
    data = {"category_profiles": [], "category_profile_versions": []}
    result = create_candidate_from_capture(data, run_id="run-1", raw_capture=_capture([]))
    assert result.get("reused") is None
    assert len(data["category_profile_versions"]) == 1
