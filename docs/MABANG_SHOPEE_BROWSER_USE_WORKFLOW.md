# Mabang Shopee Browser-Use Workflow

This document records the reusable safe-fill workflow for Shopee local listings in Mabang.

Current operating rule:

```text
Only save the draft. Do not publish.
```

## Entry Point

Open the Shopee listing workspace:

```text
https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1
```

## Login Recovery

If the dedicated automation Chrome profile is not logged in, Mabang redirects the entry point to:

```text
https://www.mabangerp.com/index.htm
```

Observed on this deployment (2026-08): a signed-out `gotoApp` open can first land on the
marketing homepage — which is served both at `/` and at `/index.htm` — where the login
form is not in the DOM. Two rules keep detection honest:

- A login gate is only ever VISIBLE `#account` / `#password` / `#account-btn` fields.
  The URL (including `index.htm`) is never treated as a login signal.
- Slider/verification wording (`安全验证|滑块|captcha|verification`) only counts inside
  frames on mabangerp.com itself (never third-party iframes like the 53KF chat widget),
  and only while the login form is actually visible.

The auto-login helper detects the standalone top-right 「登录」 entry on the marketing
homepage, clicks it (fallback: opens `index.htm` directly), waits for the login fields to
appear in any tab, and only then fills the form. The save executor likewise treats
「登录」 entry visible + no 待发布商品 content as `needs_manual_login_or_verification`, so
the worker can trigger the same auto-login recovery.

Automation may perform login only with operator-provided credentials for the current session. Do not write the password into docs, logs, payloads, `.env`, screenshots, or progress records.

Since the auto-login binding feature, the operator-provided credentials live in
设置 → 马帮连接 (`mabang.login_account` plus a Fernet-encrypted `mabang.login_password`
in the runtime configuration; the password is never returned by `GET /api/settings`
or written to logs/audit). Two flows use them automatically:

- `POST /api/mabang-browser/start` signs in right after the automation Chrome is launched.
- The automation worker calls `POST /api/mabang-browser/auto-login` when its preflight
  finds the browser down (60s cooldown) or when the executor reports
  `needs_manual_login_or_verification`, then re-runs the job once.

The credentials travel to the `scripts/mabang_auto_login.cjs` helper through the
`MABANG_ACCOUNT` / `MABANG_PASSWORD` environment variables only. The captcha rule
below is unchanged: a slider / SMS verification is never auto-solved.

Observed login selectors on the public Mabang page:

```text
Account input: #account
Password input: #password
Login button: #account-btn
```

Safe login flow:

1. Open the Shopee listing workspace URL in the dedicated Chrome CDP profile.
2. If the page title is the public Mabang ERP homepage or the visible URL is `https://www.mabangerp.com/index.htm`, treat it as not logged in.
3. Fill `#account` with the authorized account.
4. Fill `#password` with the operator-provided password for this session only.
5. Click `#account-btn`.
6. If SMS verification, slider captcha, or any second-factor check appears, stop and ask the operator to complete it manually.
7. After login, verify the page title is `马帮ERP::Shopee刊登` and the URL is the Shopee listing workspace URL.
8. Continue by clicking local `待发布商品`.

For local Shopee products, first click:

```text
待发布商品
```

Then create or open the draft listing detail page.

Observed create-detail behavior:

```text
Clicking 新增刊登 on local 待发布商品 opens a new Chrome tab.
The outer tab URL is https://www.mabangerp.com/index.php?mod=main.goPublish&url=...
The `url` query parameter is base64/base64url encoded and decodes to:
https://publish.mabangerp.com/publish-ui/#/productDetailShopee?...&create=true
```

Browser automation must treat both forms as a Shopee product detail page:

```text
1. A tab URL containing productDetailShopee directly.
2. A main.goPublish URL whose decoded `url` parameter contains productDetailShopee.
```

The actual product detail app is inside the outer page's `#iframeContent` iframe. For page-level audit and safe fill, target the iframe document, not only the outer `main.goPublish` shell.

## Safety Rules

- Allowed final action: `保存` / page footer `更新`, only for saving the draft.
- Forbidden final action: `保存并刊登`.
- Forbidden direct publish action: any button or dialog whose purpose is submitting a publish task.
- Browser automation must stop after draft save verification.
- If save status is unclear, do not repeat publish-related actions. Audit the page first.

From the current Mabang Shopee detail code:

```text
preserve(1) = save or update listing draft
preserve(2) = submit publish task
```

Use only `preserve(1)` or the visible footer action that maps to draft save/update. Never call `preserve(2)` for this workflow.

## Store And Template Defaults

Product-side data entry uses two layers:

```text
Store template: store, price, discount, copy defaults.
Category profile: Mabang category, required category attributes, size chart fallback, variation rules.
```

For the current POD Philippines mens T-shirt flow, select:

```text
Store: POD菲律宾 / 2022625115
Category profile: POD菲律宾 男装 > 上衣 > T恤
```

Size chart priority:

