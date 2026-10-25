from services.api.store_size_chart_templates import list_size_chart_templates, replace_size_chart_catalog


def test_size_chart_catalog_is_store_scoped_and_preserves_valid_imports() -> None:
    data = {"store_size_chart_templates": []}

    items = replace_size_chart_catalog(
        data,
        store_id="store-ph",
        raw_items=[
            {"template_id": "chart-1", "name": "男装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-1.png", "asset_id": "asset-1", "file_path": "C:/charts/asset-1.png"},
            {"template_id": "chart-2", "name": "童装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-2.png", "asset_id": "asset-2", "file_path": "C:/charts/asset-2.png"},
        ],
        now="2026-07-22T00:00:00Z",
        catalog_loaded=True,
    )

    assert [item["template_id"] for item in items] == ["chart-1", "chart-2"]
    assert [item["asset_id"] for item in list_size_chart_templates(data, "store-ph")] == ["asset-1", "asset-2"]
    assert list_size_chart_templates(data, "other-store") == []
