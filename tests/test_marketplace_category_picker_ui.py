from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_store_template_uses_searchable_marketplace_category_picker():
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="store-template-category-search"' in html
    assert 'role="listbox"' in html
    assert 'id="store-template-category-breadcrumbs"' in html
    assert 'id="store-template-category-path"' in html
    assert 'id="collect-selected-category-profile-btn"' in html
    assert "/api/marketplace-categories?" in script
    assert "selectMarketplaceCategory" in script
    assert "/api/category-profiles/collection-runs" in script
    assert "marketplaceCategoryRequestId" in script
    assert "clearMarketplaceCategorySelection" in script
    assert "dataset.country" in script


def test_store_template_picker_supports_cascading_category_navigation():
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert "marketplaceCategoryPath" in script
    assert "loadMarketplaceCategoryLevel" in script
    assert 'parent_id: String(parentId || "0")' in script
    assert "openMarketplaceCategoryNode" in script
    assert "renderMarketplaceCategoryBreadcrumbs" in script
    assert "category.has_children" in script
    assert "没有匹配的类目" in script
    assert "invalidateMarketplaceCategoryRequests" in script
    assert "disableMarketplaceCategoryResults" in script
    assert "state.marketplaceCategoryPath = [...nextPath]" in script
    assert "loadMarketplaceCategoryLevel(category.category_id, nextPath)" in script
