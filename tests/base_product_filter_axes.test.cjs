'use strict';

// 商品维护「分类」行三个轴各自独立、同时生效（AND）：
//   阶段（首次导入 / 未复制 / 待执行 / 已完成）、人群（男童 / 女童）、商品类目（未分类 / 女装…）。
// 现场要求：筛男童 / 女童的时候，左边的「首次导入 / 未复制」不能被甩掉，要叠着筛。
// 这里抽 app.js 里真实的 matchesBaseProductFilters / toggleBaseCategoryFilter 来跑。

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_SOURCE = fs.readFileSync(path.join(__dirname, '../apps/web/app.js'), 'utf8');

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}`);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`无法解析 ${name} 的函数体`);
}

function makeSandbox() {
  const sandbox = {
    state: {
      baseProductStageFilter: '',
      baseProductAudienceFilter: '',
      baseProductCategoryFilter: '',
      baseProductTimeFilter: 'all',
      baseProductSearchQuery: '',
    },
    rendered: 0,
    cleared: 0,
    // 三个轴的取值函数换成正文字段，测试只关心「怎么组合」。
    baseProductStage: (product) => product.stage,
    baseProductAudienceKey: (product) => product.audience,
    baseProductCategoryKey: (product) => product.category,
    matchesBaseTimeFilter: () => true,
    clearBaseProductSelectionForFilter: () => { sandbox.cleared += 1; },
    renderBaseProducts: () => { sandbox.rendered += 1; },
  };
  vm.createContext(sandbox);
  vm.runInContext(
    [
      extractFunction(APP_SOURCE, 'normalizeBaseSkuPart'),
      extractFunction(APP_SOURCE, 'baseSkuSearchTokens'),
      extractFunction(APP_SOURCE, 'baseSkuTokenMatches'),
      extractFunction(APP_SOURCE, 'matchesBaseProductSearch'),
      extractFunction(APP_SOURCE, 'matchesBaseProductFilters'),
      extractFunction(APP_SOURCE, 'toggleBaseCategoryFilter'),
      'this.matchesBaseProductFilters = matchesBaseProductFilters;',
      'this.matchesBaseProductSearch = matchesBaseProductSearch;',
      'this.toggleBaseCategoryFilter = toggleBaseCategoryFilter;',
    ].join('\n\n'),
    sandbox,
  );
  return sandbox;
}

const PRODUCT = { stage: 'imported', audience: 'boys', category: '未分类' };

test('单独一轴生效时按该轴筛', () => {
  const box = makeSandbox();
  box.state.baseProductAudienceFilter = 'boys';
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT }), true);
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT, audience: 'girls' }), false);

  box.state.baseProductStageFilter = 'uncopied';
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT }), false);
});

test('阶段和人群叠着筛（男童 + 首次导入）', () => {
  const box = makeSandbox();
  box.state.baseProductStageFilter = 'imported';
  box.state.baseProductAudienceFilter = 'boys';
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT }), true);
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT, stage: 'uncopied' }), false);
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT, audience: 'girls' }), false);

  box.state.baseProductCategoryFilter = '女装';
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT }), false);
  assert.equal(box.matchesBaseProductFilters({ ...PRODUCT, category: '女装' }), true);
});

test('点人群 chip 不碰阶段轴，点阶段 chip 不碰人群轴', () => {
  const box = makeSandbox();
  box.toggleBaseCategoryFilter('stage:imported');
  assert.equal(box.state.baseProductStageFilter, 'imported');

  box.toggleBaseCategoryFilter('audience:boys');
  assert.equal(box.state.baseProductAudienceFilter, 'boys');
  assert.equal(box.state.baseProductStageFilter, 'imported', '选男童后「首次导入」必须还在');

  box.toggleBaseCategoryFilter('audience:girls');
  assert.equal(box.state.baseProductAudienceFilter, 'girls');
  assert.equal(box.state.baseProductStageFilter, 'imported', '换人群也不该清阶段');

  // 再点一次同一枚 chip = 取消这一轴，其它轴保留。
  box.toggleBaseCategoryFilter('audience:girls');
  assert.equal(box.state.baseProductAudienceFilter, '');
  assert.equal(box.state.baseProductStageFilter, 'imported');

  // 「全部」清掉三个轴。
  box.toggleBaseCategoryFilter('未分类');
  assert.equal(box.state.baseProductCategoryFilter, '未分类');
  box.toggleBaseCategoryFilter('');
  assert.equal(box.state.baseProductStageFilter, '');
  assert.equal(box.state.baseProductAudienceFilter, '');
  assert.equal(box.state.baseProductCategoryFilter, '');
});

test('换任何一轴都会清掉勾选并重画列表', () => {
  const box = makeSandbox();
  box.toggleBaseCategoryFilter('stage:uncopied');
  assert.equal(box.cleared, 1);
  assert.equal(box.rendered, 1);
});

// 货号模糊搜索：大小写 / 空格横线无关，包含匹配优先，长货号还允许按顺序缺字。
test('按主 SKU 模糊搜索货号', () => {
  const box = makeSandbox();
  const product = { ...PRODUCT, main_sku: 'AL1399', group_id: 'G-AL1399' };
  box.state.baseProductSearchQuery = '';
  assert.equal(box.matchesBaseProductFilters(product), true, '空的搜索词等于不过滤');

  for (const query of ['al1399', 'AL-1399', 'a1399', '1399 al', '  AL 1399  ']) {
    box.state.baseProductSearchQuery = query;
    assert.equal(box.matchesBaseProductSearch(product), true, `「${query}」应命中 AL1399`);
  }

  // 4 位以上允许按顺序缺字；3 位短词只做包含匹配，免得一次捞出一大片。
  box.state.baseProductSearchQuery = 'h580';
  assert.equal(box.matchesBaseProductSearch({ ...PRODUCT, main_sku: 'HN0580' }), true);
  box.state.baseProductSearchQuery = 'h58';
  assert.equal(box.matchesBaseProductSearch({ ...PRODUCT, main_sku: 'HN0580' }), false);

  box.state.baseProductSearchQuery = 'HN0244';
  assert.equal(box.matchesBaseProductSearch(product), false);
});

test('没分配主 SKU 时按分组 SKU 兜底搜索', () => {
  const box = makeSandbox();
  const product = { ...PRODUCT, main_sku: '', group_id: 'HN0244' };
  box.state.baseProductSearchQuery = 'hn024';
  assert.equal(box.matchesBaseProductSearch(product), true);
});

test('货号搜索和其它筛选轴取交集', () => {
  const box = makeSandbox();
  box.state.baseProductSearchQuery = 'AL1399';
  box.state.baseProductStageFilter = 'imported';
  const product = { ...PRODUCT, main_sku: 'AL1399' };
  assert.equal(box.matchesBaseProductFilters(product), true);
  assert.equal(box.matchesBaseProductFilters({ ...product, stage: 'uncopied' }), false);
  assert.equal(box.matchesBaseProductFilters({ ...product, main_sku: 'AL1400' }), false);
});
