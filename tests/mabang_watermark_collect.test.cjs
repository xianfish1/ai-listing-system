'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const collector = require('../scripts/mabang_watermark_collect.cjs');

const SOURCE = fs.readFileSync(path.join(__dirname, '../scripts/mabang_watermark_collect.cjs'), 'utf8');

test('水印采集器：模板字段归一化（ID/名称别名 + 缺一不可 + 缩略图）', () => {
  assert.deepEqual(
    collector.normalizeTemplate({ watermarkId: 'wm-1', watermarkName: '主图角标', imgUrl: 'https://cdn/x.png' }),
    { template_id: 'wm-1', name: '主图角标', preview_url: 'https://cdn/x.png' },
  );
  assert.deepEqual(
    collector.normalizeTemplate({ templateId: 'wm-2', template_name: '产品图角标' }),
    { template_id: 'wm-2', name: '产品图角标', preview_url: '' },
  );
  // 缺 ID 或缺名称的行一律丢掉：执行器是按 ID + 名称双匹配点菜单的。
  assert.equal(collector.normalizeTemplate({ watermarkId: 'wm-3' }), null);
  assert.equal(collector.normalizeTemplate({ watermarkName: '只有名字' }), null);
  assert.equal(collector.normalizeTemplate(null), null);
});

test('水印采集器：Vue 列表与可见菜单行合并去重，Vue 顺序优先', () => {
  const merged = collector.mergeTemplates(
    [{ template_id: 'wm-1', name: '主图角标' }],
    [
      { template_id: 'wm-1', name: '主图角标', preview_url: 'https://cdn/dom.png' },
      { template_id: 'wm-2', name: '产品图角标', preview_url: 'https://cdn/2.png' },
      { template_id: '', name: '没有 ID' },
    ],
  );

  assert.deepEqual(merged, [
    { template_id: 'wm-1', name: '主图角标', preview_url: '' },
    { template_id: 'wm-2', name: '产品图角标', preview_url: 'https://cdn/2.png' },
  ]);
});

test('水印采集器：只认 Shopee 商品编辑页', () => {
  assert.equal(collector.isProductEditUrl('https://publish.mabangerp.com/publish-ui/#/productDetailShopee?id=1'), true);
  assert.equal(collector.isProductEditUrl('https://publish.mabangerp.com/publish-ui/#/publishProductListV2'), false);
  assert.equal(collector.isProductEditUrl(''), false);
});

test('水印采集器：--cdp-url 解析（默认 9223）', () => {
  assert.deepEqual(collector.parseArgs([]), { cdpUrl: 'http://127.0.0.1:9223' });
  assert.deepEqual(collector.parseArgs(['--cdp-url', 'http://127.0.0.1:9333']), { cdpUrl: 'http://127.0.0.1:9333' });
});

test('水印采集器是只读的：不点任何水印选项，读完把菜单收回去', () => {
  // 打开入口、读行、发 Escape —— 只允许这些页面动作。
  assert.match(SOURCE, /trigger\.click\(\)/);
  assert.match(SOURCE, /dispatchEvent\(new MouseEvent\(eventName/);
  assert.match(SOURCE, /new KeyboardEvent\('keydown', \{ key: 'Escape'/);
  // 绝不允许点菜单行（那会把水印应用到操作员的草稿上）。
  assert.doesNotMatch(SOURCE, /row\.click\(/);
  assert.doesNotMatch(SOURCE, /\.watermark-menu-item[^\n]*\.click\(/);
  assert.doesNotMatch(SOURCE, /data-watermark-id[^\n]*\n?[^\n]*\.click\(/);
  assert.match(SOURCE, /绝不点击任何水印选项/);
});
