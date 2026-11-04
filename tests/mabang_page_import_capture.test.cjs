'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function makeDomTable({ headers = [], rows = [], vue = null } = {}) {
  const headerNodes = headers.map(({ field, text }) => ({
    className: `vxe-header--column ${field}`,
    textContent: text,
    attributes: [{ name: 'colid', value: field }, { name: 'class', value: `vxe-header--column ${field}` }],
  }));
  const bodyNodes = rows.map((cells) => ({
    querySelectorAll(selector) {
      if (selector !== '.vxe-body--column') return [];
      return Object.entries(cells).map(([field, value]) => ({
        className: `vxe-body--column ${field}`,
        textContent: value?.text ?? String(value?.value ?? value ?? ''),
        attributes: [{ name: 'colid', value: field }, { name: 'class', value: `vxe-body--column ${field}` }],
        querySelectorAll(inputSelector) {
          if (inputSelector !== 'input') return [];
          return value && Object.prototype.hasOwnProperty.call(value, 'value')
            ? [{ value: String(value.value) }]
            : [];
        },
      }));
    },
  }));
  return {
    __vue__: vue,
    querySelectorAll(selector) {
      if (selector === '.vxe-header--column') return headerNodes;
      if (selector === '.vxe-body--row') return bodyNodes;
      return [];
    },
  };
}

function executeCapture({ href, columns, rows, picker = null, extraTables = [], domTables = [], importState = null, categoryInfo = {}, categoryCandidates = null, promotionDialog = null, vuexShopee = {} }) {
  const source = fs.readFileSync(path.join(__dirname, '../scripts/mabang_page_import_capture.js'), 'utf8');
  const table = { tableData: rows, getColumns: () => columns };
  if (domTables[0]) domTables[0].__vue__ = table;
  const storeCategory = {
    storeCategoryInfo: { shopId: 'cnsc-store-1', shopName: '广州小苗服饰有限公司' },
    categoryInfo: {
      categoryId: '100244',
      path: '男装>上衣>T恤',
      idPath: ['100011', '100054', '100244'],
      is_leaf: true,
      ...categoryInfo,
    },
    ProductAttributeInfo: { attributesNew: [] },
  };
  if (categoryCandidates) storeCategory.categoryNameArr = categoryCandidates;
  const detailVm = {
    $refs: {
      storeCategory,
      transactionInformationModuleRef: {
        $refs: { tradeInfoTableRef: table },
      },
    },
    $store: { state: { shopee: vuexShopee } },
  };
  const parsed = new URL(href);
  const context = {
    URL,
    window: importState ? { __AI_LISTING_CNSC_IMPORT_STATE__: importState } : {},
    location: {
      href,
      origin: parsed.origin,
      pathname: parsed.pathname,
      search: parsed.search,
      hash: parsed.hash,
    },
    document: {
      querySelector(selector) {
        if (selector === '#box1') return { __vue__: detailVm };
        if (selector === '#shops-wap') return picker;
        return null;
      },
      querySelectorAll(selector) {
        if (selector === '.vxe-table') return [...domTables, ...extraTables];
        if (selector === '.vxe-header--column') return domTables.flatMap((table) => table.querySelectorAll(selector));
        if (promotionDialog && selector === '.el-dialog,.el-dialog__wrapper,[role="dialog"]') return [promotionDialog];
        return [];
      },
    },
  };
  const capture = vm.runInNewContext(`(${source})`, context);
  return JSON.parse(JSON.stringify(capture({ platform: 'shopee', country: '' })));
}

function buildCnscPicker({ phSelected = true } = {}) {
  const storeLabel = (siteCode, storeId, storeName) => ({
    dataset: { siteCode, storeId, storeName },
    textContent: storeName,
  });
  const radio = (siteCode, storeId, storeName, checked = true) => {
    const label = storeLabel(siteCode, storeId, storeName);
    return {
      checked,
      value: storeId,
      dataset: {},
      closest() { return label; },
      parentElement: label,
    };
  };
  const group = (siteCode, countryName, radios) => ({
    dataset: { siteCode },
    textContent: countryName,
    querySelectorAll(selector) { return selector === 'input[type="radio"]' ? radios : []; },
  });
  const twGroup = group('TW', '台湾', [radio('TW', 'tw-1', '海宁TW')]);
  const phGroup = group('PH', '菲律宾', [radio('PH', 'ph-1', '海宁PH', false), radio('PH', 'ph-2', '海宁PH1', phSelected)]);
  const sipGroup = group('SIP', 'SIP', [radio('SIP', 'sip-1', '自动分发SIP')]);
  const checkbox = (value, siteGroup) => ({
    checked: true,
    value,
    dataset: { siteCode: value },
    closest() { return siteGroup; },
    parentElement: siteGroup,
  });
  const checkboxes = [checkbox('TW', twGroup), checkbox('PH', phGroup), checkbox('SIP', sipGroup)];
  return {
    querySelectorAll(selector) {
      if (selector === 'input[type="checkbox"]') return checkboxes;
      return [];
    },
  };
}

