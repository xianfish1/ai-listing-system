from __future__ import annotations

from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "apps" / "web"
INDEX = WEB / "index.html"
APP = WEB / "app.js"


def test_worker_panel_lists_one_row_per_account():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="settings-worker-accounts"' in html
    assert "settings-worker-accounts" in script
    assert "worker.workers" in script
    assert "data-worker-logs-account" in script
    assert "partial:" in script
    # 每个账号一条日志入口，按账号查日志
    assert "account_id" in script[script.index("async function refreshWorkerLogs"):script.index("const AUTO_LOGIN_ERROR_LABELS")]


def test_web_assets_cache_busting_includes_the_worker_feature():
    html = INDEX.read_text(encoding="utf-8")
    assert html.count("feature=worker-per-account-v1") == 2
