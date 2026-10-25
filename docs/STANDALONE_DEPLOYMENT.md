# Standalone Deployment

This deployment guide covers the current M1 scope: one Web/API service that manages product data and exports Mabang Excel files.

## Topology

```text
Operator machine or LAN server
  Web console
  API service
  JSON store or PostgreSQL store
  access to NAS asset paths
```

The API does not control third-party listing pages. Operators download the generated `.xlsx` and import it manually.

## Start API

```powershell
cd C:\path\to\ai-listing-system
.\scripts\run_api.ps1 -Port 8787
```

Open:

```text
http://127.0.0.1:8787
```

For LAN access, open **Settings → 局域网访问** and enable **允许局域网设备访问**. The API rebinds to `0.0.0.0` in-place (no restart), lists every usable LAN IPv4 address, and can add a Windows firewall rule scoped to the local subnet. Turning the switch off returns to loopback-only.

The legacy launcher flag still works and is useful for unattended deployments:

```powershell
.\scripts\run_api.ps1 -HostName "0.0.0.0" -Port 8787
```

When `API_HOST` is an explicit non-loopback address, the Settings switch is shown as locked by the launch parameter. Multiple clients share HTTP/1.1 keep-alive connections and the server-side read/response caches; cache rebuilds are single-flight so several clients opening the page at once do not trigger parallel full-store parsing.

## Readiness

Use the dashboard readiness panel or:

```text
GET /api/deployment/readiness
```

The readiness result checks:

- data storage availability
- Mabang template availability
- product, asset, SKU, and price counts
- exportable product count
- export audit count

## PostgreSQL Option

Local JSON storage is fine for one operator:

```text
data/store.json
```

Use PostgreSQL when multiple machines need to share the same product library:

```powershell
python scripts\verify_postgres_config.py --database-url "postgresql://USER:PASSWORD@HOST:5432/ai_listing"
python scripts\verify_postgres_config.py --database-url "postgresql://USER:PASSWORD@HOST:5432/ai_listing" --write-smoke
```

Import current JSON data:

```powershell
python scripts\migrate_json_to_db.py --dry-run
python scripts\migrate_json_to_db.py
```

Start with PostgreSQL:

```powershell
.\scripts\run_api.ps1 -HostName "0.0.0.0" -Port 8787 -StorageBackend "postgres" -DatabaseUrl "postgresql://USER:PASSWORD@HOST:5432/ai_listing"
```

## Operating Flow

1. Scan NAS or local asset folders.
2. Create products from asset groups.
3. Complete required product, SKU, price, and image fields.
4. Preview `/api/mabang/export/preview`.
5. Download `/api/mabang/export`.
6. Import the generated file manually.

## Verification

```powershell
python -m pytest -q
python scripts\verify_deployment_readiness.py
python scripts\verify_project_plan.py
```