test('current-page capture detects CNSC from hash and semantic VXE headers without leaking cKey', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17&cKey=secret-token&global=4',
    columns: [
      { field: 'col_1', title: '颜色' },
      { field: 'col_2', title: '尺码' },
      { field: 'col_4', title: 'Seller SKU' },
      { field: 'col_6', title: '全球商品价格' },
      { field: 'col_10', title: '泰国仓库存' },
      { field: 'col_12', title: '广州仓库存' },
      { field: 'col_14', title: '菲律宾仓库存' },
    ],
    rows: [{
      col_1: 'A', col_2: 'XS', col_4: '11991_A||XS', col_6: '19.2', col_10: '300', col_12: '0', col_14: '0',
    }],
  });

  assert.equal(result.listing_mode, 'cnsc_global');
  assert.deepEqual(result.listing_mode_evidence, {
    global_param: true,
    matched_headers: ['全球商品价格', '泰国仓库存', '广州仓库存', '菲律宾仓库存'],
  });
  assert.deepEqual(result.sku_rows[0], {
    color: 'A',
    size: 'XS',
    seller_sku: '11991_A||XS',
    global_price_cny: '19.2',
    warehouse_stocks: { TH: '300', CN_GZ: '0', PH: '0' },
    site_prices: {},
    stock: '',
    weight_kg: '',
    package_length_cm: '',
    package_width_cm: '',
    package_height_cm: '',
    normal_price: '',
    sale_price: '',
  });
  assert.equal(result.page.url, 'https://publish.mabangerp.com/publish-ui/');
  assert.doesNotMatch(JSON.stringify(result), /cKey|secret-token/);
});

test('ordinary current-page capture remains site mode when neither CNSC signal exists', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [{ field: 'size', title: '尺码' }, { field: 'stock', title: '库存' }],
    rows: [{ size: 'S', stock: 999 }],
  });

  assert.equal(result.listing_mode, 'site');
  assert.deepEqual(result.listing_mode_evidence, { global_param: false, matched_headers: [] });
  assert.equal(result.sku_rows[0].stock, 999);
});

test('an empty CNSC site-picker shell is not treated as a loaded target-store catalog', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [
      { field: 'col_6', title: '全球商品价格' },
      { field: 'col_10', title: '泰国仓库存' },
      { field: 'col_12', title: '广州仓库存' },
      { field: 'col_14', title: '菲律宾仓库存' },
    ],
    rows: [{ col_6: '19.2', col_10: '300', col_12: '0', col_14: '0' }],
    picker: { querySelectorAll: () => [] },
  });

  assert.equal(result.target_site_stores_loaded, false);
  assert.deepEqual(result.target_site_stores, []);
});

test('CNSC target stores come from checked site groups and selected store radios', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [
      { field: 'price', title: '全球商品价格' },
      { field: 'th', title: '泰国仓库存' },
      { field: 'gz', title: '广州仓库存' },
      { field: 'ph', title: '菲律宾仓库存' },
    ],
    rows: [{ price: 19.2, th: 300, gz: 0, ph: 0 }],
    picker: buildCnscPicker(),
  });

  assert.equal(result.target_site_stores_loaded, false);
  assert.deepEqual(result.target_site_stores, [
    { site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW' },
    { site_code: 'PH', store_id: 'ph-2', store_name: '海宁PH1' },
  ]);
  assert.equal(result.target_site_stores.some((item) => ['台湾', '菲律宾'].includes(item.store_name)), false);
});

