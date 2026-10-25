// One-shot Mabang category catalog collector.
// Connects to the dedicated Mabang Chrome (CDP), finds the publish.mabangerp.com
// iframe where the session token lives, and walks the getCategory API BFS.
// Usage: node scripts/collect_mabang_category_catalog_cdp.cjs <storeId> [cdpUrl]
const { chromium } = require('playwright');
const storeId = process.argv[2];
const cdpUrl = process.argv[3] || 'http://127.0.0.1:9223';
if (!storeId) { console.error('storeId required'); process.exit(2); }

const BFS = (storeId) => `(async () => {
  const headers = {
    key: sessionStorage.getItem('memcacheKey') || '',
    ProjectId: sessionStorage.getItem('projectId') || 'erp',
    Authorization: 'Bearer ' + (sessionStorage.getItem('token') || ''),
    'cluster-id': (JSON.parse(sessionStorage.getItem('powerInfo') || '{}').cloud === 1) ? '2' : '1',
  };
  if (!sessionStorage.getItem('token')) return { categories: [], discovery_source: 'api_session_unavailable' };
  const endpoint = 'https://api.mabangerp.com/kandeng/api/v2/shopee/getCategory';
  const seen = new Map();
  const requestedParents = new Set();
  let parents = ['0'];
  let requestCount = 0;
  while (parents.length) {
    const levelParents = parents.filter((id) => !requestedParents.has(id));
    parents = [];
    for (let offset = 0; offset < levelParents.length; offset += 8) {
      const batch = levelParents.slice(offset, offset + 8);
      batch.forEach((id) => requestedParents.add(id));
      const responses = await Promise.all(batch.map(async (parentId) => {
        const params = new URLSearchParams({ shop_id: ${JSON.stringify(String(storeId))}, parent_category_id: parentId, search_name: '', site: '', platformId: '17', is_global: '2' });
        const response = await fetch(endpoint + '?' + params, { headers });
        const payload = await response.json();
        requestCount += 1;
        if (payload.code !== 200 || !Array.isArray(payload.data)) throw new Error('request failed parent ' + parentId + ': ' + JSON.stringify(payload).slice(0,200));
        return payload.data;
      }));
      responses.forEach((rows, index) => {
        const requestedParent = batch[index];
        rows.forEach((raw) => {
          const categoryId = String(raw.category_id || raw.categoryId || raw.id || '').trim();
          if (!categoryId) return;
          const isLeaf = Number(raw.is_leaf ?? raw.isLeaf) === 1;
          const nameZh = String(raw.name_zh || raw.category_name || '').trim();
          const nameEn = String(raw.name_en || '').trim();
          seen.set(categoryId, {
            category_id: categoryId,
            parent_id: String(raw.parent_id || raw.parentId || (requestedParent === '0' ? '' : requestedParent)),
            name: String(raw.name_en || raw.name_zh || raw.category_name || raw.name || '').trim(),
            name_zh: nameZh,
            name_en: nameEn,
            is_leaf: isLeaf,
            path: String(raw.name_en_path || raw.name_init_path || '').trim(),
            path_zh: String(raw.name_zh_path || '').trim(),
          });
          if (!isLeaf && !requestedParents.has(categoryId)) parents.push(categoryId);
        });
      });
      if (seen.size > 20000) throw new Error('catalog exceeded 20000 safety limit');
      await new Promise((resolve) => setTimeout(resolve, 35));
    }
  }
  return { source_url: location.origin + location.pathname, page_title: document.title, discovery_source: 'mabang_shopee_getCategory_api', category_count: seen.size, request_count: requestCount, categories: [...seen.values()] };
})()`;

(async () => {
  const browser = await chromium.connectOverCDP(cdpUrl);
  const ctx = browser.contexts()[0];
  let done = false;
  for (const page of ctx.pages()) {
    if (!/mabangerp/.test(page.url())) continue;
    for (const frame of page.frames()) {
      if (!/publish\.mabangerp\.com/.test(frame.url())) continue;
      try {
        const capture = await frame.evaluate(BFS(storeId));
        console.log('CATALOG_RESULT ' + JSON.stringify({ capture }));
      } catch (e) {
        console.log('CATALOG_RESULT ' + JSON.stringify({ error: String(e && e.message || e) }));
      }
      done = true;
      break;
    }
    if (done) break;
  }
  if (!done) console.log('CATALOG_RESULT ' + JSON.stringify({ error: 'publish frame not found' }));
  await browser.close();
})().catch(e => { console.log('CATALOG_RESULT ' + JSON.stringify({ error: 'ERR ' + e.message })); process.exit(1); });
