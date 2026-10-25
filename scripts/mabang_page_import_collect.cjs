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
const ADD_WATERMARK_TEXT = '\u6dfb\u52a0\u6c34\u5370';
const MENU_SELECTOR = '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]';

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
    let capture = null;
    let failure = '';
    try {
      capture = await target.frame.evaluate(
        ([source, inputPlan]) => (0, eval)(`(${source})`)(inputPlan),
        [captureSource, plan],
      );
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
  collectPageImport,
};