test('CNSC target stores stay unloaded when any checked manual site lacks one selected store', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [
      { field: 'price', title: '全球商品价格' },
      { field: 'th', title: '泰国仓库存' },
      { field: 'gz', title: '广州仓库存' },
      { field: 'ph', title: '菲律宾仓库存' },
    ],
    rows: [{ price: 19.2, th: 300, gz: 0, ph: 0 }],
    picker: buildCnscPicker({ phSelected: false }),
  });

  assert.equal(result.target_site_stores_loaded, false);
  assert.deepEqual(result.target_site_stores, [{ site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW' }]);
});

test('CNSC capture accepts all eight manual sites while excluding SIP', () => {
  const sites = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR']
    .map((site) => ({ site_code: site, store_id: `${site.toLowerCase()}-1`, store_name: `海宁${site}` }));
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [{ field: 'price', title: '全球商品价格' }, { field: 'th', title: '泰国仓库存' }, { field: 'gz', title: '广州仓库存' }, { field: 'ph', title: '菲律宾仓库存' }],
    rows: [{ price: 19.2, th: 300, gz: 0, ph: 0 }],
    importState: { target_site_stores_loaded: true, target_site_stores: [...sites, { site_code: 'SIP', store_id: 'sip-1', store_name: '自动分发' }] },
  });
  assert.equal(result.target_site_stores_loaded, true);
  assert.deepEqual(result.target_site_stores.map((item) => item.site_code), ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR']);
});

test('CNSC capture preserves an explicit failed preparation instead of rereading hidden picker state', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [
      { field: 'price', title: '全球商品价格' },
      { field: 'th', title: '泰国仓库存' },
      { field: 'gz', title: '广州仓库存' },
      { field: 'ph', title: '菲律宾仓库存' },
    ],
    rows: [{ price: 19.2, th: 300, gz: 0, ph: 0 }],
    picker: buildCnscPicker(),
    importState: {
      target_site_stores_loaded: false,
      target_site_stores: [{ site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW' }],
    },
  });

  assert.equal(result.target_site_stores_loaded, false);
  assert.deepEqual(result.target_site_stores, [{ site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW' }]);
});

test('CNSC capture keeps grouped site prices and per-store discount evidence', () => {
  const sitePriceTable = {
    tableData: [{ sku: '11991_A||XS', color: 'A', size: 'XS', twOriginal: 372, twSale: 223.2 }],
    getColumns: () => [
      { field: 'sku', title: 'Seller SKU' },
      { field: 'color', title: '颜色' },
      { field: 'size', title: '尺码' },
      { title: 'TW', children: [{ field: 'twOriginal', title: '价格' }, { field: 'twSale', title: '折扣价' }] },
    ],
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [
      { field: 'sku', title: 'Seller SKU' },
      { field: 'price', title: '全球商品价格' },
      { field: 'th', title: '泰国仓库存' },
      { field: 'gz', title: '广州仓库存' },
      { field: 'ph', title: '菲律宾仓库存' },
    ],
    rows: [{ sku: '11991_A||XS', color: 'A', size: 'XS', price: 19.2, th: 300, gz: 0, ph: 0 }],
    extraTables: [{ __vue__: sitePriceTable }],
    picker: buildCnscPicker(),
    importState: {
      target_site_stores_loaded: true,
      target_site_stores: [{
        site_code: 'TW', store_id: 'tw-1', store_name: '海宁TW',
        discount_catalog_loaded: true,
        discount_catalog: [{ mabang_discount_id: 'discount-40', name: '秋季活动 40%', discount_percent: 40 }],
        mabang_discount_id: 'discount-40', discount_name: '秋季活动 40%', discount_label: '40%', discount_percent: 40,
      }],
    },
  });

  assert.deepEqual(result.sku_rows[0].site_prices, {
    TW: { original_price: 372, sale_price: 223.2 },
  });
  assert.equal(result.target_site_stores[0].discount_catalog_loaded, true);
  assert.equal(result.target_site_stores[0].mabang_discount_id, 'discount-40');
  assert.equal(result.target_site_stores[0].discount_percent, 40);
});

test('CNSC capture falls back to rendered global inputs when Vue rows omit price and warehouse fields', () => {
  const domTable = makeDomTable({
    headers: [
      { field: 'col_287', text: 'color' },
      { field: 'col_288', text: 'size' },
      { field: 'col_3', text: 'SKU' },
      { field: 'col_5', text: '* 全球商品价格' },
      { field: 'col_9', text: '*泰国仓库存' },
      { field: 'col_11', text: '*广州仓库存' },
      { field: 'col_13', text: '*菲律宾仓库存' },
    ],
    rows: [{
      col_287: 'A', col_288: 'XS', col_4: { value: '11991_A||XS' }, col_6: { value: '19.2' },
      col_10: { value: '300' }, col_12: { value: '0' }, col_14: { value: '0' },
    }],
  });
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [{ field: 'color', title: 'color' }, { field: 'size', title: 'size' }],
    rows: [{ color: 'A', size: 'XS' }],
    domTables: [domTable],
  });

  assert.deepEqual(result.sku_rows[0], {
    color: 'A',
    size: 'XS',
    seller_sku: '11991_A||XS',
    global_price_cny: '19.2',
    warehouse_stocks: { TH: '300', CN_GZ: '0', PH: '0' },
    stock: '',
    weight_kg: '',
    package_length_cm: '',
    package_width_cm: '',
    package_height_cm: '',
    normal_price: '',
    sale_price: '',
    site_prices: {},
  });
});

