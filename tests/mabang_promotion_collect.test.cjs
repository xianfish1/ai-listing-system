'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const collector = require('../scripts/mabang_promotion_collect.cjs');

const SOURCE = fs.readFileSync(path.join(__dirname, '../scripts/mabang_promotion_collect.cjs'), 'utf8');

test('店铺活动重采：--cdp-url / --store-id / --plan 解析', () => {
  assert.deepEqual(collector.parseArgs([]), { cdpUrl: 'http://127.0.0.1:9223', storeId: '', plan: {} });
  assert.deepEqual(
    collector.parseArgs(['--cdp-url', 'http://127.0.0.1:9333', '--store-id', ' 2022749877 ', '--plan', '{"platform":"shopee"}']),
    { cdpUrl: 'http://127.0.0.1:9333', storeId: '2022749877', plan: { platform: 'shopee' } },
  );
  assert.deepEqual(collector.parseArgs(['--plan', '{oops']), { cdpUrl: 'http://127.0.0.1:9223', storeId: '', plan: {} });
});

test('店铺活动重采：复用同一份页面采集器与弹窗模块', () => {
  assert.match(SOURCE, /mabang_page_import_capture\.js/);
  assert.match(SOURCE, /mabang_promotion_dialog\.page\.js/);
  assert.match(SOURCE, /listing_mode \|\| 'site'\).*=== 'cnsc_global'/);
});

test('店铺活动重采：结果里不回传 cKey 会话参数', () => {
  const url = 'https://publish.mabangerp.com/publish-ui/#/productDetailShopee?platformId=17&cKey=secret-token&global=1';
  const safe = collector.safePageUrl(url);
  assert.ok(!safe.includes('secret-token'));
  assert.ok(safe.includes('platformId=17'));
  assert.ok(safe.includes('global=1'));
  assert.ok(!collector.safePageUrl('not a url cKey=abc').includes('abc'));
});

test('店铺活动重采：浏览器连不上时返回中文指引', async () => {
  const result = await collector.collectStorePromotions({ cdpUrl: 'http://127.0.0.1:9', storeId: 'x' });
  assert.equal(result.ok, false);
  assert.equal(result.status, 'browser_unreachable');
  assert.match(result.error, /连不上马帮采集浏览器/);
});

test('店铺活动重采是只读的：不保存、不选活动', () => {
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const text of ['保存并刊登', '刊登', '发布', '提交审核']) {
    assert.ok(!code.includes(text), `重采脚本里不应该出现「${text}」`);
  }
  // 关闭弹窗走共享模块（Escape / 右上角关闭），这里不点任何活动行。
  assert.match(SOURCE, /phase/);
  assert.match(SOURCE, /store_mismatch/);
  assert.match(SOURCE, /promotion_catalog_not_loaded/);
  // 浏览器没起来时给中文指引，不要把 Playwright 的英文连接错误直接抛给操作员。
  assert.match(SOURCE, /browser_unreachable/);
  assert.match(SOURCE, /连不上马帮采集浏览器/);
});
