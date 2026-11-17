'use strict';

// 刊登模式能力层（site / cnsc_global / cnsc_3pf），与 Python
// services/api/listing_modes.py 保持同名同义。三种模式是两组正交能力：
//
// - isCnscPageMode: 走马帮「CNSC 待发布商品」页签与 global 详情页，
//   消费目标站点店铺、站点价格表、三仓库存、上架后自动发布。
// - isLocalLikeMode: 按本土要求上款，消费默认 SKU(白T恤)、尺码表、
//   描述末尾插图、默认视频、SKU 去括号。
// - isPureCnscMode: 原有的 cnsc_global 分支（单店折扣活动等）。
//
// 执行器里所有 mode 判断都必须走这里，避免第三种模式漏改。

const LISTING_MODE_SITE = 'site';
const LISTING_MODE_CNSC_GLOBAL = 'cnsc_global';
const LISTING_MODE_CNSC_3PF = 'cnsc_3pf';

const ALLOWED_LISTING_MODES = Object.freeze([
  LISTING_MODE_SITE,
  LISTING_MODE_CNSC_GLOBAL,
  LISTING_MODE_CNSC_3PF,
]);

function normalizeListingMode(value, fallback = LISTING_MODE_SITE) {
  const text = String(value == null ? '' : value).replace(/\s+/g, '').toLowerCase();
  return text || fallback;
}

function isCnscPageMode(value) {
  const mode = normalizeListingMode(value);
  return mode === LISTING_MODE_CNSC_GLOBAL || mode === LISTING_MODE_CNSC_3PF;
}

function isLocalLikeMode(value) {
  const mode = normalizeListingMode(value);
  return mode === LISTING_MODE_SITE || mode === LISTING_MODE_CNSC_3PF;
}

function isPureCnscMode(value) {
  return normalizeListingMode(value) === LISTING_MODE_CNSC_GLOBAL;
}

module.exports = {
  LISTING_MODE_SITE,
  LISTING_MODE_CNSC_GLOBAL,
  LISTING_MODE_CNSC_3PF,
  ALLOWED_LISTING_MODES,
  normalizeListingMode,
  isCnscPageMode,
  isLocalLikeMode,
  isPureCnscMode,
};