test('CNSC capture parses rendered site price cells and preserves each site percentage', () => {
  const globalDom = makeDomTable({
    headers: [
      { field: 'col_5', text: '全球商品价格' }, { field: 'col_9', text: '泰国仓库存' },
      { field: 'col_11', text: '广州仓库存' }, { field: 'col_13', text: '菲律宾仓库存' },
    ],
    rows: [{ col_6: { value: '19.2' }, col_10: { value: '300' }, col_12: { value: '0' }, col_14: { value: '0' } }],
  });
  const siteTable = { tableData: [{ color: 'A', size: 'XS' }], getColumns: () => [] };
  const siteDom = makeDomTable({
    vue: siteTable,
    headers: [
      { field: 'col_17', text: 'TW' }, { field: 'col_18', text: '价格' }, { field: 'col_20', text: '折扣活动40%' },
      { field: 'col_47', text: 'VN' }, { field: 'col_48', text: '价格' }, { field: 'col_50', text: '折扣活动20%' },
    ],
    rows: [{
      col_19: { text: 'TWD372≈USD 11.70' }, col_21: { text: '223或40.05%OFF' },
      col_49: { text: 'VND335000≈USD 12.86' }, col_51: { text: '268000或20.00%OFF' },
    }],
  });
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?global=4',
    columns: [{ field: 'price', title: '全球商品价格' }, { field: 'th', title: '泰国仓库存' }, { field: 'gz', title: '广州仓库存' }, { field: 'ph', title: '菲律宾仓库存' }],
    rows: [{ color: 'A', size: 'XS', sku: '11991_A||XS' }],
    domTables: [globalDom],
    extraTables: [siteDom],
  });

  assert.deepEqual(result.sku_rows[0].site_prices, {
    TW: { original_price: '372', sale_price: '223' },
    VN: { original_price: '335000', sale_price: '268000' },
  });
});

test('category path is emitted in the catalog canonical " > " form', () => {
  // 目录（marketplace_category_catalog.py）用 " > " 拼接；采集侧写紧凑形式会让
  // 保存模板时的路径校验失败（同类目被判成不匹配）。
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
  });

  assert.equal(result.store_category_state.category.path, '男装 > 上衣 > T恤');
});

test('category path already in catalog form is preserved unchanged', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    categoryInfo: { path: 'Men Clothes > Tops > T-shirts' },
  });

  assert.equal(result.store_category_state.category.path, 'Men Clothes > Tops > T-shirts');
});

test('comma-separated category name path becomes the canonical catalog path', () => {
  // 真机页面（HN_POD 童装）只有 name_en_path，没有 categoryInfo.path。
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    categoryInfo: { path: '', categoryId: '101757' },
    categoryCandidates: [{
      categoryId: '101757',
      name_en_path: 'Baby & Kids Fashion,Boy Clothes,Tops,T-shirts',
      name_zh_path: '婴儿和儿童时尚,男孩服装,上衣,T恤',
    }],
  });

  assert.equal(
    result.store_category_state.category.path,
    'Baby & Kids Fashion > Boy Clothes > Tops > T-shirts',
  );
});

test('messy separators and padding collapse to the canonical catalog path', () => {
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    categoryInfo: { path: '  Men Clothes  >  Tops >T-shirts  ' },
  });

  assert.equal(result.store_category_state.category.path, 'Men Clothes > Tops > T-shirts');
});

