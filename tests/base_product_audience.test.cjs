'use strict';

// 商品维护：卡片「人群」标记（男童 / 女童 / 男装 / 女装）只看标题关键词。
// 这里把 app.js 里真实的 baseProductAudienceKey + 关键词表抽出来跑，钉住三条现场规则：
//   1) boy/boys、girl/girls、man/men、woman/women 及其 Mens / Womens / 's 写法都要认；
//   2) 标题里出现两个时取**最先出现**的那个（"Boys Girls" → 男童，
//      "Men's and Women's" → 男装）；
//   3) 单词里的子串不算（woman 里的 man、women 里的 men、boyfriend 里的 boy）。

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_SOURCE = fs.readFileSync(path.join(__dirname, '../apps/web/app.js'), 'utf8');

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}`);
  let depth = 0;
  for (let i = source.indexOf('{', start); i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`无法解析 ${name} 的函数体`);
}

function extractConstArray(source, name) {
  const start = source.indexOf(`const ${name} = [`);
  assert.ok(start >= 0, `app.js 里找不到 ${name}`);
  const end = source.indexOf('\n];', start);
  assert.ok(end > start, `无法解析 ${name}`);
  return source.slice(start, end + 3);
}

const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  [
    extractConstArray(APP_SOURCE, 'BASE_PRODUCT_AUDIENCE_PATTERNS'),
    extractFunction(APP_SOURCE, 'baseProductAudienceKey'),
  ].join('\n\n'),
  sandbox,
);
const audienceKey = sandbox.baseProductAudienceKey;

function keyFor(title) {
  return audienceKey({ listing_title: title });
}

test('boy/girl/man/woman 及 Mens/Womens/\'s 写法都认', () => {
  assert.equal(keyFor('Kids Boys Spider Graphic T-Shirt'), 'boys');
  assert.equal(keyFor('Boy Cotton Tee'), 'boys');
  assert.equal(keyFor('Boy\'s Cotton Tee'), 'boys');

  assert.equal(keyFor('Kids Girl Cotton Tee'), 'girls');
  assert.equal(keyFor('Girls Unicorn T-Shirt'), 'girls');

  assert.equal(keyFor('Men\'s Casual T-Shirt'), 'men');
  assert.equal(keyFor('Mens Basic Tee'), 'men');
  assert.equal(keyFor('Man Casual Tee'), 'men');

  assert.equal(keyFor('Women\'s Loose Fit Tee'), 'women');
  assert.equal(keyFor('Womens Loose Tee'), 'women');
  assert.equal(keyFor('Woman Loose Tee'), 'women');

  assert.equal(keyFor('男童纯棉短袖'), 'boys');
  assert.equal(keyFor('女童印花T恤'), 'girls');
  assert.equal(keyFor('男装基础款'), 'men');
  assert.equal(keyFor('女装宽松款'), 'women');
});

test('标题里两个关键词都出现时取最先出现的那个', () => {
  assert.equal(keyFor('Kids T-Shirt for Boys and Girls'), 'boys');
  assert.equal(keyFor('Kids T-Shirt for Girls and Boys'), 'girls');
  assert.equal(keyFor('Men\'s and Women\'s T-shirt'), 'men');
  assert.equal(keyFor('Women\'s and Men\'s Tee'), 'women');
});

test('单词里的子串不算人群，认不出来返回空', () => {
  assert.equal(keyFor('Minecraft Graphic Tee'), '');
  assert.equal(keyFor('Boyfriend Fit Tee'), '');
  assert.equal(keyFor('Showmanship Tee'), '');
  assert.equal(keyFor('Womenish Tee'), '');
  assert.equal(keyFor('Means Nothing Tee'), '');
  assert.equal(keyFor(''), '');
  assert.equal(audienceKey({}), '');
});

test('没有 listing_title 时退回商品名，标题优先', () => {
  assert.equal(audienceKey({ listing_title: 'Boys Tee', product_name_cn: '女童款' }), 'boys');
  assert.equal(audienceKey({ product_name_cn: '女童款' }), 'girls');
  assert.equal(audienceKey({ base_name: 'Men Tee' }), 'men');
});
