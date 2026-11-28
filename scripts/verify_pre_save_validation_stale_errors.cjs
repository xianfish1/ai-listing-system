// 回归用例：保存前校验必须把「真缺的必填项」和「已经填好、只是错误没清掉」分开。
// 现场 2026-11-28：5 单（AL1345 / AL1418 / AL1499 / AL1522 / AL1460）在
// validate_before_save 被 pre_save_validation_failed 拦下，详情里只有一条
// 「商品标题: 请输入商品标题」，可标题其实已经写进去了——页面重渲染把输入框刷空后
// 又被补回，Element UI 的 validateState 停在上一轮错误上，保存闸把它当成真失败。
// 运营点「再次尝试」换一次页面时序就好了，所以这一步必须判「这一格现在有没有值」。
//
// 跑法（需要一个本机 Chrome；默认走 Playwright 自带的 Chromium）：
//   node scripts/verify_pre_save_validation_stale_errors.cjs
//   set MABANG_CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

process.env.MABANG_EXECUTOR_SILENT = '1';
const { chromium } = require('playwright');
const { validateBeforeSave, reassertBasicInfo, TEXT } = require('./mabang_playwright_executor.cjs');

const CHROME_CANDIDATES = [
  process.env.MABANG_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean);
const CHROME_PATH = CHROME_CANDIDATES.find((candidate) => fs.existsSync(candidate)) || '';

// 站点（非跨境）页面的必需阶段；少一个就会先报 required_stage_missing，测不到本用例。
const REQUIRED_STAGES = [
  'select_store',
  'select_category',
  'set_category_attributes',
  'fill_basic_info',
  'upload_product_images',
  'configure_variants',
  'disable_sku_weight',
  'fill_sku_grid',
  'fill_logistics_info',
  'select_discount_activity',
  'upload_variant_thumbnails',
];

const TITLE = 'Christmas Disney Mickey Mouse T Shirt for Kids Girl Cartoon Cotton 2-12Y';

function buildStages() {
  return Object.fromEntries(REQUIRED_STAGES.map((stage) => [stage, { ok: true }]));
}

function buildTask() {
  return {
    store: { listing_mode: 'site' },
    basic_info: { parent_sku: 'AL1499', title: TITLE },
    sku_grid: { expected_rows: 0, skus: [] },
  };
}

// 一个最小的马帮表单切片：标题表单项（可带行内错误）+ 唯一的「保存」按钮。
function buildHtml({ titleValue, errorText, navBadge }) {
  const safeTitle = String(titleValue || '').replace(/"/g, '&quot;');
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    body { font-family: sans-serif; margin: 12px; }
    .el-form-item { margin: 10px 0; }
    .el-form-item__label { display: block; margin-bottom: 4px; }
    .el-form-item__content { display: block; }
    .el-form-item__error { color: #f56c6c; font-size: 12px; min-height: 16px; }
    button { width: 90px; height: 32px; }
    #nav-badge { display: inline-block; padding: 2px 6px; }
  </style></head><body>
    <div class="el-form-item is-error" id="title-item">
      <label class="el-form-item__label">商品标题:</label>
      <div class="el-form-item__content">
        <input id="title" maxlength="120" value="${safeTitle}">
        <div class="el-form-item__error">${errorText || ''}</div>
      </div>
    </div>
    ${navBadge ? `<div id="nav-badge">${navBadge}</div>` : ''}
    <button class="el-button" type="button">${TEXT.save}</button>
  </body></html>`;
}

async function expectedBlocked(frame, label, needle = '请输入商品标题') {
  try {
    await validateBeforeSave(frame, buildTask(), buildStages());
  } catch (error) {
    assert.strictEqual(error.reason, 'pre_save_validation_failed', `${label}: 期望 pre_save_validation_failed，实际 ${error.reason}`);
    assert.ok(
      (error.details.form_errors || []).some((text) => text.includes(needle)),
      `${label}: 详情里应保留「${needle}」，实际 ${JSON.stringify(error.details.form_errors)}`,
    );
    return error.details;
  }
  assert.fail(`${label}: 期望被拦下，实际通过`);
  return null;
}

async function expectedPass(frame, label) {
  const audit = await validateBeforeSave(frame, buildTask(), buildStages());
  assert.deepStrictEqual(audit.form_errors, [], `${label}: 过期错误不应算进 form_errors`);
  return audit;
}

async function main() {
  const browser = await chromium.launch({ headless: true, ...(CHROME_PATH ? { executablePath: CHROME_PATH } : {}) });
  const page = await browser.newPage();
  const frame = page.mainFrame();
  // 发布守卫已安装：真实页面由 installPublishGuard 注入，这里每次改完 DOM 直接置位。
  const load = async (html) => {
    await page.setContent(html);
    await page.evaluate(() => {
      window.__AI_LISTING_PUBLISH_GUARD_V2_INSTALLED__ = true;
      window.__AI_LISTING_PUBLISH_GUARD__ = { blockedClicks: 0, publishClicked: false };
    });
  };
  let passed = 0;

  // 1. 标题已经填好，只是行内错误没清：不能拦。
  await load(buildHtml({ titleValue: TITLE, errorText: '请输入商品标题' }));
  const filled = await expectedPass(frame, '标题有值 + 旧错误');
  assert.strictEqual(filled.stale_form_errors.length, 1, '应把这条错误记为过期');
  assert.strictEqual(filled.stale_form_errors[0].message, '请输入商品标题');
  passed += 1;

  // 2. 标题真的空着：必须照旧拦下，并把原文带进详情。
  await load(buildHtml({ titleValue: '', errorText: '请输入商品标题' }));
  await expectedBlocked(frame, '标题为空');
  passed += 1;

  // 3. 标题有值 + nav 还在报「1 必填项未填」：交给马帮自己裁决（这条豁免原本就有）。
  await load(buildHtml({ titleValue: TITLE, errorText: '请输入商品标题', navBadge: '1 必填项未填' }));
  await expectedPass(frame, '标题有值 + nav 旧计数');
  passed += 1;

  // 4. 非「必填/为空」类错误（价格区间等）即使有值也要拦：豁免只针对过期必填错误。
  await load(buildHtml({ titleValue: TITLE, errorText: '价格必须大于 0' }));
  await expectedBlocked(frame, '非必填类错误', '价格必须大于 0');
  passed += 1;

  // 5. 标题被页面重渲染刷空：保存前的复核要把标题补回去（真正让这批任务失败的那一步）。
  await load(buildHtml({ titleValue: '', errorText: '请输入商品标题' }));
  assert.strictEqual(await page.inputValue('#title'), '', '前置：标题应为空');
  const cwd = process.cwd();
  process.chdir(fs.mkdtempSync(path.join(os.tmpdir(), 'mabang-verify-')));
  try {
    const outcome = await reassertBasicInfo(frame, buildTask());
    assert.strictEqual(outcome.title, 'refilled', `标题应被重新写入，实际 ${JSON.stringify(outcome)}`);
    assert.strictEqual(await page.inputValue('#title'), TITLE, '标题应被补回');
  } finally {
    process.chdir(cwd);
  }
  passed += 1;

  await browser.close();
  console.log(`PASS  ${passed} 条用例全部通过`);
}

main().catch((error) => {
  console.error('FAIL ', error && error.stack ? error.stack : error);
  process.exit(1);
});
