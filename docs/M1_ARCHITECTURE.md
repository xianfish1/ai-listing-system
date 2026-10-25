# M1 Architecture

M1 is a small local/LAN product-library service for generating Mabang import spreadsheets.

## Category Profile Flow

```text
Mabang read-only collection
        |
        v
raw capture -> candidate version -> operator review -> published version
                                                     |
                                                     v
                                  store template defaults/overrides
                                                     |
                                                     v
                               listing copy profile snapshot + hash
                                                     |
                                                     v
                                  schema-driven safe automation
```

Automation stops when the snapshot hash changes, a required field is missing, a page field has no mapping, or a page exposes an unknown required field. It never saves or publishes a product without operator review.

## Runtime Flow

```text
Web console
-> API service
-> JSON or PostgreSQL store
-> NAS asset paths
-> Base products
-> Store templates
-> Store listing copies
-> Mabang workbook export fallback
```

M1 now builds the product line before export: one NAS product folder becomes one base product, then operators copy it to one or more store templates. No third-party listing page is controlled by this system yet. Operators can still download the workbook and import it manually.

Browser automation execution is a later phase. M1 can now create automation jobs and browser-fill payloads from store listing copies. Browser-Use is installed in `.venv-browser-use`, and `scripts/browser_use_runner.py` can dry-run a ready job and run Browser-Use diagnostics. No Mabang publishing runner is wired yet. The future runner should consume only `ready` automation jobs and stop for operator review before submit.

## Components

- `apps/web`: static operator console.
- `services/api`: zero-dependency HTTP API.
- `services/api/mabang_export.py`: workbook validation and export.
- `workers/asset_scanner`: NAS/local asset scanner.
- `workers/executor/deepseek_adapter.py`: optional AI suggestion adapter.
- `data/store.json`: local development store.
- `storage/samples/mabang-template.xlsx`: local workbook fallback.

## Data Model

The active store contains:

- `assets`
- `base_products`
- `store_templates`
- `mabang_store_options`
- `store_listing_copies`
- `listing_skus`
- `listing_prices`
- `automation_jobs`
- `products`
- `skus`
- `prices`
- `ai_outputs`

Legacy store keys are normalized away on load so old local data can still be opened.

`products`, `skus`, and `prices` remain the legacy workbook-export shape. `base_products`, `store_templates`, and `store_listing_copies` are the new product-line shape used to prepare multi-store listings before either Excel fallback export or future browser automation.

`automation_jobs` records the Browser-Use handoff queue. Each job points at one store listing copy and stores readiness status plus missing fields such as Mabang store ID, category ID, or discount ID. Browser payloads are built on demand from the listing copy, listing SKUs, listing prices, and assets.

Store templates are the source of `马帮店铺编号`, `马帮类目编号`, and `马帮折扣编号`. Updating a store template syncs those fields to existing listing copies created from the template and refreshes any linked automation job readiness.

`mabang_store_options` stores real Mabang store names and IDs discovered through Browser-Use. The web console uses these options as the store dropdown when creating store templates, so operators do not need to type raw store IDs.

## Export Readiness

`/api/deployment/readiness` reports whether the product library and workbook template are ready for export. Per-product readiness checks validate required title, dimensions, weight, main image, SKU, and price data.

## AI Boundary

AI suggestions are audit-only until an operator explicitly applies them and saves the product.
