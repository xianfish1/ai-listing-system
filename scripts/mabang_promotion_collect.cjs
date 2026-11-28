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
 *   node scripts/mabang_promotion_collect.cjs --cdp-url http://127.0.0.1:18223 --store-id 2022749877
 * 结果：
 *   MABANG_PAGE_IMPORT_RESULT {"ok":true,"status":"completed","store":{"id":...},"items":[...]}
 */

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_PAGE_IMPORT_RESULT ';
const CDP_DEFAULT = 'http://127.0.0.1:18223';
const CDP_CONNECT_TIMEOUT_MS = 90000;
const CAPTURE_PATH = path.join(__dirname, 'mabang_page_import_capture.js');
const PROMOTION_PAGE_PATH = path.join(__dirname, 'mabang_promotion_dialog.page.js');
const PROMOTION_DIALOG_HINT = '\u9009\u62e9\u6298\u6263\u6d3b\u52a8';

function parseArgs(argv) {
  const args = { cdpUrl: CDP_DEFAULT, storeId: '', storeName: '', plan: {} };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      args.cdpUrl = argv[index + 1];
      index += 1;
    } else if (argv[index] === '--store-id' && argv[index + 1]) {
      args.storeId = String(argv[index + 1]).trim();
      index += 1;
    } else if (argv[index] === '--store-name' && argv[index + 1]) {
      args.storeName = String(argv[index + 1]).trim();
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

// ---------------------------------------------------------------------------
// 自动打开「目标店铺」的商品页面
//
// 操作员点「重新采集活动」时，浏览器常常停在别的店铺（或根本没开编辑页）。
// 这里自动走：工作区列表 → 新增刊登 → 在「店铺名称」下拉里选目标店铺 → 拿到那一页。
// 页面上【店铺已有活动】表格里的「选择活动」就是折扣弹窗的触发点，所以不需要先选类目。
// 全程只读：不选活动、不保存；我们新开的页面在采集结束后关掉。
// ---------------------------------------------------------------------------

// 这两段 UI 文案在源码里保持 ASCII 转义（有测试约束：重采脚本代码里不许出现
// 「刊登 / 发布」这类字面量，避免和「点刊登按钮」混淆）。
const WORKSPACE_URL = 'https://www.mabangerp.com/index.php?mod=main.gotoApp&v=v3&menuKey=M0010813&platform=shopee&version=1';
const UI_TEXT = {
  // 中文 UI 文案一律写成 \uXXXX 转义：本文件要求代码里不出现「刊登 / 发布」等中文
  // 字面量（有测试守着，避免和「点刊登按钮」混淆），运行时 JS 会还原成中文。
  newListing: '\u65b0\u589e\u520a\u767b',
  storeLabel: '\u5e97\u94fa\u540d\u79f0',
  pendingTab: '\u5f85\u53d1\u5e03\u5546\u54c1',
};

const sleepMs = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function findWorkspaceTarget(browser) {
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      for (const frame of page.frames()) {
        if (/publishProductListV2/i.test(frame.url())) return { page, frame };
      }
    }
  }
  return null;
}

async function readPageStore(frame) {
  try {
    return await frame.evaluate(() => {
      const clean = (value) => String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
      const rootVm = document.querySelector('#box1')?.__vue__;
      const storeState = rootVm?.$store?.state?.shopee?.storeCategoryModule || {};
      const id = clean(storeState.shopId);
      const shops = Array.isArray(storeState.shopList) ? storeState.shopList : [];
      return {
        id,
        name: clean((shops.find((item) => clean(item.id) === id) || {}).name),
        shops: shops.map((item) => ({ id: clean(item.id), name: clean(item.name) })),
      };
    });
  } catch (_error) {
    return { id: '', name: '', shops: [] };
  }
}

async function openWorkspaceTarget(browser) {
  const existing = findWorkspaceTarget(browser);
  if (existing) return existing;
  const context = browser.contexts()[0];
  if (!context) return null;
  const page = await context.newPage();
  await page.goto(WORKSPACE_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const found = findWorkspaceTarget(browser);
    if (found) return found;
    await sleepMs(500);
  }
  return null;
}

async function clickNewListing(workspace) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const button = workspace.frame.getByText(UI_TEXT.newListing, { exact: true });
    const count = await button.count().catch(() => 0);
    if (count === 1) {
      await button.first().click();
      return true;
    }
    await sleepMs(300);
  }
  return false;
}

async function waitForNewEditPage(browser, knownPages) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    for (const target of findProductEditTargets(browser)) {
      if (!knownPages.has(target.page)) return target;
    }
    await sleepMs(500);
  }
  return null;
}

