"""颜色识别可以单独指定 AI 连接（未配置/连接已删除时回落默认）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.api import settings, storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(settings, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(settings, "STORE_FILE", tmp_path / "store.json")


def _add_connection(name: str, *, default: bool = False) -> dict:
    connection = storage.create_ai_model_connection({
        "name": name,
        "protocol": "openai_chat_completions",
        "base_url": f"https://{name}.example/v1",
        "model_id": f"{name}-model",
        "api_key": f"{name}-key",
    })
    if default:
        storage.set_default_ai_model_connection(connection["id"])
    return connection


def test_color_purpose_uses_its_own_connection() -> None:
    primary = _add_connection("primary", default=True)
    color = _add_connection("color-cheap")

    storage.save_runtime_configuration({"ai": {"color_connection_id": color["id"]}})

    assert storage.get_ai_connection_for_purpose("colors")["name"] == "color-cheap"
    # 默认用途不受影响：标题/描述还是走默认连接
    assert storage.get_ai_connection_for_purpose("default")["id"] == primary["id"]


def test_color_purpose_falls_back_to_default_when_unset() -> None:
    primary = _add_connection("primary", default=True)

    assert storage.get_ai_connection_for_purpose("colors")["id"] == primary["id"]
    assert storage.get_ai_connection_for_purpose()["id"] == primary["id"]


def test_color_purpose_falls_back_when_configured_connection_is_gone() -> None:
    primary = _add_connection("primary", default=True)
    color = _add_connection("color-cheap")
    storage.save_runtime_configuration({"ai": {"color_connection_id": color["id"]}})
    storage.delete_ai_model_connection(color["id"])

    # 配置里还留着已删除的 id：不能因此让颜色识别没有连接可用
    assert storage.get_ai_connection_for_purpose("colors")["id"] == primary["id"]


def test_runtime_configuration_exposes_color_connection_id() -> None:
    config = storage.get_runtime_configuration()

    assert "color_connection_id" in config["ai"]
    saved = storage.save_runtime_configuration({"ai": {"color_connection_id": "abc"}})
    assert saved["ai"]["color_connection_id"] == "abc"


def test_settings_ui_offers_color_connection_selector() -> None:
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="settings-ai-color-connection"' in html
    assert "颜色识别使用的 AI 连接" in html
    assert "跟随默认连接" in script
    assert 'color_connection_id: $("settings-ai-color-connection")?.value || ""' in script


def test_color_stage_uses_the_color_connection() -> None:
    """颜色识别阶段要拿"颜色专用连接"，而不是无脑复用标题/描述那个。"""

    source = (PROJECT_ROOT / "services" / "api" / "main.py").read_text(encoding="utf-8")
    start = source.index("        if color_targets:")
    block = source[start: source.index('errors.append(f"颜色识别失败:', start)]

    assert 'storage.get_ai_connection_for_purpose("colors")' in block
    assert "gateway=color_gateway" in block
    assert "connection=color_connection" in block
