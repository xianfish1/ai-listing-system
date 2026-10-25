'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_PAGE_IMPORT_RESULT ';
const DEFAULT_CDP_URL = 'http://127.0.0.1:9223';
const CAPTURE_SOURCE = fs.readFileSync(path.join(__dirname, 'mabang_page_import_capture.js'), 'utf8');

function parseArgs(argv) {
  const result = { cdpUrl: DEFAULT_CDP_URL };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      result.cdpUrl = argv[index + 1];
      index += 1;
    }
  }
  return result;
}

async function readPlan() {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  return JSON.parse(input || '{}');
}

function mabangFrames(browser) {
  return browser.contexts().flatMap((context) => context.pages())
    .flatMap((page) => page.frames())
    .filter((frame) => /mabangerp\.com/i.test(frame.url()));
}

async function findEditorFrame(browser) {
  const frames = mabangFrames(browser);
  for (const frame of frames) {
    const text = await frame.locator('body').innerText({ timeout: 1000 }).catch(() => '');
    if (text.includes('店铺及类目') && text.includes('产品类目') && text.includes('交易信息')) return frame;
  }
  throw new Error('未找到当前马帮商品编辑页；请先打开具体商品的编辑页。');
}

async function collect(frame, plan) {
  return frame.evaluate(({ captureSource, inputPlan }) => {
    const capturePage = (0, eval)(`(${captureSource})`);
    return capturePage(inputPlan);
  }, { captureSource: CAPTURE_SOURCE, inputPlan: plan });
}

