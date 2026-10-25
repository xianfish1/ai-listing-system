from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.main import ApiHandler


def start_api() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def get(base_url: str, path: str) -> dict:
    with request.urlopen(f"{base_url}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    server, base_url = start_api()
    try:
        plan = get(base_url, "/api/project-plan")
    finally:
        server.shutdown()
        server.server_close()

    assert plan["primary_output"] == "mabang_excel"
    assert plan["ai_policy"]["runtime_depends_on_codex"] is False
    assert plan["ai_policy"]["auto_apply"] is False
    assert plan["execution_defaults"]["primary_action"] == "export_mabang_template"
    assert plan["execution_defaults"]["export_format"] == ".xlsx"
    assert [item["name"] for item in plan["runtime_chain"]] == [
        "NAS 素材库",
        "商品资料维护",
        "马帮导出预览",
        "马帮 Excel 下载",
    ]

    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    app_js = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")
    assert 'data-view="plan"' in html
    assert 'id="plan"' in html
    assert 'id="project-plan-summary"' in html
    assert 'id="project-runtime-chain"' in html
    assert "/api/project-plan" in app_js
    assert "renderProjectPlan" in app_js
    assert ".plan-chain" in css
    print("project_plan_ok")


if __name__ == "__main__":
    main()
