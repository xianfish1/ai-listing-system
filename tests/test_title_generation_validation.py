import json
from types import SimpleNamespace

from services.api import vision_listing, title_selection
from tests.test_vision_listing import TINY_PNG


class Gateway:
    def __init__(self, replies):
        self.replies = replies
        self.requests = []

    def generate(self, connection, request):
        self.requests.append(request)
        return SimpleNamespace(text=json.dumps(self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]), model_id="test")


def generate(tmp_path, replies, **kw):
    image = tmp_path / "main.png"
    image.write_bytes(TINY_PNG)
    gateway = Gateway(replies)
    ctx = title_selection.build_context({"title_category": "tshirt", "target_audience": "men"}, [], [], lambda _: [])
    result = vision_listing.generate_listing_from_images(gateway=gateway, connection={"id": "c"}, base_product={"main_image": image.name}, assets=[{"file_path": str(image), "file_name": image.name, "asset_type": "main"}], want_description=False, title_skill=ctx["skill"], title_context=ctx, **kw)
    return gateway, result


def test_overlong_title_retries_and_uses_complete_corrected_title(tmp_path):
    gateway, result = generate(tmp_path, [{"title": "Graphic " * 25}, {"title": "Graphic T-Shirt for Men"}])
    assert result.ok and result.title == "Graphic T-Shirt for Men"
    assert len(gateway.requests) == 2
    assert "validation" in gateway.requests[-1].user


def test_failed_correction_returns_no_title_and_audit(tmp_path):
    gateway, result = generate(tmp_path, [{"title": "Graphic " * 25}])
    assert not result.ok and result.title == ""
    assert result.title_candidates[0]["status"] == "BLOCKED"
    assert len(gateway.requests) == 2


def test_preview_three_candidates_validated_without_fabricating_alternatives(tmp_path):
    gateway, result = generate(tmp_path, [{"title": "Graphic T-Shirt for Men", "alternatives": ["Men Graphic T-Shirt", "T-Shirt for Men Graphic"]}], candidate_count=3)
    assert result.ok and len(result.title_candidates) == 3
    assert len(gateway.requests) == 1
    assert all(t["status"] != "BLOCKED" for t in result.title_candidates)


def test_material_claim_retries_instead_of_silently_accepting(tmp_path):
    gateway, result = generate(tmp_path, [{"title": "100% Cotton T-Shirt for Men"}, {"title": "T-Shirt for Men"}])
    assert result.ok and "Cotton" not in result.title
    assert len(gateway.requests) == 2


def test_spiderman_title_for_boys_is_accepted_without_retry(tmp_path):
    image = tmp_path / "main.png"
    image.write_bytes(TINY_PNG)
    gateway = Gateway([{"title": "Cartoon damit Spider-Man t shirt for kids boy short-sleeve printed"}])
    ctx = title_selection.build_context({"title_category": "tshirt", "target_audience": "boy"}, [], [], lambda _: [])
    result = vision_listing.generate_listing_from_images(
        gateway=gateway,
        connection={"id": "c"},
        base_product={"main_image": image.name},
        assets=[{"file_path": str(image), "file_name": image.name, "asset_type": "main"}],
        want_description=False,
        title_skill=ctx["skill"],
        title_context=ctx,
    )
    assert result.ok and "Spider-Man" in result.title
    assert len(gateway.requests) == 1


def detect(tmp_path, reply, **kw):
    image = tmp_path / "main.png"
    image.write_bytes(TINY_PNG)
    gateway = Gateway([reply])
    audience, error = vision_listing.detect_product_audience(
        gateway=gateway,
        connection={"id": "c"},
        base_product={"main_image": image.name},
        assets=[{"file_path": str(image), "file_name": image.name, "asset_type": "main"}],
        **kw,
    )
    return gateway, audience, error


def test_detect_product_audience_normalizes_model_answer(tmp_path):
    gateway, audience, error = detect(tmp_path, {"audience": "Girl", "reason": "girls cut"})
    assert (audience, error) == ("girls", "")
    assert len(gateway.requests) == 1
    assert gateway.requests[0].images


def test_detect_product_audience_returns_no_answer_for_unknown(tmp_path):
    _, audience, error = detect(tmp_path, {"audience": "unknown", "reason": "flat lay, unisex"})
    assert audience == ""
    assert "人群" in error


def test_detect_product_audience_never_raises_on_bad_reply(tmp_path):
    gateway = Gateway([{"audience": "girls"}])
    gateway.generate = lambda connection, request: (_ for _ in ()).throw(RuntimeError("boom"))
    image = tmp_path / "main.png"
    image.write_bytes(TINY_PNG)
    audience, error = vision_listing.detect_product_audience(
        gateway=gateway,
        connection={"id": "c"},
        base_product={"main_image": image.name},
        assets=[{"file_path": str(image), "file_name": image.name, "asset_type": "main"}],
    )
    assert audience == "" and "boom" in error
