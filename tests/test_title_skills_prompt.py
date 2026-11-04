from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from services.api.model_gateway import GenerationRequest
from services.api import vision_listing


# 1×1 red PNG — same trick as tests/test_vision_listing.py.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63f80fff00000101010045fdf6d40000000049454e"
    "44ae426082"
)


@dataclass
class _Generated:
    text: str
    model_id: str = "test-model"


class _StubGateway:
    def __init__(self, text: str) -> None:
        self.text = text
        self.last_request: GenerationRequest | None = None

    def generate(self, connection: Any, request: GenerationRequest) -> _Generated:
        self.last_request = request
        return _Generated(text=self.text)


def _write_image(tmp_path: Path, name: str = "main.png") -> Path:
    path = tmp_path / name
    path.write_bytes(TINY_PNG)
    return path


def _skill(**overrides: object) -> dict[str, Any]:
    skill = {
        "name": "Shopee 标题 · 主推款",
        "instructions": "标题开头放品类词\n避免堆砌无关修饰词",
        "title_min_chars": 30,
        "title_max_chars": 80,
        "hot_word_policy": "reference",
        "enabled": True,
        "is_default": True,
    }
    skill.update(overrides)
    return skill


def test_build_system_prompt_without_skill_returns_base_prompt() -> None:
    assert vision_listing.build_system_prompt() == vision_listing._SYSTEM_PROMPT
    # 热词在没配 Skill 时绝不注入——保持旧行为。
    assert vision_listing.build_system_prompt(None, ["hot word"]) == vision_listing._SYSTEM_PROMPT


def test_build_system_prompt_injects_rules_and_reference_hot_words() -> None:
    prompt = vision_listing.build_system_prompt(_skill(), ["oversized tee", "gift for mom"])
    assert prompt.startswith(vision_listing._SYSTEM_PROMPT)
    assert "Title length target: 30-80 characters." in prompt
    assert "- 标题开头放品类词" in prompt
    assert "- 避免堆砌无关修饰词" in prompt
    assert "Latest trending search keywords: oversized tee, gift for mom" in prompt
    assert "Weave in ONLY the ones that" in prompt
    assert "Prefer to include" not in prompt


def test_build_system_prompt_hot_word_policy_variants() -> None:
    prefer = vision_listing.build_system_prompt(_skill(hot_word_policy="prefer"), ["a", "b"])
    assert "Prefer to include as many of these as genuinely fit" in prefer

    off = vision_listing.build_system_prompt(_skill(hot_word_policy="off"), ["a", "b"])
    assert "Latest trending search keywords" not in off

    empty_words = vision_listing.build_system_prompt(_skill(), [])
    assert "Latest trending search keywords" not in empty_words


def test_generate_listing_applies_skill_prompt_and_rejects_uncorrected_long_title(tmp_path: Path) -> None:
    image = _write_image(tmp_path)
    assets = [{"file_name": image.name, "file_path": str(image), "asset_type": "main"}]
    product = {"main_image": image.name, "recognized_colors": [], "variant_count": 0}
    long_title = "Soft Cotton Men T-Shirt Short Sleeve Casual Summer Daily Wear Beach Holiday Style Top Blouse"
    gateway = _StubGateway('{"title": "' + long_title + '", "description": "d"}')

    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "c"},
        base_product=product,
        assets=assets,
        want_title=True,
        want_description=False,
        title_skill=_skill(title_max_chars=40),
        hot_words=["oversized tee"],
    )

    assert not result.ok
    assert result.title == ""
    assert result.title_candidates[0]["title"] == long_title
    assert gateway.last_request is not None
    assert "Title length target: 30-40 characters." in gateway.last_request.system
    assert "Latest trending search keywords: oversized tee" in gateway.last_request.system


def test_generate_listing_without_skill_keeps_base_prompt(tmp_path: Path) -> None:
    image = _write_image(tmp_path)
    assets = [{"file_name": image.name, "file_path": str(image), "asset_type": "main"}]
    product = {"main_image": image.name, "recognized_colors": [], "variant_count": 0}
    gateway = _StubGateway('{"title": "Tee", "description": "d"}')

    vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=True, want_description=False,
    )
    assert gateway.last_request is not None
    assert gateway.last_request.system == vision_listing._SYSTEM_PROMPT


def test_color_only_run_never_injects_title_skill(tmp_path: Path) -> None:
    variant = _write_image(tmp_path, "1 (1).png")
    assets = [{"file_name": variant.name, "file_path": str(variant), "asset_type": "variant"}]
    product = {"main_image": variant.name, "recognized_colors": [], "variant_count": 1}
    gateway = _StubGateway('{"title": "", "description": "", "color_translations": {}}')

    vision_listing.generate_listing_from_images(
        gateway=gateway, connection={"id": "c"}, base_product=product, assets=assets,
        want_title=False, want_description=False,
        title_skill=_skill(), hot_words=["ignored"],
    )
    assert gateway.last_request is not None
    assert gateway.last_request.system == vision_listing._SYSTEM_PROMPT
    assert "ignored" not in gateway.last_request.system
