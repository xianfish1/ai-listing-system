'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '../scripts/mabang_page_import_prepare.page.js'),
  'utf8',
);

function siteGroup(site, shops) {
  return { site, shops: shops.map(([id, name, checked]) => ({ id, name, isChecked: checked })) };
}

function runPrepare({ sites, pickerPresent = true }) {
  let pickerOpen = false;
  const trigger = {
    click() { pickerOpen = !pickerOpen; },
  };
  const picker = {
    __vue__: { shops: sites },
    querySelector(selector) {
      if (selector === '.shop-box') return trigger;
      if (selector === '.feat-content') return pickerOpen ? {} : null;
      return null;
    },
    querySelectorAll() { return []; },
  };
  const context = {
    window: {},
    document: {
      querySelector: (selector) => (selector === '#shops-wap' && pickerPresent ? picker : null),
      querySelectorAll: (selector) => (selector === '.vxe-header--column' ? [] : []),
    },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', opacity: '1' }),
    setTimeout,
    clearTimeout,
    URL,
  };
  const prepare = vm.runInNewContext(`(${SOURCE})`, context);
  // vm 里创建的数组原型不同，deepEqual 前先过一遍 JSON（和采集测试同一做法）。
  return Promise.resolve(prepare({})).then((value) => JSON.parse(JSON.stringify(value)));
}

const ALL_SITES = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'];

test('CNSC 店铺选择器：读出每站选中的店铺并收回选择器', async () => {
  const result = await runPrepare({
    sites: ALL_SITES.map((site) => siteGroup(site, [[`${site}-1`, `${site}仓`, true]])),
  });

  assert.equal(result.picker, true);
  assert.equal(result.loaded, true);
  assert.equal(result.count, 8);
  assert.deepEqual(result.catalogs.map((item) => item.site_code), ALL_SITES);
  assert.equal(result.catalogs[0].store_id, 'TW-1');
  assert.equal(result.picker_closed, 'toggle');
});

test('CNSC 店铺选择器：站点缺一个就标记未加载（后端会丢弃目标店铺）', async () => {
  const result = await runPrepare({
    sites: [siteGroup('TW', [['tw-1', 'TW仓', true]]), siteGroup('PH', [['ph-1', 'PH仓', false]])],
  });

  assert.equal(result.loaded, false);
  assert.equal(result.count, 1);
});

test('CNSC 店铺选择器：同一站点选中两家店时不猜（跳过该站）', async () => {
  const result = await runPrepare({
    sites: [
      siteGroup('TW', [['tw-1', 'TW仓A', true], ['tw-2', 'TW仓B', true]]),
      ...ALL_SITES.slice(1).map((site) => siteGroup(site, [[`${site}-1`, `${site}仓`, true]])),
    ],
  });

  assert.equal(result.loaded, false);
  assert.equal(result.catalogs.some((item) => item.site_code === 'TW'), false);
  assert.equal(result.count, 7);
});

test('没有店铺选择器时直接返回，不碰页面', async () => {
  const result = await runPrepare({ sites: [], pickerPresent: false });

  assert.equal(result.picker, false);
  assert.equal(result.loaded, false);
  assert.equal(result.count, 0);
});
