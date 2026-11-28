// 回归用例：描述插图「撤销失败」时的 DOM 兜底。
// 现场 2026-11-28 TH_1JH / AL1393：尺码表图上传后落到了正文最前面，Ctrl+Z 撤了 3 次没生效
// （wangEditor 的历史是异步压栈的，图出现在 DOM 里时这一步还没进撤销栈），最后只能报
// description_size_chart_position_not_at_tail。现在撤销会先静置、多按几次，仍失败就把这批
// 新图从编辑器 DOM 里摘掉，让按地址的批量插图把尺码表插回正文末尾。
//
// 这条用例只测「摘图」这一步的边界：只摘这批地址、CDN 换域名按文件名也认得出、不动正文和旧图。
// 跑法：node scripts/verify_description_media_rollback.cjs
const assert = require('node:assert');
const fs = require('node:fs');

process.env.MABANG_EXECUTOR_SILENT = '1';
const { chromium } = require('playwright');
const { removeDescriptionImages } = require('./mabang_playwright_executor.cjs');

const CHROME_CANDIDATES = [
  process.env.MABANG_CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean);
const CHROME_PATH = CHROME_CANDIDATES.find((candidate) => fs.existsSync(candidate)) || '';

const OLD_IMAGE = 'https://cf.shopee.co.th/file/old-product-image-1111';
const SIZE_CHART = 'https://cf.shopee.co.th/file/uploaded-size-chart-9999';
const SIZE_CHART_CDN = 'https://down-th.img.susercontent.com/file/uploaded-size-chart-9999?x-oss-process=image';

function buildHtml() {
  // 正文 + 一张旧图 + 一张落到正文中间的尺码表 + 尾部正文：摘完之后正文和旧图都必须还在。
  return `<!doctype html><html><head><meta charset="utf-8"></head><body>
    <div class="el-form-item">
      <label class="el-form-item__label">商品描述</label>
      <div class="el-form-item__content">
        <div class="wangEditor-container">
          <div id="desc-editor" class="wangEditor-txt" contenteditable="true">
            <p><img src="${SIZE_CHART}"></p>
            <p>PH Local Stock — fast delivery in 1~2 days.</p>
            <p><img src="${OLD_IMAGE}"></p>
            <p>Size guide below.</p>
          </div>
        </div>
      </div>
    </div>
  </body></html>`;
}

async function main() {
  const browser = await chromium.launch({ headless: true, ...(CHROME_PATH ? { executablePath: CHROME_PATH } : {}) });
  const page = await browser.newPage();
  const frame = page.mainFrame();
  let passed = 0;

  // 1. 整串地址命中：只摘掉刚刚上传的那张，正文与旧图原样保留。
  await page.setContent(buildHtml());
  const removedExact = await removeDescriptionImages(frame, 'desc-editor', [SIZE_CHART]);
  assert.strictEqual(removedExact, 1, '应只摘掉这一张新上传的图');
  let state = await page.evaluate(() => {
    const editor = document.getElementById('desc-editor');
    return {
      urls: [...editor.querySelectorAll('img')].map((image) => image.getAttribute('src')),
      text: String(editor.innerText || '').replace(/\s+/g, ' ').trim(),
      emptyParagraphs: [...editor.querySelectorAll('p')].filter((p) => !p.querySelector('img') && !String(p.textContent || '').trim()).length,
    };
  });
  assert.deepStrictEqual(state.urls, [OLD_IMAGE], '旧图必须留下');
  assert.ok(state.text.includes('PH Local Stock'), '正文必须留下');
  assert.ok(state.text.includes('Size guide below.'), '尾部正文必须留下');
  assert.strictEqual(state.emptyParagraphs, 0, '摘空的段落应一起删掉');
  passed += 1;

  // 2. CDN 换了域名、地址只剩文件名相同：按文件名也认得出。
  await page.setContent(buildHtml());
  const removedByKey = await removeDescriptionImages(frame, 'desc-editor', [SIZE_CHART_CDN]);
  assert.strictEqual(removedByKey, 1, 'CDN 换域名时应按文件名命中');
  passed += 1;

  // 3. 不在正文里的地址：一张都不该摘。
  await page.setContent(buildHtml());
  const removedNothing = await removeDescriptionImages(frame, 'desc-editor', ['https://cf.shopee.co.th/file/not-here-0000']);
  assert.strictEqual(removedNothing, 0, '地址不匹配时不能动其它图');
  passed += 1;

  await browser.close();
  console.log(`PASS  ${passed} 条用例全部通过`);
}

main().catch((error) => {
  console.error('FAIL ', error && error.stack ? error.stack : error);
  process.exit(1);
});
