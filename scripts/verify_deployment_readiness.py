from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "storage" / "tmp" / "verify-deployment-readiness-data"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["STORAGE_BACKEND"] = "json"
os.environ["DATABASE_URL"] = ""

from services.api import storage
from services.api.main import ApiHandler


def get_json(base_url: str, path: str) -> dict:
    with request.urlopen(f"{base_url}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_api() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def main() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    storage.ensure_dirs()
    storage.save_store(storage._empty_store())
    product = storage.create_product({
        "category": "POD 男装T恤",
        "platform": "shopee",
        "store": "verify-store",
        "group_id": "DEPLOY001",
        "product_name_cn": "部署测试",
        "listing_title": "Deploy Readiness Test DEPLOY001",
        "weight_kg": 0.25,
        "length_cm": 28,
        "width_cm": 22,
        "height_cm": 2,
        "main_image": "main.jpg",
    })
    storage.create_ai_output({
        "product_id": product["id"],
        "output_type": "mabang_export_audit",
        "response": "{}",
    })

    server, base_url = start_api()
    try:
        data = get_json(base_url, "/api/deployment/readiness")
        assert data["target"] == "mabang_export"
        assert data["storage"]["backend"] == "json"
        assert "legacy_nodes" not in data["counts"]
        assert "legacy_bundles" not in data["counts"]
        assert data["counts"]["products"] == 1
        assert data["counts"]["ai_outputs"] == 1
        checks = {check["code"]: check for check in data["checks"]}
        assert "template_available" in checks
        assert "exportable_products" in checks
        assert "mabang_audit" in checks
        print("deployment_readiness_ok")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
