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

test('页面采集器：活动列表没读到 / 选中活动缺有效期时展开一次「选择折扣活动」，采完收回', () => {
  // 活动列表只在弹窗打开时才有行，没打开时只能回显当前选中项（缺状态/有效期）。
  assert.match(SOURCE, /PROMOTION_DIALOG_HINT = '\\u9009\\u62e9\\u6298\\u6263\\u6d3b\\u52a8'/);
  assert.match(SOURCE, /async function openPromotionList/);
  assert.match(SOURCE, /async function closePromotionList/);
  // 弹窗开关逻辑只有一份，和「单独重采店铺活动」共用（真机触发点是表头 .change-discount）。
  assert.match(SOURCE, /mabang_promotion_dialog\.page\.js/);
  const dialogSource = fs.readFileSync(path.join(__dirname, '../scripts/mabang_promotion_dialog.page.js'), 'utf8');
  assert.match(dialogSource, /change-discount/);
  assert.match(dialogSource, /vxe-header--column/);
  // 必须先采一遍、确认目录没读到（或选中项没有效期）才去点开；CNSC 走另一条链路。
  assert.match(SOURCE, /capture = await readCapture\(\);/);
  assert.match(SOURCE, /if \(promotionCatalogMissing\(capture\) \|\| promotionValidityMissing\(capture\)\)/);
  assert.match(SOURCE, /promotion_dialog: promotionDialog/);
  // 收尾：Escape + 弹窗右上角关闭；代码里不出现任何"确定"点击。
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!code.includes('确定'), '采集器代码里不应该出现「确定」');
  assert.match(dialogSource, /el-dialog__headerbtn/);
  assert.equal(collector.promotionCatalogMissing({ listing_mode: 'site', promotion_catalog_loaded: true }), false);
  assert.equal(collector.promotionCatalogMissing({ listing_mode: 'site', promotion_catalog_loaded: false }), true);
  assert.equal(collector.promotionCatalogMissing({ listing_mode: 'cnsc_global', promotion_catalog_loaded: false }), false);
  assert.equal(collector.promotionCatalogMissing(null), false);
});

test('页面采集器：选中活动没有有效期时也要重采（首采只有 selected 标记）', () => {
  const missing = {
    listing_mode: 'site',
    promotion_catalog_loaded: true,
    promotion_options: [{ activity_id: '1', name: '新店40%', start_at: '', end_at: '', selected: true }],
  };
  assert.equal(collector.promotionValidityMissing(missing), true);
  const complete = {
    listing_mode: 'site',
    promotion_catalog_loaded: true,
    promotion_options: [{ activity_id: '1', name: '新店40%', start_at: '2026-10-19 23:57:00', end_at: '2027-04-17 01:00:00', selected: true }],
  };
  assert.equal(collector.promotionValidityMissing(complete), false);
  const byId = {
    listing_mode: 'site',
    selected_promotion_id: '1',
    promotion_options: [{ activity_id: '1', name: '新店40%', start_at: '2026-10-19 23:57:00', end_at: '' }],
  };
  assert.equal(collector.promotionValidityMissing(byId), true);
  // 没选活动（活动未配置）/ 跨境不走这条路。
  assert.equal(collector.promotionValidityMissing({ listing_mode: 'site', promotion_options: [] }), false);
  assert.equal(collector.promotionValidityMissing({
    listing_mode: 'cnsc_global',
    promotion_options: [{ activity_id: '1', start_at: '', end_at: '', selected: true }],
  }), false);
});
