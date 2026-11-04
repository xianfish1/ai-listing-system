'use strict';
/**
 * 采集马帮「当前商品编辑页」的完整快照（只读，走 Playwright/CDP）。
 *
 * 为什么有它：页面导入原本走 browser-harness（`run_mabang_page_import_audit`），而那套的
 * daemon 在本机 Python 3.14 下起不来（asyncio base_events.py:297 assert self._sockets is not None），
 * 于是「从马帮当前页导入」必然 422。自动化一直在用的 Playwright/CDP 这条路是通的，
 * 所以快照采集改走这里：连 CDP → 找商品编辑页 frame → 直接跑
 * `scripts/mabang_page_import_capture.js` 那份采集函数（同一份代码，不复制逻辑）。
 *
 * 只读：最多 hover/click 一次「添加水印」把水印菜单展开（采集器要合并可见菜单里的行），
 * 采完发 Escape 收回。**不点任何水印选项、不点保存/刊登/发布。**
 *
 * 店铺促销活动同理：活动列表只在「选择折扣活动」弹窗打开时才有行，没打开时采集只能
 * 回显当前选中的那一条（没有状态和有效期 → 后端记 unresolved → 模板下拉里看不到它，
 * 保存还会把模板上的活动清空）。所以第一遍采集发现目录没读到时，这里点开一次活动列表、
 * 等行出现后再采一遍，最后收回弹窗；只展开、不点任何活动。
 *
 * 用法：
 *   node scripts/mabang_page_import_collect.cjs --cdp-url http://127.0.0.1:9223 --plan '{"platform":"shopee","country":"PH"}'
 * 结果：
 *   MABANG_PAGE_IMPORT_RESULT {"ok":true,"status":"completed","capture":{...}}
 */

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_PAGE_IMPORT_RESULT ';
const CDP_DEFAULT = 'http://127.0.0.1:9223';
const CDP_CONNECT_TIMEOUT_MS = 90000;
const CAPTURE_PATH = path.join(__dirname, 'mabang_page_import_capture.js');
// 弹窗开关逻辑只有一份：页面导入（这里）和「单独重采店铺活动」共用它。
const PROMOTION_PAGE_PATH = path.join(__dirname, 'mabang_promotion_dialog.page.js');
const ADD_WATERMARK_TEXT = '\u6dfb\u52a0\u6c34\u5370';
const MENU_SELECTOR = '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]';
// 「选择折扣活动」：活动列表弹窗的标题文字，用来认出弹窗是否已经打开。
const PROMOTION_DIALOG_HINT = '\u9009\u62e9\u6298\u6263\u6d3b\u52a8';
// 交易信息里那行「店铺促销活动」的标签文字（不同站点/版本措辞略有差别）。
const PROMOTION_LABELS = [
  '\u5e97\u94fa\u4fc3\u9500\u6d3b\u52a8',
  '\u4fc3\u9500\u6d3b\u52a8',
  '\u6298\u6263\u6d3b\u52a8',
];

function parseArgs(argv) {
  const args = { cdpUrl: CDP_DEFAULT, plan: {} };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--cdp-url' && argv[index + 1]) {
      args.cdpUrl = argv[index + 1];
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

/** 商品编辑页可能开在多个标签里：全找出来，调用方决定（多开时取最后一个，并在结果里带上）。 */
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

/** 展开「添加水印」菜单（best-effort，只为让采集器能合并可见菜单里的行）。 */
async function openWatermarkMenu(frame) {
  return frame.evaluate(
    async ({ addText, menuSelector }) => {
      const clean = (value) => String(value === undefined || value === null ? '' : value).replace(/\s+/g, ' ').trim();
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const visible = (node) => {
        if (!node || node.isConnected === false) return false;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
      };
      const menuOpen = () => [...document.querySelectorAll(menuSelector)].some(visible);
      const candidates = [...document.querySelectorAll('button,[role="button"],a,label,.el-button,.el-link,.el-radio,span')]
        .filter((node) => visible(node) && clean(node.textContent) === addText);
      if (candidates.length !== 1 || menuOpen()) {
        return { opened: menuOpen(), method: menuOpen() ? 'already_open' : 'trigger_not_unique', triggers: candidates.length };
      }
      const trigger = candidates[0].closest('button') || candidates[0];
      const rect = trigger.getBoundingClientRect();
      for (const eventName of ['mouseenter', 'mouseover']) {
        trigger.dispatchEvent(new MouseEvent(eventName, {
          bubbles: true, cancelable: true, view: window,
          clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
        }));
      }
      let method = 'hover';
      let deadline = Date.now() + 1500;
      while (Date.now() < deadline && !menuOpen()) await sleep(100);
      if (!menuOpen()) {
        trigger.click();
        method = 'click';
        deadline = Date.now() + 4000;
        while (Date.now() < deadline && !menuOpen()) await sleep(100);
      }
      return { opened: menuOpen(), method };
    },
    { addText: ADD_WATERMARK_TEXT, menuSelector: MENU_SELECTOR },
  );
}

async function closeWatermarkMenu(frame) {
  await frame
    .evaluate(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    })
    .catch(() => {});
}

