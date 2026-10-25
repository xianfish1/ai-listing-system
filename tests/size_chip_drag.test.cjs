'use strict';

// 规格与 SKU：尺码 chip 拖动排序回归。
// 旧实现用「把 next 倒序 insertBefore 到 next[0] 之前」重排 DOM，实际落点变成
// next 的反序：把第一个尺码拖到第三个位置上，整列会被打乱。这里用最小 DOM 替身
// 直接跑 app.js 里真实的 dragstart/dragover/dragend handler，断言落点和稳定性。

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_SOURCE = fs.readFileSync(path.join(__dirname, '../apps/web/app.js'), 'utf8');
const CHIP_WIDTH = 80;

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

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.classes = new Set();
    this.listeners = new Map();
    this.classList = {
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
      contains: (name) => this.classes.has(name),
    };
  }

  addEventListener(type, handler) {
    this.listeners.set(type, handler);
  }

  matches(selector) {
    const [tag, ...classes] = selector.split('.');
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    return classes.every((name) => this.classes.has(name));
  }

  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }

  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
  }

  querySelectorAll(selector) {
    const attribute = /\[data-template-size-label="(.*)"\]/.exec(selector);
    if (attribute) return this.descendants().filter((el) => el.dataset.templateSizeLabel === attribute[1]);
    const [tag, ...classes] = selector.split('.');
    return this.descendants().filter(
      (el) => (!tag || el.tagName === tag.toUpperCase()) && classes.every((name) => el.classes.has(name)),
    );
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  insertBefore(node, reference) {
    // 与真实 DOM 一致：先把节点从旧位置摘下来，再按参考节点的当前位置插入；
    // insertBefore(node, node) 等价于插回原处的空操作。
    if (reference === node) reference = node.nextElementSibling();
    if (node.parentNode) node.parentNode.removeChild(node);
    const at = reference ? this.children.indexOf(reference) : this.children.length;
    assert.ok(at >= 0, 'insertBefore 的参考节点不在这个父节点下');
    this.children.splice(at, 0, node);
    node.parentNode = this;
    return node;
  }

  removeChild(node) {
    const at = this.children.indexOf(node);
    if (at >= 0) this.children.splice(at, 1);
    node.parentNode = null;
    return node;
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  after(node) {
    this.parentNode.insertBefore(node, this.nextElementSibling());
  }

  before(node) {
    this.parentNode.insertBefore(node, this);
  }

  nextElementSibling() {
    const siblings = this.parentNode.children;
    return siblings[siblings.indexOf(this) + 1] || null;
  }

  // 单行排布：第 n 个 chip 占 [n * CHIP_WIDTH, (n + 1) * CHIP_WIDTH)。
  getBoundingClientRect() {
    const index = this.parentNode ? this.parentNode.children.indexOf(this) : 0;
    return { left: index * CHIP_WIDTH, width: CHIP_WIDTH };
  }
}

function buildEditor(sizes, selectedSizes) {
  const selected = new Set(selectedSizes ?? sizes);
  const root = new FakeElement('fieldset');
  for (const size of sizes) {
    const label = new FakeElement('label');
    label.dataset.templateSizeLabel = size;
    if (selected.has(size)) label.classList.add('size-chip--selected');
    root.appendChild(label);
  }
  const priceGridCalls = [];
  const context = {
    HTMLElement: FakeElement,
    CSS: { escape: (value) => value },
    collectSizePriceOverrides: () => ({ M: '9.99' }),
    renderSizePriceOverrides: (values) => priceGridCalls.push(values),
    // 拖动 handler 重排后还会重绘默认 SKU 的按尺码价格（2026-09-24 起），替身补齐。
    collectDefaultSkuSizePriceOverrides: () => ({}),
    renderDefaultSkuSizePriceOverrides: () => {},
  };
  vm.createContext(context);
  vm.runInContext(
    `${extractFunction(APP_SOURCE, 'sizeChipInsertSide')}\n${extractFunction(APP_SOURCE, 'attachSizeChipDragHandlers')}`,
    context,
  );
  context.attachSizeChipDragHandlers(root);
  return { root, priceGridCalls };
}

const order = (root) => root.children.map((label) => label.dataset.templateSizeLabel);
const chip = (root, size) => root.querySelectorAll('label.size-chip--selected').find((el) => el.dataset.templateSizeLabel === size);

function fire(root, type, { target, clientX = 0 }) {
  const handler = root.listeners.get(type);
  assert.ok(handler, `root 上没有绑定 ${type}`);
  let defaultPrevented = false;
  handler({
    target,
    clientX,
    dataTransfer: { effectAllowed: '', setData() {} },
    preventDefault: () => {
      defaultPrevented = true;
    },
  });
  return defaultPrevented;
}

// 把光标停在 target 的左/右半边，拖 from 放上去。每次 dragover 都按替身布局把光标
// 下真正的 chip 当成事件目标（和浏览器一致），连发多次以验证顺序不会来回跳。
function drag(root, fromSize, targetSize, side, { moves = 3 } = {}) {
  const from = chip(root, fromSize);
  const target = chip(root, targetSize);
  const box = target.getBoundingClientRect();
  const clientX = side === 'after' ? box.left + box.width - 1 : box.left + 1;
  fire(root, 'dragstart', { target: from });
  for (let i = 0; i < moves; i += 1) {
    const under = root.children.find((el) => {
      const rect = el.getBoundingClientRect();
      return clientX >= rect.left && clientX < rect.left + rect.width;
    });
    if (under) fire(root, 'dragover', { target: under, clientX });
  }
  fire(root, 'dragend', { target: from });
  return order(root);
}

test('拖到目标 chip 左半边插到它前面，右半边插到它后面', () => {
  const cases = [
    { sizes: ['100', '110', '120', '130'], from: '100', target: '120', side: 'after', expected: ['110', '120', '100', '130'] },
    { sizes: ['100', '110', '120', '130'], from: '130', target: '100', side: 'before', expected: ['130', '100', '110', '120'] },
    { sizes: ['100', '110', '120', '130'], from: '110', target: '100', side: 'before', expected: ['110', '100', '120', '130'] },
    { sizes: ['100', '110', '120', '130'], from: '120', target: '110', side: 'before', expected: ['100', '120', '110', '130'] },
    { sizes: ['100', '110', '120', '130', '140'], from: '140', target: '100', side: 'before', expected: ['140', '100', '110', '120', '130'] },
    { sizes: ['100', '110', '120', '130', '140'], from: '100', target: '140', side: 'after', expected: ['110', '120', '130', '140', '100'] },
    { sizes: ['100', '110', '120', '130', '140'], from: '130', target: '140', side: 'after', expected: ['100', '110', '120', '140', '130'] },
  ];
  for (const item of cases) {
    const { root } = buildEditor(item.sizes);
    const actual = drag(root, item.from, item.target, item.side);
    assert.deepEqual(actual, item.expected, `${item.from} 拖到 ${item.target} 的 ${item.side} 侧`);
  }
});

test('连续 dragover 不再来回跳：光标停住时顺序稳定，价格行跟着新顺序重排', () => {
  const { root, priceGridCalls } = buildEditor(['100', '110', '120', '130']);

  // 光标停在 120 的右半边不动，dragover 连发 6 次
  assert.deepEqual(drag(root, '100', '120', 'after', { moves: 6 }), ['110', '120', '100', '130']);
  // 每次移动后价格行都按新顺序重排
  assert.ok(priceGridCalls.length >= 1);
  assert.deepEqual(priceGridCalls.at(-1), { M: '9.99' });
});

test('未勾选的尺码永远排在已勾选之后，拖动不会把它们卷进来', () => {
  const { root } = buildEditor(['100', '110', '120'], ['100', '110']);
  assert.deepEqual(drag(root, '110', '100', 'before'), ['110', '100', '120']);

  // 拖到自己已经贴着的那一侧＝原地不动，未勾选的 120 仍然跟在最后
  const { root: adjacent } = buildEditor(['100', '110', '120'], ['100', '110']);
  assert.deepEqual(drag(adjacent, '100', '110', 'before'), ['100', '110', '120']);
  assert.deepEqual(drag(adjacent, '110', '100', 'after'), ['100', '110', '120']);
});

test('拖动只搬动被拖的 chip：勾选状态和节点身份都不变，拖动样式被清掉', () => {
  const { root } = buildEditor(['100', '110', '120']);
  const dragged = chip(root, '100');

  drag(root, '100', '120', 'after');

  assert.equal(chip(root, '100'), dragged, '被拖的 chip 应该还是同一个节点');
  assert.deepEqual(order(root), ['110', '120', '100']);
  assert.equal(root.querySelectorAll('.size-chip--dragging').length, 0, 'dragend 必须清掉拖动中的样式');
});

test('按下但没移动（dragover 命中被拖的 chip 自己）不改顺序', () => {
  const { root, priceGridCalls } = buildEditor(['100', '110', '120']);

  const dragged = chip(root, '110');
  fire(root, 'dragstart', { target: dragged });
  fire(root, 'dragover', { target: dragged, clientX: dragged.getBoundingClientRect().left + 1 });
  fire(root, 'dragend', { target: dragged });

  assert.deepEqual(order(root), ['100', '110', '120']);
  assert.equal(priceGridCalls.length, 0);
});