test('promotion options come from the discount dialog table with validity and status', () => {
  // 真机弹窗里活动列表挂在表格组件的 __vue__.tableData 上；只扫描弹窗的
  // $parent 链会读空，退化成"只有 id+名称"的一条（缺状态/有效期 → 后端判成不可用）。
  const activityTable = {
    __vue__: {
      tableData: [{
        id: 8547175,
        discount_id: 928232107737088,
        discount_name: '40%',
        shopId: 2022625115,
        start_time: '2026-05-28 17:30:00',
        end_time: '2026-11-23 23:59:00',
        product_count: 1,
        flag: 2,
        discount_status: 'ongoing',
      }],
    },
  };
  const dialog = {
    textContent: '选择折扣活动 活动名称 产品数 活动时间 状态 操作',
    querySelectorAll: (selector) => (selector.includes('table') ? [activityTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
    // 页面选中的活动 = 表格里那一条 → 采集应把它标记为 selected。
    vuexShopee: { variantAttributeModule: { mdDiscountActivity: { discount_id: '928232107737088', discount_name: '40%' } } },
  });

  assert.deepEqual(result.promotion_options, [{
    activity_id: '928232107737088',
    name: '40%',
    discount_rate: '',
    start_at: '2026-05-28 17:30:00',
    end_at: '2026-11-23 23:59:00',
    status: 'ongoing',
    selected: true,
  }]);
});

test('empty dialog table leaves no fabricated promotion entry', () => {
  const emptyTable = { __vue__: { tableData: [] } };
  const dialog = {
    textContent: '选择折扣活动 暂无内容',
    querySelectorAll: (selector) => (selector.includes('table') ? [emptyTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
  });

  assert.deepEqual(result.promotion_options, []);
});

test('promotion_catalog_loaded is false when only the selected activity was echoed', () => {
  // 弹窗里没有活动行：这次没有资格判定其它活动失效。
  const emptyTable = { __vue__: { tableData: [] } };
  const dialog = {
    textContent: '选择折扣活动 暂无内容',
    querySelectorAll: (selector) => (selector.includes('table') ? [emptyTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
    vuexShopee: { variantAttributeModule: { mdDiscountActivity: { discount_id: '477160318320861', discount_name: '40%12月29到期' } } },
  });

  assert.equal(result.promotion_catalog_loaded, false);
  assert.equal(result.promotion_options.length, 1);
});

test('promotion_catalog_loaded is true when the dialog table provided activities', () => {
  const activityTable = {
    __vue__: { tableData: [{ discount_id: 928232107737088, discount_name: '40%', start_time: '2026-05-28 17:30:00', end_time: '2026-11-23 23:59:00', flag: 2 }] },
  };
  const dialog = {
    textContent: '选择折扣活动 活动名称 状态 操作',
    querySelectorAll: (selector) => (selector.includes('table') ? [activityTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
  });

  assert.equal(result.promotion_catalog_loaded, true);
});

test('a selection on another page of the activity list is kept (not judged stale)', () => {
  // 弹窗 10 条/页：本页没有选中的那条，但 total 显示还有别的页 → 不能判它失效，
  // 否则导入确认会报"所选促销活动不属于当前店铺采集目录"。
  const rows = Array.from({ length: 10 }, (_, index) => ({
    discount_id: `page1-${index}`,
    discount_name: `活动${index}`,
    start_time: '2026-10-01 00:00:00',
    end_time: '2026-12-01 00:00:00',
    flag: 2,
  }));
  const activityTable = { __vue__: { tableData: rows } };
  const dialog = {
    __vue__: { total: 25 },
    textContent: '选择折扣活动 活动名称 状态 操作',
    querySelectorAll: (selector) => (selector.includes('table') ? [activityTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
    vuexShopee: { variantAttributeModule: { mdDiscountActivity: { discount_id: 'picked-on-page-2', discount_name: '第二页活动' } } },
  });

  assert.equal(result.promotion_catalog_loaded, true);
  const kept = result.promotion_options.find((item) => item.activity_id === 'picked-on-page-2');
  assert.ok(kept, '分页列表里缺失的选中项要回显，不能丢');
  assert.equal(kept.selected, true);
  // 只有名称，没有状态/有效期 → 后端判 unresolved，不会冒充可用。
  assert.equal(kept.status, '');
  assert.ok(result.promotion_options.some((item) => item.activity_id === 'page1-0'));
});

test('a stale page selection is not echoed when the store list was read', () => {
  // 页面上选中的活动不在店铺列表里（草稿从别处带过来的）→ 不能回显并绑定它。
  const activityTable = {
    __vue__: { tableData: [{ discount_id: 928232107737088, discount_name: '40%', start_time: '2026-05-28 17:30:00', end_time: '2026-11-23 23:59:00', flag: 2, shopId: 2022625115 }] },
  };
  const dialog = {
    textContent: '选择折扣活动 活动名称 状态 操作',
    querySelectorAll: (selector) => (selector.includes('table') ? [activityTable] : []),
  };
  const result = executeCapture({
    href: 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17',
    columns: [],
    rows: [],
    promotionDialog: dialog,
    vuexShopee: { variantAttributeModule: { mdDiscountActivity: { discount_id: '477160318320861', discount_name: '40%12月29到期' } } },
  });

  assert.equal(result.promotion_catalog_loaded, true);
  assert.deepEqual(result.promotion_options.map((item) => item.activity_id), ['928232107737088']);
  assert.equal(result.promotion_options[0].selected, false);
});
