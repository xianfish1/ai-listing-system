'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const collector = require('../scripts/mabang_page_import_collect.cjs');

const SOURCE = fs.readFileSync(path.join(__dirname, '../scripts/mabang_page_import_collect.cjs'), 'utf8');

test('页面采集器：--cdp-url / --plan 解析（plan 坏了退回空对象）', () => {
  assert.deepEqual(collector.parseArgs([]), { cdpUrl: 'http://127.0.0.1:9223', plan: {} });
  assert.deepEqual(
    collector.parseArgs(['--cdp-url', 'http://127.0.0.1:9333', '--plan', '{"platform":"shopee","country":"PH"}']),
    { cdpUrl: 'http://127.0.0.1:9333', plan: { platform: 'shopee', country: 'PH' } },
  );
  assert.deepEqual(collector.parseArgs(['--plan', '{oops']), { cdpUrl: 'http://127.0.0.1:9223', plan: {} });
});

test('页面采集器：只认 Shopee 商品编辑页', () => {
  assert.equal(collector.isProductEditUrl('https://publish.mabangerp.com/publish-ui/#/productDetailShopee?id=9'), true);
  assert.equal(collector.isProductEditUrl('https://publish.mabangerp.com/publish-ui/#/publishProductListV2'), false);
});

test('页面采集器复用同一份采集脚本，不复制逻辑', () => {
  // 采集逻辑只有 scripts/mabang_page_import_capture.js 一份：这里读它、在页面里执行它。
  assert.match(SOURCE, /mabang_page_import_capture\.js/);
  assert.match(SOURCE, /readFileSync\(CAPTURE_PATH/);
  assert.match(SOURCE, /\(0, eval\)\(`\(\$\{source\}\)`\)\(inputPlan\)/);
  assert.match(SOURCE, /capture\.source !== 'mabang_current_page'/);
});

test('页面采集器是只读的：不点保存/刊登/发布', () => {
  // 注释里会提到这些词（说明"不点它们"），所以先剥掉注释只查代码。
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const text of ['保存并刊登', '刊登', '发布', '提交审核']) {
    assert.ok(!code.includes(text), `采集器代码里不应该出现「${text}」`);
  }
  // 只允许为了读水印目录展开一次「添加水印」，并且必须收回去。
  assert.match(SOURCE, /ADD_WATERMARK_TEXT = '\\u6dfb\\u52a0\\u6c34\\u5370'/);
  assert.match(SOURCE, /new KeyboardEvent\('keydown', \{ key: 'Escape'/);
  assert.match(SOURCE, /不点任何水印选项、不点保存\/刊登\/发布/);
  // 采集结果里必须带上「采集时打开了哪些商品编辑页」，多开时能看出来。
  assert.match(SOURCE, /product_pages: targets\.map/);
});
