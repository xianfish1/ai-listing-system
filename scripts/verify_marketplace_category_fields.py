from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["DATA_DIR"] = str(PROJECT_ROOT / "storage" / "tmp" / "verify-marketplace-category-data")
os.environ["STORAGE_BACKEND"] = "json"

from scripts.migrate_category_profiles import migrate_store
from services.api import storage
from services.api.category_profile_service import create_candidate_from_capture, publish_version
from services.api.category_rules import build_profile_snapshot, hash_profile_snapshot, validate_attribute_values


def main() -> None:
    original_store = storage.load_store()
    try:
        data = storage._empty_store()
        migrate_store(data, storage.CATEGORY_PROFILES, now="2026-07-12T00:00:00Z")

        kids_capture = {
            "platform": "shopee",
            "country": "PH",
            "marketplace_category_id": "900001",
            "marketplace_category_path": "Baby & Kids Fashion > Tops",
            "display_name": "Shopee PH Kids Tops",
            "page_url": "https://www.mabangerp.com/category/900001",
            "fields": [
                {
                    "field_id": "age_group",
                    "label": "Age Group",
                    "type": "single_select",
                    "required": True,
                    "options": [{"value": "kids", "label": "Kids"}],
                    "automation": {"page_field_id": "age_group"},
                },
                {
                    "field_id": "height",
                    "label": "Suitable Height",
                    "type": "number",
                    "required": False,
                    "required_when": {"field": "age_group", "op": "eq", "value": "kids"},
                    "options": [],
                    "automation": {"page_field_id": "height"},
                },
            ],
            "variation": {"required": True, "axes": ["size"]},
        }
        candidate = create_candidate_from_capture(
            data,
            run_id="verify-kids",
            raw_capture=kids_capture,
            now="2026-07-12T00:01:00Z",
        )
        kids_version = publish_version(
            data,
            candidate["version"]["id"],
            reviewer="verification",
            note="Fixture reviewed for verification",
            now="2026-07-12T00:02:00Z",
        )

        mens_profile = next(item for item in data["category_profiles"] if item["id"] == "pod-ph-mens-tee")
        mens_version = next(item for item in data["category_profile_versions"] if item["id"] == mens_profile["published_version_id"])
        mens_missing = validate_attribute_values(mens_version, {}).missing_field_ids
        kids_missing = validate_attribute_values(kids_version, {"age_group": "kids"}).missing_field_ids

        assert "material" in mens_missing
        assert mens_missing != kids_missing
        assert kids_missing == ["height"]

        snapshot = build_profile_snapshot(candidate["profile"], kids_version)
        assert hash_profile_snapshot(snapshot) == hash_profile_snapshot(dict(reversed(list(snapshot.items()))))
        print("marketplace_category_profile_rules_ok")
    finally:
        storage.save_store(original_store)


if __name__ == "__main__":
    main()