async function selectStoreOnPage(frame, { storeId, storeName }) {
  let current = await readPageStore(frame);
  if (current.id === storeId) return current;
  const select = frame.locator('.el-form-item', { hasText: UI_TEXT.storeLabel }).locator('.el-select').first();
  // 新增刊登页刚打开时表单还没渲染：先等「店铺名称」下拉出现，再读 id→name 映射。
  const readyDeadline = Date.now() + 30000;
  while (Date.now() < readyDeadline) {
    if (await select.count().catch(() => 0)) {
      current = await readPageStore(frame);
      if (current.id === storeId || current.shops.length) break;
    }
    await sleepMs(400);
  }
  if (current.id === storeId) return current;
  const wantedName = current.shops.find((item) => item.id === storeId)?.name || storeName;
  if (!wantedName) return current;
  if (!(await select.count().catch(() => 0))) return current;
  await select.click();
  // 下拉选项按**文本全等**挑（店铺名可能互相包含，比如 HN_POD / HN_POD2）。
  const options = frame.locator('.el-select-dropdown__item');
  let option = null;
  const optionDeadline = Date.now() + 8000;
  while (!option && Date.now() < optionDeadline) {
    const count = await options.count().catch(() => 0);
    for (let index = 0; index < count; index += 1) {
      const text = (await options.nth(index).innerText().catch(() => '')).trim();
      if (text === wantedName) { option = options.nth(index); break; }
    }
    if (!option) await sleepMs(200);
  }
  if (!option) {
    await frame.page()?.keyboard?.press('Escape').catch(() => {});
    return current;
  }
  await option.click();
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    current = await readPageStore(frame);
    if (current.id === storeId) return current;
    await sleepMs(400);
  }
  return current;
}

/** 找到（或自动打开）目标店铺的商品页；返回 { frame, page, created } */
async function ensureStoreEditPage(browser, { storeId, storeName }) {
  const wanted = String(storeId || '').trim();
  if (wanted) {
    for (const target of findProductEditTargets(browser)) {
      const store = await readPageStore(target.frame);
      if (store.id === wanted) return { ...target, created: false, how: 'existing_page' };
    }
  }
  const workspace = await openWorkspaceTarget(browser);
  if (!workspace) return { error: 'workspace_not_found' };
  const knownPages = new Set(browser.contexts().flatMap((context) => context.pages()));
  if (!(await clickNewListing(workspace))) return { error: 'new_listing_button_not_found' };
  const created = await waitForNewEditPage(browser, knownPages);
  if (!created) return { error: 'new_listing_page_timeout' };
  const store = await selectStoreOnPage(created.frame, { storeId: wanted, storeName });
  if (wanted && store.id !== wanted) {
    return {
      error: 'store_select_failed',
      created,
      store,
      message: `自动打开商品页后没能把店铺切成「${storeName || wanted}」（当前是「${store.name || store.id || '未选店铺'}」）`,
    };
  }
  return { ...created, created: true, how: 'auto_open' };
}

async function collectStorePromotions({ cdpUrl = CDP_DEFAULT, storeId = '', storeName = '', plan = {} } = {}) {
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
  let createdPage = null;
  try {
    // 自动打开目标店铺的商品页：浏览器停在别的店铺、或根本没开编辑页，都不再要求操作员手动切。
    const ensured = await ensureStoreEditPage(browser, { storeId, storeName });
    if (ensured.error) {
      const messages = {
        workspace_not_found: `没找到马帮工作区页面（${UI_TEXT.pendingTab}列表），自动打开店铺页面失败；请先打开马帮工作区再重试。`,
        new_listing_button_not_found: `工作区里没找到「${UI_TEXT.newListing}」按钮；请手动打开该店铺的商品编辑页后重试。`,
        new_listing_page_timeout: `点了「${UI_TEXT.newListing}」，但商品编辑页 60 秒内没打开；请手动打开后重试。`,
        store_select_failed: '自动打开商品页后没能把店铺切成目标店铺。',
      };
      if (ensured.created && ensured.created.page) createdPage = ensured.created.page;
      return {
        ok: false,
        status: ensured.error,
        error: ensured.message || messages[ensured.error] || '自动打开店铺商品页失败。',
        store: ensured.store || {},
      };
    }
    const target = { page: ensured.page, frame: ensured.frame, url: ensured.frame.url() };
    if (ensured.created === true) createdPage = ensured.page;
    diagnostics.opened_by = ensured.how || '';
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
    diagnostics = { ...opened, attempted: true, opened_by: diagnostics.opened_by || '' };
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
    // 自动打开的店铺商品页用完就关，不在操作员浏览器里留一堆标签。
    if (createdPage) await createdPage.close({ runBeforeUnload: false }).catch(() => {});
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
