'use strict';

// 商品维护「颜色图顺序跟随产品图顺序」联动（2026-10-10）。
//
// 把 app.js 里真实的联动函数和拖动处理函数（moveBaseImage / moveBaseColor）抠进
// vm 里跑，桩掉渲染函数：断言两个方向都成立——
//   拖动产品图 → 颜色顺序按各颜色图在新产品图顺序里的位置重排；
//   拖动颜色   → 它的颜色图挪到对应槽位（主图/模特图的位置不动），顺序收敛一致；
// 未选颜色图、或那张图不在产品图顺序里的颜色一律排在最后；开关关闭时两边互不影响。

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

const LINKAGE_FUNCTIONS = [
  'baseColorFollowEnabled',
  'baseColorImageFileName',
  'baseColorImageSlots',
  'syncBaseColorsToImageOrder',
  'syncBaseImageOrderToColors',
  'applyBaseColorFollowFromImages',
  'applyBaseColorFollowFromColors',
  'moveBaseImage',
  'moveBaseColor',
];

function buildEditor({ images = [], colors = [], map = {}, follow = true } = {}) {
  const state = {
    editingBaseProductImages: [...images],
    editingBaseColors: [...colors],
    editingBaseVariantImageMap: { ...map },
    editingBaseColorOrderFollowsImages: follow,
    basePriceColorRows: [],
  };
  const renders = { imageOrder: 0, imagePicker: 0, colorMap: 0, priceColors: 0 };
  const context = {
    state,
    renderBaseImageOrder: () => { renders.imageOrder += 1; },
    renderBaseImagePicker: () => { renders.imagePicker += 1; },
    renderBaseVariantImageMap: () => { renders.colorMap += 1; },
    renderBasePriceColors: () => { renders.priceColors += 1; },
  };
  vm.createContext(context);
  vm.runInContext(LINKAGE_FUNCTIONS.map((name) => extractFunction(APP_SOURCE, name)).join('\n'), context);
  return {
    state,
    renders,
    images: () => [...state.editingBaseProductImages],
    colors: () => [...state.editingBaseColors],
    ...context,
  };
}

test('拖动产品图：颜色顺序按各颜色图在新产品图顺序里的位置重排', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg', 'Black.jpg'],
    colors: ['White', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });

  editor.moveBaseImage(2, 1); // Black.jpg 挪到 White.jpg 前面

  assert.deepEqual(editor.images(), ['主图.jpg', 'Black.jpg', 'White.jpg']);
  assert.deepEqual(editor.colors(), ['Black', 'White']);
  assert.ok(editor.renders.colorMap >= 1, '颜色图区应该重画');
  assert.ok(editor.renders.priceColors >= 1, '价格行应该跟着新颜色顺序重画');
});

test('拖动颜色行：它的颜色图挪到对应槽位，主图/模特图位置不动', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg', 'Black.jpg', '模特1.jpg'],
    colors: ['White', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });

  editor.moveBaseColor(1, 0); // Black 拖到最前

  assert.deepEqual(editor.images(), ['主图.jpg', 'Black.jpg', 'White.jpg', '模特1.jpg']);
  assert.deepEqual(editor.colors(), ['Black', 'White']);
  assert.ok(editor.renders.imageOrder >= 1, '产品图顺序区应该重画');
  assert.ok(editor.renders.imagePicker >= 1, '可选产品图应该跟着重画');
});

test('未选颜色图的颜色排在最后，且不改动它们之间的相对顺序', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg', 'Black.jpg'],
    colors: ['White', '缺图色', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });

  editor.moveBaseColor(1, 0); // 想把「缺图色」拖到最前
  assert.deepEqual(editor.colors(), ['White', 'Black', '缺图色'], '未匹配的应该回到最后');

  // 两个未匹配颜色之间仍然可以互相换位，只是整体跟在匹配颜色后面。
  const pair = buildEditor({
    images: ['主图.jpg', 'White.jpg'],
    colors: ['White', '甲', '乙'],
    map: { White: 'White.jpg' },
  });
  pair.moveBaseColor(2, 1);
  assert.deepEqual(pair.colors(), ['White', '乙', '甲']);
});

test('颜色图不在产品图顺序里也算未匹配', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg'],
    colors: ['White', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });

  editor.applyBaseColorFollowFromImages();

  assert.deepEqual(editor.colors(), ['White', 'Black']);
  assert.deepEqual(editor.images(), ['主图.jpg', 'White.jpg'], '不该凭空把 Black.jpg 塞进产品图');
});

test('全部颜色都没有颜色图时，颜色顺序仍然可以自由拖动', () => {
  const editor = buildEditor({
    images: ['主图.jpg', '模特1.jpg'],
    colors: ['丙', '甲', '乙'],
    map: {},
  });

  editor.moveBaseColor(0, 2);

  assert.deepEqual(editor.colors(), ['甲', '乙', '丙']);
});

test('开关关闭时，拖动任一边都不影响另一边', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg', 'Black.jpg'],
    colors: ['White', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
    follow: false,
  });

  editor.moveBaseImage(2, 1);
  assert.deepEqual(editor.images(), ['主图.jpg', 'Black.jpg', 'White.jpg']);
  assert.deepEqual(editor.colors(), ['White', 'Black'], '关闭后产品图变序不该带动颜色');

  editor.moveBaseColor(1, 0);
  assert.deepEqual(editor.colors(), ['Black', 'White']);
  assert.deepEqual(editor.images(), ['主图.jpg', 'Black.jpg', 'White.jpg'], '关闭后颜色变序不该带动产品图');
});

test('颜色行带价格行一起拖动时，价格行按新顺序重排', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'White.jpg', 'Black.jpg'],
    colors: ['White', 'Black'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });
  editor.state.basePriceColorRows = [{ color: 'White' }, { color: 'Black' }];

  editor.moveBaseColor(1, 0);

  assert.deepEqual(editor.state.basePriceColorRows.map((row) => row.color), ['Black', 'White']);
});

test('重复同步是幂等的，不会越拖越乱', () => {
  const editor = buildEditor({
    images: ['主图.jpg', 'Black.jpg', 'White.jpg'],
    colors: ['Black', 'White'],
    map: { White: 'White.jpg', Black: 'Black.jpg' },
  });

  editor.applyBaseColorFollowFromImages();
  editor.applyBaseColorFollowFromImages();
  editor.applyBaseColorFollowFromColors();

  assert.deepEqual(editor.colors(), ['Black', 'White']);
  assert.deepEqual(editor.images(), ['主图.jpg', 'Black.jpg', 'White.jpg']);
});

test('缺字段的旧商品默认打开联动', () => {
  const editor = buildEditor({});
  assert.equal(editor.baseColorFollowEnabled(), true);

  editor.state.editingBaseColorOrderFollowsImages = false;
  assert.equal(editor.baseColorFollowEnabled(), false);
});
