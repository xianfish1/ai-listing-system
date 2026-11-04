'use strict';
/**
 * 单独重采「店铺促销活动」目录（本土单店 · 只读）。
 *
 * 页面导入如果没读到活动列表，模板里的活动就只剩名称（没有有效期 → 后端判 unresolved
 * → 下拉里看不到）。这里提供一个定向补采入口：只想刷新活动目录时不用再走一遍整页导入。
 *
 * 流程：连 CDP → 找商品编辑页 → 打开「选择折扣活动」弹窗（只展开）→ 用同一份
 * `mabang_page_import_capture.js` 读活动行 → 收回弹窗。**不选任何活动、不保存。**
 *
 * 用法：
 *   node scripts/mabang_promotion_collect.cjs --cdp-url http://127.0.0.1:9223 --store-id 2022749877
 * 结果：
 *   MABANG_PAGE_IMPORT_RESULT {"ok":true,"status":"completed","store":{"id":...},"items":[...]}
 */

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_PAGE_IMPORT_RESULT ';
const CDP_DEFAULT = 'http://127.0.0.1:9223';
const CDP_CONNECT_TIMEOUT_MS = 90000;
const CAPTURE_PATH = path.join(__dirname, 'mabang_page_import_capture.js');
const PROMOTION_PAGE_PATH = path.join(__dirname, 'mabang_promotion_dialog.page.js');
const PROMOTION_DIALOG_HINT = '\u9009\u62e9\u6298\u6263\u6d3b\u52a8';

function parseArgs(argv) {
  const args = { cdpUrl: CDP_DEFAULT, storeId: '', plan: {} };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      args.cdpUrl = argv[index + 1];
      index += 1;
    } else if (argv[index] === '--store-id' && argv[index + 1]) {
      args.storeId = String(argv[index + 1]).trim();
      index += 1;
    } else if (argv[index] === '--plan' && argv[index + 1]) {
      try {
        args.plan = JSON.parse(argv[index + 1]);
      } catch (_error) {
        args.plan = {};
      }
      index += 1;
    }
  }
  return args;
}

function isProductEditUrl(url) {
  return /productDetailShopee/i.test(String(url || ''));
}

