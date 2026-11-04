'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const executor = require('../scripts/mabang_playwright_executor.cjs');

// 执行器的事件流默认会写 stdout（给 runner 解析），跑 node --test 时这些非 TAP 行会把
// 报告打断（计数只显示一部分），所以测试里默认关掉；需要截事件的用例自己临时打开。
process.env.MABANG_EXECUTOR_SILENT = '1';

test('CNSC listing copy editor presents the frozen price data and editable warehouse overrides', () => {
  const app = fs.readFileSync(path.join(__dirname, '../apps/web/app.js'), 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '../apps/web/index.html'), 'utf8');

  assert.match(app, /data-listing-copy-cnsc-warehouse/);
  assert.match(app, /warehouse_stocks/);
  assert.match(app, /site_price_matrix/);
  assert.match(app, /isCNSCListingCopy/);
  assert.match(html, /listing-copy-promotion-section/);
});

test('CNSC site selection scopes shop rows and confirmation to the real picker', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  assert.match(source, /const picker = frame\.locator\('#shops-wap'\)/);
  assert.match(source, /const confirm = picker\.getByRole\('button', \{ name: TEXT\.confirm, exact: true \}\)/);
  assert.match(source, /cnsc_target_site_ambiguous/);
  assert.match(source, /cnsc_required_columns_missing/);
  assert.match(source, /cnsc_value_mismatch/);
});

test('CNSC pre-save verification reopens the shop picker and verifies exact checked stores', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  assert.match(source, /async function verifyCNSCTargetSites\(frame, task\)/);
  assert.match(source, /cnsc_target_site_not_selected/);
  assert.match(source, /cnsc_unexpected_target_site_selected/);
  assert.match(source, /automaticCNSCSIPCandidates/);
  assert.match(source, /verifyCNSCTargetSites\(frame, task\)/);
});

test('CNSC draft inbox matching requires exactly one Parent SKU row', () => {
  assert.deepEqual(
    executor.matchCNSCDraftRows('CNSC1001', ['CNSC1001 28 �?���?', 'OTHER1001']),
    { parent_sku: 'CNSC1001', match_count: 1, rows: ['CNSC1001 28 �?���?'] },
  );
  assert.equal(executor.matchCNSCDraftRows('CNSC1001', ['CNSC1001 A', 'CNSC1001 B']).match_count, 2);
  assert.equal(executor.matchCNSCDraftRows('ABC', ['ABC1 28 �?���?']).match_count, 0);
});

test('per-row fill requires only stock and writes weight/dims only when the column exists', () => {
  // Checked/legacy state: the grid still has weight+package columns �?fill them.
  const legacyColumns = new Set(['stock', 'weight', 'packageLength', 'packageWidth', 'packageHeight']);
  const legacyRow = {};
  executor.applyTransactionInformation(legacyRow, legacyColumns, {
    stock: 20, weight_kg: '0.35', package_length_cm: '30', package_width_cm: '24', package_height_cm: '3',
  });
  assert.deepEqual(legacyRow, { stock: 20, weight: '0.35', packageLength: '30', packageWidth: '24', packageHeight: '3' });

  // Unchecked state: weight/package columns are gone (moved to 物流信息) �?only stock.
  const unchecked = new Set(['stock', 'sku', 'price']);
  const uncheckedRow = {};
  executor.applyTransactionInformation(uncheckedRow, unchecked, {
    stock: 20, weight_kg: '0.35', package_length_cm: '30', package_width_cm: '24', package_height_cm: '3',
  });
  assert.deepEqual(uncheckedRow, { stock: 20 });

  // Stock is the only hard per-row requirement.
  assert.throws(() => executor.applyTransactionInformation({}, new Set(['stock']), { weight_kg: '0.35' }), /transaction_information_incomplete/);
});

test('主 SKU 行按颜色和尺码匹配，不能按马帮价格顺序错开', () => {
  // AL0471 现场�??：马�?��际�?顺序与�?划的 Purple �?Red �?White �?PureWhite
  // 不同。若仍用 row[index] 取�?�?SKU，PureWhite 会得�?White �?SKU、Red �?
  // 得到 Purple �?SKU。必须以页面显示的�?色和尺码作为行身份�??
  const planned = [
    { color_code: 'Purple', size: '100cm', sku_code: 'AL0471_Purple||100cm' },
    { color_code: 'Red', size: '100cm', sku_code: 'AL0471_Red||100cm' },
    { color_code: 'White', size: '100cm', sku_code: 'AL0471_White||100cm' },
    { color_code: 'PureWhite', size: '100cm', sku_code: 'AL9999-PureWhite-100cm' },
  ];
  const pageRows = [
    { color: 'Red', size: '100cm', sku: '' },
    { color: 'PureWhite', size: '100cm', sku: '' },
    { color: 'Purple', size: '100cm', sku: '' },
    { color: 'White', size: '100cm', sku: '' },
  ];

  assert.deepEqual(executor.matchSiteSkuRows(planned, pageRows), [
    { row_index: 0, sku_code: 'AL0471_Red||100cm' },
    { row_index: 1, sku_code: 'AL9999-PureWhite-100cm' },
    { row_index: 2, sku_code: 'AL0471_Purple||100cm' },
    { row_index: 3, sku_code: 'AL0471_White||100cm' },
  ]);
});

test('主 SKU 行的颜色尺码重复或缺失会阻止保存', () => {
  const planned = [{ color_code: 'Red', size: '100cm', sku_code: 'AL0471_Red||100cm' }];

  assert.throws(
    () => executor.matchSiteSkuRows(planned, [{ color: 'Red', size: '100cm' }, { color: 'Red', size: '100cm' }]),
    (error) => error && error.reason === 'site_sku_page_row_identity_not_unique',
  );
  assert.throws(
    () => executor.matchSiteSkuRows(planned, [{ color: 'Purple', size: '100cm' }]),
    (error) => error && error.reason === 'site_sku_page_row_not_in_plan',
  );
});

test('logisticsValuesComplete requires positive weight and all three package dims', () => {
  assert.equal(
    executor.logisticsValuesComplete({ weight_kg: '0.35', package_length_cm: '30', package_width_cm: '24', package_height_cm: '3' }),
    true,
  );
  assert.equal(executor.logisticsValuesComplete({ package_length_cm: '30', package_width_cm: '24', package_height_cm: '3' }), false);
  assert.equal(executor.logisticsValuesComplete({ weight_kg: '0.35', package_length_cm: '30', package_width_cm: '24' }), false);
  assert.equal(executor.logisticsValuesComplete({ weight_kg: '0', package_length_cm: '30', package_width_cm: '24', package_height_cm: '3' }), false);
  assert.equal(executor.logisticsValuesComplete(null), false);
});

test('logistics value formatting keeps weight decimals and rounds package dims to integers', () => {
  assert.equal(executor.formatWeightKg('0.250'), '0.25');
  assert.equal(executor.formatWeightKg('1.0'), '1');
  assert.equal(executor.formatPackageCm('30.0'), '30');
  assert.equal(executor.formatPackageCm('24.6'), '25');
});

test('CNSC payload validation blocks missing identity, prices, and warehouse values', () => {
  const base = {
    store: {
      listing_mode: 'cnsc_global',
      cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', mabang_discount_id: 'discount-ph' }],
    },
    cnsc: {
      price_tiers: [{
        id: 'standard',
        sizes: ['M'],
        global_price_cny: 19.2,
        site_prices: { PH: { sale_price: 746, original_price: 1243 } },
      }],
    },
    sku_grid: {
      skus: [{ global_price_cny: 19.2, warehouse_stocks: { TH: 300, CN_GZ: 0, PH: 0 } }],
    },
  };
  assert.deepEqual(executor.validateCNSCTaskPayload(base), { ok: true, errors: [] });
  const invalid = structuredClone(base);
  invalid.store.target_site_stores[0].store_id = '';
  invalid.store.target_site_stores[0].store_name = '';
  invalid.sku_grid.skus[0].warehouse_stocks.TH = -1;
  invalid.sku_grid.skus[0].global_price_cny = 0;
  assert.equal(executor.validateCNSCTaskPayload(invalid).ok, false);
});

test('CNSC payload validation requires every target site to have a price matrix', () => {
  const task = {
    store: {
      listing_mode: 'cnsc_global',
      cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', mabang_discount_id: 'discount-ph' }],
    },
    cnsc: { price_tiers: [{ id: 'standard', sizes: ['M'], global_price_cny: 19.2, site_prices: {} }] },
    sku_grid: {
      skus: [{ global_price_cny: 19.2, warehouse_stocks: { TH: 300, CN_GZ: 0, PH: 0 } }],
    },
  };
  const result = executor.validateCNSCTaskPayload(task);
  assert.equal(result.ok, false);
  assert.deepEqual(result.errors, ['cnsc_site_price_missing:standard:PH']);
});

test('CNSC warehouse stocks resolve per warehouse: SKU value -> copy default -> 0', () => {
  // �?���?泰国/广州/菲律宾仓库存 三列当必�?��留空才报错，0 �?��法�?�必须照�???
  const grid = {
    warehouse_stock_defaults: { TH: 300, CN_GZ: 0, PH: 0 },
    skus: [
      { sku_code: 'A||M', warehouse_stocks: { TH: 0, CN_GZ: 300, PH: 0 } },
      { sku_code: 'B||M', warehouse_stocks: { CN_GZ: 300 } },
      { sku_code: 'C||M', warehouse_stocks: {} },
      { sku_code: 'D||M' },
    ],
  };
  assert.deepEqual(executor.expectedCNSCDomRows(grid), [
    { sku: 'A||M', global_price: undefined, TH: 0, CN_GZ: 300, PH: 0 },
    { sku: 'B||M', global_price: undefined, TH: 300, CN_GZ: 300, PH: 0 },
    { sku: 'C||M', global_price: undefined, TH: 300, CN_GZ: 0, PH: 0 },
    { sku: 'D||M', global_price: undefined, TH: 300, CN_GZ: 0, PH: 0 },
  ]);
  // �?��级默认也没有时兜�?0，绝不留空�??
  assert.deepEqual(executor.expectedCNSCDomRows({ skus: [{ sku_code: 'E||M' }] }), [
    { sku: 'E||M', global_price: undefined, TH: 0, CN_GZ: 0, PH: 0 },
  ]);
  // 解析器�?脏�?�兜�?0 不抛错；真�?的脏数据�?validateCNSCTaskPayload 在填表前拦截�?
  assert.equal(executor.resolveCNSCWarehouseStock({ warehouse_stocks: { TH: -1 } }, {}, 'TH'), 0);
});

test('CNSC payload validation fills missing warehouse stocks but still blocks dirty values', () => {
  const base = {
    store: {
      listing_mode: 'cnsc_global',
      cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', mabang_discount_id: 'discount-ph' }],
    },
    cnsc: {
      price_tiers: [{
        id: 'standard',
        sizes: ['M'],
        global_price_cny: 19.2,
        site_prices: { PH: { sale_price: 746, original_price: 1243 } },
      }],
    },
    sku_grid: {
      // �?SKU �?��库存不拦：执行器�?�?��默�?�? 回�??�?��必填列�??
      skus: [{ global_price_cny: 19.2, warehouse_stocks: { CN_GZ: 300 } }],
    },
  };
  assert.deepEqual(executor.validateCNSCTaskPayload(base), { ok: true, errors: [] });
  const dirty = structuredClone(base);
  dirty.sku_grid.skus[0].warehouse_stocks.TH = -1;
  const result = executor.validateCNSCTaskPayload(dirty);
  assert.equal(result.ok, false);
  assert.deepEqual(result.errors, ['cnsc_warehouse_stock_invalid:TH']);
});

test('CNSC payload guard accepts a color-tier original-only payload (global=cost, sale derived)', () => {
  // Real color-bound CNSC copy shape: base page stores only original_price per site
  // (v28+), so tier sale_price is blank and the SKU site_price_matrix carries the
  // derived sale (original × (1 - %OFF)). The color tier's 全球商品价格 is filled
  // from its RMB cost at copy time (user decision 2026-09-04) �?the live CNSC page
  // requires *全球商品价格 on every SKU row.
  const task = {
    store: {
      listing_mode: 'cnsc_global',
      cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', store_name: 'PH�?', mabang_discount_id: 'discount-ph' }],
    },
    cnsc: {
      price_tiers: [{
        id: 'white', sizes: [], colors: ['white'], global_price_cny: 12.5,
        site_prices: { PH: { original_price: 696 } },
      }],
    },
    sku_grid: {
      skus: [{
        sku_code: 'DL0001_white||M', global_price_cny: 12.5,
        site_price_matrix: { PH: { original_price: 696, sale_price: 417.6 } },
        warehouse_stocks: { TH: 300, CN_GZ: 0, PH: 0 },
      }],
    },
  };
  assert.deepEqual(executor.validateCNSCTaskPayload(task), { ok: true, errors: [] });
});

test('CNSC payload guard accepts a color-tier hand-filled sale and a uniform-tier derived sale', () => {
  // Copy editor keeps both price boxes: a hand-filled sale that differs from the
  // derived 696×0.6=417.6 is legitimate (the editor may set any sale �?original),
  // and the executor must not re-derive %OFF to reject it.
  const color = {
    store: {
      listing_mode: 'cnsc_global', cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', store_name: 'PH�?', mabang_discount_id: 'd' }],
    },
    cnsc: { price_tiers: [{ id: 'white', sizes: [], colors: ['white'], global_price_cny: 12.5,
      site_prices: { PH: { sale_price: 420, original_price: 696 } } }] },
    sku_grid: { skus: [{ sku_code: 'A||M', global_price_cny: 12.5, warehouse_stocks: { TH: 1, CN_GZ: 1, PH: 1 } }] },
  };
  assert.deepEqual(executor.validateCNSCTaskPayload(color), { ok: true, errors: [] });
  // Uniform tier (no colors) with a derived sale (696 = 1160 × 0.6) is fine even
  // when tier site_prices omits sale entirely �?the SKU matrix carries the pair.
  const uniform = {
    store: {
      listing_mode: 'cnsc_global', cnsc_store: { id: 'cnsc-1' },
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', store_name: 'PH�?', mabang_discount_id: 'd' }],
    },
    cnsc: { price_tiers: [{ id: 'standard', sizes: ['M'], colors: [], global_price_cny: 30,
      site_prices: { PH: { original_price: 1160 } } }] },
    sku_grid: { skus: [{ sku_code: 'B||M', global_price_cny: 30,
      site_price_matrix: { PH: { original_price: 1160, sale_price: 696 } },
      warehouse_stocks: { TH: 1, CN_GZ: 1, PH: 1 } }] },
  };
  assert.deepEqual(executor.validateCNSCTaskPayload(uniform), { ok: true, errors: [] });
});

test('CNSC payload guard requires global price on every tier (color and size)', () => {
  // The CNSC *全球商品价格 column is mandatory per SKU; a tier without a positive
  // global (color-only or not) must fail so the page never reaches the "请输入价�?
  // pre-save block.
  const sizeTier = {
    store: {
      listing_mode: 'cnsc_global', cnsc_store: { id: 'cnsc-1' }, default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', store_name: 'PH�?', mabang_discount_id: 'd' }],
    },
    cnsc: { price_tiers: [{ id: 'standard', sizes: ['M'], colors: [], global_price_cny: '',
      site_prices: { PH: { sale_price: 696, original_price: 1160 } } }] },
    sku_grid: { skus: [{ sku_code: 'B||M', global_price_cny: '',
      site_price_matrix: { PH: { original_price: 1160, sale_price: 696 } },
      warehouse_stocks: { TH: 1, CN_GZ: 1, PH: 1 } }] },
  };
  const tierResult = executor.validateCNSCTaskPayload(sizeTier);
  assert.equal(tierResult.ok, false);
  assert.ok(tierResult.errors.includes('cnsc_global_price_invalid:standard'));
  assert.ok(tierResult.errors.includes('cnsc_sku_global_price_invalid'));
  // A color-only tier with a missing global fails the same way.
  const colorTier = structuredClone(sizeTier);
  colorTier.cnsc.price_tiers = [{ id: 'white', sizes: [], colors: ['white'], global_price_cny: '',
    site_prices: { PH: { sale_price: 696, original_price: 1160 } } }];
  const colorResult = executor.validateCNSCTaskPayload(colorTier);
  assert.equal(colorResult.ok, false);
  assert.ok(colorResult.errors.includes('cnsc_global_price_invalid:white'));
  assert.ok(colorResult.errors.includes('cnsc_sku_global_price_invalid'));
  // A malformed original (blank/negative) still fails closed for any tier shape.
  const missingOriginal = structuredClone(sizeTier);
  missingOriginal.cnsc.price_tiers[0].global_price_cny = 30;
  missingOriginal.cnsc.price_tiers[0].site_prices.PH = { sale_price: 1, original_price: '' };
  missingOriginal.sku_grid.skus[0].global_price_cny = 30;
  missingOriginal.sku_grid.skus[0].site_price_matrix.PH = { sale_price: 1, original_price: '' };
  assert.deepEqual(executor.validateCNSCTaskPayload(missingOriginal).errors, ['cnsc_site_price_missing:standard:PH']);
});

test('CNSC target resolution scopes duplicate store names by site before identity fallback', () => {
  const candidates = [
    { siteCode: 'TW', labelText: '海宁店', identities: ['tw-1'] },
    { siteCode: 'PH', labelText: '海宁店', identities: ['ph-1'] },
  ];

  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_name: '海宁店' }),
    candidates[1],
  );
});

test('CNSC target resolution accepts store id/name captured from real radio ancestors', () => {
  const candidates = [
    {
      siteCode: 'PH',
      labelText: '海宁店（已�?�）',
      identities: ['on'],
      storeIds: ['ph-1'],
      names: ['海宁�?'],
    },
  ];

  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_id: 'ph-1', store_name: '海宁�?' }),
    candidates[0],
  );
});

test('CNSC candidate collection reads real per-shop checkbox DOM', async () => {
  const empty = {
    async count() { return 0; },
    first() { return empty; },
    async getAttribute() { return ''; },
    async isVisible() { return false; },
    async isChecked() { return false; },
  };
  const inputFor = (node) => ({
    async count() { return 1; },
    first() { return this; },
    async getAttribute(attribute) { return node.attrs[attribute] || ''; },
    async isChecked() { return Boolean(node.checked); },
  });
  const labelFor = (node) => {
    const input = inputFor(node);
    return {
      async count() { return 1; },
      async isVisible() { return true; },
      async innerText() { return node.text; },
      async getAttribute(attribute) { return node.attrs[attribute] || ''; },
      locator(selector) {
        if (selector === 'input[type="checkbox"]') return input;
        if (selector.includes('ancestor::*[@data-store-id')) return node.storeMetadata || empty;
        if (selector.includes('ancestor::*[@data-id')) return node.genericMetadata || empty;
        return empty;
      },
    };
  };
  const shops = [
    { text: '海宁店（已�?�）', attrs: { 'data-shop-id': 'ph-1', 'data-shop-name': '海宁�?' }, checked: true },
    { text: '另一家店', attrs: { 'data-shop-id': 'ph-2', 'data-shop-name': '另一家店' }, checked: false },
  ];
  const shopLabels = shops.map(labelFor);
  const siteGroup = {
    async isVisible() { return true; },
    async innerText() { return '菲律宾'; },
    async getAttribute(attribute) { return attribute === 'data-site-code' ? 'PH' : ''; },
    locator(selector) {
      if (selector === 'label.shops-checkbox') return {
        async count() { return shopLabels.length; },
        first() { return shopLabels[0]; },
        nth(index) { return shopLabels[index]; },
      };
      if (selector === '.title-wap-checkbox input[type="checkbox"]') return empty;
      return empty;
    },
  };
  const picker = {
    async count() { return 1; },
    locator(selector) {
      if (selector === '.site') return { async count() { return 1; }, nth() { return siteGroup; } };
      if (selector === 'label.el-checkbox') return { async count() { return 0; }, nth() { return empty; } };
      return empty;
    },
  };
  const candidates = await executor.collectCNSCSitePickerCandidates(picker);
  assert.equal(candidates.length, 2);
  assert.equal(candidates[0].storeIds[0], 'ph-1');
  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_id: 'ph-1', store_name: '海宁�?' }),
    candidates[0],
  );
});