// Runs before the read-only capture so that data locked behind the shop picker
// and per-site "选择折扣活动" dialogs is materialized into the DOM/Vue tree.
// Everything happens in the page context; the picker is closed with 取消 and
// each discount dialog is closed after read so no state is mutated on save.
async function prepareImportState(frame) {
  return frame.evaluate(async () => {
    const clean = (v) => String(v == null ? '' : v).trim();
    const isVisible = (node) => node && node.offsetParent !== null;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const waitUntil = async (predicate, tries = 60) => {
      for (let i = 0; i < tries; i += 1) {
        const value = predicate();
        if (value) return value;
        await sleep(100);
      }
      return null;
    };
    const clickForText = (scope, text) => [...scope.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
      .filter(isVisible).find((node) => clean(node.textContent) === text || clean(node.textContent).startsWith(text));
    const promotionDialog = () => [...document.querySelectorAll('.el-dialog,.el-dialog__wrapper,[role="dialog"]')]
      .filter(isVisible).find((node) => clean(node.textContent).includes('选择折扣活动'));

    const state = { target_site_stores_loaded: false, target_site_stores: [] };
    const picker = document.querySelector('#shops-wap');
    if (!picker) {
      window.__AI_LISTING_CNSC_IMPORT_STATE__ = state;
      return { loaded: false, count: 0, picker: false };
    }
    const pickerTrigger = picker.querySelector('.shop-box');
    let pickerOpened = false;
    if (pickerTrigger && !picker.querySelector('.feat-content')) {
      pickerTrigger.click();
      pickerOpened = true;
      await waitUntil(() => picker.querySelector('.feat-content'));
    }
    const aliases = { '台湾': 'TW', '菲律宾': 'PH', '新加坡': 'SG', '泰国': 'TH', '马来西亚': 'MY', '墨西哥': 'MX', '越南': 'VN', '巴西': 'BR', SIP: 'SIP' };
    const requiredManualSites = ['TW', 'PH', 'SG', 'TH', 'MY', 'MX', 'VN', 'BR'];
    const makeTargetStore = (siteCode, shop) => {
      const storeId = clean(shop?.id || shop?.shopId || shop?.store_id || shop?.storeId);
      const storeName = clean(shop?.name || shop?.store_name || shop?.storeName);
      if (!storeId && !storeName) return null;
      return { site_code: siteCode, store_id: storeId, store_name: storeName, discount_catalog_loaded: false, discount_catalog: [] };
    };
    const shopVm = picker?.__vue__ || picker?.__vueParentComponent?.proxy;
    const vueSiteGroups = Array.isArray(shopVm?.shops) ? shopVm.shops : [];
    if (vueSiteGroups.length) {
      for (const siteGroup of vueSiteGroups) {
        const siteCode = clean(siteGroup?.site).toUpperCase();
        if (!requiredManualSites.includes(siteCode)) continue;
        const selected = (Array.isArray(siteGroup?.shops) ? siteGroup.shops : [])
          .filter((shop) => shop?.isChecked === true);
        if (selected.length !== 1) continue;
        const target = makeTargetStore(siteCode, selected[0]);
        if (target) state.target_site_stores.push(target);
      }
    } else {
      const siteGroups = [...picker.querySelectorAll('.site')];
      for (const siteGroup of siteGroups) {
        const titleText = clean(siteGroup.querySelector('.title-wap-checkbox')?.textContent || siteGroup.textContent);
        const rawCode = clean(siteGroup.dataset.siteCode || '').toUpperCase();
        const siteCode = /^(TW|PH|SG|TH|MY|MX|VN|BR|SIP)$/.test(rawCode)
          ? rawCode
          : Object.entries(aliases).find(([name]) => titleText.includes(name))?.[1] || '';
        if (!requiredManualSites.includes(siteCode)) continue;
        const selected = [...siteGroup.querySelectorAll('.shops-checkbox')]
          .filter((label) => label.querySelector('input[type="checkbox"]')?.checked);
        if (selected.length !== 1) continue;
        const label = selected[0];
        const input = label.querySelector('input[type="checkbox"]');
        const target = makeTargetStore(siteCode, {
          id: label.dataset.storeId || label.dataset.shopId || input?.dataset.storeId || input?.dataset.shopId || input?.value,
          name: label.dataset.storeName || label.dataset.shopName || label.textContent,
        });
        if (target) state.target_site_stores.push(target);
      }
    }
    const targetCodes = state.target_site_stores.map((item) => item.site_code);
    state.target_site_stores_loaded = requiredManualSites.every((code) => targetCodes.filter((value) => value === code).length === 1)
      && new Set(targetCodes).size === requiredManualSites.length;
    if (pickerOpened) {
      const cancel = clickForText(picker, '取消');
      if (cancel) cancel.click();
      else state.target_site_stores_loaded = false;
    }

    // Per-site discount activities: walk the sku table headers, pair each 折扣活动
    // sub-header with the site-code header at the same column index, then click the
    // trigger inside that specific header cell (avoids relying on document-order
    // between site codes and .change-discount buttons matching up).
    const visibleHeaders = [...document.querySelectorAll('.vxe-header--column')].filter(isVisible);
    const siteHeaderCodes = visibleHeaders.map((node) => {
      const text = clean(node.textContent).toUpperCase();
      return /^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(text) ? text : '';
    });
    const activityHeaders = visibleHeaders
      .map((node, index) => ({ node, index, text: clean(node.textContent) }))
      .filter((entry) => entry.text.includes('折扣活动'));
    const findSiteForActivityHeader = (entry) => {
      // Site header ancestors: look up-left through earlier visible headers for the
      // most recent recognized site code; also check the DOM ancestry for a
      // .vxe-header--column carrying the code.
      for (let i = entry.index; i >= 0; i -= 1) {
        if (siteHeaderCodes[i]) return siteHeaderCodes[i];
      }
      for (let node = entry.node.parentElement; node; node = node.parentElement) {
        const code = clean(node?.dataset?.siteCode || '').toUpperCase();
        if (/^(TW|PH|SG|TH|MY|MX|VN|BR)$/.test(code)) return code;
      }
      return '';
    };
    const findTriggerIn = (header) => header.querySelector('.change-discount')
      || [...header.querySelectorAll('button,[role="button"],.el-button,.el-link,a,span')]
        .find((node) => isVisible(node) && (clean(node.textContent) === '选择活动'
          || clean(node.textContent).includes('选择折扣')));
    for (const entry of activityHeaders) {
      const siteCode = findSiteForActivityHeader(entry);
      const store = siteCode ? state.target_site_stores.find((item) => item.site_code === siteCode) : null;
      if (store) {
        // Even before clicking, harvest whatever the header currently shows so we
        // still return something if the dialog fails to open on this run.
        const headerText = clean(entry.node.textContent).replace(/^折扣活动[\s·:：]*/, '');
        const percentMatch = headerText.match(/(\d+(?:\.\d+)?)\s*%/);
        if (percentMatch) store.discount_percent = Number(percentMatch[1]);
        const stripped = headerText.replace(/(\d+(?:\.\d+)?)\s*%(?:OFF)?/i, '').replace(/选择活动|批量修改|活动待配置/g, '').trim();
        if (stripped) { store.discount_name = stripped; store.discount_label = stripped; }
      }
      const trigger = findTriggerIn(entry.node);
      if (!store || !trigger) continue;
      let dialog = null;
      try {
        trigger.click();
        dialog = await waitUntil(promotionDialog);
        if (!dialog) continue;
        await waitUntil(() => ![...dialog.querySelectorAll('.el-loading-mask')].some(isVisible));
        const containers = [];
        for (let vm = dialog.__vue__ || dialog.__vueParentComponent?.proxy, depth = 0; vm && depth < 8; vm = vm.$parent, depth += 1) containers.push(vm, vm.$data);
        const rawOptions = containers.flatMap((item) => ['tableData', 'activityList', 'promotionList', 'discountList']
          .flatMap((key) => Array.isArray(item?.[key]) ? item[key] : []));
        const seen = new Set();
        store.discount_catalog = rawOptions.map((item) => {
          const id = clean(item.discount_id || item.discountId || item.activity_id || item.activityId || item.id);
          return {
            mabang_discount_id: id,
            name: clean(item.discount_name || item.discountName || item.activity_name || item.name),
            discount_percent: Number(String(clean(item.discount_percent || item.discountPercent || item.discount_rate || item.discountRate)).replace('%', '')) || '',
            selected: item.selected === true || item.checked === true,
          };
        }).filter((item) => item.mabang_discount_id && item.name && !seen.has(item.mabang_discount_id) && seen.add(item.mabang_discount_id));
        let selected = store.discount_catalog.filter((item) => item.selected);
        if (selected.length !== 1) {
          const headerText = clean(entry.node.textContent);
          selected = store.discount_catalog.filter((item) => headerText.includes(item.name));
        }
        store.discount_catalog_loaded = store.discount_catalog.length > 0 && selected.length === 1;
        if (store.discount_catalog_loaded) {
          store.mabang_discount_id = selected[0].mabang_discount_id;
          store.discount_name = selected[0].name;
          store.discount_label = selected[0].name;
          store.discount_percent = selected[0].discount_percent;
        }
      } finally {
        if (dialog) {
          const close = dialog.querySelector('.el-dialog__headerbtn,[aria-label="Close"],[aria-label="关闭"]')
            || clickForText(dialog, '取消')
            || clickForText(dialog, '关闭');
          if (close) close.click();
          await waitUntil(() => !promotionDialog());
        }
      }
    }
    window.__AI_LISTING_CNSC_IMPORT_STATE__ = state;
    return { loaded: state.target_site_stores_loaded, count: state.target_site_stores.length, picker: true };
  });
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  const plan = await readPlan();
  const browser = await chromium.connectOverCDP(args.cdpUrl);
  const frame = await findEditorFrame(browser);
  await prepareImportState(frame);
  const capture = await collect(frame, plan);
  const store = capture.store_category_state?.store || {};
  const category = capture.store_category_state?.category || {};
  if (!store.id || !store.name || !category.id || !category.path || !capture.category_attributes.length) {
    throw new Error('当前马帮编辑页的店铺、叶子类目或平台属性未解析到；请保持页面完全加载后重试。');
  }
  return { ok: true, status: 'completed', capture };
}

run().then((result) => {
  process.stdout.write(RESULT_MARKER + JSON.stringify(result) + '\n');
  process.exit(0);
}).catch((error) => {
  process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false, status: 'failed', error: String(error && error.message || error) }) + '\n');
  process.exit(1);
});
