# AI Listing System

This context covers how operators prepare reusable product data and turn it into exportable listing records.

## Language

**Template-first creation**:
The primary workflow for creating a new product draft from a reusable template preset. It standardizes the default fields before any product-specific edits.
_Avoid_: quick create, blank create, manual create

**Product template**:
A reusable heavy preset for product defaults such as category, dimensions, pricing defaults, description structure, and most editable product-page fields. In this system it is organized primarily by store, not by category. It is the starting point for new drafts, not a live product record.
_Avoid_: product, listing, draft copy

**Product copy**:
A new draft created from an existing product so operators can reuse proven data and adjust only the differences. It is a secondary acceleration path after template-first creation.
_Avoid_: duplicate template, clone template, template variant

**Base product**:
A product record created from one NAS product folder before store-specific publishing rules are applied. It owns the shared assets, main SKU, recognized colors, generated SKUs, and reusable product identity.
_Avoid_: store listing, shop product, template record

**AI listing re-recognition**:
A targeted re-run of one AI listing step on a base product — title, description, or color images — on demand. It rewrites only the named field: title and description keep each other and recognized attributes untouched, while color re-recognition re-examines every current color image (including filename-named ones) and preserves manual colors that have no image. The base product batch bar runs it for the checked products.
_Avoid_: full AI retry for one field, blind color overwrite, description overwritten by title retry

**Main SKU**:
The reusable SKU root for a base product, automatically recognized from the NAS product folder name when possible, such as reading `AL0002` from `AL0002 我的世界`. Store templates use this value inside SKU templates.
_Avoid_: group id, listing SKU, store SKU

**NAS product folder**:
One operator-selected folder on NAS that represents exactly one base product and contains that product's images, size chart, video, and source files. Product maintenance should create a base product from this folder through direct folder selection when the browser supports it.
_Avoid_: asset root, batch scan directory, product library

**Product maintenance**:
The operator-facing workspace for preparing reusable product data. It contains base product maintenance and store template maintenance, but does not include browser automation task execution.
_Avoid_: product list, listing page, automation page

**Automation operations dashboard**:
The desktop home workspace that summarizes the listing workflow using only verifiable system data: product and template counts, listing-copy readiness, automation job states, worker and Mabang connectivity, collection freshness, recent execution events, and saved-draft outcomes. It does not present marketplace sales, balance, or performance metrics unless an authoritative platform data source is connected.
_Avoid_: marketing homepage, simulated analytics, marketplace sales dashboard, settings page

**Next work action**:
The single dashboard command that navigates the operator to the highest-priority unresolved workflow step, such as a failed task, an unchecked listing copy, an unavailable worker, copying an existing base product, or creating the first base product. It always names its destination and never creates an automation task or saves a Mabang draft directly from the dashboard.
_Avoid_: start publishing, blind batch execution, dashboard-side Mabang save

**On-demand automation worker**:
The default worker lifecycle where no automation worker process or collection browser runs while there is nothing to execute. Creating ready tasks wakes as many account-slot workers as there are queued tasks for that account (never more than the configured slots), and a worker that reports idle with no claimable, in-flight, or page-import work for its account is stopped together with its collection browser once the configured idle window passes. An operator's manual stop or pause holds that target against automatic waking until a new task arrives or the operator starts it again.
_Avoid_: always-on worker, wake every slot, idle shutdown of a busy worker, silent revival of a manually stopped worker

**Runtime configuration**:
The operator-managed settings that control AI model connections, store collection metadata, NAS scanning, and worker/browser connectivity. AI, store data, and NAS changes take effect for subsequent operations immediately; an active worker or browser connection may require an explicit restart notice. The system safety rule that automation only saves Mabang drafts and never publishes is immutable and view-only.
_Avoid_: environment-only setting, silent worker restart, configurable publish permission

**Product card**:
A compact visual summary for one base product in product maintenance. It replaces the flat base product table as the primary way to select a base product and open its maintenance dialog.
_Avoid_: product row, listing card, store listing card

**Default product image order**:
The automatically selected product-image sequence on a base product: the recognized main image first, then model images, then color images, each group in natural file-name order until the 9-image product limit is reached. Detail images, print artwork, size charts, source files, and videos are never selected by default.
_Avoid_: all recognized images, color-first image order, asset scan order