```text
1. NAS product folder size chart
2. Category profile default size chart path
3. needs_manual before Browser-Use automation
```

Current store template:

```text
Store display name: POD菲律宾
Mabang store ID: 2022625115
Platform: Shopee
Country/site: PH
Currency: PHP
```

Category:

```text
Men Clothes > Tops > T-Shirts
Mabang category ID: 100244
```

Discount:

```text
Only select a promotion activity collected for the current store.
Never reuse a fixed activity ID from this document or from another store.
When the current page has no real activity ID, show 活动未配置 and block automation.
```

## Current-Page Template Import

The import flow is deliberately split into two operations:

1. `采集当前页` reads the current Mabang detail page through the existing CDP session and stores a sanitized snapshot only. It never saves the Mabang page and never writes a store template, category version, store option, or promotion catalog.
2. `确认导入` writes the inspected snapshot atomically into the store option, category rule version, store promotion catalog, and store template.

The preview must contain the store/category, selected attributes, colors/sizes, transaction information, and promotion state. Only non-empty selected attributes become template values. A required attribute that is not selected remains a diagnostic and blocks automation; it is not written as an empty template value. Empty weight or package dimensions remain empty. A missing real promotion ID is always `活动未配置`.

If the CDP session has expired, log in again in the dedicated Chrome profile and retry collection. Do not substitute field IDs, category values, activity IDs, or transaction values from another page.

Logistics:

```text
Weight: 0.25 kg
Package size: 28 / 22 / 2 cm
Condition: New
Preorder: No
Stock per SKU: 999
```

## Category Attributes

Use these defaults for the current POD T-shirt workflow:

```text
Brand: NoBrand
Pattern: Print
Material: Cotton
Sleeve length: Short Sleeve
Plus size: Plus
Product type: Variation
```

Variation axes:

```text
Attribute 1: color
Attribute 2: size
```

Colors:

```text
black
blue
green
pink
purple
red
white
```

Sizes:

```text
S
M
L
XL
2XL
```

Expected SKU row count:

```text
7 colors x 5 sizes = 35 rows
```

SKU format:

```text
{GROUP_ID}-{COLOR_UPPER}-{SIZE}-Mens Tee
```

Example:

```text
AL0071-BLACK-S-Mens Tee
```

## NAS Asset Rules

Use ASCII temporary upload copies for Browser-Use uploads. Direct UNC paths with Chinese characters can corrupt during browser automation.

Product images:

```text
tmp_upload\product-images\p1.jpg
tmp_upload\product-images\p2.jpg
...
tmp_upload\product-images\p9.jpg
```

Variant thumbnails:

```text
tmp_upload\variant-thumbnails\black.jpg
tmp_upload\variant-thumbnails\blue.jpg
tmp_upload\variant-thumbnails\green.jpg
tmp_upload\variant-thumbnails\pink.jpg
tmp_upload\variant-thumbnails\purple.jpg
tmp_upload\variant-thumbnails\red.jpg
tmp_upload\variant-thumbnails\white.jpg
```

Product image requirement:

```text
Upload 9 product images.
Allow drag-and-drop ordering on the product page before automation.
```

Variant thumbnail requirement:

```text
Each color must have a Mabang COS URL after upload.
The UI should show 2048 x 2048 for the current AL0071 color images.
```

## Browser-Use Environment

Project directory:

```powershell
cd C:\Users\Administrator\Documents\Codex\2026-06-16\files-mentioned-by-the-user-batch-2\outputs\ai-listing-system
```

Dedicated Chrome CDP endpoint:

```powershell
$env:BU_CDP_URL='http://127.0.0.1:9223'
```

Health check:

```powershell
.\.venv-browser-use\Scripts\browser-use.exe --doctor
```

Expected local browser state:

```text
Chrome running
daemon alive
active browser connections: 1
```

Browser Use Cloud auth is optional and not required for this local workflow.

## Window Mode (Headed / Headless)

设置 → 马帮连接 → `无头启动采集/Worker 浏览器` controls how the automation browser
starts (`mabang.headless` in the runtime configuration, default off):

```text
off  = 有头模式: a visible Chrome window the operator can act in.
on   = 无头模式: --headless=new --window-size=1600,1000, no visible window.
```

Both modes drive the same CDP endpoint, so `mabang_auto_login.cjs`, the page-import
capture script and the Playwright save executor are unchanged — they only ever talk
to CDP. Consequences worth knowing:

- The setting applies to newly launched browsers. `launch_mabang_browser` reads the
  running collection Chrome's window mode (`--headless` in its command line) and
  stops it first when the requested mode differs, so flipping the switch takes
  effect on the next 启动自动化浏览器 / Worker recovery instead of being ignored.
- A signed-in profile carries over between modes: cookies live in
  `storage/browser-use-profile`, so switching modes normally still reports
  `already_logged_in` (verified 2026-09-15).
- Headless cannot be used for interactive steps. A Mabang slider / SMS
  verification still needs the visible window, so switch the setting off before
  handling one.

## Safe Fill Checklist