/**
 * 这次采集有没有读到店铺活动列表。
 *
 * CNSC（跨境）的每站活动走另一条链路（target_site_stores 里的 discount_catalog），
 * 这里的单店活动列表弹窗不适用，所以只看本土（site）。
 */
function promotionCatalogMissing(capture) {
  if (!capture || typeof capture !== 'object') return false;
  if (String(capture.listing_mode || 'site').trim().toLowerCase() === 'cnsc_global') return false;
  return capture.promotion_catalog_loaded !== true;
}

/**
 * 列表读到了、但当前选中的活动没有有效期：同样要再展开一次弹窗。
 *
 * 马帮的活动行只在「选择折扣活动」弹窗里才有 start_time/end_time；只回显选中项时
 * 名称能拿到、日期永远是空的（界面显示「未设置有效期」→ 后端判 unresolved → 模板
 * 下拉里看不到它）。所以这里不能只看 catalog_loaded。
 */
function promotionValidityMissing(capture) {
  if (!capture || typeof capture !== 'object') return false;
  if (String(capture.listing_mode || 'site').trim().toLowerCase() === 'cnsc_global') return false;
  const items = Array.isArray(capture.promotion_options) ? capture.promotion_options : [];
  // 采集结果里的选中项有两种形态：首采只有 `selected: true` 标记（selected_promotion_id
  // 由后端归一化时才补上），重采后才带 id。两种都认，避免漏掉「有名称没有效期」。
  const selectedId = String(capture.selected_promotion_id || '').trim();
  const selected = items.find((item) => item && item.selected === true)
    || (selectedId
      ? items.find((item) => String(item && item.activity_id || '').trim() === selectedId)
      : null);
  if (!selected) return false;
  return !String(selected.start_at || '').trim() || !String(selected.end_at || '').trim();
}

function readPromotionPageSource() {
  return fs.readFileSync(PROMOTION_PAGE_PATH, 'utf8');
}

function runPromotionPage(frame, phase) {
  const source = readPromotionPageSource();
  return frame.evaluate(
    ([pageSource, options]) => (0, eval)(`(${pageSource})`)(options),
    [source, { phase, hint: PROMOTION_DIALOG_HINT, labels: PROMOTION_LABELS }],
  );
}

/**
 * 展开「选择折扣活动」列表（best-effort，只为让采集器读到活动状态与有效期）。
 *
 * 本土真机里「折扣活动」是 SKU 表头 colgroup 里的 `span.change-discount` 文字链
 * （点它调用 mdChangeDiscount 弹窗），不是 .el-select/input；旧实现只按表单项找
 * 输入框，永远点不开。这里把触发点识别搬到共享页面模块里。
 */
async function openPromotionList(frame) {
  return runPromotionPage(frame, 'open');
}

/** 收回活动列表弹窗：先 Escape，没关上再点弹窗右上角关闭（不碰任何活动行/确定按钮）。 */
async function closePromotionList(frame) {
  return runPromotionPage(frame, 'close').catch(() => ({ closed: 'error' }));
}

