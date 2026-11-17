'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const RESULT_MARKER = 'MABANG_PAGE_IMPORT_RESULT ';
const DEFAULT_CDP_URL = 'http://127.0.0.1:18223';
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

const PREPARE_PAGE_PATH = path.join(__dirname, 'mabang_page_import_prepare.page.js');

// Runs before the read-only capture so that data locked behind the shop picker
// and per-site "选择折扣活动" dialogs is materialized into the DOM/Vue tree.
// Everything happens in the page context; the picker and each dialog are closed
// after reading so no state is mutated on save. The page-side logic lives in
// scripts/mabang_page_import_prepare.page.js — one copy shared with the
// Playwright collector (mabang_page_import_collect.cjs).
async function prepareImportState(frame) {
  const source = fs.readFileSync(PREPARE_PAGE_PATH, 'utf8');
  return frame.evaluate((pageSource) => (0, eval)(`(${pageSource})`)({}), source);
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
