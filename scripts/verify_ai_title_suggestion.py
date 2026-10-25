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

DATA_DIR = PROJECT_ROOT / "storage" / "tmp" / "verify-ai-title-suggestion-data"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["DEEPSEEK_ENABLE_LIVE"] = "0"

from services.api import storage
from services.api.main import ApiHandler


def post_json(base_url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
        "group_id": "AITITLE001",
        "product_name_cn": "超天酱",
        "listing_title": "Old Manual Title AITITLE001",
        "weight_kg": 0.25,
        "main_image": "main.jpg",
    })
    server, base_url = start_api()
    try:
        before = storage.get_item("products", product["id"])
        result = post_json(base_url, f"/api/products/{product['id']}/ai/title-suggestion", {"max_length": 180})
        assert result["policy"]["suggestion_only"] is True
        assert result["policy"]["auto_apply"] is False
        assert result["policy"]["browser_control"] is False
        assert result["policy"]["publish_control"] is False
        suggestion = result["suggestion"]
        assert suggestion["title"]
        assert "AITITLE001" in suggestion["title"]
        assert suggestion["external_call_used"] is False
        assert suggestion["validation"]["ok"] is True
        output = result["ai_output"]
        assert output["output_type"] == "title_suggestion"
        assert output["product_id"] == product["id"]
        assert output["response"] == suggestion["title"]

        after = storage.get_item("products", product["id"])
        assert before["listing_title"] == after["listing_title"], "AI suggestion must not auto-apply to product title"

        history = get_json(base_url, f"/api/products/{product['id']}/ai/outputs?output_type=title_suggestion")
        assert len(history["items"]) == 1
        assert history["items"][0]["id"] == output["id"]
        print("ai_title_suggestion_ok")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