**Print artwork**:
A production-only asset recognized from print-related file names such as `前印花.png`, `后印花.png`, `前胸印花图.png`, or `前胸透明图.png`. It is never a product image, a main image, or a color image, and any color word in its file name describes the print ink rather than a garment color, so it never creates a recognized color.
_Avoid_: print photo, color image, product image, print ink as garment color

**Optional product image**:
An eligible image in the base product's asset folder that was not part of the default product image order and can be manually added in product maintenance.
_Avoid_: unused image, hidden image, leftover image

**File management**:
The operator-facing view of NAS assets as folders and files that can be scanned, inspected, and used by base products. It presents assets like a file manager instead of a flat asset table.
_Avoid_: asset list, media dump, scan table

**Store listing copy**:
A store-specific draft copied from a base product and then filled with that store's category path, pricing, discount, and copy defaults. One base product can produce many store listing copies.
_Avoid_: base product, global product, asset group

**Heavy template**:
A product template that pre-fills nearly the entire product-maintenance page, leaving only product-specific differences to adjust. It prioritizes speed over minimalism.
_Avoid_: light template, field pack, quick preset

**Store-scoped template**:
A product template owned by a specific store and optimized for that store's publishing habits, defaults, category mapping, price, discount, and copy style. A store can have multiple templates for different product lines or marketplace categories. It also belongs to exactly one Mabang account: the template list, the editor's store picker, and the copy-to-template picker only show the templates and stores of the currently selected account, so two accounts that share a store name or store ID never see each other's templates.
_Avoid_: single store default, category-only template, global template, generic preset, cross-account template list

**Mabang publishing identifiers**:
The internal store, category, and promotion identifiers required by Mabang automation. Operators select readable Chinese store names, category paths, and promotion labels; raw Mabang IDs are stored behind those selections and are not shown as editable or display fields.
_Avoid_: raw ID fields, Mabang number labels, operator-entered identifiers

**Store template variant**:
One reusable template under a store, identified for operators by its template name and marketplace category. Copying a base product to a store template variant creates one store listing copy. Legacy product-line and title-suffix values may remain readable for compatibility but are not entered, displayed, filtered, copied, or used by new templates and automation.
_Avoid_: product-line template, title-suffix template, store, base product, category-only template

