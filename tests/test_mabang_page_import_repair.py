from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from services.api.mabang_import_snapshot import normalize_mabang_capture
from services.api.mabang_page_import_repair import MabangPageImportRepairError, repair_import_pollution


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mabang_page_capture_pod_ph.json"
ROOT = Path(__file__).resolve().parents[1]
POLLUTED_FIELDS = ["尺码图表:上传图片选择模板", "变种属性:"]


def _trusted_snapshot() -> dict:
    return normalize_mabang_capture(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _polluted_store() -> dict:
    fields = [
        {"field_id": "100134", "label": "材料", "required": True, "type": "multi_select", "options": []},
        *[{"field_id": field_id, "label": field_id, "required": True, "type": "text", "options": []} for field_id in POLLUTED_FIELDS],
    ]
    return {
        "mabang_store_options": [],
        "category_profiles": [{"id": "shopee-ph-100244", "platform": "shopee", "country": "PH", "marketplace_category_id": "100244", "active_version_id": "bad-v", "published_version_id": "bad-v"}],
        "category_profile_versions": [{"id": "bad-v", "profile_id": "shopee-ph-100244", "version": 1, "status": "active", "fields": fields}],
        "store_promotion_activities": [{"activity_id": "other", "store_id": "other-store"}],
        "store_templates": [
            {"id": "target", "mabang_store_id": "2022625115", "mabang_category_id": "100244", "attribute_values": {"100134": ["1149"], **{field: "bad" for field in POLLUTED_FIELDS}}, "sizes": [], "version": 1},
            {"id": "other", "mabang_store_id": "other-store", "mabang_category_id": "other-category", "attribute_values": {"keep": "value"}, "sizes": ["M"], "version": 1},
        ],
        "store_template_versions": [],
    }


def test_repair_only_touches_target_store_and_category() -> None:
    original = _polluted_store()

    repaired, report = repair_import_pollution(
        original,
        store_id="2022625115",
        category_id="100244",
        snapshot=_trusted_snapshot(),
    )

    assert report["removed_field_ids"] == POLLUTED_FIELDS
    assert repaired["store_templates"][0]["sizes"] == ["S", "M", "L", "XL", "2XL"]
    assert repaired["store_templates"][0]["attribute_values"] == {
        "100134": ["1149"], "100162": ["1486"], "100168": "1500", "101004": "5487"
    }
    assert repaired["store_templates"][1] == original["store_templates"][1]
    assert original == _polluted_store()
    assert repair_import_pollution(repaired, store_id="2022625115", category_id="100244", snapshot=_trusted_snapshot())[1]["changed"] is False


def test_repair_rejects_missing_or_mismatched_snapshot_without_mutating_data() -> None:
    data = _polluted_store()
    before = deepcopy(data)
    wrong = _trusted_snapshot()
    wrong["store"]["id"] = "other-store"

    with pytest.raises(MabangPageImportRepairError, match="可信快照"):
        repair_import_pollution(data, store_id="2022625115", category_id="100244", snapshot={})
    with pytest.raises(MabangPageImportRepairError, match="不匹配"):
        repair_import_pollution(data, store_id="2022625115", category_id="100244", snapshot=wrong)

    assert data == before


def test_repair_script_requires_a_snapshot_run_and_supports_dry_run() -> None:
    script = ROOT / "scripts" / "repair_mabang_page_import_pollution.py"
    source = script.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--snapshot-run-id" in source
    assert "--dry-run" in source
    assert "--apply" in source
    assert "locked_store" in source
