from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator


COLLECTIONS = [
    "assets",
    "products",
    "skus",
    "prices",
    "base_products",
    "base_product_prices",
    "store_templates",
    "mabang_accounts",
    "mabang_store_options",
    "store_listing_copies",
    "listing_skus",
    "listing_prices",
    "automation_jobs",
    "category_profiles",
    "category_profile_versions",
    "category_collection_runs",
    "marketplace_categories",
    "marketplace_category_catalog_runs",
    "category_quick_templates",
    "ai_outputs",
    "ai_model_connections",
    "description_generation_profiles",
    "store_template_versions",
    "store_promotion_activities",
    "store_watermark_templates",
    "store_watermark_catalog_runs",
    "store_size_chart_templates",
    "store_promotion_catalog_runs",
    "store_collection_runs",
    "mabang_page_import_runs",
    "listing_copy_revisions",
    "runtime_configuration",
]

SINGLETON_COLLECTIONS = {"runtime_configuration"}


class PostgresDriverMissing(RuntimeError):
    pass


class PostgresJsonStore:
    """PostgreSQL-backed compatibility store for the current JSON data model.

    This keeps the public storage API stable while moving the source of truth
    from a local JSON file into PostgreSQL. It intentionally stores each item as
    JSONB so the M1 API can keep evolving without a large relational migration
    in the same step.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.driver = self._detect_driver()

    @staticmethod
    def _detect_driver() -> str:
        try:
            import psycopg  # noqa: F401

            return "psycopg"
        except ImportError:
            pass
        try:
            import psycopg2  # noqa: F401

            return "psycopg2"
        except ImportError as exc:
            raise PostgresDriverMissing(
                "PostgreSQL storage requires psycopg or psycopg2. "
                "Install one of them, then set STORAGE_BACKEND=postgres and DATABASE_URL."
            ) from exc

    @contextmanager
    def connect(self):
        if self.driver == "psycopg":
            import psycopg

            conn = psycopg.connect(self.dsn, autocommit=False)
        else:
            import psycopg2

            conn = psycopg2.connect(self.dsn)
            conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS listing_store_items (
                      collection TEXT NOT NULL,
                      item_id TEXT NOT NULL,
                      payload JSONB NOT NULL,
                      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                      PRIMARY KEY (collection, item_id)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_listing_store_items_collection "
                    "ON listing_store_items(collection)"
                )

    def load_store(self) -> dict[str, list[dict[str, Any]]]:
        self.ensure_schema()
        data = {collection: [] for collection in COLLECTIONS}
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT collection, payload FROM listing_store_items "
                    "ORDER BY collection, updated_at, item_id"
                )
                for collection, payload in cur.fetchall():
                    if collection not in data:
                        data[collection] = []
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    data[collection].append(dict(payload or {}))
        for collection in SINGLETON_COLLECTIONS:
            data[collection] = data.get(collection, [{}])[0] if data.get(collection) else {}
        return data

    def save_store(self, data: dict[str, Any]) -> None:
        self.ensure_schema()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('ai_listing_store'))")
                for collection in COLLECTIONS:
                    raw_items = data.get(collection) or {}
                    items = [raw_items] if collection in SINGLETON_COLLECTIONS else raw_items
                    cur.execute("DELETE FROM listing_store_items WHERE collection = %s", (collection,))
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_id = self._item_id(collection, item)
                        cur.execute(
                            """
                            INSERT INTO listing_store_items(collection, item_id, payload, updated_at)
                            VALUES (%s, %s, %s::jsonb, NOW())
                            ON CONFLICT (collection, item_id)
                            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                            """,
                            (collection, item_id, json.dumps(item, ensure_ascii=False)),
                        )

    @contextmanager
    def locked_store(self) -> Iterator[tuple[dict[str, list[dict[str, Any]]], Any]]:
        self.ensure_schema()
        conn_ctx = self.connect()
        conn = conn_ctx.__enter__()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('ai_listing_store'))")
            data = {collection: [] for collection in COLLECTIONS}
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT collection, payload FROM listing_store_items "
                    "ORDER BY collection, updated_at, item_id"
                )
                for collection, payload in cur.fetchall():
                    if collection not in data:
                        data[collection] = []
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    data[collection].append(dict(payload or {}))

            def save_locked(updated: dict[str, Any]) -> None:
                with conn.cursor() as cur:
                    for collection in COLLECTIONS:
                        raw_items = updated.get(collection) or {}
                        items = [raw_items] if collection in SINGLETON_COLLECTIONS else raw_items
                        cur.execute("DELETE FROM listing_store_items WHERE collection = %s", (collection,))
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            item_id = self._item_id(collection, item)
                            cur.execute(
                                """
                                INSERT INTO listing_store_items(collection, item_id, payload, updated_at)
                                VALUES (%s, %s, %s::jsonb, NOW())
                                ON CONFLICT (collection, item_id)
                                DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                                """,
                                (collection, item_id, json.dumps(item, ensure_ascii=False)),
                            )

            for collection in SINGLETON_COLLECTIONS:
                data[collection] = data.get(collection, [{}])[0] if data.get(collection) else {}
            yield data, save_locked
        except Exception:
            conn_ctx.__exit__(*__import__("sys").exc_info())
            raise
        else:
            conn_ctx.__exit__(None, None, None)

    @staticmethod
    def _item_id(collection: str, item: dict[str, Any]) -> str:
        if item.get("id"):
            return str(item["id"])
        basis = json.dumps(item, ensure_ascii=False, sort_keys=True)
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{collection}:{basis}"))