test('CNSC relaxed name matching still fails closed when multiple shops share a prefix', () => {
  const candidates = [
    { siteCode: 'PH', labelText: '海宁东店（已选）', identities: [] },
    { siteCode: 'PH', labelText: '海宁西店（已选）', identities: [] },
  ];

  assert.throws(
    () => executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_name: '海宁店' }),
    (error) => error && error.reason === 'cnsc_target_site_ambiguous',
  );
});

test('CNSC target-not-found details include the site-scoped candidates for diagnosis', () => {
  assert.throws(
    () => executor.resolveCNSCTargetCandidate(
      [{ siteCode: 'PH', identities: ['ph-actual'], labelText: '另一家店' }],
      { site_code: 'PH', store_id: 'ph-missing', store_name: '�?���?' },
    ),
    (error) => error
      && error.reason === 'cnsc_target_site_not_found'
      && error.details.available.length === 1
      && error.details.available[0].identities.includes('ph-actual'),
  );
});

test('CNSC target selection re-resolves a shop label after Vue rerender', async () => {
  let pickerOpen = false;
  let labelsReady = false;
  let checked = false;
  let labelReads = 0;
  const empty = {
    async count() { return 0; },
    first() { return this; },
    async getAttribute() { return ''; },
    async isVisible() { return false; },
    async isChecked() { return false; },
  };
  const makeInput = () => ({
    async count() { return 1; },
    first() { return this; },
    async getAttribute(attribute) {
      return { value: 'ph-1', 'data-shop-id': 'ph-1', 'data-shop-name': '海宁�?' }[attribute] || '';
    },
    async isChecked() { return checked; },
  });
  const makeLabel = (stale) => ({
    async count() { return 1; },
    async isVisible() { return pickerOpen; },
    async innerText() { return '海宁店'; },
    async getAttribute(attribute) {
      return { 'data-shop-id': 'ph-1', 'data-shop-name': '海宁店', class: 'shops-checkbox' }[attribute] || '';
    },
    locator(selector) {
      if (selector === 'input[type="checkbox"]') return makeInput();
      if (selector.includes('ancestor::*[@data-store-id')) return empty;
      if (selector.includes('ancestor::*[@data-id')) return empty;
      return empty;
    },
    async click() {
      if (stale) throw new Error('locator.click: stale Vue node');
      checked = true;
    },
  });
  const siteGroup = {
    async isVisible() { return pickerOpen; },
    async innerText() { return '菲律宾'; },
    async getAttribute(attribute) { return attribute === 'data-site-code' ? 'PH' : ''; },
    locator(selector) {
      if (selector === 'label.shops-checkbox') return {
        async count() { return labelsReady ? 1 : 0; },
        first() { return makeLabel(false); },
        nth() {
          labelReads += 1;
          return makeLabel(labelReads === 1);
        },
      };
      if (selector === '.title-wap-checkbox input[type="checkbox"]') return empty;
      return empty;
    },
  };
  const confirm = { async count() { return 1; }, async click() { pickerOpen = false; } };
  const picker = {
    async count() { return 1; },
    locator(selector) {
      if (selector === '.shop-box') return {
        async count() { return 1; },
        async click() {
          pickerOpen = true;
          setTimeout(() => { labelsReady = true; }, 200);
        },
      };
      if (selector === '.site') return { async count() { return 1; }, nth() { return siteGroup; } };
      return empty;
    },
    getByRole(role, options) {
      return role === 'button' && options?.name === executor.TEXT.confirm ? confirm : empty;
    },
  };
  const frame = {
    locator(selector) { return selector === '#shops-wap' ? picker : empty; },
  };
  const task = {
    store: {
      listing_mode: 'cnsc_global',
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-1', store_name: '海宁�?' }],
    },
  };

  const result = await executor.selectCNSCTargetSites(frame, task);
  assert.deepEqual(result, { selected: ['ph-1'] });
  assert.equal(labelReads >= 2, true);
  assert.equal(checked, true);
});

// ---------------------------------------------------------------------------
// Collection/matching hardening for the real "shop is visible in the popup but
// the runner reports cnsc_target_site_not_found" bug. These mocks model the
// Element-UI picker: collapsible `.site` groups, `label.shops-checkbox` rows
// that can report isVisible()===false while still present, and per-shop inputs.
// ---------------------------------------------------------------------------
function emptyLocator() {
  const empty = {
    async count() { return 0; },
    first() { return empty; },
    nth() { return empty; },
    async getAttribute() { return ''; },
    async isVisible() { return false; },
    async isChecked() { return false; },
    async innerText() { return ''; },
    async evaluate() { return ''; },
    async click() {},
    locator() { return empty; },
  };
  return empty;
}

function shopLabelMock(node, empty) {
  const input = {
    async count() { return 1; },
    first() { return input; },
    async getAttribute(attribute) { return node.attrs[attribute] || ''; },
    async isChecked() { return Boolean(node.checked); },
  };
  return {
    async count() { return 1; },
    async isVisible() { return node.visible !== false; },
    async innerText() { return node.text || ''; },
    async evaluate() { return node.text || ''; },
    async getAttribute(attribute) { return node.attrs[attribute] || ''; },
    async click() { node.checked = true; },
    locator(selector) {
      if (selector === 'input[type="checkbox"]') return input;
      return empty;
    },
  };
}

function siteGroupMock(group, empty) {
  const shopCount = () => (group.collapsed ? 0 : group.shops.length);
  const shopCollection = () => ({
    async count() { return shopCount(); },
    first() { return shopCount() ? shopLabelMock(group.shops[0], empty) : empty; },
    nth(index) { return shopLabelMock(group.shops[index], empty); },
  });
  const toggle = {
    async count() { return 1; },
    first() { return toggle; },
    async click() { group.collapsed = false; },
  };
  return {
    async isVisible() { return group.visible !== false; },
    async innerText() { return group.title || ''; },
    async getAttribute(attribute) { return (group.attrs || {})[attribute] || ''; },
    locator(selector) {
      if (selector === 'label.shops-checkbox') return shopCollection();
      if (selector === 'label.el-checkbox') return { async count() { return 0; }, nth() { return empty; }, first() { return empty; } };
      if (selector === '.title-wap-checkbox input[type="checkbox"]') return empty;
      if (selector.includes('el-collapse-item__header')) return { async count() { return 1; }, first() { return toggle; } };
      return empty;
    },
  };
}

function pickerMock(groups, { snapshot = '' } = {}) {
  const empty = emptyLocator();
  const siteGroups = groups.map((group) => siteGroupMock(group, empty));
  return {
    empty,
    async count() { return 1; },
    async evaluate() { return snapshot; },
    locator(selector) {
      if (selector === '.shop-box') return { async count() { return 1; }, async click() {} };
      if (selector === '.site') return { async count() { return siteGroups.length; }, nth(index) { return siteGroups[index]; } };
      if (selector === 'label.el-checkbox') return { async count() { return 0; }, nth() { return empty; } };
      return empty;
    },
  };
}

test('CNSC collection keeps present-but-invisible shop rows and matches them by id (direct regression)', async () => {
  const picker = pickerMock([{
    title: '菲律�?',
    attrs: { 'data-site-code': 'PH' },
    shops: [{ text: '海宁�?', visible: false, attrs: { 'data-shop-id': 'ph-1', 'data-shop-name': '海宁�?' } }],
  }]);
  const candidates = await executor.collectCNSCSitePickerCandidates(picker);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].visible, false);
  assert.equal(candidates[0].storeId, 'ph-1');
  assert.equal(candidates[0].storeName, '海宁�?');
  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_id: 'ph-1', store_name: '海宁�?' }),
    candidates[0],
  );
});

test('CNSC collection expands a collapsed accordion group before reading its shops', async () => {
  const group = {
    title: '菲律�?',
    attrs: { 'data-site-code': 'PH' },
    collapsed: true,
    shops: [{ text: '海宁�?', attrs: { 'data-shop-id': 'ph-1', 'data-shop-name': '海宁�?' } }],
  };
  const picker = pickerMock([group]);
  const candidates = await executor.collectCNSCSitePickerCandidates(picker);
  assert.equal(group.collapsed, false);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].storeId, 'ph-1');
});

test('CNSC collection retains a shop whose site code cannot be derived and matches it by store id', async () => {
  const picker = pickerMock([{
    title: '�?��地区',
    attrs: {},
    shops: [{ text: '�?���?', attrs: { 'data-shop-id': 'x-1', 'data-shop-name': '�?���?' } }],
  }]);
  const candidates = await executor.collectCNSCSitePickerCandidates(picker);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].siteCode, '');
  // Empty-site candidate stays matchable within a site-scoped resolve.
  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_id: 'x-1', store_name: '�?���?' }),
    candidates[0],
  );
});

test('CNSC collection derives site code from a broadened alias table and data attributes', async () => {
  const picker = pickerMock([
    { title: '哥伦比亚', attrs: {}, shops: [{ text: 'CO�?', attrs: { 'data-shop-id': 'co-1' } }] },
    { title: '', attrs: { 'data-code': 'CL' }, shops: [{ text: 'CL�?', attrs: { 'data-shop-id': 'cl-1' } }] },
  ]);
  const candidates = await executor.collectCNSCSitePickerCandidates(picker);
  assert.equal(candidates.find((c) => c.storeId === 'co-1').siteCode, 'CO');
  assert.equal(candidates.find((c) => c.storeId === 'cl-1').siteCode, 'CL');
});

test('normalizeName folds width/case/NBSP and strips Mabang selection suffixes', () => {
  assert.equal(executor.normalizeName('海宁店（已选）'), executor.normalizeName('海宁店'));
  assert.equal(executor.normalizeName('Shop A'), 'shop a');
  assert.equal(executor.normalizeName('ＳＨＯＰ（已选）'), 'shop');
  assert.equal(executor.normalizeName('海宁店默认'), '海宁店');
});

test('CNSC resolution matches a status-suffixed label to the plain configured name', () => {
  const candidates = [{ siteCode: 'PH', labelText: '海宁店（已选）', identities: [], names: ['海宁店（已选）'] }];
  assert.equal(
    executor.resolveCNSCTargetCandidate(candidates, { site_code: 'PH', store_name: '海宁店' }),
    candidates[0],
  );
});

test('CNSC target-not-found details expose the full picker scrape, not only the site-scoped list', () => {
  assert.throws(
    () => executor.resolveCNSCTargetCandidate(
      [
        { siteCode: 'PH', identities: ['ph-actual'], labelText: '另一家店', names: ['另一家店'] },
        { siteCode: 'TW', identities: ['tw-1'], labelText: '�?��店�?标成TW', names: ['�?��店�?标成TW'] },
      ],
      { site_code: 'PH', store_id: 'ph-missing', store_name: '�?���?' },
    ),
    (error) => error
      && error.reason === 'cnsc_target_site_not_found'
      && Array.isArray(error.details.available)
      && error.details.available.length === 1
      && Array.isArray(error.details.all_candidates)
      && error.details.all_candidates.length === 2
      && error.details.all_candidates.some((candidate) => candidate.site_code === 'TW'),
  );
});

test('selectCNSCTargetSites attaches the live picker DOM snapshot when matching fails', async () => {
  const html = '<div id="shops-wap"><div class="site" data-site-code="PH">菲律�?海宁�?/div></div>';
  const picker = pickerMock([{
    title: '菲律�?',
    attrs: { 'data-site-code': 'PH' },
    shops: [{ text: '另一家店', attrs: { 'data-shop-id': 'ph-9', 'data-shop-name': '另一家店' } }],
  }], { snapshot: html });
  const frame = { locator(selector) { return selector === '#shops-wap' ? picker : picker.empty; } };
  const task = {
    store: {
      listing_mode: 'cnsc_global',
      target_site_stores: [{ site_code: 'PH', store_id: 'ph-missing', store_name: '�?���?' }],
    },
  };
  await assert.rejects(
    executor.selectCNSCTargetSites(frame, task),
    (error) => error
      && error.reason === 'cnsc_target_site_not_found'
      && typeof error.details.picker_snapshot === 'string'
      && error.details.picker_snapshot.includes('shops-wap'),
  );
});