**Store template copy**:
A new store-scoped template created from an existing template while preserving reusable product-page defaults. When the target is on the same platform and site, category values and required-field selections may be copied; when the site changes, only site-independent values are copied and site-bound category, currency, price, required fields, and promotion data must be collected or selected again. A same-store copy (the template card's 复制 action) keeps every store-bound value as-is — category, required fields, prices, promotion, watermarks, description — and only renames the copy to `<name> - 副本`, numbering further copies. The copy belongs to the account that owns its target store, so a save-as that moves a template to another account's store transfers ownership while a same-store copy stays in the source template's account.
_Avoid_: template rename, live template update, cross-site blind clone

**Category profile**:
A reusable category preset that owns the marketplace category path, Mabang category ID, required category attributes, and variation rules. It can suggest defaults when a store template is created, but store templates own the values used by store listing copies.
_Avoid_: store template, product template, manual category fields

**Store template size chart**:
The size chart selected or uploaded on a store template, shown with a thumbnail, and captured as an immutable asset snapshot in every new store listing copy created from that template. Its source is explicit: operator-uploaded image, an existing size-chart template, or a locally stored image captured from a Mabang page import. Replacing the template image affects future copies only; existing copies retain their snapshot unless an operator explicitly replaces it there. The listing copy's snapshot is the source of truth for automation payloads.
_Avoid_: base product size chart, silent update of existing copies, NAS fallback size chart, category-owned size chart

**Store watermark profile**:
A store-owned reference to Mabang's native watermark templates, applied when a base product becomes a store listing copy. It independently selects a main-image watermark for image position 1 and a product-image watermark for positions 2 onward; either selection may be disabled. The profile stores the Mabang template ID and readable name, while Mabang performs the image processing. The base product's original images are never modified, and automation must verify each selected template before applying it. Failure of one image role skips only that role and does not block saving the Mabang draft; the task result records the failed role, selected template, cause, and affected image positions.
_Avoid_: locally composited watermark image, one watermark for all image roles, mutable source image, runtime-only watermark

**Store watermark catalog**:
The complete set of Mabang native watermark templates collected for one selected store, including each template's readable name, internal ID, and preview metadata when available. A store template selects the main-image and product-image entries from this store-bound catalog; copying to another store clears both selections until the target store catalog is explicitly collected.
_Avoid_: global watermark list, position-based selection, cross-store watermark reuse

**Base product video**:
The MP4 product video captured from or uploaded to a NAS product folder and owned by the base product. Other video formats are unsupported and are not transcoded. A store listing copy inherits the video when created, while an operator may replace or remove the video on that copy without changing the base product or other copies. A video is valid for automation only when it is 10–60 seconds long, no larger than 1280×1280 pixels, and no larger than 30 MB. Missing video is a non-blocking reminder; an invalid video produces a visible validation warning and automation skips video upload while continuing the remaining save flow. Store templates may define video usage rules but do not own product-specific video files.
_Avoid_: template-owned product video, shared mutable listing video, automation-time video discovery

**Multi-template copy**:
A product-maintenance workflow where an operator opens a searchable picker, selects one or more complete store template variants, and creates one store listing copy with its own generated description per selected template from the same base product. Incomplete templates remain visible with `需要补充` but cannot be selected.
_Avoid_: one-by-one copy, hidden incomplete template, bulk product duplication, multi-store product

**Product-line first rollout**:
A delivery order where the system first stabilizes base products, store template variants, and store listing copies before adding browser automation for Mabang publishing.
_Avoid_: automation-first rollout, publish-first workflow

**Automatic creation**:
A new-product flow where the operator clicks a NAS product folder and the system immediately creates the draft from recognized assets and template defaults without a separate confirmation step.
_Avoid_: preview-first creation, manual build, staged import

**Flat Chinese asset naming**:
A folder-level naming convention that identifies asset roles from Chinese file names in one product folder, such as `主图.jpg`, `模特图1.jpg`, `白色.jpg`, `黑色.jpg`, and `视频.mp4`.
_Avoid_: nested asset structure, English-only naming, mixed ad hoc naming

**Template-sized SKU generation**:
A SKU generation rule where colors come from recognized asset file names and sizes come from the selected store-scoped template. The system expands `color x size` automatically during product creation.
_Avoid_: filename-sized SKUs, manual-first SKUs, size-free variants

**SKU template**:
A store-template-owned rule for formatting SKU rows when a store listing copy is generated. The current canonical pattern is `{main_sku}_{color}||{size}`, for example `YSC008P_White||110`.
_Avoid_: hard-coded SKU string, manual SKU naming, global SKU format

**Maintained color**:
An operator-edited color option on a base product. It starts from recognized asset file names when possible, but operators can add or rename colors in product maintenance; store listing SKUs use the maintained color name for `{color}`.
_Avoid_: raw recognized color, filename-only color, hidden color code

**Color-image follow order**:
The product-maintenance switch that keeps the maintained color order equal to the position of each color's own image inside the product image order. With it on, dragging a product image reorders colors and dragging a color moves that color's image; a color with no image, or whose image is not in the product image order, stays last. It is stored per base product as `color_order_follows_images` and defaults to on, including for products created before the switch existed. Changing only this switch never marks the product as edited.
_Avoid_: auto color sort, alphabetical color order, color sync, image re-upload

**Store promotion activity**:
A Mabang promotion belonging to one specific store. Operators select it by a readable Chinese label containing the activity name, discount, and validity period, while the system preserves its underlying Mabang activity ID for automation. A missing or unavailable activity may be shown as a non-blocking warning when a task is created, but automation must verify the activity before saving the Mabang draft.
_Avoid_: Mabang discount number, global discount, raw activity ID

**Store promotion catalog**:
The complete set of promotion activities collected from the selected Mabang store during a page import, including each activity's readable name, discount, validity period, status, and internal ID. When a copied template changes stores, the operator explicitly requests the target store's promotion catalog after selecting that store; the collection must verify the target-store identity and must not silently switch stores. A template selects one default activity; recollection may mark it expired but never silently replaces it with another activity.
_Avoid_: current-selection-only capture, cross-store activity, automatic promotion substitution

**Expired promotion activity**:
A store promotion activity whose explicit validity period has ended, or whose collected Mabang status explicitly reports expiration. Absence from the local catalog or failure to find it in the Mabang selection dialog does not prove expiration.
_Avoid_: missing promotion, unresolved promotion, activity not found

**Unresolved promotion activity**:
A template or listing copy promotion that cannot be matched to an activity in the target store by its internal ID or verified readable identity. Automation may be created with this warning, but execution must enter manual confirmation rather than silently saving without the promotion.
_Avoid_: expired promotion, skipped promotion, automatic fallback promotion

**Mabang page import**:
A read-only template-creation workflow that captures the currently selected store, marketplace category, required category fields, selected field values, and store promotion activity from an open Mabang listing page. It creates or updates a store template without saving or publishing the Mabang product page. A successful import makes that template the operator's active maintenance record so any missing required value can be completed immediately.
_Avoid_: browser plugin, Mabang page submission, automatic publishing

**Required-field snapshot**:
The required marketplace category field metadata captured by a Mabang page import, separated from the current page's non-empty selected template values. If Mabang later introduces another required field, or the imported page leaves one unselected, the template becomes `需要补充`; the system does not guess a value and cannot generate an automation task until the field is completed.
_Avoid_: inferred required value, silently compatible template, field-name-only capture

**Template import conflict**:
A Mabang page import targeting a store and marketplace category that already has a template. The operator chooses either `更新现有模板` or `另存为新模板`; updating is recommended by default and retains the previous template version for history.
_Avoid_: silent overwrite, forced duplicate template, destructive replacement

**Description generation mode**:
The strategy selected by a description template version for producing listing copy. The initial supported modes are `组合生成`, which keeps approved opening and after-sales sections fixed while AI writes the product-specific middle section, and `完全生成`, which asks AI to draft the entire description subject to approved facts and validation rules.
_Avoid_: unversioned prompt, implicit generation behavior, one global description style

**Description generation profile**:
A readable, versioned description configuration selected by a store template. It owns the generation mode, approved instructions, fixed sections, and protected facts, while provider credentials and raw model identifiers remain outside operator-facing templates.
_Avoid_: raw model setting, API key field, provider-specific template, unversioned prompt

**Combined description generation**:
A description generation mode composed of a fixed store opening, an AI-generated product introduction, and fixed approved after-sales terms. AI may edit only the product introduction.
_Avoid_: AI-edited after-sales policy, AI-edited shipping promise, free-form full description

**Full description generation**:
A description generation mode where AI drafts the complete structure and wording while preserving protected facts supplied by the description template version, including shipping time, package quantity, and after-sales commitments. Validation must reject changed or invented protected facts.
_Avoid_: unrestricted generation, invented service promise, modified shipping facts

**Generated listing description**:
The description produced once when a base product is copied to a store template and saved on the resulting store listing copy. Operators may edit and approve this saved text before automation; browser automation reads the saved final description and never calls an AI model during form execution.
_Avoid_: runtime AI generation, unsaved suggestion, template-only description

**Automation-ready listing copy**:
A store listing copy whose operator edits have been saved and whose required template, category, promotion, description, pricing, size, and asset checks pass when the operator explicitly requests an automation task. Saving a listing copy does not itself create a task, and no separate description-confirmation action is required.
_Avoid_: auto-queued edit, unsaved automation payload, separate description approval

**Description pending**:
The state of a store listing copy created successfully when AI description generation fails or has not yet produced valid text. The operator may write the description manually or retry generation, but the copy cannot produce an automation task until a valid description is saved.
_Avoid_: failed copy, empty automation description, automatic retry during browser execution

**Template transaction defaults**:
The single required set of stock quantity, weight in kilograms, and package length, width, and height in centimeters owned by a store template. It is copied to the store listing copy, may be edited there as one set before task creation, applies uniformly to every SKU, and is filled into Mabang `交易信息`; automation must not invent fallback values.
_Avoid_: executor hard-coded defaults, per-SKU transaction overrides, base-product-only shipping data, optional transaction fields

**Forty-percent discount**:
A pricing rule where the operator enters the original price and the system calculates a 40% reduction, so the sale price equals 60% of the original price, with currency-appropriate rounding.
_Avoid_: sale price equals 40% of original price, manually inconsistent discount price

**Template size set**:
The checked sizes used by a store template to generate variants. Standard choices are `100`, `110`, `120`, `130`, `140`, `150`, `S`, `M`, `L`, `XL`, `XXL`, and `XXXL`; operators may add custom values, which become reusable checkboxes on that template.
_Avoid_: comma-separated size input, global custom size, filename-derived size

**Category rule version**:
An automatically activated snapshot of the required fields collected for one store and marketplace category. It records its version, collection time, category path, and field changes without a manual reviewer, approval, or rejection workflow; templates become `需要补充` when a newer version adds required fields without values.
_Avoid_: approval queue, reviewer field, rejected rule version, unversioned category rules

**Marketplace category picker**:
A searchable hierarchical dialog for choosing a leaf marketplace category on a store template. It shows the selected path, required-field count, and collection status; an uncollected leaf may be selected but leaves the template in `需要从马帮导入` until a Mabang page import supplies its rule snapshot and values.
_Avoid_: raw category ID input, non-leaf selection, inline result list, fabricated category tree

**Product asset compaction**:
The automatic, irreversible rewrite of a base product's local uploaded images after its listing is complete: PNG colour and product images become same-stem JPEGs bounded by the configured longest edge, and every reference to them (`assets`, `base_products`, `store_listing_copies`, `listing_skus.variant_image`) is rewritten in one transaction so name-based image resolution keeps working. Only base products whose listing is complete, whose copies are all complete, with no in-flight automation job, and whose assets live under local storage are eligible; NAS-referenced assets are never touched.
_Avoid_: rescanning assets after compaction, renaming only one collection, converting NAS files, compressing before the delay has elapsed

**Asset retention**:
The configurable age (default 30 days, counted from the base product's completion time) after which a completed product's local asset folder becomes eligible for cleanup in the `商品素材` category. Rolling a product back clears its completion time and therefore restarts the clock.
_Avoid_: fixed hard-coded retention, counting from import time, deleting without an ownership check

**Unowned product assets**:
Uploaded asset folders under locked local storage that no base product references any more because the product was deleted or re-imported. They may still be referenced by asset records, historical execution snapshots, or dangling listing copies, so they are cleaned only through the separate manual `已删商品遗留素材` category and never by the daily automatic pass.
_Avoid_: treating them as free space, automatic deletion, deleting folders that still contain an upload session

**Asset cleanup trace**:
The `assets_compacted_at`, `assets_compacted_bytes`, `assets_cleaned_at`, and `assets_cleaned_bytes` fields written on a base product when its local assets were compacted or removed. They explain a later `upload_file_missing` block and are not an edit: an operator's manual edit marker is untouched.
_Avoid_: counting compaction as a manual edit, silently deleting assets without a trace

**Current version**:
The semantic version stored in the root `VERSION` file and exposed by `/api/health` and `/api/updates/status`. It is the only authority for update comparison and release tagging; a git tag or changelog is derived from it, never the other way round.
_Avoid_: version inferred from the latest tag, hard-coded version constant, changelog as version source

**Release package**:
The zip built from the release whitelist (code directories plus explicitly listed startup files) with a root `manifest.json` recording each file's SHA-256 and size, published as a GitHub Release asset next to a sibling `.sha256`. Runtime data, storage, and `.env` are never inside it.
_Avoid_: zipping the whole working directory, package containing data or credentials, hand-edited archive

**Update backup**:
The copy of every file replaced during an update, written under `storage/updates/backups/<timestamp>-from-<previous version>/` with a `backup.json` index. It is the source for automatic rollback when replacement fails and for manual rollback by the operator.
_Avoid_: in-place overwrite without backup, backup inside the code directories, deleting the backup right after a successful update