async function collectPageImport({ cdpUrl = CDP_DEFAULT, plan = {} } = {}) {
  if (!fs.existsSync(CAPTURE_PATH)) {
    return { ok: false, status: 'failed', error: '页面采集脚本缺失（mabang_page_import_capture.js）' };
  }
  const captureSource = fs.readFileSync(CAPTURE_PATH, 'utf8');
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: CDP_CONNECT_TIMEOUT_MS });
  try {
    const targets = findProductEditTargets(browser);
    if (!targets.length) {
      return {
        ok: false,
        status: 'not_on_product_edit_page',
        error: '当前浏览器没有打开 Shopee 商品编辑页。请在马帮里打开一个商品（编辑），再点采集。',
      };
    }
    const target = targets[targets.length - 1];
    const menu = await openWatermarkMenu(target.frame).catch((error) => ({ opened: false, method: `error:${String(error).slice(0, 60)}` }));
    const readCapture = () =>
      target.frame.evaluate(
        ([source, inputPlan]) => (0, eval)(`(${source})`)(inputPlan),
        [captureSource, plan],
      );
    let capture = null;
    let failure = '';
    let promotionDialog = { attempted: false };
    try {
      capture = await readCapture();
      // 活动列表没读到，或读到了但选中活动没有有效期：点开一次「选择折扣活动」把行
      // 加载出来再采一遍。有效期只在弹窗表格里，回显的选中项永远没有日期。
      promotionDialog.catalog_loaded_before = capture.promotion_catalog_loaded === true;
      promotionDialog.validity_missing_before = promotionValidityMissing(capture);
      if (promotionCatalogMissing(capture) || promotionValidityMissing(capture)) {
        promotionDialog.attempted = true;
        const opened = await openPromotionList(target.frame).catch((error) => ({
          opened: false,
          method: `error:${String(error).slice(0, 60)}`,
        }));
        promotionDialog.opened = Boolean(opened && opened.opened);
        promotionDialog.method = (opened && opened.method) || '';
        promotionDialog.trigger = (opened && opened.trigger) || '';
        promotionDialog.rows = (opened && opened.rows) || 0;
        promotionDialog.rows_ready = Boolean(opened && opened.rows_ready);
        promotionDialog.triggers = (opened && opened.triggers) || 0;
        if (promotionDialog.opened) {
          try {
            const enriched = await readCapture();
            promotionDialog.catalog_loaded_after = enriched.promotion_catalog_loaded === true;
            promotionDialog.validity_missing_after = promotionValidityMissing(enriched);
            promotionDialog.promotion_count_after = Array.isArray(enriched.promotion_options)
              ? enriched.promotion_options.length
              : 0;
            // 这次真的读到了店铺活动列表才替换：只有回显的选中项时保留第一次的结果，
            // 免得把首采里更完整的信息丢掉。
            if (!promotionCatalogMissing(enriched)) {
              capture = enriched;
              promotionDialog.reloaded = true;
            } else {
              promotionDialog.reloaded = false;
            }
          } finally {
            const closed = await closePromotionList(target.frame);
            promotionDialog.closed = typeof closed === 'string' ? closed : (closed && closed.closed) || '';
          }
        }
      }
    } catch (error) {
      failure = String((error && error.message) || error);
    } finally {
      if (menu && menu.opened && menu.method !== 'already_open') await closeWatermarkMenu(target.frame);
    }
    if (failure) {
      return { ok: false, status: 'failed', error: `页面采集失败：${failure.slice(0, 300)}` };
    }
    if (!capture || typeof capture !== 'object' || Array.isArray(capture)) {
      return { ok: false, status: 'failed', error: '页面采集返回的结果格式不对' };
    }
    if (capture.source !== 'mabang_current_page') {
      return { ok: false, status: 'failed', error: '页面采集结果来源无效（不是马帮当前页）' };
    }
    return {
      ok: true,
      status: 'completed',
      capture,
      page_url: target.url,
      product_pages: targets.map((item) => item.url),
      watermark_menu: menu,
      promotion_dialog: promotionDialog,
    };
  } finally {
    await browser.close().catch(() => {});
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let payload;
  try {
    payload = await collectPageImport({ cdpUrl: args.cdpUrl, plan: args.plan });
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
  promotionCatalogMissing,
  promotionValidityMissing,
  readPromotionPageSource,
  openPromotionList,
  closePromotionList,
  collectPageImport,
};