test('CNSC site price plan carries per-store activity, inherited 40 percent, and SKU prices', () => {
  const task = {
    store: {
      listing_mode: 'cnsc_global',
      default_site_discount_percent: 40,
      target_site_stores: [{ site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW', mabang_discount_id: 'discount-40', discount_name: '秋季 40%' }],
    },
    sku_grid: {
      expected_rows: 1,
      skus: [{ sku_code: '11991_A||XS', color_code: 'A', size: 'XS', site_price_matrix: { TW: { original_price: 372, sale_price: 223.2 } } }],
    },
  };

  assert.deepEqual(executor.buildCNSCSitePricePlan(task), [{
    site_code: 'TW',
    store_id: 'tw-1',
    store_name: '海宁TW',
    mabang_discount_id: 'discount-40',
    discount_name: '秋季 40%',
    discount_percent: 40,
    rows: [{ seller_sku: '11991_A||XS', color: 'A', size: 'XS', original_price: 372, sale_price: 223.2 }],
  }]);
});

test('CNSC discount execution follows page site headers instead of template array order', () => {
  const templatePlan = [
    { site_code: 'TW', mabang_discount_id: 'tw-discount' },
    { site_code: 'PH', mabang_discount_id: 'ph-discount' },
  ];

  assert.deepEqual(
    executor.orderCNSCSiteDiscountPlan(['PH', 'TW'], templatePlan),
    [templatePlan[1], templatePlan[0]],
  );
  assert.throws(
    () => executor.orderCNSCSiteDiscountPlan(['PH'], templatePlan),
    (error) => error && error.reason === 'cnsc_site_discount_header_site_mismatch',
  );
});

test('CNSC site price readback uses displayed precision and blocks wrong discount values', () => {
  const plan = [{
    site_code: 'PH', discount_percent: 40,
    rows: [{ seller_sku: '11991_A||XS', color: 'A', size: 'XS', original_price: 1243, sale_price: 746 }],
  }];
  assert.deepEqual(executor.validateCNSCSitePriceReadback({
    sites: [{ site_code: 'PH', activity_id: 'discount-ph', rows: [{ seller_sku: '11991_A||XS', original_price: '1243', sale_price: '746', discount_percent: '40' }] }],
  }, plan, [{ site_code: 'PH', mabang_discount_id: 'discount-ph' }]), { ok: true, sites: 1, rows: 1, mismatches: [] });

  const invalid = executor.validateCNSCSitePriceReadback({
    sites: [{ site_code: 'PH', activity_id: 'discount-ph', rows: [{ seller_sku: '11991_A||XS', original_price: '1243', sale_price: '800', discount_percent: '40' }] }],
  }, plan, [{ site_code: 'PH', mabang_discount_id: 'discount-ph' }]);
  assert.equal(invalid.ok, false);
  assert.equal(invalid.reason, 'cnsc_site_price_mismatch');
});

test('CNSC activity verification accepts the selected activity read from the page header', () => {
  const plan = [{
    site_code: 'TW', discount_name: '秋季 40%', discount_percent: 40,
    rows: [{ seller_sku: '11991_A||XS', color: 'A', size: 'XS', original_price: 372, sale_price: 223.2 }],
  }];
  const snapshot = {
    sites: [{
      site_code: 'TW', activity_id: '', activity_name: '折扣活动 秋季 40% 批量应用',
      rows: [{ seller_sku: '11991_A||XS', original_price: '372', sale_price: '223.2', discount_percent: '40' }],
    }],
  };

  assert.deepEqual(
    executor.validateCNSCSitePriceReadback(snapshot, plan, [{ site_code: 'TW', mabang_discount_id: 'tw-discount', discount_name: '秋季 40%' }]),
    { ok: true, sites: 1, rows: 1, mismatches: [] },
  );
});

test('multi-select enum click re-reads checked state before clicking and closes the panel after verify', () => {
  // 2026-09-27 真机：重试�?用同�?详情页（openListingWorkspace 先�?用已有�?情页），
  // 上一�?��下的已勾选�?选项�?��点一�?�?toggle 切掉 �?ON→OFF �?���?
  // select_value_not_verified �?��打在 材料/领口 上�?��?�自�??�项」分�?��有同款保护，
  // 枚举点击分支也必须有：点击前读勾选�?�，已勾选不点；多�?�成功后收起面板�?
  // 避免后续属�?�在多个�??面板间�?义�??
  // 收面板的方式后续又纠正过�?次：点触发�?头会把�?选待提交值丢掉（现场实证），
  // �?��改走 closeOpenSelectPanels（Esc），见�?�本土类�?��性：多�?�下拉用 Esc 收起」�??
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function selectElementOption('), source.indexOf('async function selectElementOptionOrCustom'));
  assert.match(fn, /selectionEvidenceMatches\(preTexts, value, optionClass, optionAriaSelected\)/, '点击前�?选项勾�??class');
  assert.match(fn, /aria-selected/, '点击前回读 aria-selected');
  assert.match(fn, /selectionEvidenceMatches\(preTexts, value, optionClass, optionAriaSelected\)/, '点击前回读标签和勾选 class');
  assert.match(fn, /if \(!alreadyPicked\)\s*\{[\s\S]*?await optionLocators\.first\(\)\.click\(\{ timeout: 5000 \}\);/, '已勾选就不再点');
  assert.match(fn, /selectionEvidenceMatches\(actualTexts, value, refreshedClass, refreshedAriaSelected\)/, '验证接受勾选 class 作为选中证据');
  assert.match(fn, /refreshedAriaSelected/, '验证接受 aria-selected 作为�?��证据');
  assert.match(fn, /�??选择/, '多�?�提交前点击�??选择');
  assert.match(fn, /el-select__tags/, '多�?�（tags 控件）才收起面板');
  assert.match(fn, /closeOpenSelectPanels\(frame, panelItem, options\)/, '成功后按策略收起面板');
});

test('selected readonly shop select can read the selected label from its placeholder', async () => {
  const input = {
    async getAttribute(name) {
      if (name === 'readonly') return '';
      if (name === 'placeholder') return 'HN_POD';
      return null;
    },
    async inputValue() { return ''; },
  };
  const item = {
    locator(selector) {
      if (selector === executor.CONTROL_INPUT_SELECTOR) return { count: async () => 1, first: () => input };
      if (selector === '.el-select') return { count: async () => 1 };
      return { allInnerTexts: async () => [] };
    },
  };
  assert.deepEqual(await executor.selectedTextsForItem(item), ['HN_POD']);
});

test('detail page reuse is limited to the requested listing mode', () => {
  assert.equal(typeof executor.detailPageMatchesTask, 'function');
  assert.equal(
    executor.detailPageMatchesTask(
      'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17&global=1',
      { store: { listing_mode: 'cnsc_global' } },
    ),
    true,
  );
  assert.equal(
    executor.detailPageMatchesTask(
      'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17&global=1',
      { store: { listing_mode: 'site' } },
    ),
    false,
  );
  assert.equal(
    executor.detailPageMatchesTask(
      'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
      { store: { listing_mode: 'site' } },
    ),
    true,
  );
});

test('selected option evidence accepts the committed label or Element UI selected state', () => {
  assert.equal(typeof executor.selectionEvidenceMatches, 'function');
  assert.equal(executor.selectionEvidenceMatches(['棉花 (Cotton)'], '棉花 (Cotton)', '', ''), true);
  assert.equal(executor.selectionEvidenceMatches([], '棉花 (Cotton)', 'el-select-dropdown__item selected hover', ''), true);
  assert.equal(executor.selectionEvidenceMatches([], '棉花 (Cotton)', '', 'true'), true);
  assert.equal(executor.selectionEvidenceMatches([], '棉花 (Cotton)', 'el-select-dropdown__item hover', 'false'), false);
});

test('category picker waits for the asynchronous dialog after clicking 选择类目', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function selectCategory('), source.indexOf('const ATTRIBUTE_LABELS'));
  assert.match(fn, /dialogDeadline/);
  assert.match(fn, /while/);
});

test('�?��刊登导航停在待发布商品，不切到线上商品的「站点商品�?�页�?', () => {
  // 现场�?026-09-27，CDP 9223，本�?HN_POD 任务 71181e90）：
  //   待发布商�?�?草�?�?定时任务/刊登�?�?+ 「新增刊登�??
  //   站点商品   �?正在�?�?已售�?已下�?已�?�?疑似删除，没有�?�新增刊登�??
  // 错�?实现让本土分�?��「站点商品�?�，于是 findFrameWithExactText('新�?刊登') 读到 0�?
  // �?��作业 3 �?attempt 全部�?workspace_frame_not_unique 阻断�?
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function openListingWorkspace('), source.indexOf('function actionableCNSCTargets'));
  assert.doesNotMatch(fn, /localTab/);
  assert.doesNotMatch(fn, /TEXT\.localPending/);
  assert.equal(executor.TEXT.localPending, undefined);
  // �?��仍走 待发布商�?�?新�?刊登；跨境仍�?CNSC待发布商品（�??不改跨�?行为）�??
  assert.match(fn, /findFrameWithExactText\(page, TEXT\.pending\)/);
  assert.match(fn, /TEXT\.cnscPending/);
});

test('�?��页�?切换后等待新增刊登按�?��步出�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function openListingWorkspace('), source.indexOf('function actionableCNSCTargets'));
  assert.match(fn, /newListingDeadline/);
  assert.match(fn, /while \(Date\.now\(\) < newListingDeadline\)/);
});

test('面板收起策略：本土用 Esc，跨境沿用触发�?�?', () => {
  assert.equal(typeof executor.panelCloseStrategy, 'function');
  assert.equal(executor.panelCloseStrategy({ siteMultiSelectHandling: true }), 'escape');
  assert.equal(executor.panelCloseStrategy({ loose: true }), 'arrow');
  assert.equal(executor.panelCloseStrategy(), 'arrow');
});

test('多�?�的值只认控件自�?��签，不�?选项行的 selected �?', () => {
  // 现场 2026-09-27：控件里�?�?��签都没有，�?点过的那�?行照样带 selected �?
  // 诊断兜底判成「已验证」，阶�?�?count=15，表单里 材料/图�?/风格/型号 全空�?
  assert.equal(typeof executor.tagBasedEvidence, 'function');
  assert.equal(executor.tagBasedEvidence(['棉花 (Cotton)'], '棉花 (Cotton)'), true);
  assert.equal(executor.tagBasedEvidence(['棉花 (Cotton)', '羊毛 (Fleece)'], '羊毛 (Fleece)'), true);
  assert.equal(executor.tagBasedEvidence([], '棉花 (Cotton)'), false);
  assert.equal(executor.tagBasedEvidence(['羊毛 (Fleece)'], '棉花 (Cotton)'), false);
  assert.equal(executor.tagBasedEvidence(undefined, '棉花 (Cotton)'), false);

  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const selectFn = source.slice(source.indexOf('async function selectElementOption('), source.indexOf('async function selectElementOptionOrCustom('));
  // �?��多�?�分�?��先按控件标�?�?alreadyPicked，再走专门的提交�?���?
  assert.match(selectFn, /const tagBasedSite = panelCloseStrategy\(options\) === 'escape'/, '�?��多�?��?识别出来');
  assert.match(selectFn, /alreadyPicked = tagBasedSite\s*\n?\s*\? tagBasedEvidence\(preTexts, value\)/, '多�?�只用标签当证据');
  assert.match(selectFn, /clickOptionUntilTagCommitted\(frame, item, labels, value, optionLocators, options\)/, '点不落�?��?重试提交');
  assert.match(selectFn, /method: 'element_ui_tag'/, '成功�?��打标签证�?��方法�?');
  // 三�?都提交不上必须显式失败，不能再静默算成功�?
  assert.match(selectFn, /if \(!committed\.value\) \{[\s\S]*?throw new BlockedError\('select_value_not_verified'/, '提交不上要报�?');
});

test('executor 有收尾模式：超时�?��后只关残留�?情页（保留工作区）并回报成功弹窗�?', () => {
  // 现场 2026-09-27 22:21（任�?45fff5a5）：保存其实成功了（页面上�?�刊登信�?��存成功�?�）�?
  // �?worker �?executor 预算 480s 正好到点，node �?taskkill，close_saved_detail_page
  // 没跑�?�?页面连成功弹窗一起留在浏览器里�?�runner 会用这个模式补一次收尾�??
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /if \(argv\[index\] === '--close-detail-pages'\) result\.closeDetailPages = true;/, 'CLI �?�?');
  const fn = source.slice(source.indexOf('async function closeLeftoverDetailPages('), source.indexOf('async function run()'));
  assert.match(fn, /productDetailShopee/, '�??详情�?');
  assert.match(fn, /TEXT\.savedSuccess/, '回报带着「保存成功�?�弹窗的页面�?');
  assert.match(fn, /page\.close\(\{ runBeforeUnload: false \}\)/, '关掉残留详情�?');
  assert.match(fn, /stage: 'close_leftover_detail_pages'/, '留下�??计的 stage 事件');

  const runFn = source.slice(source.indexOf('async function run()'));
  assert.match(runFn, /if \(args\.closeDetailPages\) \{/, '收尾模式要在读任务�?划（stdin）之前返�?');
  assert.match(runFn, /status: 'closed_detail_pages'/, '返回结构化结�?');
});

test('视�?库：搜索词写进真正的搜索框（不是日期框），搜索类型用 DOM 点击切换', () => {
  // 现场 2026-09-27（任�?45fff5a5）：工具栏�?�?�?���??输入框是「上传开始日期�?�，
  // 原实现把视�?ID写进那里 �?搜索从未过滤，列表永远是�?1 页那 10 �?�?
  // video_select_failed:row_not_found（真机上那条视�?其实在库里，�?ID 搜得到）�?
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const typeFn = source.slice(source.indexOf('async function typeVideoSearchValue('), source.indexOf('// 视频库的搜索类型下拉'));
  assert.match(typeFn, /\\u4e0a\\u4f20\(\\u5f00\\u59cb\|\\u7ed3\\u675f\)\\u65e5\\u671f/, '跳过上传�?�?结束日期两个�?');
  assert.match(typeFn, /\\u6761\\\/\\u9875/, '跳过分页的�?�条/页�?��?');
  assert.match(typeFn, /norm\(node\.placeholder\) === '\\u8bf7\\u8f93\\u5165'/, '优先 placeholder=请输�?的搜索�?');
  assert.match(typeFn, /HTMLInputElement\.prototype, 'value'/, '原生 setter 写�?�（实测能触发过滤）');
  assert.match(typeFn, /dispatchEvent\(new Event\('input'/, '派发 input 事件');

  const switchFn = source.slice(source.indexOf('async function switchVideoSearchType('), source.indexOf('// 库里没匹配上时回报现场'));
  assert.match(switchFn, /dialog\.querySelector\('\.el-select'\)/, '找工具栏的搜索类型下�?');
  assert.match(switchFn, /\.el-input__suffix/, 'DOM 点开下拉（Playwright click 会�?整屏 overlay 挡住�?');
  assert.match(switchFn, /el-select-dropdown/, '在可�?popper 里点选项');
  assert.match(switchFn, /return optionClicked && now === compactText\(label\)/, '切换后回读确�?');

  const idFn = source.slice(source.indexOf('async function selectTemplateVideoById('), source.indexOf('async function uploadProductVideo('));
  assert.match(idFn, /await switchVideoSearchType\(frame, VIDEO_TEXT\.idSearchType\)/, '先切到�?��?频ID�?');
  assert.match(idFn, /await typeVideoSearchValue\(frame, videoId\)/, '搜索词写进搜索�?');
  assert.match(idFn, /pickVideoLibraryRow\(frame, \{[\s\S]*?wantedId: videoId/, '�?videoId 找�?');
  assert.match(idFn, /videoTitle/, '带主 SKU 作为标�?兜底');
  assert.match(idFn, /videoLibrarySummary\(frame, videoId, videoTitle\)/, '失败时回报现场（搜了�?�?库里共�?少条�?');
});

test('视频库查找有界：不再每次 30s 默认超时拖满 12 分钟', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /const VIDEO_SEARCH_ATTEMPTS = 4;/, '重试次数收敛�?4');
  const pickFn = source.slice(source.indexOf('async function pickVideoLibraryRow('), source.indexOf('// 把搜索词写进视�?库搜索�?'));
  assert.match(pickFn, /for \(let i = 0; i < VIDEO_SEARCH_ATTEMPTS/, '按常量收�?');
  assert.match(pickFn, /clickVideoAdminSearch\(frame\)/, '搜索按钮�?DOM click');
  assert.doesNotMatch(pickFn, /\.click\(\)\.catch\(\(\) => \{\}\);\s*\n\s*await sleep\(3000\)/, '不再 3s �?�?���?');
});

test('执�?器把进度实时回报给本�?API（面板实时�?骤），无 runtime 时完全不发�?�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /let progressRuntime = null;/, '默�?没有上报上下�?');
  assert.match(source, /function setProgressRuntime\(runtime\) \{/, '运�?上下文入�?');
  assert.match(source, /if \(!runtime \|\| !runtime\.job_id \|\| !runtime\.lease_token\) return;/, '�?job_id/租约时不上报（手动干跑�?为不变）');
  assert.match(source, /setProgressRuntime\(task\.runtime\);/, '读入任务计划后�?�?��下文');
  const emitFn = source.slice(source.indexOf('function emit(event, data = {}) {'), source.indexOf('function cleanText(value)'));
  assert.match(emitFn, /process\.stdout\.write\(EVENT_MARKER/, '仍然照旧�?stdout（runner 靠它解析完整步�?�?');
  assert.match(emitFn, /reportProgress\(event, data\);/, '同时实时回报');
  const reportFn = source.slice(source.indexOf('function reportProgress(event, payload) {'), source.indexOf('function emit(event, data = {}) {'));
  assert.match(reportFn, /\/api\/automation\/jobs\/\$\{progressRuntime\.jobId\}\/progress/, '回传�?��');
  assert.match(reportFn, /AbortSignal\.timeout\(8000\)/, '单条上报有超�?');
  assert.match(reportFn, /\.catch\(\(\) => \{/, '上报失败不影响执�?');
});

test('每个预保存阶段都记录耗时（定�?看着卡住"的那�?步）', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function runPreSaveAttempt('), source.indexOf('// Close every open detail form page'));
  assert.match(fn, /const stageStartedAt = Date\.now\(\)/, '阶�?�?始打�?');
  assert.match(fn, /phase: 'stage_timing', stage: failedStage, attempt/, '按阶段名记录');
  assert.match(fn, /ms: Date\.now\(\) - stageStartedAt, ok: true/, '成功也�?耗时');
  assert.match(fn, /ms: Date\.now\(\) - stageStartedAt, ok: false/, '失败也�?留下耗时与错�?��');
  assert.match(fn, /throw error;/, '失败仍然抛给上层（不改变重试�?���?');
});

test('�?��属�?�一律先写页面表单模型（�?��枚举�?��值），跨境不参与', () => {
  // 现场：�?�?单�?�的点�?��?�只停在"待提�?态（12:52:28 有标�?�?12:52:29 收面板就空了），
  // 而且每个属�?�白�?~60s。模型（attributesNew[i].value）才�?���?��存的数据源，
  // 非枚举�?�也进得去（原产�?Philippines 实测写入并保持）�?
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /async function applyAttributeValueInModel\(frame, fieldId, values\)/, '要有写模型的辅助函数');
  assert.match(source, /storeCategory\.ProductAttributeInfo\.attributesNew/, '写的�?��面表单模�?');
  assert.match(source, /custom: resolved\.some\(\(entry\) => entry\.from_options === false\)/, '非枚举自�??��?标�?出来');
  assert.match(source, /reason: 'attribute_not_found'/, '找不到属性�?如实报错');

  const fn = source.slice(source.indexOf('async function setCategoryAttributes('), source.indexOf('async function fillBasicInfo('));
  // 批量写：�?�?evaluate 写完全部属�?�，�?��里只做渲染校验（原来逐个�?= 15 次往�?30.4s）�??
  assert.match(fn, /const outcome = await applyAttributeValuesInModel\(frame, batch\)/, '�?次批量写模型');
  assert.match(fn, /const prewritten = new Map\(\)/, '记住批量写入的结�?');
  assert.match(fn, /phase: 'model_batch_write'/, '批量写入留�?�时日志');
  assert.match(fn, /const applied = prewritten\.get\(String\(field\.field_id\)\) \|\| await applyAttributeValueInModel\(frame, field\.field_id, candidateValues\)/, '命中批量写入就不再单�?��');
  assert.match(fn, /method: applied\.custom \? 'model_custom' : 'model'/, '�?��值与枚举值分�?记方法名');
  assert.match(fn, /const rendered = await attributeValueRendered\(frame, labels, expected\)/, '写完必须�?UI 渲染�??');
  assert.doesNotMatch(fn, /tagBasedControl/, '不再�??多�?�生效（单�??文本同样走模型）');
});

test('模型写入后校验渲染：多�?�只认标签，单�??文本才�?控件回�?�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const verifyFn = source.slice(source.indexOf('async function attributeValueRendered('), source.indexOf('// 往马帮的自填/搜索输入框写值'));
  assert.match(verifyFn, /const tagBased = \(await item\.locator\('\.el-select__tags'\)\.count\(\)/, '先看�?���??选控�?');
  assert.match(verifyFn, /tagBased \? await tagTextsForItem\(item\) : await selectedTextsForItem\(item\)/, '多�?�只认标签，单�?��?回�?�?');
  assert.match(verifyFn, /const rawInput = tagBased/, '文本控件才比 input 里的显示�?');
  assert.match(verifyFn, /const deadline = Date\.now\(\) \+ timeoutMs/, '渲染等待有截止时�?');
});

test('多�?�提交循�?��点前等面板稳定，点后等异步提交再收面板，收完还�?复核', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const commitFn = source.slice(source.indexOf('async function clickOptionUntilTagCommitted('), source.indexOf('async function closeOpenSelectPanels('));
  assert.match(commitFn, /await waitForSettledSelectPanel\(frame\)/, '点击前等面板稳定');
  assert.match(commitFn, /const commitDeadline = Date\.now\(\) \+ 3000/, '点完给马�?��异�?提交留时�?');
  assert.match(commitFn, /await closeOpenSelectPanels\(frame, /, '提交落定后才收面�?');
  assert.match(commitFn, /const settledTags = await tagTextsForItem\(settled\)/, '收面板后复核标�?还在不在');
  assert.match(commitFn, /if \(tagBasedEvidence\(settledTags, value\)\) return/, '�?��复核通过才算成功');
  assert.match(commitFn, /clicks >= maxClicks/, '点击次数有上�?');
  assert.match(commitFn, /await option\.click\(\{ timeout: 5000 \}\)\.catch\(\(\) => \{\}\)/, '点击要有显式超时且不炸掉整单');

  const tagFn = source.slice(source.indexOf('async function tagTextsForItem('), source.indexOf('// 多选（tags 控件）的取值判定'));
  assert.match(tagFn, /\.el-select__tags \.el-tag, \.el-select__tags-text/, '�??控件�?��的标签元�?');
  assert.doesNotMatch(tagFn, /placeholder|inputValue/, '不能�?input/placeholder 回显当证�?');

  const settleFn = source.slice(source.indexOf('async function waitForSettledSelectPanel('), source.indexOf('async function clickOptionUntilTagCommitted('));
  assert.match(settleFn, /stablePolls = 2/, '连续两次一致才叫稳定');
  assert.match(settleFn, /el-select-dropdown:visible/, '看的是下拉面板可见性');
});

test('类目属性填写：多选下拉用 Esc 收起（点触发器收会把刚选的值丢掉）', () => {
  // 现场实证�?026-09-27，CDP 9223，本�?HN_POD 详情页�?�材料�?�）�?
  //   点�?�项 �?tags=["棉花 (Cotton)"]；点触发�?��关闭 �?tags=[]（�?�丢了）�?
  //   点�?�项 �?Esc 关闭 �?tags 保留。所以本土收面板必须�?closeOpenSelectPanels�?
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const closeFn = source.slice(source.indexOf('async function closeOpenSelectPanels('), source.indexOf('// 往马帮的自填/搜索输入框写值'));
  assert.match(closeFn, /press\('Escape'\)/, 'Esc 关闭保留多�?�待提交�?');
  assert.match(closeFn, /el-select \.el-input__suffix/, '跨�?/单�?�仍�???回触发�?�?');
  assert.match(closeFn, /panelCloseStrategy/, '按策略分流，不用两�?�?���?');

  const selectFn = source.slice(source.indexOf('async function selectElementOption('), source.indexOf('async function selectElementOptionOrCustom('));
  // 成功�?��（稳定回�?+ 诊断兜底）都要收面板，且不再直接点触发�?头�??
  assert.doesNotMatch(selectFn, /const closeArrow = panelItem/, '不允许再按�?�办法点�?��收�?�?');
  assert.match(selectFn, /await closeOpenSelectPanels\(frame, panelItem, options\)/, '稳定回�?成功后按策略收面�?');
  assert.match(selectFn, /await closeOpenSelectPanels\(frame, item, options\)/, '诊断兜底/�??�确认路径也要收面板');
  assert.match(selectFn, /await closeOpenSelectPanels\(frame, null, options\)/, '起手先清掉上�?格残留的面板');

  const customFn = source.slice(source.indexOf('async function addCustomSelectOption('), source.indexOf('async function selectElementOption('));
  assert.match(customFn, /closeOpenSelectPanels\(frame, /);
});

test('类目属�?�下拉点击有显式�?��时，不再�?30s 默�?值空�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const selectFn = source.slice(source.indexOf('async function selectElementOption('), source.indexOf('async function selectElementOptionOrCustom('));
  assert.doesNotMatch(selectFn, /arrow\.click\(\);/, '触发�?��点击必须给超�?');
  assert.doesNotMatch(selectFn, /\.first\(\)\.click\(\);/, '选项点击必须给超�?');
  assert.match(selectFn, /arrow\.click\(\{ timeout: 5000 \}\)/);
  assert.match(selectFn, /optionLocators\.first\(\)\.click\(\{ timeout: 5000 \}\)/);
});

test('�?��现场属�?��?取等到类�?��性定义加载完再返�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const waitFn = source.slice(source.indexOf('async function readLiveCategoryAttributesWhenReady('), source.indexOf('// 将店铺模板保存的数字属�?��?�映�?'));
  assert.match(waitFn, /const deadline = Date\.now\(\) \+ timeoutMs/);
  assert.match(waitFn, /await readLiveCategoryAttributes\(frame\)/);
  // �?��等待、跨境单次�?�?
  assert.match(source, /const live = siteMode\s*\n?\s*\? await readLiveCategoryAttributesWhenReady\(frame\)/);
  assert.match(source, /: await readLiveCategoryAttributes\(frame\)/);
  // �?�� matchOptions 带上多�?�收面板策略
  assert.match(source, /siteMode \? \{ loose: true, siteMultiSelectHandling: true \} : \{\}/);
});

test('CNSC site price readback pairs activity headers by page site order, not plan index', () => {
  // 2026-09-27 真机：页面列序（TW PH SG TH MY MX VN BR）≠ 任务 target 顺序
  // （BR MX MY PH SG TH TW VN），�?plan 下标配�?把别站的活动读过�?�?
  // BR/MX/TW/VN activity_mismatch（MY/PH/SG/TH �?40% 名字兜底碰巧放过）�??
  // 回�?必须�?siteDiscountRegions 同一套配对：站点组表头文�?站点码，按码找下标�??
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const readbackBody = source.slice(source.indexOf('async function readCNSCSitePriceSnapshot'), source.indexOf('async function fillCNSCSitePricesAndDiscounts'));
  assert.match(readbackBody, /\/\^\(TW\|PH\|SG\|TH\|MY\|MX\|VN\|BR\)\$\//, '回�?要收集页面站点组表头（与 siteDiscountRegions 同一正则�?');
  assert.match(readbackBody, /pageSiteCodes\.indexOf\(siteCode\)/, '按站点码在页面列序里的下标取活动表头');
  assert.doesNotMatch(readbackBody, /activityHeaders\[sites\.indexOf\(site\)\]/, '不允许再�?plan 下标配�?');
});

test('CNSC auto-publish toggle is disjoint from every publish action label', () => {
  // enableCNSCAutoPublish only checks the "上架后自动发布到站点店铺" checkbox; it must
  // never coincide with a publish button so the publish guard can never mistake it
  // for a publish action. The run still only saves a draft.
  const normalize = (value) => String(value || '').replace(/\s+/g, '');
  const toggle = normalize(executor.TEXT.autoPublishToSites);
  assert.equal(executor.PUBLISH_TEXTS.some((label) => normalize(label) === toggle), false);
  assert.equal(executor.PUBLISH_TEXTS.some((label) => toggle.endsWith(normalize(label))), false);
});

test('enableCNSCAutoPublish toggles via the visible el-checkbox label, never check() on the hidden input', async () => {
  // Regression: Mabang renders this checkbox with Element UI, which keeps the native
  // input at opacity:0 �?check() timed out with "element is not visible" on the real
  // page. The mock deliberately makes check() succeed so a regression to check()
  // shows up as checkCalls > 0 instead of silently passing.
  const makeFrame = (initialState, options = {}) => {
    let checked = initialState;
    const events = { labelClicks: 0, checkCalls: 0, inputClicks: 0 };
    const toggle = {
      count: async () => 1,
      isChecked: async () => checked,
      check: async () => { events.checkCalls += 1; checked = true; },
      click: async () => { events.inputClicks += 1; },
      locator: () => ({
        count: async () => (options.labels ?? 1),
        click: async () => {
          events.labelClicks += 1;
          if (options.labelToggles) checked = true;
        },
      }),
    };
    return { frame: { getByRole: () => toggle }, events };
  };
  const task = { store: { listing_mode: 'cnsc_global' } };
  const rejects = (promise, reason) => assert.rejects(promise, (error) => error.reason === reason);

  const unchecked = makeFrame(false, { labelToggles: true });
  assert.deepEqual(await executor.enableCNSCAutoPublish(unchecked.frame, task), { checked: true });
  assert.equal(unchecked.events.labelClicks, 1);
  assert.equal(unchecked.events.checkCalls, 0);
  assert.equal(unchecked.events.inputClicks, 0);

  const checked = makeFrame(true, { labelToggles: true });
  assert.deepEqual(await executor.enableCNSCAutoPublish(checked.frame, task), { checked: true });
  assert.equal(checked.events.labelClicks, 0);
  assert.equal(checked.events.checkCalls, 0);

  await rejects(executor.enableCNSCAutoPublish(makeFrame(false).frame, task), 'cnsc_auto_publish_not_enabled');
  await rejects(
    executor.enableCNSCAutoPublish(makeFrame(false, { labels: 0 }).frame, task),
    'cnsc_auto_publish_toggle_not_found',
  );

  let touched = false;
  const skipped = { getByRole: () => { touched = true; } };
  assert.deepEqual(await executor.enableCNSCAutoPublish(skipped, { store: { listing_mode: 'shopee' } }), { skipped: true });
  assert.equal(touched, false);
});

test('discount display tolerance mirrors the backend 0.5*10^-decimals rule', () => {
  // Parity with services/api/storage.py _cnsc_configuration_issues (%OFF tolerance):
  // half a unit at the displayed precision, so a value passing the backend gate never
  // trips the executor's cnsc_site_discount_price_mismatch check.
  const expected = (value) => {
    const text = String(value ?? '').trim();
    const decimals = text.includes('.') ? text.split('.')[1].length : 0;
    return 0.5 * (10 ** -decimals) + Number.EPSILON;
  };
  for (const value of ['746', '223.2', '19.20', '1243', '0.5']) {
    assert.equal(executor.discountDisplayTolerance(value), expected(value));
  }
});

test('CNSC DOM grid plan maps grouped VXE headers to editable body columns', () => {
  const headers = [
    { className: 'vxe-header--column col_3 col--group', text: ' SKU ' },
    { className: 'vxe-header--column col_5 col--group', text: '* 全球商品价格' },
    { className: 'vxe-header--column col_9 col--group', text: '*泰国仓库存' },
    { className: 'vxe-header--column col_11 col--group', text: '*广州仓库存' },
    { className: 'vxe-header--column col_13 col--group', text: '*菲律宾仓库存' },
  ];
  const grid = {
    expected_rows: 1,
    skus: [{ sku_code: '11991_A||XS', global_price_cny: 19.2, warehouse_stocks: { TH: 300, CN_GZ: 0, PH: 0 } }],
  };
  const plan = executor.buildCNSCDomGridPlan(grid, headers, ['col_4', 'col_6', 'col_10', 'col_12', 'col_14'], 1);

  assert.deepEqual(plan, {
    ok: true,
    rows: 1,
    columns: { sku: 'col_4', global_price: 'col_6', TH: 'col_10', CN_GZ: 'col_12', PH: 'col_14' },
  });
  assert.deepEqual(
    executor.buildCNSCDomGridPlan(grid, headers, ['col_4', 'col_6', 'col_10', 'col_12'], 1),
    { ok: false, reason: 'cnsc_required_columns_missing', missing: ['PH'] },
  );
});

test('CNSC DOM grid readback blocks changed SKU, price, or warehouse values', () => {
  const headers = [
    { className: 'col_3', text: 'SKU' },
    { className: 'col_5', text: '全球商品价格' },
    { className: 'col_9', text: '泰国仓库存' },
    { className: 'col_11', text: '广州仓库存' },
    { className: 'col_13', text: '菲律宾仓库存' },
  ];
  const grid = {
    expected_rows: 1,
    skus: [{ sku_code: '11991_A||XS', global_price_cny: 19.2, warehouse_stocks: { TH: 300, CN_GZ: 0, PH: 0 } }],
  };
  const snapshot = {
    headers,
    body_column_ids: ['col_4', 'col_6', 'col_10', 'col_12', 'col_14'],
    rows: [{ col_4: '11991_A||XS', col_6: '19.2', col_10: '300', col_12: '0', col_14: '0' }],
  };

  assert.deepEqual(executor.validateCNSCDomGridReadback(snapshot, grid), { ok: true, rows: 1, mismatches: [] });
  snapshot.rows[0].col_12 = '5';
  assert.deepEqual(executor.validateCNSCDomGridReadback(snapshot, grid), {
    ok: false,
    reason: 'cnsc_value_mismatch',
    rows: 1,
    mismatches: [{ row_index: 0, fields: ['CN_GZ'] }],
  });
});

test('watermark selection requires one matching id and verified name', () => {
  const catalog = [
    { template_id: 'wm-1', name: 'Main watermark' },
    { template_id: 'wm-2', name: 'Product watermark' },
  ];
  assert.deepEqual(
    executor.resolveWatermarkTemplate(catalog, { template_id: 'wm-2', name: 'Product watermark' }),
    catalog[1],
  );
  assert.equal(
    executor.resolveWatermarkTemplate(catalog, { template_id: 'wm-2', name: 'Wrong name' }),
    null,
  );
});

test('media stage failures become independent warnings', async () => {
  const result = await executor.runOptionalMediaStages({}, {
    watermarks: {
      main_image: { template_id: 'main', name: 'Main', image_positions: [1] },
      product_images: { template_id: 'product', name: 'Product', image_positions: [2, 3] },
    },
    video: { action_status: 'ready', upload_path: 'product.mp4' },
  }, {
    applyWatermark: async (_frame, role) => {
      if (role === 'main_image') throw new Error('main failed');
      return { status: 'applied' };
    },
    uploadVideo: async () => { throw new Error('video failed'); },
  });

  assert.equal(result.apply_main_image_watermark.status, 'warning');
  assert.equal(result.apply_product_image_watermark.status, 'applied');
  assert.equal(result.video_upload.status, 'warning');
  assert.equal(result.warnings.length, 2);
});

test('media stage results carry ok so the step list stops painting the video as pending', async () => {
  // 现场：模板默认视频其实附加成功了（status=attached），但执行步骤只认 ok，
  // 于是「商品视频」永远显示成未完成。这里钉住 status→ok 的折算。
  const attached = await executor.runOptionalMediaStages({}, { video: { default_video_id: 'v1', select_by_id: true } }, {
    applyWatermark: async () => ({ status: 'skipped', code: 'watermark_unconfigured' }),
    uploadVideo: async () => ({ status: 'attached', video_id: 'v1', source: 'template_default' }),
  });
  assert.equal(attached.video_upload.ok, true);
  // 跳过（没配视频/水印）不是失败，保持 null，由界面按「已跳过」渲染。
  assert.equal(attached.apply_main_image_watermark.ok, null);

  const skipped = await executor.runOptionalMediaStages({}, { video: {} }, {
    applyWatermark: async () => ({ status: 'applied' }),
    uploadVideo: async () => ({ status: 'skipped', code: 'video_missing' }),
  });
  assert.equal(skipped.video_upload.ok, null);
  assert.equal(skipped.video_upload.status, 'skipped');

  const warned = await executor.runOptionalMediaStages({}, { video: {} }, {
    applyWatermark: async () => ({ status: 'applied' }),
    uploadVideo: async () => ({ status: 'warning', code: 'video_store_not_found' }),
  });
  assert.equal(warned.video_upload.ok, false);
});

// ---------------------------------------------------------------------------
// Watermark interaction mock �?mirrors the REAL Mabang markup (read from publish-ui
// main.js on 2026-10-01): an el-dropdown (trigger=click) whose rows are
// .el-dropdown-menu__item carrying only the template name (no id attribute), plus an
// image grid where each card has an el-checkbox and applying a watermark only touches
// the CHECKED images (and swaps their <img src> when done).
// ---------------------------------------------------------------------------
function watermarkFrameMock({ options, triggerOpen = false, triggerText = '', imageCount = 4, checked = [] }) {
  const events = { hovers: 0, clicks: 0, optionClicks: [], escapes: 0, checkboxClicks: [] };
  const frozen = new Set(); // 模拟"点了没反�?的图
  let open = triggerOpen;
  let selectedName = '';
  const sources = Array.from({ length: imageCount }, (_, index) => `https://cdn/img-${index + 1}.png`);
  const empty = {
    async count() { return 0; },
    async isVisible() { return false; },
    async getAttribute() { return null; },
    async innerText() { return ''; },
    async isChecked() { return false; },
    async click() {},
    async hover() {},
    first() { return empty; },
    nth() { return empty; },
    locator() { return empty; },
  };
  const boxes = Array.from({ length: imageCount }, (_, index) => {
    const position = index + 1;
    const toggle = () => {
      if (frozen.has(position)) return;
      item.checked = !item.checked;
      events.checkboxClicks.push(position);
    };
    const input = {
      async count() { return 1; },
      async isVisible() { return false; }, // 真实页面上这�?checkbox �??觉隐藏的
      async isChecked() { return item.checked; },
      async evaluate(callback) { callback({ click: toggle }); },
      async click() { toggle(); },
      first() { return input; },
      locator() { return empty; },
    };
    const item = {
      position,
      input,
      checked: checked.includes(position),
      async count() { return 1; },
      async getAttribute() { return 'images-item mr32'; },
      locator(selector) {
        if (selector === 'input[type="checkbox"]') return input;
        if (selector === '.check') return { async count() { return 1; }, async click() { toggle(); }, first() { return this; }, locator() { return empty; } };
        return empty;
      },
      async click() { toggle(); },
    };
    return item;
  });
  const boxCollection = {
    async count() { return boxes.length; },
    nth(index) { return boxes[index] || empty; },
    first() { return boxes[0] || empty; },
  };
  // 真实结构�?picture-box > .images-item（每张一�?��，勾选状态在里面的原�?checkbox 上�??
  const imageScope = {
    async count() { return 1; },
    first() { return imageScope; },
    nth() { return imageScope; },
    locator(selector) { return selector === '.images-item' ? boxCollection : empty; },
  };
  const makeRow = (option) => ({
    async count() { return 1; },
    async isVisible() { return open; },
    async getAttribute() { return null; }, // 真实页面行里没有 data 属�??
    async innerText() { return option.name; },
    async scrollIntoViewIfNeeded() {},
    locator() { return empty; },
    async click() {
      events.optionClicks.push(option.template_id);
      open = false;
      selectedName = option.name;
      // �?���??理勾选的图，并把它们的地�?换成新图（回读校验就看这�?���?
      for (const box of boxes) {
        if (box.checked) sources[box.position - 1] = `https://cdn/watermarked-${box.position}.png`;
      }
    },
  });
  const rowCollection = () => ({
    async count() { return (options || []).length; },
    nth(index) { return (options || [])[index] ? makeRow(options[index]) : empty; },
  });
  const menuCollection = {
    async count() { return open ? 1 : 0; },
    async isVisible() { return open; },
    nth(index) { return index === 0 ? menu : empty; },
    first() { return menu; },
  };
  const menu = {
    async count() { return open ? 1 : 0; },
    async isVisible() { return open; },
    nth(index) { return index === 0 ? menu : empty; },
    first() { return menu; },
    locator(selector) {
      if (selector.includes('data-watermark-id') || selector.includes('.watermark-item') || selector.includes('.el-dropdown-menu__item')) return rowCollection();
      return empty;
    },
  };
  const trigger = {
    async count() { return 1; },
    async isVisible() { return true; },
    async innerText() { return selectedName || triggerText; },
    async getAttribute(attribute) {
      return attribute === 'aria-expanded' ? (open ? 'true' : null) : null;
    },
    // 真实页面 button 和它内部�?span 文�?都等于�?�添加水印�?�：去重靠这�?��记�??
    async evaluate() { return 'wm-trigger-mock'; },
    async hover() { events.hovers += 1; open = true; },
    async click() { events.clicks += 1; open = true; },
    locator(selector) { return selector.includes('ancestor::button') ? { async count() { return 0; } } : empty; },
  };
  const triggerCollection = {
    async count() { return 1; },
    nth(index) { return index === 0 ? trigger : empty; },
    first() { return trigger; },
  };
  const frame = {
    getByText(text, opts) { return text === executor.TEXT.addWatermark ? triggerCollection : empty; },
    locator(selector) {
      if (selector === '.picture-box') return imageScope;
      if (selector === '.el-dropdown-menu' || selector === '[role="menu"]') return menuCollection;
      return empty;
    },
    async evaluate(_fn, arg) {
      if (arg && arg.scopes) return sources.slice(); // 读图片地�?
      return { progress: false, done: true, selectionWarning: false }; // 处理完成通知
    },
    // Escape 要发�?page：Frame 上没�?keyboard（真机踩�?TypeError）�??
    page() {
      return { keyboard: { async press() { events.escapes += 1; open = false; } } };
    },
  };
  return { frame, events, trigger, menu, boxes, sources, freeze(position) { frozen.add(position); }, setOpen(value) { open = value; } };
}

test('水印：先按范围勾选图片，再点名称�?��匹配的模板，并回读图片地�?变化', async () => {
  const mock = watermarkFrameMock({
    options: [
      { template_id: 'wm-1', name: '主图角标' },
      { template_id: 'wm-2', name: '产品图�?�?' },
    ],
    triggerText: '添加水印',
    imageCount: 4,
    checked: [3],
  });
  const result = await executor.applyWatermark(mock.frame, 'main_image', {
    template_id: 'wm-1', name: '主图角标', image_positions: [1], image_range: { kind: 'first', positions: [1] },
  });

  assert.equal(result.status, 'applied');
  assert.equal(result.method, 'menu_option_clicked');
  assert.equal(result.matched_by, 'name', '真实页面行里没有 id，只能按名称匹配');
  assert.deepEqual(result.selected_positions, [1], '主图水印�?���?1 �?');
  assert.deepEqual(result.changed_positions, [1]);
  assert.deepEqual(mock.events.optionClicks, ['wm-1']);
  assert.equal(mock.events.clicks >= 1, true, '真实 trigger �?click，不�?hover');
  // 收尾要把勾选状态复原成操作员原来的样子（第 3 张原本勾着，用完要勾回去）。
  assert.equal(mock.boxes[2].checked, true, '用完必须复原操作员原来的勾选');
  assert.equal(mock.boxes[0].checked, false, '为选范围临时勾上的第 1 张要取消');
});

test('水印：产品图范围�?���?2 张起，名称�?不上绝不点任何�?', async () => {
  const mock = watermarkFrameMock({
    options: [
      { template_id: 'wm-1', name: '正确角标' },
      { template_id: 'wm-2', name: '另一�??�?' },
    ],
    imageCount: 4,
  });
  await assert.rejects(
    executor.applyWatermark(mock.frame, 'product_images', {
      template_id: 'wm-1', name: '不存在的名称', image_positions: [2, 3],
    }),
    (error) => error && error.reason === 'watermark_template_not_found',
  );
  assert.deepEqual(mock.events.optionClicks, []);
});

test('水印：勾选没勾上就直接报错，不下单给模板（否则马�?��会弹"请先勾�??�?', async () => {
  const mock = watermarkFrameMock({ options: [{ template_id: 'wm-1', name: '角标' }], imageCount: 2 });
  // �?2 张的勾�?��?点了不生效（模拟页面没响应）：所有点击路径都不改变状态�??
  mock.freeze(2);
  await assert.rejects(
    executor.applyWatermark(mock.frame, 'product_images', {
      template_id: 'wm-1', name: '角标', image_positions: [2],
    }),
    (error) => error && error.reason === 'watermark_image_selection_not_verified',
  );
  assert.deepEqual(mock.events.optionClicks, []);
});

test('watermark apply enforces role positions (main only 1, product only 2+)', async () => {
  await assert.rejects(
    executor.applyWatermark({}, 'main_image', {
      template_id: 'wm-1', name: '角标', image_positions: [2],
    }),
    (error) => error && error.reason === 'watermark_position_out_of_role',
  );
  await assert.rejects(
    executor.applyWatermark({}, 'product_images', {
      template_id: 'wm-1', name: '角标', image_positions: [1, 2],
    }),
    (error) => error && error.reason === 'watermark_position_out_of_role',
  );
});

test('watermark menu interaction never opens a publish/刊登 click path', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const watermark = source.slice(source.indexOf('function resolveWatermarkTemplate'), source.indexOf('const VIDEO_TEXT_TITLE'));
  assert.equal(/TEXT\.(list|publish|submitReview|saveAndList)/.test(watermark), false);
  assert.match(watermark, /watermark_template_ambiguous/);
  assert.match(watermark, /watermark_template_not_found/);
  assert.match(watermark, /watermark_menu_not_visible/);
  // Selection is never by menu position; matching is exact-name (the real rows carry no id).
  assert.equal(/\.nth\([^)]*\)\.click\(/.test(watermark), false);
  assert.match(watermark, /name === wantedName/);
  // 真实机制�??�只处理勾�?�的图�?�：范围必须先落到勾选�?上，并在点模板前回�?校验�?
  assert.match(watermark, /async function selectWatermarkImages/);
  assert.match(watermark, /watermark_image_selection_not_verified/);
  assert.match(watermark, /await selectWatermarkImages\(frame, positions\)/);
  // 探针必须�?Element UI 的�?类名，否则菜单开�?也会�?��成没�?�?
  assert.match(watermark, /\.el-dropdown-menu__item/);
  // Frame 上没�?keyboard：Escape 必须发给 page（真机上 frame.keyboard �?TypeError�?
  // 而且会把 finally 里的勾�?��?原一起带崩）。注释里会提到这�?��，所以只查代码�??
  const watermarkCode = watermark.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.equal(/frame\.keyboard/.test(watermarkCode), false);
  assert.match(watermark, /frame\.page\?\.\(\)\?\.keyboard\?\.press\('Escape'\)/);
});

const ACTION_SELECTOR = 'button,[role="button"],a,.el-button,input[type="button"],input[type="submit"]';

function collection(items) {
  return {
    async count() { return items.length; },
    nth(index) { return items[index]; },
  };
}

function buildDialogPage({ closeThrows = false, includeDialog = true } = {}) {
  let closed = false;
  let fallbackCloseCalled = false;
  let createClicked = false;
  let closeClicked = false;

  const createButton = {
    async isVisible() { return true; },
    async innerText() { return '创建新商品'; },
    async inputValue() { return ''; },
    async click() { createClicked = true; },
  };
  const closeButton = {
    async isVisible() { return true; },
    async innerText() { return '关闭此页面'; },
    async inputValue() { return ''; },
    locator(selector) { return selector.startsWith('xpath=ancestor::') ? dialog : collection([]); },
    async click() {
      closeClicked = true;
      if (closeThrows) throw new Error('dialog click failed');
      closed = true;
    },
  };
  const dialog = {
    async count() { return includeDialog ? 1 : 0; },
    async isVisible() { return includeDialog; },
    async innerText() { return includeDialog ? '刊登信息保存成功!\n创建新商品\n关闭此页�?' : ''; },
    locator(selector) { return selector === ACTION_SELECTOR ? collection([createButton, closeButton]) : collection([]); },
  };
  const frame = {
    isDetached() { return false; },
    locator(selector) { return selector === ACTION_SELECTOR ? collection([createButton, closeButton]) : collection([]); },
  };
  const page = {
    frames() { return [frame]; },
    isClosed() { return closed; },
    async close() { fallbackCloseCalled = true; closed = true; },
  };
  return {
    page,
    frame,
    state: () => ({ closed, fallbackCloseCalled, createClicked, closeClicked }),
  };
}

test('clicks only the exact close action in the visible saved dialog', async () => {
  const scenario = buildDialogPage();

  const result = await executor.closeSavedDetailPage(scenario.page, scenario.frame);

  assert.deepEqual(result, { page_closed: true, close_method: 'success_dialog' });
  assert.deepEqual(scenario.state(), {
    closed: true,
    fallbackCloseCalled: false,
    createClicked: false,
    closeClicked: true,
  });
});

test('uses page close only after the exact saved dialog was found', async () => {
  const scenario = buildDialogPage({ closeThrows: true });

  const result = await executor.closeSavedDetailPage(scenario.page, scenario.frame);

  assert.deepEqual(result, { page_closed: true, close_method: 'playwright_fallback' });
  assert.equal(scenario.state().fallbackCloseCalled, true);
  assert.equal(scenario.state().createClicked, false);
});

test('does not close the page when the exact saved dialog is missing', async () => {
  const scenario = buildDialogPage({ includeDialog: false });

  await assert.rejects(
    executor.closeSavedDetailPage(scenario.page, scenario.frame),
    (error) => error && error.reason === 'saved_success_dialog_not_unique',
  );
  assert.equal(scenario.state().fallbackCloseCalled, false);
  assert.equal(scenario.state().createClicked, false);
});

test('requires stable logical closure instead of a transient missing frame', async () => {
  const checks = [true, false, true, true, true];
  let probeCount = 0;
  const page = { isClosed: () => false };

  const closed = await executor.waitForSavedDetailPageToClose(page, {
    timeoutMs: 1000,
    intervalMs: 0,
    stableRequired: 3,
    closureProbe: async () => checks[probeCount++] ?? true,
  });

  assert.equal(closed, true);
  assert.equal(probeCount, 5);
});

test('insufficient save evidence never closes the detail page', async () => {
  const originalNow = Date.now;
  const originalSetTimeout = global.setTimeout;
  let now = 0;
  let closeCalled = false;
  Date.now = () => { now += 6000; return now; };
  global.setTimeout = (callback) => { callback(); return 0; };
  const page = { close: async () => { closeCalled = true; } };
  const frame = {
    evaluate: async () => ({ success_message: true, product_id: false, publish_clicked: false }),
  };

  try {
    await assert.rejects(
      executor.verifySavedDraft(page, frame, { network: [] }, { basic_info: {} }),
      (error) => error && error.reason === 'save_result_not_proven',
    );
  } finally {
    Date.now = originalNow;
    global.setTimeout = originalSetTimeout;
  }
  assert.equal(closeCalled, false);
});

test('马帮拒绝保存的提示被捕获成具体错误码，而不是只报「无法确认草稿已保存」', async () => {
  const originalNow = Date.now;
  const originalSetTimeout = global.setTimeout;
  let now = 0;
  Date.now = () => { now += 6000; return now; };
  global.setTimeout = (callback) => { callback(); return 0; };
  const rejectionText = '商品描述中的图片宽度不能小于700，请点击上方编辑图片按钮进行修改';
  const page = { close: async () => {}, frames: () => [] };
  const frame = {
    evaluate: async (fn, arg) => (
      Array.isArray(arg)
        ? [rejectionText]
        : { success_message: false, product_id: false, publish_clicked: false }
    ),
  };

  try {
    await assert.rejects(
      executor.verifySavedDraft(page, frame, { network: [] }, { basic_info: {} }),
      (error) => error
        && error.reason === 'description_image_width_too_small'
        && error.details.save_clicked === true
        && error.details.publish_clicked === false
        && error.details.page_message === rejectionText,
    );
  } finally {
    Date.now = originalNow;
    global.setTimeout = originalSetTimeout;
  }
});

test('classifySaveRejection 只认已知提示，别的内容不误判', () => {
  assert.deepEqual(
    executor.classifySaveRejection([' 商品描述中的图片宽度不能小于700，请点击上方编辑图片按钮进行修改 ']),
    {
      code: 'description_image_width_too_small',
      message: '商品描述中的图片宽度不能小于700，请点击上方编辑图片按钮进行修改',
    },
  );
  assert.equal(executor.classifySaveRejection(['保存成功']), null);
  assert.equal(executor.classifySaveRejection(undefined), null);
});

test('retryable pre-save failure closes detail pages before waiting for retry', async () => {
  const events = [];
  const error = new Error('retry');

  await executor.handleFailedPreSaveAttempt({}, error, [{ attempt: 1 }], true, {
    closeDetails: async () => { events.push('closed'); },
    wait: async () => { events.push('waited'); },
  });

  assert.deepEqual(events, ['closed', 'waited']);
  assert.equal(error.attempts, undefined);
});

test('terminal pre-save failure closes detail pages before rethrowing', async () => {
  const events = [];
  const attempts = [{ attempt: 1 }];
  const error = new Error('terminal');

  await assert.rejects(
    executor.handleFailedPreSaveAttempt({}, error, attempts, false, {
      closeDetails: async () => { events.push('closed'); },
      wait: async () => { events.push('waited'); },
    }),
    (thrown) => thrown === error && thrown.attempts === attempts,
  );

  assert.deepEqual(events, ['closed']);
});

test('retry cleanup uses the detail frame URL when the form is mid-render', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const fn = source.slice(source.indexOf('async function closeAllDetailPages'), source.indexOf('async function handleFailedPreSaveAttempt'));
  assert.match(fn, /productDetailShopee/);
  assert.match(fn, /page\.frames\(\)\.some/);
});

test('CNSC picker reuses an already-open shop list instead of toggling it closed', async () => {
  let boxClicks = 0;
  const label = { async isVisible() { return true; } };
  const siteGroup = {
    async isVisible() { return true; },
    locator(selector) {
      if (selector === 'label.shops-checkbox') return { async count() { return 1; }, first() { return label; } };
      if (selector === 'label.el-radio') return { async count() { return 0; } };
      return { async count() { return 0; } };
    },
  };
  const picker = {
    async count() { return 1; },
    locator(selector) {
      if (selector === '.shop-box') return { async count() { return 1; }, async click() { boxClicks += 1; } };
      if (selector === '.site') return { async count() { return 1; }, nth() { return siteGroup; } };
      if (selector === 'label.shops-checkbox') return { async count() { return 1; } };
      if (selector === 'label.el-radio') return { async count() { return 0; } };
      return { async count() { return 0; } };
    },
  };
  const frame = { locator(selector) { assert.equal(selector, '#shops-wap'); return picker; } };

  const result = await executor.openCNSCSitePicker(frame);

  assert.equal(result, picker);
  assert.equal(boxClicks, 0);
});

test('terminal pre-save failure preserves detail pages when takeover is enabled', async () => {
  const events = [];
  const attempts = [{ attempt: 3 }];
  const error = new Error('terminal');

  await assert.rejects(
    executor.handleFailedPreSaveAttempt({}, error, attempts, false, {
      preserveDetails: true,
      closeDetails: async () => { events.push('closed'); },
    }),
    (thrown) => thrown === error && thrown.attempts === attempts,
  );

  assert.deepEqual(events, []);
});

test('resume start stage rejects unknown stage explicitly', () => {
  assert.throws(
    () => executor.resolvePreSaveStartIndex('does_not_exist'),
    (error) => error && error.reason === 'resume_stage_unknown',
  );
});

test('resume failure helper preserves attempts and failed stage while closing detail by default', async () => {
  const events = [];
  const error = new Error('resume_failed');
  error.failedStage = 'select_category';
  const attempts = [{ attempt: 1, ok: false, failed_stage: 'select_category' }];

  await assert.rejects(
    executor.handleFailedPreSaveAttempt({}, error, attempts, false, {
      closeDetails: async () => { events.push('closed'); },
      preserveDetails: false,
    }),
    (thrown) => thrown === error && thrown.attempts === attempts && thrown.failedStage === 'select_category',
  );

  assert.deepEqual(events, ['closed']);
});

test('enum attribute with a value outside its options uses the Mabang "�?��选项" flow', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  // 原产�?Philippines 这类�??��?�：搜索 �?添加 �?�?���?���?�?�?�?点自�?��（→ �??选择）�??
  assert.match(source, /async function addCustomSelectOption\(frame, item, labels, value, options = \{\}\)/);
  assert.match(source, /panel\.locator\('\.searchBox input:visible, input:visible'\)/);
  assert.match(source, /panel\.locator\('button:visible, \.el-button:visible'\)\.filter\(\{ hasText: \/\^\\s\*添\\s\*加\\s\*\$\/ \}\)/);
  assert.match(source, /'自填选项'/);
  assert.match(source, /�??选择/);
  // 找不到�?�项时才走自�?���?��的�?�中结果要�?核，�?��效必须报错�?�不�?��默�?�过�?
  assert.match(source, /async function selectElementOptionOrCustom\(frame, item, labels, values, options = \{\}\)/);
  assert.match(source, /error\.message !== 'select_option_not_found'/);
  assert.match(source, /select_custom_value_not_verified/);
  assert.match(source, /select_custom_option_not_unique/);
  assert.match(source, /\.el-select__tags-text/);
  assert.match(source, /\.el-select__selected-item/);
  // 属�?�填写阶段接�?select �?��上（而不�?��成文�??打字）�??
  assert.match(source, /selectElementOptionOrCustom\(\s*\n\s*frame,\s*\n\s*await waitForFormItemForLabels\(frame, labels[^)]*\),/);
});

test('an already open Mabang dropdown is never toggled shut before the custom option flow', () => {
  // 回归：执行器对原产地=Philippines �?select_option_not_found�?
  // selectElementOption 找不到�?�项时面板还�?�?，addCustomSelectOption 里又点了�?�?
  // trigger，�??Element UI 的下拉是 toggle —�??面板�?��掉，等可见面板的�?��必然超时�?
  // 返回 null 后把原错�?��出去，�?�一�?��也没�???
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  assert.match(source, /async function isSelectPanelOpen\(item\)/);
  assert.match(source, /\.el-select__caret\.is-reverse/);
  assert.match(source, /async function openSelectPanel\(frame, item\)/);
  assert.match(source, /if \(!\(await isSelectPanelOpen\(item\)\)\) \{/);
  // 两�?�?面板都必须先判开合状态：selectElementOption 与自�?���???
  const guarded = source.match(/if \(!\(await isSelectPanelOpen\(item\)\)\) \{/g) || [];
  assert.ok(guarded.length >= 2, `expected both open paths to be guarded, got ${guarded.length}`);
  assert.match(source, /const panel = await openSelectPanel\(frame, item\);/);
  // �?��值的提交方式：现�?UI �?��步�?��?�搜索�?�?�� �?点�?�添加�?�→ 同一行换�?.addCustomBox
  // （placeholder「�?输入�?��义�?��?? ✔）�?�?�� �?�?✔；�?UI �?��索�? + 「添加�?�一步到位�??
  assert.match(source, /async function submitCustomSelectValue\(panel, target\)/);
  assert.match(source, /const addFound = \(await addButton\.count\(\)\.catch\(\(\) => 0\)\) > 0;/);
  assert.match(source, /const boxDeadline = Date\.now\(\) \+ 3000;/);
  assert.match(source, /const box = panel\.locator\('\.addCustomBox'\)/);
  assert.match(source, /box\.locator\('\.el-icon-check:visible'\)/);
  assert.match(source, /if \(!\(await submitCustomSelectValue\(panel, target\)\)\) return null;/);
  // 面板里的控件常有多份（隐藏模板�? + 真�?�?��的那份）：一律用 `:visible` 复合 locator�?
  // 按序号取 nth(i) 会在 popper �?��后落到隐藏元素上，点击卡�?actionability 直到超时�?
  assert.match(source, /frame\s*\n\s*\.locator\('\.el-select-dropdown:visible'\)\s*\n\s*\.filter\(\{ has: frame\.locator\('input'\) \}\)/);
  assert.match(source, /box\.locator\('input:visible'\)/);
  // 必须逐字�?��：fill() 不触发组件的按键事件，组件内部�?�仍为空 �?�?�?�?么也加不进去�?
  assert.match(source, /async function typeIntoField\(field, target\)/);
  assert.match(source, /await field\.pressSequentially\(target, \{ delay: 25, timeout: 10000 \}\)/);
  assert.match(source, /if \(!\(await typeIntoField\(input, target\)\)\) return false;/);
  const exported = require('../scripts/mabang_playwright_executor.cjs');
  assert.equal(typeof exported.isSelectPanelOpen, 'function');
  assert.equal(typeof exported.openSelectPanel, 'function');
  assert.equal(typeof exported.submitCustomSelectValue, 'function');
  assert.equal(typeof exported.typeIntoField, 'function');
});

test('form items match on their own label so 尺码 cannot hit 包裹尺寸', () => {
  // 子串匹配让类�?��性�?�尺寸�?�撞上物流�?�包裹尺寸�?�（�?�?�?3 �?��入�?）：
  // 轻则 field_not_unique，重则把尺码文本�?��包裹尺�?�?
  assert.equal(executor.ownLabelText('*尺码图表:上传图片选择模板'), '尺码图表上传图片选择模板');
  assert.equal(executor.ownLabelText('包裹尺�?'), '包裹尺�?');
  assert.equal(executor.ownLabelText('尺�?'), '尺�?');
  assert.notEqual(executor.ownLabelText('包裹尺�?'), executor.ownLabelText('尺�?'));
  assert.notEqual(executor.ownLabelText('*尺码图表:上传图片选择模板'), executor.ownLabelText('尺�?'));
  // 必填星号/结尾冒号/全�?都归�?化，同一字�?的写法差异不影响匹配�?
  assert.equal(executor.ownLabelText('原产地（Region of Origin）：'), executor.ownLabelText('原产地（Region of Origin）'));
  assert.equal(executor.ownLabelText('*商品标题:'), executor.ownLabelText('商品标题'));

  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  // 先精�??�后子串；必�?��性缺失�?明确报错�?
  assert.match(source, /async function formItemByOwnLabel\(frame, label, options = \{\}\)/);
  assert.match(source, /const exact = await formItemByOwnLabel\(frame, label, options\);/);
  assert.match(source, /throw new BlockedError\('select_field_not_found', \{ field: labels\[0\] \}\);/);
});

test('site attributes tolerate the （Size） suffix, expand 显示所有属性 and leave CNSC untouched', () => {
  // 实机取证（店�?HN_POD / 类目 101757）：页面把类�?��性�?�尺寸�?�渲染成
  // 「尺寸（Size）：」，且默认折在�?�显示所有属性�?�后�??�只做精�?��配修复不够�?��??
  // 精确匹配失败后子串回�?仍会命中�?尺码图表」�?�包裹尺寸�?�（都是 3 输入框）�?
  // 于是又一�?field_not_unique。这里锁住三件事�?
  //   1) loose 归一化剥掉括号英文后�?；默认（跨�?）保持不变�??
  const loose = { loose: true };
  assert.equal(executor.ownLabelText('尺�?（Size）：'), '尺�?(Size)');
  assert.equal(executor.ownLabelText('尺�?（Size）：', loose), '尺�?');
  assert.equal(executor.ownLabelText('性别（Gender）：', loose), '性别');
  assert.equal(executor.ownLabelText('包裹尺�?', loose), '包裹尺�?');
  assert.equal(executor.ownLabelText('*尺码图表:上传图片选择模板', loose), '尺码图表上传图片选择模板');
  // 干扰项即便在 loose 下也不会以目标文案开头，前缀锚定会把它们排除�?
  assert.equal(executor.ownLabelText('包裹尺�?', loose).startsWith(executor.ownLabelText('尺�?', loose)), false);
  assert.equal(executor.ownLabelText('*尺码图表:上传图片选择模板', loose).startsWith(executor.ownLabelText('尺�?', loose)), false);
  assert.equal(executor.ownLabelText('尺�?（Size）：', loose).startsWith(executor.ownLabelText('尺�?', loose)), true);

  //   2) 展开器与锚定函数�?��、可测�??
  assert.equal(typeof executor.prefixAnchoredItems, 'function');
  assert.equal(typeof executor.expandHiddenCategoryAttributes, 'function');

  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  // 回�??仍存�?���?loose 时先过一遍前�?锚定�?
  assert.match(source, /const candidates = options\.loose === true \? await prefixAnchoredItems\(visible, label, options\) : visible;/);
  assert.match(source, /if \(text\.startsWith\(wanted\)\) anchored\.push\(item\);/);
  // 展开�?��按钮文�?点，且在展开后再查一次�??
  assert.match(source, /frame\.locator\('button\.el-button--text', \{ hasText: '显示所有属性' \}\)/);
  assert.match(source, /present = await findFormItemPresent\(frame, labels, 2000, matchOptions\);/);

  //   3) 容错全部限定在本土：跨�?走原来的匹配参数�?
  assert.match(source, /const siteMode = String\(task\.store\?\.listing_mode \|\| ''\) !== 'cnsc_global';/);
  assert.match(source, /const matchOptions = siteMode \? \{ loose: true, siteMultiSelectHandling: true \} : \{\};/);
  assert.match(source, /if \(!present && siteMode && !hiddenExpanded\) \{/);
});

// ---------------------------------------------------------------------------
// �?��商品描述�?��插图：尺码表在前、产品图�?5 张在后，且只在产品图上传完成后做�?
// 现场勘查�?scratch/description-area-capture.json）：描述�?wangEditor�?
// #textDescription_product_img 打开「引用产品图」弹窗（列表 = 已上传产品图），
// 「图�?�?网络图片」按 URL 插图，两条路都把 <p><img src=...></p> 追加到光标�?�?
// ---------------------------------------------------------------------------
test('contenteditable 描述保留模板空�?，只规范换�?�?', () => {
  assert.equal(executor.normalizeContentEditableText('A\nB'), 'A\nB');
  // 固定描述靠空行分段：fill() �?"\n\n" 落成 A / 空�?�?/ B，�?觉上正好�?行留白，
  // �?以必须原样�?�进 wangEditor，不能再折叠成单换�?�?
  assert.equal(executor.normalizeContentEditableText('A\n\nB\r\n\r\nC'), 'A\n\nB\n\nC');
  assert.equal(executor.normalizeContentEditableText(' A  B '), ' A  B ');
});

test('描述插图阶�?排在上传商品图之后，跨�?不参�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const stages = source.slice(source.indexOf('const PRE_SAVE_STAGES = ['), source.indexOf('function resolvePreSaveStartIndex'));
  assert.match(stages, /\['upload_product_images'[\s\S]*\['append_description_media'[\s\S]*\['__optional_media__'/);

  // 跨�? CNSC 的描述页没有这�?入口 �?直接跳过（不点保存�?�不做任�?DOM 操作）�??
  assert.match(source, /async function appendDescriptionMedia\(frame, task\) \{[\s\S]*?listing_mode \|\| ''\) === 'cnsc_global'\) return \{ skipped: true \};/);
});

test('尺码表图先插、引用产品图后插，且失败降级为�?�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const body = source.slice(
    source.indexOf('async function insertDescriptionMediaBatch'),
    source.indexOf('async function appendDescriptionMedia('),
  );
  const sizeChartInsert = body.indexOf('insertDescriptionImageByUrl(item, sizeChartUrl, ensureCaret)');
  const productInsert = body.indexOf('insertDescriptionProductImages(frame, editorId');
  assert.equal(sizeChartInsert > 0, true);
  assert.equal(productInsert > sizeChartInsert, true);
  // 光标必须落到描述�?��再插图，落不下就整批不插（不把图塞进正文�?��）�??
  assert.match(body, /const caret = await focusDescriptionEnd\(frame, editor, editorId\);/);
  assert.match(body, /if \(!caret\) throw new BlockedError\('description_caret_not_at_end'\);/);
  // �?URL 插图前再钉一次光标：�?���?会�? wangEditor 保存�?range 漂回正文�?���?
  // 钉不住就整批不插（宁�?��插也不把描述切两半）�?
  assert.match(body, /const ensureCaret = async \(\) => \{[\s\S]{0,160}?throw new BlockedError\('description_caret_not_at_end'\);[\s\S]{0,20}?\};/);
  assert.match(source, /await editor\.press\('Control\+End'\)\.catch\(\(\) => \{\}\);/);
  assert.match(source, /range\.collapse\(false\);/);
  // 位置不�?就把这批撤掉重来，最�?3 �??�撤�?次数按这批插入的图数封顶（撤多了会撤到�?文）�?
  assert.match(source, /const DESCRIPTION_MEDIA_ATTEMPTS = 3;/);
  assert.match(source, /description_media_position_not_at_tail/);
  // 回滚基线带上已经插进去的�?��尺码图（sizeChartExisting），�?��它一起撤掉�??
  assert.match(source, /const undone = await undoDescriptionInserts\(frame, editor, editorId, sizeChartExisting\.length, pending\.length\);/);
  assert.match(source, /const attempts = Math\.max\(3, Math\.min\(20, Number\(insertedCount\) \+ 2\)\);/);
  // �?批插到一半失败也要撤干净：否则尺码表留在正文里把描述切成两半�?
  assert.match(source, /catch \(error\) \{[\s\S]{0,400}?const rolled = await undoDescriptionInserts\(frame, editor, editorId, sizeChartExisting\.length, pending\.length\);[\s\S]{0,120}?if \(!rolled\) break;[\s\S]{0,40}?continue;/);
  // 失败不阻�?��稿：appendDescriptionMedia 捕获�?emit warning�?
  assert.match(source, /const warning = \{\s*status: 'warning',/);
  assert.match(source, /emit\('stage', \{ stage: 'append_description_media', \.\.\.warning \}\)/);
});

test('落光标前先把描述正文滚到�?底下，滚不到底就不插', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const focusBody = source.slice(
    source.indexOf('async function focusDescriptionEnd'),
    source.indexOf('async function readDescriptionImageUrls'),
  );
  // 滚到�?�?落光�?�?再钉�?次盒子贴�?�?光标在末尾且盒子在底�?��两样都成立才算就�???
  assert.match(focusBody, /await scrollDescriptionToBottom\(frame, editorId\);/);
  assert.match(focusBody, /await scrollDescriptionEditorBoxToBottom\(frame, editorId\);/);
  assert.match(focusBody, /\(await descriptionCaretAtEnd\(frame, editorId\)\) && \(await descriptionScrolledToBottom\(frame, editorId\)\)/);
  // �?��编辑器自己的滚动盒子，再把它滚进视野�?
  const scrollBody = source.slice(
    source.indexOf('async function scrollDescriptionToBottom'),
    source.indexOf('async function scrollDescriptionEditorBoxToBottom'),
  );
  assert.match(scrollBody, /editor\.scrollTop = editor\.scrollHeight;/);
  assert.match(scrollBody, /editor\.scrollIntoView\(\{ block: 'nearest' \}\)/);
});

test('滚到底只看编辑器�?��的滚动盒子，页面主滚动�?器到不了底不�?', async () => {
  // 2026-09-23 现场埋点：main.el-main 内�? 4051px、可�?785px，描述表单项下面还有变体�?
  // SKU 网格、物流信�?��表单项�?��?��?求�?先�?器全到底时每�?次尝试都失败（description_caret_not_at_end），
  // 描述里一张图都插不进去�?�判定收窄到编辑器自己的滚动盒子后不再�?页面结构判�?�?
  const editor = { clientHeight: 368, scrollHeight: 645, scrollTop: 0 };
  const frame = { evaluate: async (fn) => fn('editor-id') };
  const original = global.document;
  global.document = { getElementById: () => editor };
  try {
    assert.equal(await executor.descriptionScrolledToBottom(frame, 'editor-id'), false);
    editor.scrollTop = 277;
    assert.equal(await executor.descriptionScrolledToBottom(frame, 'editor-id'), true);
    // 正文不长（内容比盒子�?��时没有可滚区域，算到底�??
    editor.scrollHeight = 368;
    editor.scrollTop = 0;
    assert.equal(await executor.descriptionScrolledToBottom(frame, 'editor-id'), true);
  } finally {
    global.document = original;
  }
});

test('插图地址判同一张图：换了 CDN 主机或加了参数也算同一张', () => {
  assert.equal(executor.descriptionImageKey('https://cf.shopee.co.id/file/abc123'), 'abc123');
  assert.equal(executor.descriptionImageKey('https://down-ph.img.susercontent.com/file/abc123?x-oss-process=image'), 'abc123');
  assert.equal(executor.descriptionImageKey('https://cos.example/size.png#big'), 'size.png');
  // �?��没有文件名的地址（空 src �?��析成页面地址）拿不到�?��，不能当图片用�??
  assert.equal(executor.descriptionImageKey('https://erp.mabangerp.com/'), '');
  assert.equal(executor.descriptionImageKey(''), '');

  const plan = [
    { kind: 'size_chart', url: 'https://cf.shopee.co.id/file/size99' },
    { kind: 'product_image', url: 'https://cf.shopee.co.id/file/pic01' },
  ];
  // 描述里存的是另一�?CDN + 带参数的同名文件：不能算「没插上」�??
  assert.deepEqual(
    executor.missingDescriptionMedia(plan, ['https://down-th.img.susercontent.com/file/pic01?x-oss-process=image/resize']),
    [plan[0]],
  );
  assert.deepEqual(executor.missingDescriptionMedia(plan, ['https://cf.shopee.co.id/file/size99', 'https://cf.shopee.co.id/file/pic01']), []);

  // 位置校验同样按身份比：插进去的是变体地址时，仍�?认出它压在�?文末尾�??
  const html = '<p>正文</p><p><img src="https://down-th.img.susercontent.com/file/pic01?x=1"></p>';
  assert.equal(executor.descriptionMediaAppendedAtTail(html, ['https://cf.shopee.co.id/file/pic01'], []), true);
  assert.equal(executor.descriptionMediaAppendedAtTail(`${html}<p>尾随正文</p>`, ['https://cf.shopee.co.id/file/pic01'], []), false);
});

test('连着的两张图都按文件名命�?��不会跳过�?���?', () => {
  const a = 'https://cf.shopee.co.id/file/picA';
  const b = 'https://cf.shopee.co.id/file/picB';
  // 两张图挨在一起：按文件名命中的游标�?果按整串地址推进，就会越过�?二张�?
  const html = '<p>正文</p><p><img src="https://x.io/picA"></p><p><img src="https://x.io/picB"></p>';
  assert.equal(executor.descriptionMediaAppendedAtTail(html, [a, b], []), true);
  assert.equal(executor.descriptionMediaAppendedAtTail(`<p>正文</p><p><img src="https://x.io/picB"></p>`, [a, b], []), false);
});

test('产品图收集：�?src 的占位图与静态图标不进插图�?�?', async () => {
  // �??原�?属�?�：src 为空时浏览器会把 .src 解析成当前页面地�?。现�?2026-09-23 �?
  // <div class="img-content"><img src=""></div> 就是这样�?https://publish.mabangerp.com/publish-ui/
  // 混进插图计划，校验时永远缺一张，整批插图回滚�?
  const collect = async (images) => {
    const nodes = images.map((image) => ({ getAttribute: () => image.attr, ...(image.resolved === undefined ? {} : { src: image.resolved }) }));
    const section = { querySelectorAll: () => nodes };
    const original = global.document;
    global.document = { querySelector: () => ({ closest: () => section }) };
    try {
      return await executor.collectProductImageUrls({ evaluate: async (fn, arg) => fn(arg) });
    } finally {
      global.document = original;
    }
  };
  const urls = await collect([
    { attr: '', resolved: 'https://publish.mabangerp.com/publish-ui/' },
    { attr: '/publish-ui/static/img/product_img_icon.svg', resolved: 'https://publish.mabangerp.com/publish-ui/static/img/product_img_icon.svg' },
    { attr: 'https://erp.mabangerp.com/' },
    { attr: 'https://cf.shopee.co.id/file/pic01' },
    { attr: 'https://cf.shopee.co.id/file/pic02' },
  ]);
  assert.deepEqual(urls, ['https://cf.shopee.co.id/file/pic01', 'https://cf.shopee.co.id/file/pic02']);
});

test('产品图收集：满 9 张后上传 input 被移除，仍按 .picture-group 卡片收集', async () => {
  // 现场 2026-10-27（任务 6f4ff5af / 78fce435）：产品图区满 9 张时马帮把
  // input[type=file][multiple] 整个删掉，只认 input 的收集恒返回 []，描述插图计划里
  // 只剩尺码表 → 产品图一张都不插。这里按卡片（.picture-group > .images-item）兜底。
  const img = (attr) => ({ getAttribute: () => attr });
  const card = (src) => ({ querySelectorAll: () => [img(src), img('/publish-ui/static/img/icon-ai.abc.svg')] });
  const productCards = [
    card('https://cos.example/upload/p1.png'),
    card('https://cos.example/upload/p2.png'),
    card('https://cos.example/upload/p3.png'),
  ];
  const group = (cards) => ({
    querySelectorAll: (selector) => (
      selector === '.images-item' ? cards : cards.flatMap((entry) => entry.querySelectorAll('img'))
    ),
  });
  const groups = [
    group([]), // 主图区那块 .picture-group 没有 .images-item 卡片
    group(productCards),
  ];
  const original = global.document;
  global.document = {
    // 满 9 张：input 已经不在页面上了
    querySelector: () => null,
    querySelectorAll: (selector) => (selector === '.picture-group' ? groups : []),
  };
  try {
    const urls = await executor.collectProductImageUrls({ evaluate: async (fn, arg) => fn(arg) });
    assert.deepEqual(urls, [
      'https://cos.example/upload/p1.png',
      'https://cos.example/upload/p2.png',
      'https://cos.example/upload/p3.png',
    ]);
  } finally {
    global.document = original;
  }
});

test('描述插图：上传进去的尺码图要验位置，不在末尾就撤回交给按地址重插', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const body = source.slice(
    source.indexOf('async function appendDescriptionMediaOnce'),
    source.indexOf('// 插一批图：尺码表先、产品图后'),
  );
  // 上传尺码图之后必须读回正文验位置（原先直接当 existing，落在最前面也照样成功）
  assert.match(body, /const uploadedHtml = await readDescriptionHtml\(frame, editorId\);/);
  assert.match(body, /if \(descriptionMediaAppendedAtTail\(uploadedHtml, \[sizeChartUrl\], existing, \{ minTextBefore: beforeTextLength \}\)\)/);
  // 位置不对：先撤回这张，再由批量插图按地址插到末尾
  assert.match(body, /const undone = await undoDescriptionInserts\(frame, editor, editorId, existing\.length, 1\);/);
  assert.match(body, /size_chart:upload-reinsert/);
  // 撤不回来：不插第二张，位置问题作为警告交运营
  assert.match(body, /description_size_chart_position_not_at_tail/);
  // 产品图一张地址都没收集到时要报出来，不能 already:true 假装成功
  assert.match(body, /description_product_images_not_collected/);
});

test('描述用哪张尺码表图：模板模式用店铺模板里�?并上传的�?���?', () => {
  const os = require('node:os');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'size-chart-'));
  const file = path.join(dir, 'size-chart.png');
  fs.writeFileSync(file, 'x');
  try {
    const sourceOf = (sizeChart) => executor.descriptionSizeChartSource({ images: { size_chart: sizeChart } });
    // 选择模板 + 上传了本地图 �?走�?�上传图片�?�把这张图传进描述（页面上没有可引用的地�?）�??
    assert.deepEqual(sourceOf({ source: 'template', template_id: 't1', image_path: file }), { kind: 'file', path: file });
    // �??�了模板、没上传图（或文件已不在）→ 回落页面预�?地址那条�?��取不到就降级为�?告）�?
    assert.deepEqual(sourceOf({ source: 'template', template_id: 't1', image_path: '' }), { kind: 'url' });
    assert.deepEqual(sourceOf({ source: 'template', image_path: path.join(dir, 'missing.png') }), { kind: 'url' });
    // 上传模式不变：仍引用尺码图表预�?图的�?��地址�?
    assert.deepEqual(sourceOf({ source: 'upload', image_path: file }), { kind: 'url' });
    assert.deepEqual(executor.descriptionSizeChartSource({ images: { size_chart_image_path: file } }), { kind: 'url' });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('描述�?��插本地尺码图：走「上传图片�?�页签，插完按马�?��的地�?继续原流�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const insertBody = source.slice(
    source.indexOf('async function insertDescriptionImageByFile'),
    source.indexOf('async function uploadDescriptionSizeChartFile'),
  );
  // 这一页�?点开的是系统文件对话框，面板里没�?file input，只能拦文件选择器�??
  assert.match(insertBody, /TEXT\.uploadImage/);
  assert.match(insertBody, /\.upload-icon-container/);
  assert.match(insertBody, /page\.waitForEvent\('filechooser'/);
  assert.match(insertBody, /chooser\.setFiles\(filePath\)/);
  const uploadBody = source.slice(
    source.indexOf('async function uploadDescriptionSizeChartFile'),
    source.indexOf('async function closeReferenceProductImageDialog'),
  );
  // 先落光标再�?�文件（wangEditor 按内部保存的 range 插图），插完读回�?��地址�?
  assert.match(uploadBody, /await ensureCaret\(\);/);
  assert.match(uploadBody, /const urls = await waitForDescriptionImages\(/);
  assert.match(uploadBody, /DESCRIPTION_SIZE_CHART_UPLOAD_TIMEOUT/);
  assert.match(uploadBody, /return String\(urls\[urls\.length - 1\] \|\| ''\);/);
  // 插进去之后并�?existing：去重�?�顺序�?�位�?��验与「网络图片�?�那条路完全�?致�??
  assert.match(source, /sizeChartExisting = \[\.\.\.existing, sizeChartUrl\]\.filter\(Boolean\);/);
  assert.match(source, /const plan = buildDescriptionMediaPlan\(\{ sizeChartUrl, productImageUrls \}\);/);
  assert.match(source, /const pending = missingDescriptionMedia\(plan, sizeChartExisting\);/);
  // �?��图没传上去只降级为�?告，产品图照插�??
  assert.match(source, /warnings\.push\('description_size_chart_upload_failed'\);/);
});

test('插入位置校验：本批图必须压在正文�?下面', () => {
  const size = 'https://cos.example/size.png';
  const p1 = 'https://cos.example/p1.png';
  const p2 = 'https://cos.example/p2.png';
  // 追加在末尾：通过�?
  const tailHtml = `<p>正文</p><p><img src="${size}" style="max-width:100%;"></p><p><img src="${p1}" style="max-width:100%;"></p><p><img src="${p2}" style="max-width:100%;"></p>`;
  assert.equal(executor.descriptionMediaAppendedAtTail(tailHtml, [size, p1, p2], []), true);
  // 插到正文�?��（后面还有原正文）：拒绝�?
  const middleHtml = `<p><img src="${size}" style="max-width:100%;"></p><p><img src="${p1}" style="max-width:100%;"></p><p>正文后半�?/p><p><img src="${p2}" style="max-width:100%;"></p>`;
  assert.equal(executor.descriptionMediaAppendedAtTail(middleHtml, [size, p1, p2], []), false);
  // 顺序反了：拒绝�??
  const swapped = `<p>正文</p><p><img src="${p2}" style="max-width:100%;"></p><p><img src="${size}" style="max-width:100%;"></p>`;
  assert.equal(executor.descriptionMediaAppendedAtTail(swapped, [size, p2], []), false);
  // 描述里原有图片必须仍在本批之前，否则说明插反了位�???
  const existing = 'https://cos.example/old.png';
  assert.equal(executor.descriptionMediaAppendedAtTail(`<p><img src="${existing}"></p><p>正文</p><p><img src="${p1}"></p>`, [p1], [existing]), true);
  assert.equal(executor.descriptionMediaAppendedAtTail(`<p><img src="${p1}"></p><p>正文</p><p><img src="${existing}"></p>`, [p1], [existing]), false);
});

test('描述插图计划：尺码表在前、产品图全部插入（不再截�?5 张）', () => {
  const plan = executor.buildDescriptionMediaPlan({
    sizeChartUrl: 'https://cos.example/size.png',
    productImageUrls: ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'],
  });
  assert.deepEqual(plan, [
    { kind: 'size_chart', url: 'https://cos.example/size.png' },
    { kind: 'product_image', url: 'p1' },
    { kind: 'product_image', url: 'p2' },
    { kind: 'product_image', url: 'p3' },
    { kind: 'product_image', url: 'p4' },
    { kind: 'product_image', url: 'p5' },
    { kind: 'product_image', url: 'p6' },
    { kind: 'product_image', url: 'p7' },
  ]);

  // 尺码图地�?取不到（模板预�?缺失）时�?��产品图，同样不截�???
  assert.deepEqual(
    executor.buildDescriptionMediaPlan({ productImageUrls: ['p1', 'p2'] }),
    [{ kind: 'product_image', url: 'p1' }, { kind: 'product_image', url: 'p2' }],
  );
  assert.deepEqual(executor.buildDescriptionMediaPlan({}), []);
  // 收集上限�?��护栏（商品图上限 9 张），不�?��务截�???
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const max = source.match(/const DESCRIPTION_MEDIA_MAX_IMAGES = (\d+);/);
  assert.ok(max && Number(max[1]) >= 20, '收集上限必须远大于商品图上限，否则还�?���?���?');
  assert.doesNotMatch(source, /DESCRIPTION_MEDIA_LIMIT/);
});

test('引用产品图弹窗：按地�?勾�?�待插的图，全部产品图都能插�?', async () => {
  const listed = ['a.png', 'b.png', 'c.png', 'd.png', 'e.png', 'f.png', 'g.png', 'h.png'];
  // preChecked = 弹窗打开时就已勾上的行（重开弹窗�?��带着上�?的状态）�?
  const make = ({ preChecked = [] } = {}) => {
    const clicks = [];
    const checked = new Set(preChecked);
    const state = { clicks, checked, confirmed: false, cancelled: false };
    const nodes = listed.map((url, index) => ({
      querySelector(selector) {
        if (String(selector).includes('checkbox')) return { checked: checked.has(index) };
        return { src: url };
      },
    }));
    const items = {
      async count() { return listed.length; },
      first() { return { async waitFor() { return true; } }; },
      async evaluateAll(fn) { return fn(nodes); },
      nth(index) {
        return {
          locator() {
            return {
              first: () => ({
                async click() {
                  // label 点击�?��换�?义：没勾上就勾上�?
                  clicks.push(index);
                  if (checked.has(index)) checked.delete(index); else checked.add(index);
                },
              }),
            };
          },
        };
      },
    };
    const dialog = {
      first() { return { async isVisible() { return !state.cancelled; } }; },
      locator(selector) {
        if (selector === '.images-item') return items;
        return {
          filter(options) {
            const isConfirm = options.hasText instanceof RegExp && options.hasText.test('确 定');
            return {
              first: () => (isConfirm
                ? { async count() { return 1; }, async click() { state.confirmed = true; } }
                : { async count() { return 1; }, async click() { state.cancelled = true; } }),
            };
          },
        };
      },
    };
    // 页面上的链：frame.locator('#<id>_product_img') 打开弹窗�?
    // frame.locator('.el-dialog__wrapper:visible').filter({hasText}).first() 拿弹窗�??
    const frame = {
      locator(selector) {
        if (selector.startsWith('#')) return { async count() { return 1; }, async click() { return true; } };
        return { filter: () => ({ first: () => dialog }) };
      },
    };
    return { frame, state };
  };

  // 描述里一张都没有�? 张全部勾上（不再�?���?5 张）�?
  const first = make();
  const added = await executor.insertDescriptionProductImages(first.frame, 'textDescription', listed);
  assert.equal(added, 8);
  assert.deepEqual(first.state.clicks, [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.equal(first.state.checked.size, 8);
  assert.equal(first.state.confirmed, true);

  // 重跑：a/b 已经在描述里 �?�?��剩下 6 张，不会�?a/b 再插�?遍�??
  const second = make();
  const subset = await executor.insertDescriptionProductImages(
    second.frame, 'textDescription', ['c.png', 'd.png', 'e.png', 'f.png', 'g.png', 'h.png'],
  );
  assert.equal(subset, 6);
  assert.deepEqual(second.state.clicks, [2, 3, 4, 5, 6, 7]);

  // 弹窗重开时已勾上 a/b：这两�?不能再点（点�?下会取消勾�?�，白插两张）�??
  const third = make({ preChecked: [0, 1] });
  const again = await executor.insertDescriptionProductImages(third.frame, 'textDescription', listed);
  assert.equal(again, 8);
  assert.deepEqual(third.state.clicks, [2, 3, 4, 5, 6, 7]);
  assert.equal(third.state.checked.size, 8);

  // 弹窗里一张都匹配不上（页面结构变了）�?报错并关掉弹窗，让调用方�?回按 URL 插入�?
  const fourth = make();
  await assert.rejects(
    () => executor.insertDescriptionProductImages(fourth.frame, 'textDescription', ['zzz.png']),
    /description_product_images_not_listed/,
  );
  assert.equal(fourth.state.clicks.length, 0);
  assert.equal(fourth.state.cancelled, true);
  assert.equal(fourth.state.confirmed, false);
});

test('重跑不再重�?插图：已在描述里的地�?不进计划', () => {
  const plan = executor.buildDescriptionMediaPlan({ sizeChartUrl: 'size.png', productImageUrls: ['p1', 'p2'] });
  assert.deepEqual(executor.missingDescriptionMedia(plan, []).map((entry) => entry.url), ['size.png', 'p1', 'p2']);
  assert.deepEqual(executor.missingDescriptionMedia(plan, ['size.png']).map((entry) => entry.url), ['p1', 'p2']);
  assert.deepEqual(executor.missingDescriptionMedia(plan, ['size.png', 'p1', 'p2']), []);
});

test('描述编辑器缺失时�?��警告，不抛错（草稿照样保存）', async () => {
  // 编辑器存在但拿不�?id：与「描述区结构变了」同类，必须降级成�?告�?�不�?��错�??
  const editorWithoutId = {
    async count() { return 1; },
    first() { return editorWithoutId; },
    async getAttribute() { return ''; },
  };
  const item = {
    locator(selector) {
      return selector.includes('el-form-item__label') ? labelLocator : editorWithoutId;
    },
    async isVisible() { return true; },
  };
  const labelLocator = {
    async count() { return 1; },
    first() { return labelLocator; },
    async innerText() { return '商品描述:'; },
  };
  const items = { async count() { return 1; }, nth() { return item; } };
  const frame = {
    locator() { return items; },
  };
  const events = [];
  const originalWrite = process.stdout.write.bind(process.stdout);
  // emit 的事件流默认可以用 MABANG_EXECUTOR_SILENT=1 关掉（跑测试时 TAP 不被污染）；
  // 这个用例正是要截事件，所以临时把它打开。
  const originalSilent = process.env.MABANG_EXECUTOR_SILENT;
  process.env.MABANG_EXECUTOR_SILENT = '0';
  process.stdout.write = (chunk) => { events.push(String(chunk)); return true; };
  let result;
  try {
    result = await executor.appendDescriptionMedia(frame, { store: { listing_mode: 'site' } });
  } finally {
    process.stdout.write = originalWrite;
    if (originalSilent === undefined) delete process.env.MABANG_EXECUTOR_SILENT;
    else process.env.MABANG_EXECUTOR_SILENT = originalSilent;
  }
  assert.equal(result.status, 'warning');
  assert.equal(result.code, 'description_editor_id_missing');
  const emitted = events
    .filter((line) => line.startsWith('MABANG_PLAYWRIGHT_EVENT '))
    .map((line) => JSON.parse(line.replace('MABANG_PLAYWRIGHT_EVENT ', '')));
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].stage, 'append_description_media');
  assert.equal(emitted[0].status, 'warning');
});

test('跨�?任务直接跳过描述插图，不碰页�?', async () => {
  const result = await executor.appendDescriptionMedia(undefined, { store: { listing_mode: 'cnsc_global' } });
  assert.deepEqual(result, { skipped: true });
});

test('存成 text 的类目属性按页面控件决定填法，条件下拉不再被 fill 卡满 30s', () => {
  // 现场：HN_POD �?��刊登，条�?100413) 在档案里�?type=text（input_type=2�?
  // 枚举与自�?��组都�?��的），页�?��却是 readonly �?el-select 触发器�?�旧代码�?
  // 档�?当成文本框，fill("new") 等满 30s �?"element is not editable"，整单失败�??
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  // 文本分支不再直接 setField，改走按控件判断�?selectElementOptionOrCustom�?
  assert.doesNotMatch(source, /results\[field\.field_id\] = await setField\(frame, labels, value, matchOptions\)/);
  assert.match(source, /results\[field\.field_id\] = await selectElementOptionOrCustom\(\s*\n\s*frame,\s*\n\s*present,/);
  // 控件类型�?�� DOM：有 .el-select 或输入�? readonly 都算下拉，档案里�?type 不作数�??
  assert.match(source, /async function isSelectControl\(item\)/);
  assert.match(source, /item\.locator\('\.el-select'\)\.count\(\)/);
  // 打字前先判可编辑：readonly 立即�?field_not_editable，不再留 30s 超时�?
  assert.match(source, /if \(!\(await isFillableControl\(field\)\)\) \{\s*\n\s*throw new BlockedError\('field_not_editable'/);
  assert.equal(typeof executor.isSelectControl, 'function');
  assert.equal(typeof executor.isFillableControl, 'function');
});

test('isSelectControl：有 el-select �?readonly 输入框才算下�?', async () => {
  const fakeItem = (selects, readOnly) => ({
    locator(selector) {
      if (selector === '.el-select') return { async count() { return selects; } };
      return { first: () => ({ async getAttribute() { return readOnly; } }) };
    },
  });
  assert.equal(await executor.isSelectControl(fakeItem(0, null)), false);      // 真文�??（尺寸）
  assert.equal(await executor.isSelectControl(fakeItem(0, 'readonly')), true); // 条件下拉
  assert.equal(await executor.isSelectControl(fakeItem(2, null)), true);       // �??�枚举下�?
  assert.equal(await executor.isSelectControl(null), false);
});

test('下拉搜索框里�?T-shirt 不是已提交�?�项，不能跳过点�?', async () => {
  const makeLocator = ({ value = '', readonly = null, texts = [] } = {}) => ({
    async count() { return 1; },
    first() { return this; },
    async getAttribute(name) { return name === 'readonly' ? readonly : null; },
    async inputValue() { return value; },
    async allInnerTexts() { return texts; },
  });
  const searchable = {
    locator(selector) {
      if (selector.includes('tags') || selector.includes('selected-item') || selector.includes('aria-selected')) {
        return makeLocator({ texts: [] });
      }
      return makeLocator({ value: 'T-shirt', readonly: null });
    },
  };
  assert.deepEqual(await executor.selectedTextsForItem(searchable), []);

  const committed = {
    locator(selector) {
      if (selector.includes('tags') || selector.includes('selected-item') || selector.includes('aria-selected')) {
        return makeLocator({ texts: ['T-shirt'] });
      }
      return makeLocator({ value: '', readonly: null });
    },
  };
  assert.deepEqual(await executor.selectedTextsForItem(committed), ['T-shirt']);
});

test('isFillableControl：readonly 立即判否，disabled 等它恢�?', async () => {
  const fakeInput = (attrs) => ({ async getAttribute(name) { return attrs[name] ?? null; } });
  const startedAt = Date.now();
  assert.equal(await executor.isFillableControl(fakeInput({ readonly: 'readonly' }), 5000), false);
  assert.ok(Date.now() - startedAt < 1000, 'readonly 的控件不该等满超�?');
  assert.equal(await executor.isFillableControl(fakeInput({}), 5000), true);
  // disabled �?��表单渲染�?��暂时态：恢�?成可编辑就放行，不然白等�?次重试�??
  let disabledCalls = 0;
  const disabledThenReady = {
    async getAttribute(name) {
      return name === 'disabled' && disabledCalls++ < 2 ? 'disabled' : null;
    },
  };
  assert.equal(await executor.isFillableControl(disabledThenReady, 3000), true);
  assert.ok(disabledCalls >= 3, `disabled 也要轮询等待，实际调用了 ${disabledCalls} 次`);
});

test('真文�??属�?�的值仍按打字写入，不�?�?��成下�?', async () => {
  // 反方向也要守住：尺�?(102530) 这类 input_type=3 的属性在页面上是真文�??�?
  // �?��由到下拉�?��就会去开�?�?��存在的面板�?�这里用�?DOM 走完整条文本�?���?
  // �?��值确实按打字写进去了（�?�不�?? fill 卡住或走下拉）�??
  const written = [];
  let currentValue = '';
  const control = {
    async count() { return 1; },
    first() { return control; },
    async getAttribute() { return null; },
    async isVisible() { return true; },
    async fill(value) { written.push(String(value)); currentValue = String(value); },
    async inputValue() { return currentValue; },
  };
  const candidates = { async count() { return 1; }, nth() { return control; } };
  const label = { async count() { return 1; }, first() { return label; }, async innerText() { return '尺码:'; } };
  const item = {
    locator(selector) {
      if (selector.includes('el-form-item__label')) return label;
      if (selector === '.el-select') return { async count() { return 0; } };
      if (selector.startsWith('input:not(')) return candidates;
      return control;
    },
    async isVisible() { return true; },
  };
  // setField 会重新扫一遍表单（formItemForLabels 走 locator('.el-form-item').filter），
  // 真实 Playwright 的 Locator 本就有 .filter，替身也要带上。
  const items = { async count() { return 1; }, nth() { return item; }, filter() { return items; } };
  const frame = { locator() { return items; } };

  const result = await executor.selectElementOptionOrCustom(frame, item, ['尺�?', '102530'], ['90cm/100cm'], {});
  assert.deepEqual(result, { value: '90cm/100cm', method: 'text_input' });
  assert.deepEqual(written, ['90cm/100cm']);
});

test('�?��页面必填属�?�优先从模板值映射，不必等待 AI', () => {
  assert.equal(typeof executor.resolveTemplateAttributeFields, 'function');
  const fields = executor.resolveTemplateAttributeFields([
    {
      field_id: '100134', label: '材料', type: 'multi_select', required: true,
      options: [{ value: '1149', label: '棉花 (Cotton)' }],
    },
    {
      field_id: '100168', label: '套筒长度', type: 'single_select', required: true,
      options: [{ value: '1500', label: '�?? (Short Sleeves)' }],
    },
    {
      field_id: '100162', label: '图�?', type: 'multi_select', required: true,
      options: [{ value: '1486', label: '打印 (Print)' }],
    },
  ], {
    '100134': ['1149'],
    '100168': '1500',
  });
  assert.deepEqual(fields.map((field) => field.field_id), ['100134', '100168']);
  assert.deepEqual(fields[0].option_labels, ['棉花 (Cotton)']);
  assert.equal(fields[1].input_kind, 'select');
});

test('AI 必填属�?�推荐上下文包含标�?、�?情和识别属�?�，且只接收页面字�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /templateFields/);
  assert.match(source, /recognized_attributes/);
  assert.match(source, /basic\.title/);
  assert.match(source, /basic\.description/);
  assert.match(source, /const byId = new Map\(missing\.map/);
});
// 尺码图表表单项里，�?�上传图�?/ 选择模板」两�?��式单选和模板下拉渲染在同�?�?
// .el-form-item 里，�?radio �?DOM 里排在前�?��opacity:0，但 Playwright 仍判�??�?
// 且永远不�?readonly）�?�回读�?�?��"�?���?input"，�?到的就是 radio：模板在页面�?
// 已经选好，却回�?为空 �?select_value_not_verified �?size_chart_template_unresolved
// （现�?job 9c9effcb / 86d59af8）�?�这里把两个输入的区分钉死�??
test('size chart template read-back reads the select input, never the mode radios', async () => {
  const radio = {
    async getAttribute() { return null; },
    async inputValue() { return 'sizeChartId'; },
  };
  const templateInput = {
    async getAttribute(name) { return name === 'readonly' ? '' : null; },
    async inputValue() { return '21#儿�?�??'; },
  };
  const tags = { async allInnerTexts() { return []; }, async count() { return 0; }, first() { return tags; } };
  const itemFor = (control, loose) => ({
    locator(selector) {
      if (selector === executor.CONTROL_INPUT_SELECTOR) return control;
      if (selector === '.el-select input,.el-input input,input') return loose;
      return tags;
    },
  });

  const withSelect = itemFor({ async count() { return 1; }, first() { return templateInput; } }, { async count() { return 1; }, first() { return radio; } });
  assert.equal(await executor.valueInputForItem(withSelect), templateInput);
  assert.deepEqual(await executor.selectedTextsForItem(withSelect), ['21#儿�?�??']);

  // 没有"带�?�输入�?"的表单项（例如上传模式下�?�� radio + file）保持�?��?为：
  // �?回�?松�?�择器的�?���?���?���?readonly 判定决定读不读它的�?��??
  const uploadOnly = itemFor({ async count() { return 0; } }, { async count() { return 1; }, first() { return radio; } });
  assert.equal(await executor.valueInputForItem(uploadOnly), radio);
});
// 保存响应监听：只认写请求的成功响应，且能收尾摘掉�?
test('save response watcher records only successful write requests and stops cleanly', () => {
  const handlers = new Set();
  const page = {
    on(event, handler) { if (event === 'response') handlers.add(handler); },
    off(event, handler) { if (event === 'response') handlers.delete(handler); },
  };
  const watcher = executor.watchSaveResponses(page);
  const emitResponse = ({ method = 'POST', url, status = 200, ok = true }) => {
    for (const handler of handlers) {
      handler({ request: () => ({ method: () => method }), url: () => url, ok: () => ok, status: () => status });
    }
  };

  emitResponse({ method: 'GET', url: 'https://publish.mabangerp.com/api/product/list' });
  emitResponse({ method: 'POST', url: 'https://publish.mabangerp.com/api/product/save', status: 500, ok: false });
  assert.deepEqual(watcher.network, []);

  emitResponse({ url: 'https://publish.mabangerp.com/api/product/save' });
  assert.deepEqual(watcher.network, [{ path: '/api/product/save', status: 200 }]);

  watcher.stop();
  assert.equal(handlers.size, 0);
  emitResponse({ url: 'https://publish.mabangerp.com/api/product/save' });
  assert.equal(watcher.network.length, 1, 'stop 之后不能再�?�?');
});

// HN0012 现场：保存点击后 1.8s 内没等到响应，�?�代码在这里把监�?��了，响应再回来也�?��
// �?�?��"保存成功"�?条证�?�?save_result_not_proven（草稿其实已经存�?��。校验期间到�?
// 的迟到响应必须照样算证据，且校验（成�?失败）结束后都�? stop�?
test('a save response that arrives after the click still proves the draft was saved', async () => {
  const originalNow = Date.now;
  const originalSetTimeout = global.setTimeout;
  let now = 0;
  Date.now = () => { now += 1000; return now; };
  global.setTimeout = (callback) => { callback(); return 0; };
  const scenario = buildDialogPage();
  const network = [];
  let polls = 0;
  scenario.frame.evaluate = async () => {
    polls += 1;
    if (polls >= 3) network.push({ path: '/api/product/save', status: 200 });
    return { success_message: true, product_id: false, publish_clicked: false };
  };
  let stopped = 0;

  try {
    const result = await executor.verifySavedDraft(
      scenario.page,
      scenario.frame,
      { network, stopWatchingSave: () => { stopped += 1; } },
      { store: { listing_mode: 'site' }, basic_info: { parent_sku: 'HN0012' } },
    );

    assert.equal(result.ok, true);
    assert.equal(result.status, 'saved');
    assert.equal(result.evidence.save_response, true, '迟到的响应�?算进证据');
    assert.equal(result.evidence.success_message, true);
    assert.equal(result.publish_clicked, false);
    assert.equal(stopped, 1, '校验结束后必须摘掉保存响应监�?');
  } finally {
    Date.now = originalNow;
    global.setTimeout = originalSetTimeout;
  }
});

test('an unproven save still stops the response watcher', async () => {
  const originalNow = Date.now;
  const originalSetTimeout = global.setTimeout;
  let now = 0;
  Date.now = () => { now += 6000; return now; };
  global.setTimeout = (callback) => { callback(); return 0; };
  const frame = { evaluate: async () => ({ success_message: true, product_id: false, publish_clicked: false }) };
  const page = { close: async () => {} };
  let stopped = 0;

  try {
    await assert.rejects(
      executor.verifySavedDraft(page, frame, { network: [], stopWatchingSave: () => { stopped += 1; } }, { basic_info: {} }),
      (error) => error && error.reason === 'save_result_not_proven',
    );
  } finally {
    Date.now = originalNow;
    global.setTimeout = originalSetTimeout;
  }
  assert.equal(stopped, 1);
});

test('产品图最后一张放尺码�?��上传�?form 列表走，�?��就是尺码�?', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const runner = fs.readFileSync(path.join(__dirname, '../scripts/browser_use_runner.py'), 'utf8');

  // 执�?器优先用 form 列表（真实产品图 + �?��尺码�?��，没有时�?回真实产品图列表�?
  assert.match(source, /const formPaths = Array\.isArray\(images\.product_image_form_paths\)/);
  assert.match(source, /size_chart_last_product_image: Boolean\(images\.size_chart_last_product_image\)/);
  assert.match(source, /size_chart_slot_skipped: cleanText\(images\.size_chart_slot_skipped\)/);

  // 任务组�?�?form 列表与开关一起带到执行器，水印槽位按真实产品图张数算�?
  assert.match(runner, /"product_image_form_paths": product_image_form_paths/);
  assert.match(runner, /"size_chart_last_product_image": size_chart_last_product_image/);
  assert.match(runner, /product_image_count = len\(product_image_form_paths\) - \(1 if size_chart_last_product_image/);
});

test('产品图路径保留连续空格：真实目录名带着两个空格也能上传，不再误报 upload_file_missing', async () => {
  const os = require('node:os');

  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'executor-path-'));
  // NAS 素材里真实出现过「卫衣  1」这种两个空格的目录名。
  const folder = path.join(root, '卫衣  1');
  fs.mkdirSync(folder);
  const image = path.join(folder, 'SKU-10-黑色A.jpg');
  fs.writeFileSync(image, 'image-bytes');
  const uploaded = [];
  let cards = 0;
  const input = {
    getAttribute: async () => 'multiple',
    // 页面上每传完一张，卡片数 +1（真实页面就是这么涨的）。
    setInputFiles: async (filePath) => { uploaded.push(filePath); cards += 1; },
  };
  const frame = {
    evaluate: async () => cards,
    locator: () => ({ count: async () => 1, first: () => input }),
  };

  try {
    assert.equal(executor.cleanFilePath('  C:\\a  b\\c.jpg\n'), 'C:\\a  b\\c.jpg');
    const result = await executor.uploadProductImages(frame, {
      images: { product_image_form_paths: [image] },
      store: { listing_mode: 'site' },
    });
    assert.deepEqual(uploaded, [image], '上传给页面的必须是磁盘上的真实路径（中间两个空格原样保留）');
    assert.equal(result.count, 1);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('产品图落地判定数 .picture-group 里的 .images-item（回归 2026-10-26 四单误报 did_not_land）', async () => {
  const state = { cards: 0 };
  const group = {
    querySelectorAll: (selector) => (selector === '.images-item'
      ? Array.from({ length: state.cards }, (_, index) => ({ index }))
      : []),
  };
  const input = { closest: (selector) => (selector === '.picture-group' ? group : null) };
  const previous = global.document;

  const withDocument = async (doc, run) => {
    global.document = doc;
    try {
      return await run();
    } finally {
      if (previous === undefined) delete global.document;
      else global.document = previous;
    }
  };

  // 马帮真实结构：卡片是同组内的 .images-item；页面上没有任何 Element UI 上传列表节点，
  // 旧实现（数 .el-upload-list__item / upload-list）会恒为 0，于是每张图等满 90s。
  await withDocument({
    querySelector: (selector) => (selector === 'input[type="file"][multiple]' ? input : null),
    querySelectorAll: () => [],
  }, async () => {
    const frame = { evaluate: async (fn) => fn() };
    assert.equal(await executor.countProductImageCards(frame), 0);
    state.cards = 3;
    assert.equal(await executor.countProductImageCards(frame), 3);
  });

  // 没有 multiple 文件输入 = 页面结构不对，返回 -1（0 会被误当成"还没落地"）。
  await withDocument({ querySelector: () => null, querySelectorAll: () => [] }, async () => {
    assert.equal(await executor.countProductImageCards({ evaluate: async (fn) => fn() }), -1);
  });

  // 图片满 9 张后马帮会把 input 整个移除：此时必须还能从唯一的 .picture-group 数到卡片，
  // 否则最后一张永远"没落地"（2026-10-27 任务 9da6b384 的 index 8 / expected_after 9）。
  state.cards = 9;
  await withDocument({
    querySelector: () => null,
    querySelectorAll: (selector) => (selector === '.picture-group' ? [group] : []),
  }, async () => {
    assert.equal(await executor.countProductImageCards({ evaluate: async (fn) => fn() }), 9);
  });
});

test('表单里已经有产品图时拒绝重复上传（可重试错误，重开干净表单）', async () => {
  const uploaded = [];
  const frame = {
    evaluate: async () => 3,
    locator: () => ({
      count: async () => 1,
      first: () => ({
        getAttribute: async () => 'multiple',
        setInputFiles: async (file) => { uploaded.push(file); },
      }),
    }),
  };
  await assert.rejects(
    executor.uploadProductImages(frame, {
      images: { product_image_paths: ['C:\\tmp\\A红色.png'] },
      store: { listing_mode: 'site' },
    }),
    (error) => error.reason === 'product_image_slots_already_filled' && error.details.count === 3,
  );
  assert.deepEqual(uploaded, [], '已经在页面上的图不能再传一遍');

  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /'product_image_slots_already_filled',/);
});

test('失败的那次尝试上报 stage/code，界面才能显示失败原因而不是一行「步骤」', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const app = fs.readFileSync(path.join(__dirname, '../apps/web/app.js'), 'utf8');

  // 进度接口只存 stage / code 两个字段（storage.record_automation_job_progress）。
  assert.match(source, /emit\('attempt_failed', \{ \.\.\.entry, stage: entry\.failed_stage, code: entry\.error \}\)/);
  // 步骤列表要给失败行补上中文原因。
  // 媒体阶段（视频/漏插图）也要给「跳过」补原因，所以判定从 ok === false 扩成 failed||skipped。
  assert.match(app, /const stepCode = step\.code \|\| stageInfo\.code \|\| "";/);
  assert.match(app, /const reason = \(failed \|\| skipped\) && stepCode \? automationErrorLabel\(stepCode\) : ""/);
  assert.match(app, /class="automation-step-reason"/);
});

test('详情插图跳过产品图区�?��那张尺码�?', () => {
  const { descriptionProductImageUrls, buildDescriptionMediaPlan } = executor;
  const chart = 'https://cf.shopee.com/file/size-chart-1';
  const a = 'https://cf.shopee.com/file/product-a';
  const b = 'https://cf.shopee.com/file/product-b';

  // 1) 地址同名（同�?张尺码表�?�� CDN 主机名）�?去掉
  assert.deepEqual(
    descriptionProductImageUrls([a, b, 'https://down-th.img.susercontent.com/file/size-chart-1'], { sizeChartUrl: chart }),
    { urls: [a, b], dropped: 1 },
  );

  // 2) 地址不同名，但任务标了�?�产品图区末位是尺码表�?�→ 去掉�?后一�?
  assert.deepEqual(
    descriptionProductImageUrls([a, b, chart], { sizeChartUrl: '', sizeChartLastProductImage: true }),
    { urls: [a, b], dropped: 1 },
  );

  // 3) �?关没�?、地�?也不同名 �?原样保留
  assert.deepEqual(
    descriptionProductImageUrls([a, b], { sizeChartUrl: chart }),
    { urls: [a, b], dropped: 0 },
  );

  // 4) 计划顺序：尺码表在最前，产品图在后，�?��不再�?��码表
  const selected = descriptionProductImageUrls([a, b, chart], { sizeChartUrl: '', sizeChartLastProductImage: true });
  const plan = buildDescriptionMediaPlan({ sizeChartUrl: chart, productImageUrls: selected.urls });
  assert.deepEqual(plan.map((entry) => entry.kind), ['size_chart', 'product_image', 'product_image']);
  assert.equal(plan[plan.length - 1].url, b);
});

test('没有枚举的属性（条件/型号）按"条件那套逻辑"判定：认模型值，不点不存在的添加自选', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  // 1) 校验函数支持"模型也算证据"（条件 100413 / 型号 101639 在页面上没有枚举，
  //    马帮那个控件不会给枚举外的值渲染 tag）。
  const verifyFn = source.slice(source.indexOf('async function attributeValuesRendered('), source.indexOf('async function attributeValueRendered('));
  assert.match(verifyFn, /model_evidence: Boolean\(entry && entry\.model_evidence\)/);
  assert.match(verifyFn, /target\.model_evidence && modelText && sameText\(modelText, expected\)/);
  assert.match(verifyFn, /storeCategory\.ProductAttributeInfo\.attributesNew/);

  // 2) 本土循环里按"profile 里有没有枚举"决定哪些字段走这套判定。
  assert.match(source, /const modelEvidenceFields = new Set\(/);
  assert.match(source, /model_evidence: modelEvidenceFields\.has\(String\(entry\.field_id\)\)/);

  // 3) 模型写成功就收工（跟条件�?样），不再掉进型号没有入口的 DOM 点�?�；
  //    模型写失败也如实报原因，不�? select_option_not_found 把整单打死�??  assert.match(source, /note: 'model_value_without_enum'/);
  assert.match(source, /method: 'model_failed'/);
  // �۵��ڡ���ʾ�������ԡ�����Ŀ�ѡ�ֵ����ģ���� �� ������һ���������㣬����������
  assert.match(source, /note: 'model_value_item_hidden'/);
  assert.match(source, /modelEvidenceFields\.has\(String\(field\.field_id\)\)/);

  // 4) 有枚举的多�?�（材料/图�?/风格）仍旧走 tag 判定：不能因为这次改动放宽它�???  assert.match(verifyFn, /const sources = tagBased\s*\? tags\s*: \[\.\.\.tags, inputValue, genericPlaceholder \? '' : placeholder\]\.filter\(Boolean\);/);
});

test('模板值存的是选项 id 时：用副本快照的 id→标签翻成人能读的型号文字', () => {
  assert.equal(typeof executor.snapshotAttributeOptionLabels, 'function');
  assert.equal(executor.looksLikeOptionId('1790002081766'), true);
  assert.equal(executor.looksLikeOptionId('T-shirt'), false);
  assert.equal(executor.looksLikeOptionId('90cm/100cm'), false);

  const labels = executor.snapshotAttributeOptionLabels({
    fields: [
      { field_id: '101639', label: '型号', type: 'multi_select', options: [{ value: '1790002081766', label: 'T-shirt' }] },
      { field_id: '100134', label: '材料', options: [] },
    ],
  });
  assert.deepEqual([...labels.get('101639').entries()], [['1790002081766', 'T-shirt']]);
  assert.equal(labels.has('100134'), false);

  // 现场：该类目页面上的型号下拉是空的（暂无数据）→ 只能靠副本快照把 id 翻成文字。
  const fields = executor.resolveTemplateAttributeFields(
    [{ field_id: '101639', label: '型号', type: 'text', required: false, options: [] }],
    { '101639': ['1790002081766'] },
    labels,
  );
  assert.equal(fields.length, 1);
  assert.deepEqual(fields[0].option_labels, ['T-shirt']);
  assert.deepEqual(fields[0].matched_value_ids, []);
  assert.equal(fields[0].unresolved_option_id, false);
  assert.equal(fields[0].input_kind, 'text');
});

test('选项 id 翻不出文字时不当自定义值写进刊登（unresolved_option_id）', () => {
  const fields = executor.resolveTemplateAttributeFields(
    [{ field_id: '101639', label: '型号', type: 'text', required: false, options: [] }],
    { '101639': ['1790002081766'] },
    null,
  );
  assert.equal(fields.length, 1);
  assert.equal(fields[0].unresolved_option_id, true);
  assert.deepEqual(fields[0].option_labels, []);
  assert.deepEqual(fields[0].matched_value_ids, []);

  // 页面有枚举且匹配上时仍按 id 走：老路径不变，value_ids 语义保留。
  const matched = executor.resolveTemplateAttributeFields(
    [{ field_id: '100134', label: '材料', type: 'multi_select', required: true, options: [{ value: '1149', label: '棉花 (Cotton)' }] }],
    { '100134': ['1149'] },
    null,
  );
  assert.deepEqual(matched[0].matched_value_ids, ['1149']);
  assert.deepEqual(matched[0].option_labels, ['棉花 (Cotton)']);
  assert.equal(matched[0].input_kind, 'select');
  assert.equal(matched[0].unresolved_option_id, false);
});

test('自定义值通道写文字：文本分支不用 value_ids，模型批写只用真匹配到的 id', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /const value = optionLabels\.join\(', '\);/);
  assert.doesNotMatch(source, /const value = \(field\.value_ids \|\| \[\]\)\.join/);
  assert.match(source, /const values = \[\.\.\.\(field\.matched_value_ids \?\? field\.value_ids \?\? \[\]\), \.\.\.fieldLabels\]\.filter\(Boolean\);/);
  assert.match(source, /note: 'option_id_not_resolved'/);
  assert.match(source, /snapshotAttributeOptionLabels\(task\.category_profile_snapshot/);
});

test('自选值必须走 UI「搜索 → 添加 → 请输入自定义值」，不再认"只写页面模型"这条捷径', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.equal(typeof executor.applyCustomValueThroughUi, 'function');
  // 1) custom 值先走 UI 自选通道，成功记 custom_option_ui（现场：只写模型保存出来草稿里是空的）
  assert.match(source, /if \(applied\.ok && applied\.custom\) \{/);
  assert.match(source, /method: 'custom_option_ui'/);
  // 2) UI 自选失败：退回模型结果，但写成看得见的 warning，不假装成功
  assert.match(source, /note: 'custom_option_ui_failed'/);
  assert.match(source, /custom_option_ui_failed: customOptionUiFailures/);
  // 3) 模型捷径只对"真匹配到页面枚举"的值成立
  assert.doesNotMatch(
    source,
    /if \(applied\.ok && confirmed\.has\(String\(field\.field_id\)\)\) \{[\s\S]{0,400}?applied\.custom \? 'model_custom' : 'model'/,
  );
  // 4)「添加」不再只认 <button>：现场它是搜索框右边的文字链接/span，找不到按钮要再按 a/span 找一遍
  assert.match(source, /a:visible, span:visible, i:visible, \.add-btn:visible/);
  assert.match(source, /const addClickable = addFound \|\| \(await addLink\.count\(\)\.catch\(\(\) => 0\)\) > 0;/);
  const helper = source.slice(
    source.indexOf('async function applyCustomValueThroughUi('),
    source.indexOf('async function selectStore('),
  );
  assert.match(helper, /selectElementOptionOrCustom\(/);
  assert.match(helper, /failed: true/);
});

test('多选（tag）控件上的自选值：不接受"模型写完渲染出来的值"，必须真的走「添加」', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');

  // 1) 本土循环按控件类型决定是否强制走 UI 自选：tag 型 el-select = 多选（型号就是）。
  //    现场 2026-10-06：执行器记 already_selected，页面模型里 101639 还是原始文字、自填分组为空，
  //    马帮保存出来的草稿型号为空；手工「搜索 → 添加 → 请输入自定义值 → ✔」后模型才变成选项 id。
  assert.match(source, /const forceAdd = await isTagBasedControl\(present\);/);
  assert.match(source, /\{ forceAdd, fieldId: String\(field\.field_id\) \}/);

  // 2) 强制通道不再先走 selectElementOption（它会被"模型渲染出来的标签"骗成 already_selected）
  const helper = source.slice(
    source.indexOf('async function applyCustomValueThroughUi('),
    source.indexOf('// 强制自选通道的"这格真的带上了值吗"回读'),
  );
  assert.match(helper, /if \(settings && settings\.forceAdd\) \{/);
  assert.match(helper, /registeredCustomOption\(frame, fieldId, targets\)/);
  assert.match(helper, /addCustomSelectOption\(frame, present, labels, target, options\)/);
  assert.match(helper, /custom_option_registered/);
  assert.match(helper, /custom_option_add_failed/);
  // 单选/文本属性的老路径保持不变：仍然先试枚举再退回自选
  assert.match(helper, /selectElementOptionOrCustom\(frame, present, labels, targets, options\)/);

  // 3) 两个新工具函数在位
  assert.equal(typeof executor.registeredCustomOption, 'function');
  assert.equal(typeof executor.isTagBasedControl, 'function');
  assert.match(source, /async function registeredCustomOption\(frame, fieldId, values\)/);
  assert.match(source, /async function isTagBasedControl\(item\)/);
});

test('自填行的 ✔ 已经把值提交进控件后，不再点列表项（多选点一下就是取消选择）', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const helper = source.slice(
    source.indexOf('async function addCustomSelectOption('),
    source.indexOf('async function siteSelectOptions('),
  );
  assert.match(helper, /const echoed = await formItemForLabels\(frame, labels, options\);/);
  assert.match(helper, /method: 'custom_option' \}/);
});

test('强制自选通道：添加前先把"只写模型"造出来的假值清零，失败再写回', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  // 现场 2026-10-06 真机：控件里已经渲染着模型写进去的 T-shirt 时，走添加不会注册，
  // 反而把值清掉（custom_option_add_missed: before=["T-shirt","T-shirt"], after=[],
  // registered=false）。所以先清零、加成功后再由马帮写回选项 id。
  assert.equal(typeof executor.clearAttributeValueInModel, 'function');
  assert.match(source, /async function clearAttributeValueInModel\(frame, fieldId\)/);
  const helper = source.slice(
    source.indexOf('async function applyCustomValueThroughUi('),
    source.indexOf('// 强制自选通道的"这格真的带上了值吗"回读'),
  );
  assert.match(helper, /const cleared = await clearAttributeValueInModel\(frame, fieldId\);/);
  assert.match(helper, /await applyAttributeValueInModel\(frame, fieldId, targets\)\.catch\(\(\) => null\);/);
  const clearHelper = source.slice(
    source.indexOf('async function clearAttributeValueInModel('),
    source.indexOf('async function registeredCustomOption('),
  );
  assert.match(clearHelper, /attribute\.value = isArray \? \[\] : '';/);
  // 注册过的自选项要写回页面模型（保存读的是模型，不是控件标签）
  assert.match(helper, /await applyAttributeValueInModel\(frame, fieldId, \[option\.value\]\)/);
});

test('强制自选通道：页面模型里已经注册过同名自选项就不再重复添加，直接把它的选项 id 写回模型', async () => {
  const calls = [];
  let evaluateCount = 0;
  const frame = {
    locator: () => ({ async count() { return 0; } }),
    async evaluate() {
      calls.push('evaluate');
      evaluateCount += 1;
      // 1) registeredCustomOption：马帮已经登记过这个自选项（带平台 id）
      if (evaluateCount === 1) return { value: '1791278718240', label: 'T-shirt' };
      // 2) applyAttributeValuesInModel：把 id 写进页面表单模型（保存时用的就是它）
      return { results: [{ field_id: '101639', ok: true, applied: ['1791278718240'], labels: ['T-shirt'], is_array: true, custom: false }] };
    },
  };
  const result = await executor.applyCustomValueThroughUi(
    frame, ['型号', '101639'], ['T-shirt'], {}, {}, { forceAdd: true, fieldId: '101639' },
  );
  assert.equal(result.failed, undefined);
  assert.equal(result.method, 'custom_option_registered');
  assert.equal(result.value, 'T-shirt');
  assert.deepEqual(calls, ['evaluate', 'evaluate']);
});

test('强制自选通道：控件加不进去、页面也没注册过 → 如实报 custom_option_add_failed', async () => {
  const empty = {
    async count() { return 0; },
    first() { return empty; },
    async getAttribute() { return null; },
    async innerText() { return ''; },
    async allInnerTexts() { return []; },
    async inputValue() { return ''; },
    filter() { return empty; },
    nth() { return empty; },
  };
  const item = { locator: () => empty };
  const frame = { locator: () => empty, async evaluate() { return null; } };
  const result = await executor.applyCustomValueThroughUi(
    frame, ['型号', '101639'], ['T-shirt'], {}, item, { forceAdd: true, fieldId: '101639' },
  );
  assert.equal(result.failed, true);
  assert.equal(result.error, 'custom_option_add_failed');
});

test('applyCustomValueThroughUi：拿不到表单项时如实报错、不抛异常', async () => {
  const frame = { locator() { return { async count() { return 0; }, nth() { return null; } }; } };
  const result = await executor.applyCustomValueThroughUi(frame, ['型号', '101639'], ['T-shirt'], {}, null);
  assert.equal(result.failed, true);
  assert.ok(result.error);
});

test('描述图片宽度：没有图或都够宽时直接跳过，不打开「编辑图片」弹窗', async () => {
  const noImages = { evaluate: async () => [] };
  assert.deepEqual(
    await executor.ensureDescriptionImageWidths(noImages),
    { status: 'skipped', reason: 'no_description_images' },
  );

  const wide = { evaluate: async () => [{ src: 'a', width: 1000, height: 1000, loaded: true }] };
  const result = await executor.ensureDescriptionImageWidths(wide);
  assert.equal(result.status, 'skipped');
  assert.equal(result.reason, 'widths_ok');
});

test('描述图片宽度修复接在保存前校验之前（append 之后），阈值 700 / 目标 1000', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  assert.match(source, /const DESCRIPTION_IMAGE_MIN_WIDTH = 700/);
  assert.match(source, /const DESCRIPTION_IMAGE_TARGET_WIDTH = 1000/);
  assert.match(source, /\['fix_description_image_widths', async \(d\) => ensureDescriptionImageWidths\(d\.frame\)\]/);
  const stage = source.indexOf("['fix_description_image_widths'");
  const append = source.indexOf("['append_description_media'");
  const validate = source.indexOf("['validate_before_save'");
  assert.ok(stage > append, '描述图片都在 append_description_media 之后才齐');
  assert.ok(stage < validate, '修复必须发生在保存前校验/保存之前');
});

test('描述插图：正文后到把图挤到前面时位置校验必须失败', () => {
  // 现场症状：图有时跑到描述最前面。空正文时落光标＝落在位置 0，正文随后才填上，
  // 旧校验只看「最后一张图之后没有正文」，这种情形会误判成功。
  const pic = 'https://cf.shopee.co.id/file/pic01';
  const textBefore = '<p>这件T恤是纯棉面料，适合夏天日常穿着。</p>';
  const minTextBefore = executor.descriptionTextLength(textBefore);

  const ok = `${textBefore}<p><img src="${pic}"></p>`;
  assert.equal(executor.descriptionMediaAppendedAtTail(ok, [pic], [], { minTextBefore }), true);

  // 图插在最前面、正文后到：图后面虽然没正文，但图前面的正文变少了 → 不算插在末尾。
  const imagesFirst = `<p><img src="${pic}"></p>${textBefore}`;
  assert.equal(executor.descriptionMediaAppendedAtTail(imagesFirst, [pic], [], { minTextBefore }), false);
});

test('描述插图：正文没填上之前不许插（避免插在位置 0）', () => {
  const expected = '这件T恤是纯棉面料，适合夏天日常穿着。\n\n机洗冷水。';
  assert.equal(executor.descriptionTextLoaded('', expected), false);
  assert.equal(executor.descriptionTextLoaded('其它商品的正文', expected), false);
  assert.equal(executor.descriptionTextLoaded(`这件T恤是纯棉面料，适合夏天日常穿着。 机洗冷水。`, expected), true);
  // 任务里本来就没有详情：空正文是正常的，不算「没准备好」。
  assert.equal(executor.descriptionTextLoaded('', ''), true);
});

test('描述插图：引用产品图弹窗在点确定前要重新钉光标', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const body = source.slice(
    source.indexOf('async function insertDescriptionProductImages'),
    source.indexOf('// 插图落到正文中间时撤掉这批'),
  );
  // 勾选期间焦点在弹窗里，range 可能已经退回开头：确定前必须再钉一次。
  assert.match(body, /if \(typeof ensureCaret === 'function'\) await ensureCaret\(\);/);
  const batch = source.slice(
    source.indexOf('async function insertDescriptionMediaBatch'),
    source.indexOf('// 描述插图失败不阻断草稿'),
  );
  assert.match(batch, /insertDescriptionProductImages\(frame, editorId, pendingProducts\.map\(\(entry\) => entry\.url\), ensureCaret\)/);
});

test('描述插图：插完要静置再复验一次位置，抓正文异步回填', () => {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_playwright_executor.cjs'), 'utf8');
  const body = source.slice(
    source.indexOf('async function appendDescriptionMediaOnce'),
    source.indexOf('// 插一批图：尺码表先、产品图后'),
  );
  assert.match(body, /await sleep\(DESCRIPTION_TAIL_SETTLE_MS\);/);
  assert.match(body, /descriptionMediaAppendedAtTail\(settledHtml, expectedOrder, sizeChartExisting, \{ minTextBefore: beforeTextLength \}\)/);
  // 正文没填上就不插，交给警告（description_content_not_ready）
  assert.match(body, /description_content_not_ready/);
});
