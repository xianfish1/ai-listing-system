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

function runPrepare({ sites, pickerPresent = true, activity = null }) {
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
  let dialogOpen = false;
  let dialogRows = [];
  const dialogClose = { click() { dialogOpen = false; } };
  const dialog = {
    textContent: '选择折扣活动 活动名称 产品数 活动时间 状态 操作 关 闭',
    getBoundingClientRect: () => ({ left: 0, right: 600, top: 40, bottom: 400, width: 600, height: 360 }),
    querySelector(selector) {
      if (selector.includes('el-dialog__headerbtn')) return dialogClose;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '.el-loading-mask') return [];
      if (selector === '.vxe-table') return dialogOpen ? [{ __vue__: { tableData: dialogRows } }] : [];
      return [];
    },
  };
  const activityTrigger = { click() { dialogOpen = true; } };
  const activityHeader = {
    textContent: activity ? activity.headerText : '',
    getBoundingClientRect: () => ({ left: 0, right: 100, top: 10, bottom: 30, width: 100, height: 20 }),
    querySelector(selector) {
      if (selector === '.change-discount') return activityTrigger;
      return null;
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    parentElement: null,
  };
  if (activity) {
    setTimeout(() => { dialogRows = activity.rows; }, activity.dialogDelay || 0);
  }
  const context = {
    window: {},
    document: {
      querySelector: (selector) => (selector === '#shops-wap' && pickerPresent ? picker : null),
      querySelectorAll: (selector) => {
        if (selector === '.vxe-header--column') return activity ? [activityHeader] : [];
        if (selector === '.el-dialog,.el-dialog__wrapper,[role="dialog"]') return dialogOpen ? [dialog] : [];
        return [];
      },
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

test('3PF/部分站点：只勾了部分站点也算已加载（不再要求 8 站全勾）', async () => {
  const result = await runPrepare({
    sites: [siteGroup('TW', [['tw-1', 'TW仓', true]]), siteGroup('PH', [['ph-1', 'PH仓', false]])],
  });

  assert.equal(result.loaded, true);
  assert.equal(result.count, 1);
  assert.deepEqual(result.catalogs.map((item) => item.site_code), ['TW']);
});

test('一个站点都没勾时不算已加载（避免把模板目标店铺清空）', async () => {
  const result = await runPrepare({
    sites: ALL_SITES.map((site) => siteGroup(site, [[`${site}-1`, `${site}仓`, false]])),
  });

  assert.equal(result.loaded, false);
  assert.equal(result.count, 0);
  assert.ok(result.errors.some((message) => message.includes('没有勾选任何店铺')));
});

test('站点名用中文（台湾/菲律宾）也能识别成 TW/PH', async () => {
  const result = await runPrepare({
    sites: [
      siteGroup('台湾', [['tw-1', 'TW仓', true]]),
      siteGroup('菲律宾', [['ph-1', 'PH仓', true]]),
    ],
  });

  assert.equal(result.loaded, true);
  assert.deepEqual(result.catalogs.map((item) => item.site_code), ['TW', 'PH']);
});

test('店铺勾选兼容 checked/selected 字段（不只 isChecked）', async () => {
  const result = await runPrepare({
    sites: [
      { site: 'TW', shops: [{ id: 'tw-1', name: 'TW仓', checked: true }] },
      { site: 'PH', shops: [{ id: 'ph-1', name: 'PH仓', selected: true }] },
    ],
  });

  assert.equal(result.loaded, true);
  assert.deepEqual(result.catalogs.map((item) => item.site_code), ['TW', 'PH']);
});

test('CNSC 店铺选择器：同一站点选中两家店时取第一家并记警告', async () => {
  const result = await runPrepare({
    sites: [
      siteGroup('TW', [['tw-1', 'TW仓A', true], ['tw-2', 'TW仓B', true]]),
      ...ALL_SITES.slice(1).map((site) => siteGroup(site, [[`${site}-1`, `${site}仓`, true]])),
    ],
  });

  assert.equal(result.loaded, true);
  assert.equal(result.count, 8);
  assert.equal(result.catalogs.find((item) => item.site_code === 'TW').store_id, 'tw-1');
  assert.ok(result.errors.some((message) => message.includes('勾选了 2 家店铺')));
});

test('没有店铺选择器时直接返回，不碰页面', async () => {
  const result = await runPrepare({ sites: [], pickerPresent: false });

  assert.equal(result.picker, false);
  assert.equal(result.loaded, false);
  assert.equal(result.count, 0);
});

test('每站折扣活动：等弹窗行渲染后按表头活动名精确选中（PH40% 不撞 40%）', async () => {
  // 真机弹窗先出现「暂无内容」，几百毫秒后 vxe 才出 3 行；早读会误判空清单。
  const result = await runPrepare({
    sites: [siteGroup('PH', [['2022777126', 'Nov.熵域PH', true]])],
    activity: {
      headerText: '折扣活动PH40%',
      dialogDelay: 250,
      rows: [
        { discount_id: 526436339695915, discount_name: '40%', start_time: '2026-11-16 16:32:00', end_time: '2027-04-30 00:00:00' },
        { discount_id: 526429746245858, discount_name: 'PH40%', start_time: '2026-11-16 16:30:00', end_time: '2027-05-14 23:59:00' },
      ],
    },
  });

  assert.equal(result.activity_headers, 1);
  const catalog = result.catalogs.find((item) => item.site_code === 'PH');
  assert.equal(catalog.discount_catalog_loaded, true);
  assert.equal(catalog.activities, 2);
  // 表头显示 PH40%，必须选中 PH40% 那行，而不是同名的 40% 行。
  assert.equal(catalog.discount_name, 'PH40%');
  assert.equal(catalog.mabang_discount_id, '526429746245858');
  assert.equal(catalog.discount_percent, 40);
});