/** 结果里不回传 cKey 之类的登录参数：它等同于该账号的会话凭证。 */
function safePageUrl(url) {
  try {
    const parsed = new URL(String(url || ''));
    for (const key of ['cKey', 'token', 'access_token']) parsed.searchParams.delete(key);
    const rebuilt = `${parsed.origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
    // cKey 也可能藏在 hash 的查询串里，统一再抹一遍。
    return rebuilt
      .replace(/([?&])cKey=[^&\s]*/gi, '$1')
      .replace(/\?&/, '?')
      .replace(/&&/g, '&')
      .replace(/[?&]$/, '');
  } catch (_error) {
    return String(url || '')
      .replace(/cKey=[^&\s]*/gi, '')
      .replace(/[?&]$/, '');
  }
}

function findProductEditTargets(browser) {
  const targets = [];
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      for (const frame of page.frames()) {
        if (isProductEditUrl(frame.url())) targets.push({ page, frame, url: frame.url() });
      }
    }
  }
  return targets;
}

async function collectStorePromotions({ cdpUrl = CDP_DEFAULT, storeId = '', plan = {} } = {}) {
  if (!fs.existsSync(CAPTURE_PATH) || !fs.existsSync(PROMOTION_PAGE_PATH)) {
    return { ok: false, status: 'failed', error: '采集脚本缺失（页面采集器或活动弹窗脚本）' };
  }
  const captureSource = fs.readFileSync(CAPTURE_PATH, 'utf8');
  const dialogSource = fs.readFileSync(PROMOTION_PAGE_PATH, 'utf8');
  let browser;
  try {
    browser = await chromium.connectOverCDP(cdpUrl, { timeout: CDP_CONNECT_TIMEOUT_MS });
  } catch (_error) {
    return {
      ok: false,
      status: 'browser_unreachable',
      error: `连不上马帮采集浏览器（${cdpUrl}）。请先在「马帮连接」里启动浏览器，并打开该店铺的商品编辑页后再点「重新采集活动」。`,
    };
  }
  let diagnostics = { attempted: false };
  try {
    const targets = findProductEditTargets(browser);
    if (!targets.length) {
      return {
        ok: false,
        status: 'not_on_product_edit_page',
        error: '当前浏览器没有打开 Shopee 商品编辑页。请在马帮里打开该店铺的商品（编辑），再点「重新采集活动」。',
      };
    }
    const target = targets[targets.length - 1];
    const readCapture = () => target.frame.evaluate(
      ([source, inputPlan]) => (0, eval)(`(${source})`)(inputPlan),
      [captureSource, plan || {}],
    );
    const runDialog = (phase) => target.frame.evaluate(
      ([source, options]) => (0, eval)(`(${source})`)(options),
      [dialogSource, { phase, hint: PROMOTION_DIALOG_HINT }],
    );

    const opened = await runDialog('open').catch((error) => ({
      opened: false,
      method: `error:${String(error).slice(0, 60)}`,
    }));
    diagnostics = { ...opened, attempted: true };
    if (!opened || !opened.opened) {
      return {
        ok: false,
        status: 'promotion_dialog_not_opened',
        error: '没能打开马帮的「选择折扣活动」弹窗；请确认当前页是 本土(site) 商品编辑页且交易信息/SKU 表头已加载。',
        diagnostics,
        page_url: safePageUrl(target.url),
      };
    }
    let capture = null;
    try {
      capture = await readCapture();
    } finally {
      const closed = await runDialog('close').catch(() => ({ closed: 'error' }));
      diagnostics.closed = (closed && closed.closed) || '';
    }
    if (!capture || typeof capture !== 'object' || capture.source !== 'mabang_current_page') {
      return { ok: false, status: 'failed', error: '页面采集结果无效（不是马帮当前页）', diagnostics };
    }
    if (String(capture.listing_mode || 'site').toLowerCase() === 'cnsc_global') {
      return {
        ok: false,
        status: 'cnsc_global_not_supported',
        error: '当前是跨境（CNSC）商品页，站点活动走「目标站点店铺」链路；「重新采集活动」只用于本土店铺模板。',
        diagnostics,
      };
    }
    const store = (capture.store_category_state && capture.store_category_state.store) || {};
    const capturedStoreId = String(store.id || '').trim();
    const capturedStoreName = String(store.name || '').trim();
    const wanted = String(storeId || '').trim();
    if (wanted && capturedStoreId && wanted !== capturedStoreId) {
      return {
        ok: false,
        status: 'store_mismatch',
        error: `当前马帮页面是店铺「${capturedStoreName || capturedStoreId}」，不是要采集的店铺；活动目录未写入`,
        store: { id: capturedStoreId, name: capturedStoreName },
        diagnostics,
        page_url: safePageUrl(target.url),
      };
    }
    const items = Array.isArray(capture.promotion_options) ? capture.promotion_options : [];
    const catalogLoaded = capture.promotion_catalog_loaded === true;
    if (!catalogLoaded) {
      return {
        ok: false,
        status: 'promotion_catalog_not_loaded',
        error: '弹窗打开了但没读到活动行（店铺可能没有活动，或列表仍在加载）；活动目录未写入',
        store: { id: capturedStoreId, name: capturedStoreName },
        items,
        diagnostics,
        page_url: safePageUrl(target.url),
      };
    }
    return {
      ok: true,
      status: 'completed',
      store: { id: capturedStoreId, name: capturedStoreName },
      items,
      catalog_loaded: true,
      diagnostics,
      page_url: safePageUrl(target.url),
    };
  } finally {
    await browser.close().catch(() => {});
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let payload;
  try {
    payload = await collectStorePromotions(args);
  } catch (error) {
    payload = { ok: false, status: 'failed', error: String((error && error.message) || error) };
  }
  process.stdout.write(RESULT_MARKER + JSON.stringify(payload) + '\n');
}

if (require.main === module) {
  main().catch((error) => {
    process.stdout.write(RESULT_MARKER + JSON.stringify({ ok: false, status: 'failed', error: String(error) }) + '\n');
    process.exitCode = 1;
  });
}

module.exports = {
  RESULT_MARKER,
  parseArgs,
  isProductEditUrl,
  safePageUrl,
  collectStorePromotions,
};
