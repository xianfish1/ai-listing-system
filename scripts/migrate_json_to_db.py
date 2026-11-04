from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.api.db_store import COLLECTIONS, PostgresJsonStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import data/store.json into PostgreSQL storage.")
    parser.add_argument("--store-file", default=str(PROJECT_ROOT / "data" / "store.json"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store_file = Path(args.store_file)
    if not store_file.exists():
        raise SystemExit(f"Store file not found: {store_file}")
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required. Pass --database-url or set the env var.")

    data = json.loads(store_file.read_text(encoding="utf-8"))
    # 拆分集合（automation_jobs / listing_skus / base_products 等）不在主库里，
    # 按当前 _SPLIT_STORE_KEYS 把同目录的 <key>.json 合并回来，缺文件按空集合算。
    from services.api.storage import _SPLIT_STORE_KEYS  # noqa: PLC0415 - 避免循环导入顶层化

    for key in _SPLIT_STORE_KEYS:
        split_path = store_file.parent / f"{key}.json"
        if key in data or not split_path.exists():
            continue
        data[key] = json.loads(split_path.read_text(encoding="utf-8"))
    summary = {collection: len(data.get(collection) or []) for collection in COLLECTIONS}
    print(json.dumps({"store_file": str(store_file), "summary": summary, "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    store = PostgresJsonStore(args.database_url)
    store.save_store(data)
    loaded = store.load_store()
    loaded_summary = {collection: len(loaded.get(collection) or []) for collection in COLLECTIONS}
    if loaded_summary != summary:
        raise SystemExit(f"Import count mismatch: expected={summary} actual={loaded_summary}")
    print("json_to_postgres_ok")


if __name__ == "__main__":
    main()
