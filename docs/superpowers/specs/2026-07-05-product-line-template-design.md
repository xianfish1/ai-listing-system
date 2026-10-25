# Product Line and Store Template Design

## Summary

This design makes the product workflow the main line before any browser automation work. Operators first create a base product from one NAS product folder, then copy that base product to one or more store template variants. Each store listing copy carries the store-specific category, price, discount, currency, and copy rules that will later feed either Mabang Excel export or browser automation.

The browser automation phase is intentionally deferred. It will publish or fill Mabang pages from store listing copies after the product data model is stable.

## Goals

- Let operators click `Create product`, choose one NAS product folder, and immediately create a base product from the folder.
- Recognize the first supported asset naming convention for new folders: flat Chinese-style names such as main image, model images, color images, size chart, video, and source files.
- Generate final store listing SKUs automatically by combining colors recognized from asset file names with sizes from the selected store template variant.
- Support stores with multiple store template variants, because one store can have different categories, prices, discounts, and copy rules per product line.
- Let operators select multiple store template variants and create one store listing copy per selected template.
- Keep Excel export as an audit and fallback path while preparing the model for future browser automation.

## Non-Goals

- Do not build Browser-Use, Playwright, or Mabang page automation in this phase.
- Do not require old NAS folders to be renamed before they can be scanned.
- Do not make Mabang Excel the source of truth for store IDs, discount IDs, or category IDs.
- Do not publish directly to any marketplace or third-party page from the product creation flow.

## Domain Model

**Base product**

A base product is created from one NAS product folder. It owns shared product identity, asset root, main image, model images, color images, generated SKUs, and base product text. It is not the final store-specific listing.

**Store**

A store represents a real sales account, such as one Shopee or Lazada shop. It stores platform, country, currency, display name, and later the browser automation identifiers discovered from Mabang.

**Store template variant**

A store template variant belongs to one store. A store can have multiple variants, such as `Shopee-MY / Men's T-Shirt` and `Shopee-MY / Kids T-Shirt`. The variant stores category mapping, price defaults, discount defaults, size rules, title rules, description rules, and any later `马帮店铺编号`, `马帮类目编号`, or `马帮折扣编号` values.

**Store listing copy**

A store listing copy is generated from one base product and one store template variant. It carries the final store-specific category, price, discount, currency, title, description, SKU rows, and status for export or automation.

## NAS Creation Flow

1. Operator clicks `Create product`.
2. Operator chooses or enters one NAS product folder path.
3. System scans only that folder as one product.
4. System detects `group_id`, product name, asset root, main image, model images, color images, video, source file, and optional size chart.
5. System creates a base product immediately.
6. System records recognized colors and source assets on the base product.
7. System shows the created base product and its detected assets, with warnings when recognition was weak.

The first implementation can accept a typed or pasted path. A native folder picker can be added later if the browser/runtime supports it reliably.

## Asset Naming Rule

The first supported rule is a flat one-folder naming convention. New NAS folders should prefer names equivalent to:

```text
主图.jpg
模特图1.jpg
模特图2.jpg
白色.jpg
黑色.jpg
尺码表.png
视频.mp4
源文件.psd
```

Recognition rules:

- `主图*` or `main*` becomes `main`.
- `模特图*` or `model*` becomes `model`.
- Chinese color names such as `白色`, `黑色`, `红色`, `蓝色`, `绿色`, `灰色`, `粉色`, `紫色`, `黄色`, and `米色` become color variants.
- `尺码表*` or `size*` becomes `size_chart`.
- Video extensions become `video`.
- Design source extensions such as `.psd`, `.ai`, `.cdr`, and `.pdf` become `source`.

Old folders remain best-effort compatible. The new rule is the recommended standard for new product folders.

## Store Template Flow

After a base product exists, the operator can click `Copy to store templates`.

The copy dialog should show a searchable multi-select list of store template variants. Each row should show store, platform, country, currency, product line, category name, normal price, sale price, discount label, and template status.

When the operator confirms, the system creates one store listing copy per selected store template variant. Each copy receives:

- base product asset references
- generated SKU rows from `recognized colors x selected template sizes`
- store and platform
- marketplace category path and later category ID
- currency
- normal price and sale price
- discount label and later discount ID
- title generated from base product and template rules
- description generated from base product and template rules
- status `draft`

## Data Shape

The current `products`, `skus`, and `prices` collections can be evolved without throwing away existing data.

Suggested collections:

- `base_products`: shared NAS-derived product records.
- `store_templates`: store template variants and their pricing/category/copy rules.
- `store_listing_copies`: store-specific drafts copied from base products.
- `listing_skus`: SKU rows scoped to a store listing copy when a store needs different SKU data.
- `listing_prices`: price rows scoped to a store listing copy.

If implementation needs a smaller first step, existing `products` can temporarily hold base products and a `listing_kind` field can distinguish base products from store listing copies. The target model should still be the separate collections above.

## UI Shape

The product page should separate the workflow into three operational areas:

- Base products: create from NAS path, inspect recognized assets, edit shared fields.
- Store templates: maintain store template variants and their category, price, discount, size, title, and description rules.
- Store listing copies: review generated copies, edit store-specific values, export or later publish.

The primary action on a base product is `Copy to store templates`. Export actions should move to store listing copies so operators do not accidentally export an unscoped base product.

## Validation

Base product readiness:

- `group_id` exists.
- `asset_root` exists.
- main image is recognized or manually selected.
- at least one color variant is recognized.

Store template readiness:

- store is present.
- platform and country are present.
- currency is present.
- category path is present.
- normal price and sale price are present.
- size rule is present.

Store listing copy readiness:

- base product reference exists.
- store template reference exists.
- title exists.
- description exists.
- category path exists.
- SKU rows exist.
- price rows exist.

## Browser Automation Later

Browser automation should consume store listing copies, not base products. Its first job should be a store template collector that opens Mabang, reads available stores, categories, discounts, logistics, and template IDs, then updates store template variants.

The product line does not depend on this. Until automation is ready, store listing copies can still be exported or inspected locally.

## Assumptions

- The first product creation flow uses one NAS folder per base product.
- New folders will follow the flat Chinese asset naming rule where possible.
- SKU sizes come from the selected store template variant when store listing copies are created.
- A store can have multiple template variants.
- Multi-template copy is the default copy workflow.
- Excel export remains useful as fallback and audit output, but not as the source of store-specific IDs.
