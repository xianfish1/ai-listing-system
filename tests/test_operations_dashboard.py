from __future__ import annotations

from copy import deepcopy

import pytest

from services.api import storage
from services.api.operations_dashboard import build_dashboard


NOW = "2026-07-19T12:00:00+00:00"


def empty_store() -> dict:
    return deepcopy(storage.default_store())


def with_needs_confirmation_job() -> dict:
    data = empty_store()
    data["automation_jobs"].append({"id": "job-1", "status": "needs_confirmation", "created_at": NOW})
    return data


def with_unchecked_listing_copy() -> dict:
    data = empty_store()
    data["store_listing_copies"].append({"id": "copy-1", "status": "draft", "updated_at": NOW})
    return data


def with_ready_job_and_offline_worker() -> dict:
    data = empty_store()
    data["automation_jobs"].append({"id": "job-1", "status": "ready", "created_at": NOW})
    return data


def with_base_product_only() -> dict:
    data = empty_store()
    data["base_products"].append({"id": "base-1", "created_at": NOW})
    return data


def test_dashboard_counts_only_persisted_real_records() -> None:
    data = empty_store()
    data["base_products"] = [{"id": "a"}, {"id": "b"}]
    data["store_templates"] = [{"id": "template"}]
    data["store_listing_copies"] = [{"id": "copy", "status": "checked"}]
    data["automation_jobs"] = [{"id": "job", "status": "failed", "created_at": NOW}]

    result = build_dashboard(data, now=NOW)

    assert result["metrics"]["base_products"] == 2
    assert result["metrics"]["failed_jobs"] == 1
    assert "sales" not in result["metrics"]
    assert "token_balance" not in result["metrics"]


def test_recent_results_do_not_expose_execution_snapshots_or_local_paths() -> None:
    data = empty_store()
    data["automation_jobs"] = [{
        "id": "job-1", "status": "failed", "store_name": "PH", "created_at": NOW,
        "last_error": "failed", "execution_snapshot": {"path": "C:/private/product.jpg"},
    }]

    result = build_dashboard(data, now=NOW)

    assert result["recent_results"] == [{
        "id": "job-1", "status": "failed", "store_name": "PH", "updated_at": NOW,
        "last_error": "failed", "warning_count": 0,
    }]
    assert "private" not in str(result)


@pytest.mark.parametrize(("state_factory", "action"), [
    (with_needs_confirmation_job, "open_automation_job"),
    (with_unchecked_listing_copy, "open_listing_copy"),
    (with_ready_job_and_offline_worker, "open_automation"),
    (with_base_product_only, "copy_base_product"),
    (empty_store, "create_base_product"),
])
def test_next_work_action_priority(state_factory, action: str) -> None:
    assert build_dashboard(state_factory(), now=NOW)["next_action"]["type"] == action


def test_dashboard_markup_keeps_operational_panels_inside_home_view() -> None:
    html = (storage.settings.WEB_DIR / "index.html").read_text(encoding="utf-8")
    home = html[html.index('<section id="dashboard"'):html.index('<section id="product-maintenance"')]

    for control in ["dashboard-current-batch", "dashboard-next-action", "dashboard-recent-results", "dashboard-connections"]:
        assert f'id="{control}"' in home


def test_dashboard_metrics_do_not_report_zero_before_initial_load() -> None:
    html = (storage.settings.WEB_DIR / "index.html").read_text(encoding="utf-8")
    home = html[html.index('<section id="dashboard"'):html.index('<section id="product-maintenance"')]

    for metric_id in ["metric-products", "metric-assets", "metric-exportable"]:
        assert f'id="{metric_id}" data-loading-metric="true"' in home
