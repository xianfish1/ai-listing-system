from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.db_store import COLLECTIONS, PostgresDriverMissing, PostgresJsonStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify PostgreSQL storage readiness.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument(
        "--require-db",
        action="store_true",
        help="Fail when DATABASE_URL or a PostgreSQL driver is missing.",
    )
    parser.add_argument(
        "--write-smoke",
        action="store_true",
        help="Create schema and perform a small write/read smoke test.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.database_url:
        result = {
            "ok": False,
            "skipped": True,
            "reason": "DATABASE_URL is not configured",
            "next": "Set DATABASE_URL, then rerun with --write-smoke before switching STORAGE_BACKEND=postgres.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.require_db:
            raise SystemExit(1)
        return

    try:
        store = PostgresJsonStore(args.database_url)
    except PostgresDriverMissing as exc:
        result = {
            "ok": False,
            "skipped": True,
            "reason": str(exc),
            "next": "Install psycopg or psycopg2 in the Python environment used by the API.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.require_db:
            raise SystemExit(1)
        return

    if not args.write_smoke:
        print(json.dumps({
            "ok": True,
            "database_configured": True,
            "driver": store.driver,
            "write_smoke": False,
            "next": "Run again with --write-smoke to create schema and verify read/write.",
        }, ensure_ascii=False, indent=2))
        return

    store.ensure_schema()
    marker = f"postgres-smoke-{uuid.uuid4()}"
    data = {collection: [] for collection in COLLECTIONS}
    data["ai_outputs"].append({
        "id": marker,
        "product_id": marker,
        "output_type": "postgres_smoke",
        "response": "ok",
        "meta_json": {"source": "verify_postgres_config"},
    })
    store.save_store(data)
    loaded = store.load_store()
    outputs = loaded.get("ai_outputs") or []
    if not any(output.get("id") == marker for output in outputs):
        raise SystemExit("PostgreSQL write smoke failed: marker output not found after reload.")
    print(json.dumps({
        "ok": True,
        "database_configured": True,
        "driver": store.driver,
        "write_smoke": True,
        "collections": {collection: len(loaded.get(collection) or []) for collection in COLLECTIONS},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
