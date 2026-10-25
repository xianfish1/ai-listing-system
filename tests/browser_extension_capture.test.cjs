'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.join(__dirname, '..');

test('browser extension embeds the exact CNSC v3 server capture core', () => {
  const core = fs.readFileSync(path.join(root, 'scripts/mabang_page_import_capture.js'), 'utf8').trim();
  const extension = fs.readFileSync(path.join(root, 'browser_extension/page_capture.js'), 'utf8');

  assert.equal(extension.includes(`const captureFn = ${core};`), true);
  assert.match(extension, /const handleRequest = async \(event\) =>/);
  assert.match(extension, /await prepareCNSCImportState\(\)/);
  assert.match(extension, /target_site_stores_loaded/);
  assert.match(extension, /discount_catalog_loaded/);
});

test('capture sync generator reports a clean extension artifact', () => {
  const generator = require('../scripts/sync_browser_extension_capture.cjs');
  assert.equal(generator.buildPageCapture(), fs.readFileSync(path.join(root, 'browser_extension/page_capture.js'), 'utf8'));
});