1. Open the Mabang Shopee workspace URL.
2. Click `待发布商品`.
3. Create or open a listing draft.
4. Select store `POD菲律宾`.
5. Select category `Men Clothes > Tops > T-Shirts`.
6. Fill basic info:
   - Source URL, preferably `NAS: {GROUP_ID}` when UNC text is unsafe.
   - Parent SKU.
   - Product title.
   - Description.
7. Fill category attributes:
   - Brand `NoBrand`.
   - Pattern `Print`.
   - Material `Cotton`.
   - Sleeve length `Short Sleeve`.
   - Plus size `Plus`.
8. Upload and order 9 product images.
   - Then, once the images are uploaded, append them to the bottom of the description:
     the size chart first, then every uploaded product image (the full list, not just
     the first few). The description editor is a
     wangEditor: `#textDescription_product_img` opens the 引用产品图 dialog (its list
     is exactly this draft's uploaded product images), and 图片 → 网络图片 inserts a
     size chart URL. Both insert at the caret, so the caret must sit at the end of the
     description body; the executor's `append_description_media` stage does this and
     verifies the images really landed at the tail (undo + retry otherwise).
9. Set product type to variation.
10. Create `color` and `size` variation attributes.
11. Upload color thumbnails with ASCII temp files.
12. Rebuild or verify the SKU grid has 35 rows.
13. Fill every SKU row:
   - SKU.
   - Price `89`.
   - Discount price `69`.
   - Stock `999`.
   - Weight `0.25`.
   - Package size `28 / 22 / 2`.
14. Select or inject discount activity `40%` with ID `928232107737088`.
15. Verify the left side sections all show `已完成`.
16. Save the draft only.
17. Do not publish.

## Final Audit Checklist

Before saving:

```text
No "必填项未填" text exists on the page.
Product images show "已上传9张".
Variation attributes are color and size.
SKU table has 35 rows.
Transaction source data also has 35 rows.
Missing SKU count is 0.
Missing price count is 0.
Missing discount price count is 0.
Missing stock count is 0.
Missing weight/package count is 0.
Missing color thumbnail URL count is 0.
Missing size attribute count is 0.
Discount activity is 40%.
Publish action guard is active.
```

For AL0071 on 2026-07-07, the final pre-save audit was:

```text
Left sections: all completed
Product images: 9
Rows: 35
Missing required row fields: 0
Missing color thumbnails: 0
Discount activity: 40%
Discount price: 69
Publish guard: active
```

Screenshot:

```text
mabang-final-ready-20260707.png
```

## Save-Only Notes From 2026-07-07

The page footer rendered the save action as:

```text
更 新
```

The page code showed:

```text
preserve(1) = save/update listing information
preserve(2) = submit publish task
```

`preserve(1)` was triggered once for AL0071. The page did not show a save-success dialog during the polling window, and `saveLoading` remained true. No publish-success text appeared, and `publishLoading` stayed false after the correct `preserve(1)` call.

Treat this as:

```text
Save attempted, success confirmation not captured.
Do not publish.
Do not repeat save blindly if saveLoading is still true.
First check Mabang draft list for the saved AL0071 draft.
```

Retest later on 2026-07-07:

```text
Draft list search type: Parent SKU (fuzzy)
Draft list keyword: AL0071
Draft list result: 0 rows / no content
Detail page saveLoading: true
Detail page publishLoading: false
Detail page SKU table rows: 35
Transaction source rows: 35
Missing required row fields: 0
Discount activity: 40%, ID 928232107737088
Publish success text: not present
Save success text: not present
```

Conclusion:

```text
AL0071 has not been confirmed as saved to the Mabang draft list.
The detail page still holds a complete filled form, but the previous save request appears stuck.
Before retrying save, instrument network/XHR or reset the stuck save state deliberately.
Never use preserve(2).
```

## Troubleshooting

### Trade Information Reverts To 7 Rows

Uploading color thumbnails can cause Mabang to regenerate the variation table as 7 color-only rows. Fix by rebuilding:

```text
color x size = 35 rows
```

Then merge the real color thumbnail COS URLs into:

```text
variantAttributeInfo.shopeeAttributes
variantAttributeInfo.attributesTable[*].attributes[color].url
```

### Transaction Section Shows Required Gaps

The Vxe table can show 35 rows while the transaction module source still has 7 rows. Sync both:

```text
VxeTable.tableData
transactionInformationModule.variantAttributeInfo.attributesTable
```

Then run the page verification method if available:

```text
transactionInformationModule.fromVerify()
```

Avoid relying on `detail.accomplish()` as a hard requirement; it can throw if optional internal arrays are undefined.

### Save Button Is Not Visible

The Shopee detail page can show `更 新` in the footer instead of `保存`. Confirm page code or state before acting:

```text
preserve(1) is save/update
preserve(2) is publish task submission
```

### Chinese Paths Break Uploads

Do not upload directly from Chinese UNC filenames through Browser-Use. Copy files first into ASCII temp paths under `tmp_upload`.
