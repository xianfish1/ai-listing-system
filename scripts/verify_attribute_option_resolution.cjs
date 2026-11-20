// 回归用例：模板属性值 → 马帮页面选项的解析。
// 现场 2026-11-20：原产地选「泰国」，刊登出来是「日本」。
// 根因：选项 id 都是短数字，双向子串匹配让 "129"（泰国）命中 id="1" 的「日本」。
// 跑法：node scripts/verify_attribute_option_resolution.cjs
const assert = require('node:assert');
const { resolveOptionFromValue } = require('./mabang_playwright_executor.cjs');

const ORIGIN_OPTIONS = [
  { value: '1', label: '日本 (Japan)' },
  { value: '35', label: '中国大陆 (Mainland China)' },
  { value: '48', label: '韩国 (Korea)' },
  { value: '68', label: '印尼 (Indonesia)' },
  { value: '93', label: '其他 (Others)' },
  { value: '110', label: '美国 (America)' },
  { value: '112', label: '台湾 (Taiwan)' },
  { value: '129', label: '泰国 (Thailand)' },
  { value: '130', label: '越南 (Vietnam)' },
  { value: '131', label: '马来西亚 (Malaysia)' },
  { value: '132', label: '菲律宾 (Philippines)' },
  { value: '133', label: '新加坡 (Singapore)' },
];

const cases = [
  // [取值, 期望命中的 value, 说明]
  ['129', '129', '泰国 id 必须精确命中自己，不能被 id=1 的日本吃掉'],
  ['1', '1', '日本 id'],
  ['110', '110', '美国 id'],
  ['112', '112', '台湾 id'],
  ['130', '130', '越南 id'],
  ['131', '131', '马来西亚 id'],
  ['132', '132', '菲律宾 id'],
  ['133', '133', '新加坡 id'],
  ['泰国 (Thailand)', '129', '完整标签'],
  ['泰国', '129', '中文标签'],
  ['Thailand', '129', '英文标签'],
  ['Philippines', '132', '自选值风格（英文）'],
  ['马来西亚', '131', '中文标签'],
  ['Japan', '1', '英文标签'],
  ['999', null, '枚举里没有的数字 id → 不硬猜'],
  ['Atlantis', null, '枚举里没有的文字 → 不硬猜'],
];

let failed = 0;
for (const [input, expectedValue, note] of cases) {
  const hit = resolveOptionFromValue(ORIGIN_OPTIONS, input);
  const actual = hit ? hit.value : null;
  const ok = actual === expectedValue;
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${JSON.stringify(input)} -> ${actual === null ? 'null' : JSON.stringify(hit.label)}${ok ? '' : `  (期望 ${expectedValue})`}  ${note}`);
}
assert.strictEqual(failed, 0, `${failed} 条用例失败`);
console.log(`\n${cases.length} 条用例全部通过`);
