'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const harness = require('../scripts/mabang_browser_harness.cjs');

test('harness rejects publish targets before touching the page', () => {
  assert.throws(
    () => harness.validateAction({ type: 'click', target: '发布' }),
    (error) => error.code === 'forbidden_action',
  );
});

test('harness resolves CNSC shop by site, name, and id', () => {
  const candidates = [{ siteCode: 'TW', labelText: '海宁TW', storeIds: ['59760739'], names: ['海宁TW'] }];
  assert.deepEqual(
    harness.resolveShopTarget(candidates, { site: 'TW', name: '海宁TW', id: '59760739' }),
    candidates[0],
  );
});

test('harness snapshot is bounded and excludes credential-like fields', async () => {
  const page = {
    url: () => 'https://publish.mabangerp.com/productDetailShopee',
    title: async () => '商品详情',
    locator: () => ({ evaluate: async () => '<input name="password" value="secret">' }),
  };
  const snapshot = await harness.buildPageSnapshot(page, { failedStage: 'select_cnsc_target_sites' });
  assert.equal(snapshot.url.includes('productDetailShopee'), true);
  assert.equal(snapshot.dom.includes('secret'), false);
  assert.equal(snapshot.dom.length <= 12000, true);
});

test('harness uses stable semantic selectors for title and description fields', () => {
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '../scripts/mabang_browser_harness.cjs'), 'utf8');
  assert.match(source, /input\[placeholder\*="标题"\]/);
  assert.match(source, /input\[name\*="title" i\]/);
  assert.match(source, /target === 'description_input'/);
});
