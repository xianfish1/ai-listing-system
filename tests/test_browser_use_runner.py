from __future__ import annotations

import ast
import base64
import json
import os
import re
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import browser_use_runner


def _generated_page_import_function(name: str, namespace: dict):
    source = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, "<generated-mabang-page-import>", "exec"), namespace)
    return namespace[name]


def _run_mabang_capture(config: dict) -> dict:
    source_path = browser_use_runner.MABANG_PAGE_IMPORT_CAPTURE_PATH
    node_script = r"""
const fs = require('node:fs');
const config = JSON.parse(process.argv[2]);
const capturePage = (0, eval)(`(${fs.readFileSync(process.argv[1], 'utf8')})`);
const selectedCategory = {
  category_name: 'T恤',
  category_name_cn: 'T-Shirts',
  categoryId: 100244,
  category_id_path: '100011,100054,100244',
  name_zh_path: '男装,上衣,T恤',
  name_en_path: 'Men Clothes,Tops,T-Shirts',
  ...(config.selectedCategory || {}),
};
const storeCategory = {
  ProductAttributeInfo: {
    attributesNew: [{ attribute_id: 100134, name_cn: '材料', mandatory: 1, value: [1149], attribute_value_list: [{ value_id: 1149, name_cn: '棉花' }] }],
  },
  categoryNameArr: [selectedCategory],
  shopList: [{ id: 2022625115, name: 'POD菲律宾' }],
  ...(config.storeCategory || {}),
};
const transactionRef = {
  transactionInformation: config.transactionInformation ?? { stock: 300 },
  formData: config.formData,
  promotionOptions: config.promotionOptions,
  activityOptions: config.activityOptions,
  $refs: {
    tradeInfoTableRef: { tableData: config.tradeInfoTableData || [] },
  },
  changeDiscountInfo: config.changeDiscountInfo,
};
const detailVm = {
  $refs: { storeCategory, transactionInformationModuleRef: transactionRef },
  $store: { state: { shopee: { storeCategoryModule: {
    shopId: 2022625115,
    categoryId: config.moduleCategoryId ?? 100244,
    categoryIdAll: [100011, 100054, 100244],
    categoryNameAll: ['男装', '上衣', 'T恤'],
    shopList: [{ id: 2022625115, name: 'POD菲律宾' }],
  },
  logisticsInformationModule: config.logisticsInformationModule,
  variantAttributeModule: config.variantAttributeModule,
  productMaterialModule: config.productMaterialModule,
} } },
};
global.location = { origin: 'https://publish.mabangerp.com', pathname: '/publish-ui/' };
global.document = {
  querySelector: (selector) => selector === '#box1' ? { __vue__: detailVm } : null,
  querySelectorAll: (selector) => {
    if (selector === '.vxe-table') return [{ __vue__: { tableData: [{ color: 'black', size: 'S', stock: 300 }] } }];
    if (selector === '.el-form-item,.form-item' && config.selectVms) return [{
      textContent: '\u5c3a\u7801\u56fe\u8868',
      querySelectorAll: (childSelector) => childSelector === '.el-select'
        ? config.selectVms.map((vm) => ({ __vue__: vm })) : [],
    }];
    if (selector.includes('.el-dialog')) return config.dialogVms || [];
    return [];
  },
};
const menuNode = (item) => {
  const text = item.text ?? `${item.name} \u5173\u95ed\u6c34\u5370`;
  const node = {
    textContent: text,
    getAttribute: (attribute) => {
      const map = {
        'data-watermark-id': item.template_id,
        'data-watermark-name': item.name,
        title: item.name,
      };
      return map[attribute] ?? null;
    },
    querySelector: (childSelector) => {
      if (childSelector === 'img') {
        return item.preview_url ? { src: item.preview_url } : null;
      }
      if (childSelector === '[data-watermark-name],.watermark-name,.template-name') return null;
      return null;
    },
    querySelectorAll: () => [],
  };
  return node;
};
if (config.watermarkMenu) {
  const menu = {
    textContent: '\u6dfb\u52a0\u6c34\u5370 ' + (config.watermarkMenu.options || []).map((item) => item.name).join(' '),
    querySelectorAll: (selector) => {
      if (selector === '[data-watermark-id],[data-template-id],[data-templateid],[data-watermarkid],.watermark-template,.watermark-item,.watermark-list-item') {
        return (config.watermarkMenu.options || []).map(menuNode);
      }
      return [];
    },
  };
  const priorQuerySelectorAll = global.document.querySelectorAll.bind(global.document);
  global.document.querySelectorAll = (selector) => {
    if (selector === '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]' && config.watermarkMenu.visible !== false) return [menu];
    return priorQuerySelectorAll(selector);
  };
}
process.stdout.write(JSON.stringify(capturePage({ platform: 'shopee', country: 'PH' })));
"""
    result = subprocess.run(
        ["node", "-e", node_script, str(source_path), json.dumps(config, ensure_ascii=False)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _ready_payload() -> dict:
    return {
        "ok": True,
        "job": {
            "id": "job-1",
            "listing_copy_id": "copy-1",
            "status": "ready",
            "store_name": "Shopee-MY-A",
        },
        "listing_copy": {
            "id": "copy-1",
            "listing_title": "DL0001 cotton t-shirt",
            "platform": "shopee",
            "country": "MY",
            "store_name": "Shopee-MY-A",
        },
        "skus": [{"sku_code": "DL0001-BLACK-S"}, {"sku_code": "DL0001-WHITE-S"}],
        "prices": [{"sku_code": "DL0001-BLACK-S"}, {"sku_code": "DL0001-WHITE-S"}],
        "assets": [],
        "readiness": {"ready": True, "missing_fields": []},
        "browser_inputs": {
            "mabang_store_id": "store-123",
            "mabang_category_id": "cat-456",
            "mabang_discount_id": "discount-789",
            "discount_label": "40%",
            "main_image": r"\\NAS\DL0001\main.jpg",
            "variant_image_paths": {},
            "variation": {
                "type": "variant",
                "axes": ["color", "size"],
                "colors": ["black", "white"],
                "sizes": ["S"],
            },
            "sku_count": 2,
        },
    }


def test_dry_run_summary_blocks_jobs_missing_mabang_ids() -> None:
    summary = browser_use_runner.build_dry_run_summary(
        {
            **_ready_payload(),
            "readiness": {
                "ready": False,
                "missing_fields": ["mabang_store_id", "mabang_category_id"],
            },
        }
    )

    assert summary["ok"] is False
    assert summary["status"] == "blocked"
    assert summary["missing_fields"] == ["mabang_store_id", "mabang_category_id"]
    assert summary["next_step"] == "complete_store_template_ids"


def test_dry_run_summary_for_ready_job_never_marks_publish() -> None:
    summary = browser_use_runner.build_dry_run_summary(_ready_payload())

    assert summary["ok"] is True
    assert summary["status"] == "ready"
    assert summary["mode"] == "dry_run"
    assert summary["job_id"] == "job-1"
    assert summary["store_name"] == "Shopee-MY-A"
    assert summary["mabang_store_id"] == "store-123"
    assert summary["sku_count"] == 2
    assert summary["price_count"] == 2
    assert "publish" not in " ".join(summary["planned_actions"]).lower()


def test_variant_thumbnail_uploads_are_copied_to_ascii_temp_files(tmp_path: Path) -> None:
    source = tmp_path / "A黑色.jpg"
    source.write_bytes(b"black image")

    uploads = browser_use_runner.prepare_variant_thumbnail_uploads(
        {"black": str(source)},
        tmp_path / "uploads",
    )

    assert uploads["black"]["source_path"] == str(source)
    assert Path(uploads["black"]["upload_path"]).name == "black.jpg"
    assert Path(uploads["black"]["upload_path"]).read_bytes() == b"black image"
    assert Path(uploads["black"]["upload_path"]).name.isascii()


def test_copy_with_retry_recovers_from_transient_nas_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "src.jpg"
    source.write_bytes(b"payload")
    target = tmp_path / "dst.jpg"

    calls = {"n": 0}
    real_copy2 = browser_use_runner.shutil.copy2

    def flaky_copy2(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "拒绝访问")
        return real_copy2(src, dst)

    monkeypatch.setattr(browser_use_runner.shutil, "copy2", flaky_copy2)
    monkeypatch.setattr(browser_use_runner.time, "sleep", lambda *_: None)

    browser_use_runner._copy_with_retry(source, target)

    assert calls["n"] == 3
    assert target.read_bytes() == b"payload"


def test_copy_with_retry_raises_after_exhausting_attempts(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "src.jpg"
    source.write_bytes(b"payload")
    target = tmp_path / "dst.jpg"

    def always_fail(src, dst):
        raise PermissionError(5, "拒绝访问")

    monkeypatch.setattr(browser_use_runner.shutil, "copy2", always_fail)
    monkeypatch.setattr(browser_use_runner.time, "sleep", lambda *_: None)

    with pytest.raises(OSError):
        browser_use_runner._copy_with_retry(source, target, attempts=3)


def test_browser_task_includes_video_and_two_watermark_roles(tmp_path: Path) -> None:
    video = tmp_path / "商品视频.mp4"
    video.write_bytes(b"video")
    images = [tmp_path / name for name in ["one.jpg", "two.jpg", "three.jpg"]]
    for image in images:
        image.write_bytes(b"image")
    payload = deepcopy(_ready_payload())
    payload["browser_inputs"].update({
        "product_image_paths": [str(image) for image in images],
        "video": {
            "path": str(video), "file_name": "商品视频.mp4", "validation_status": "valid",
            "validation_errors": [], "duration_seconds": 30, "width": 720, "height": 720,
        },
        "main_image_watermark": {"template_id": "wm-main", "name": "主图水印"},
        "product_image_watermark": {"template_id": "wm-product", "name": "产品图水印"},
    })

    plan = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert plan["video"]["validation_status"] == "valid"
    assert Path(plan["video"]["upload_path"]).name == "product.mp4"
    assert plan["watermarks"]["main_image"]["image_positions"] == [1]
    assert plan["watermarks"]["product_images"]["image_positions"] == [2, 3]


def test_size_chart_last_product_image_reaches_task_and_keeps_watermark_slots(tmp_path: Path) -> None:
    """本土「产品图最后一张放尺码表」：form 列表进任务，水印槽位仍按真实产品图算。

    产品图区末位那张尺码表不该被产品图水印盖到（默认槽位 2..N 只覆盖真实产品图），
    所以 watermarks.product_images.image_positions 要按 form 里真实产品图的张数算。
    """

    files = [tmp_path / name for name in ["one.jpg", "two.jpg", "three.jpg", "chart.png"]]
    for path in files:
        path.write_bytes(b"image")
    payload = deepcopy(_ready_payload())
    payload["browser_inputs"].update({
        "product_image_paths": [str(path) for path in files[:3]],
        "product_image_form_paths": [str(path) for path in files],
        "size_chart_last_product_image": True,
        "product_image_watermark": {"template_id": "wm-product", "name": "产品图水印"},
    })

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert [Path(item).name for item in task["images"]["product_image_form_paths"]] == [
        "one.jpg", "two.jpg", "three.jpg", "chart.png",
    ]
    assert [Path(item).name for item in task["images"]["product_image_paths"]] == ["one.jpg", "two.jpg", "three.jpg"]
    assert task["images"]["size_chart_last_product_image"] is True
    assert task["watermarks"]["product_images"]["image_positions"] == [2, 3], "尺码表那张不进水印槽位"

    # 开关关掉（副本上手动关 / 跨境）：三个槽位照旧，任务里也没有尺码表那一份。
    payload["browser_inputs"]["size_chart_last_product_image"] = False
    payload["browser_inputs"]["product_image_form_paths"] = [str(path) for path in files[:3]]
    off_task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)
    assert off_task["images"]["size_chart_last_product_image"] is False
    assert [Path(item).name for item in off_task["images"]["product_image_form_paths"]] == ["one.jpg", "two.jpg", "three.jpg"]
    assert off_task["watermarks"]["product_images"]["image_positions"] == [2, 3]


def test_invalid_video_is_skipped_without_blocking_the_task(tmp_path: Path) -> None:
    payload = deepcopy(_ready_payload())
    payload["browser_inputs"]["video"] = {
        "validation_status": "invalid", "validation_errors": ["video_duration_too_short"]
    }

    plan = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert plan["ok"] is True
    assert plan["video"]["action_status"] == "skipped"
    assert plan["video"]["validation_errors"] == ["video_duration_too_short"]


def test_browser_use_task_plan_includes_save_draft_but_forbids_publish(tmp_path: Path) -> None:
    source = tmp_path / "A黑色.jpg"
    source.write_bytes(b"black image")
    payload = _ready_payload()
    payload["browser_inputs"]["variant_image_paths"] = {"black": str(source)}

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert task["mode"] == "save_draft"
    assert task["safety"]["allow_submit"] is False
    assert task["safety"]["allow_save"] is True
    assert task["safety"]["allow_publish"] is False
    assert "保存" not in task["safety"]["forbidden_button_texts"]
    assert "保存并刊登" in task["safety"]["forbidden_button_texts"]
    assert "发布" in task["safety"]["forbidden_button_texts"]
    assert task["store"]["mabang_store_id"] == "store-123"
    assert task["category"]["mabang_category_id"] == "cat-456"
    assert task["discount"]["mabang_discount_id"] == "discount-789"
    assert task["discount"]["label"] == "40%"
    assert task["variant_thumbnail_uploads"]["black"]["upload_path"].endswith("black.jpg")
    assert task["sku_grid"]["expected_rows"] == 2
    assert task["actions"][-1]["type"] == "save_draft"
    assert not any(action["type"] in {"submit", "publish"} for action in task["actions"])


def test_description_media_action_is_site_only_and_follows_image_upload(tmp_path: Path) -> None:
    """本土在商品图上传之后才往描述末尾插图（引用产品图弹窗列的是已上传的图）。"""
    site_task = browser_use_runner.build_browser_use_task_plan(_ready_payload(), tmp_path / "uploads", allow_save=True)
    types = [action["type"] for action in site_task["actions"]]
    assert "append_description_media" in types
    assert types.index("append_description_media") == types.index("upload_product_images") + 1
    label = next(action["label"] for action in site_task["actions"] if action["type"] == "append_description_media")
    assert "描述" in label and "尺码表" in label

    cnsc_payload = _ready_payload()
    cnsc_payload["browser_inputs"]["listing_mode"] = "cnsc_global"
    cnsc_task = browser_use_runner.build_browser_use_task_plan(cnsc_payload, tmp_path / "uploads", allow_save=True)
    assert "append_description_media" not in [action["type"] for action in cnsc_task["actions"]]


def test_task_plan_uses_the_grid_order_from_browser_inputs(tmp_path: Path) -> None:
    """SKU 网格顺序必须来自 browser_inputs，而不是快照顶层那份。

    顶层 skus 是 color_code 排序的旧格式（默认 SKU 行夹在中间），而网格按颜色轴生成
    （默认 SKU 在末尾）。执行器按行号逐行填写，两者不一致会从默认 SKU 那一行起整块
    错行：SKU 与单尺码覆盖价都会落到别的颜色行上。
    """

    payload = deepcopy(_ready_payload())
    payload["skus"] = [
        {"sku_code": "DL0001-BLACK-S"},
        {"sku_code": "AL9999-PureWhite-S"},
        {"sku_code": "DL0001-WHITE-S"},
    ]
    payload["prices"] = [
        {"sku_code": "DL0001-BLACK-S", "sale_price": 10},
        {"sku_code": "DL0001-WHITE-S", "sale_price": 20},
        {"sku_code": "AL9999-PureWhite-S", "sale_price": 30},
    ]
    payload["browser_inputs"]["skus"] = [
        {"sku_code": "DL0001-BLACK-S"},
        {"sku_code": "DL0001-WHITE-S"},
        {"sku_code": "AL9999-PureWhite-S"},
    ]
    payload["browser_inputs"]["prices"] = payload["prices"]

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert [sku["sku_code"] for sku in task["sku_grid"]["skus"]] == [
        "DL0001-BLACK-S",
        "DL0001-WHITE-S",
        "AL9999-PureWhite-S",
    ]
    assert [price["sku_code"] for price in task["sku_grid"]["prices"]] == [
        "DL0001-BLACK-S",
        "DL0001-WHITE-S",
        "AL9999-PureWhite-S",
    ]


def test_browser_use_task_plan_limits_shopee_title_at_a_word_boundary(tmp_path: Path) -> None:
    payload = _ready_payload()
    source_title = " ".join(["AnimeGraphicCottonTee"] * 8)
    payload["browser_inputs"]["title"] = source_title

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    title = task["basic_info"]["title"]
    assert len(title) <= 100
    assert source_title.startswith(title)
    assert title.endswith("AnimeGraphicCottonTee")


def test_browser_task_plan_blocks_explicit_missing_upload_file(tmp_path: Path) -> None:
    payload = _ready_payload()
    payload["browser_inputs"]["size_chart_image_path"] = str(tmp_path / "missing-size-chart.png")

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert task["ok"] is False
    assert task["status"] == "blocked"
    assert task["error"] == "Automation upload files are missing"
    assert task["missing_files"] == ["missing-size-chart.png"]
    assert task["safety"]["allow_publish"] is False


def test_browser_task_plan_keeps_mabang_size_chart_template_without_upload(tmp_path: Path) -> None:
    payload = _ready_payload()
    payload["browser_inputs"]["size_chart"] = {
        "source": "template",
        "template_id": "106239228",
        "template_name": "男装T恤",
        "image_path": "",
    }

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert task["ok"] is True
    assert task["images"]["size_chart"] == payload["browser_inputs"]["size_chart"]


def test_browser_task_plan_excludes_category_attribute_display_metadata(tmp_path: Path) -> None:
    payload = _ready_payload()
    payload["browser_inputs"]["category_attributes"] = {
        "brand": "NoBrand",
        "material": "Cotton",
        "display": {"brand": "NoBrand", "material": "Cotton"},
    }

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert task["category_attributes"] == {"brand": "NoBrand", "material": "Cotton"}


def test_resolve_category_attribute_fields_marks_free_text_values_as_text_input() -> None:
    # Free-text attributes (尺寸 100-150cm, 条件 Brand New) carry no enum options:
    # the automation plan must mark them input_kind=text so the executor types the
    # value instead of opening a nonexistent dropdown. Enum-backed selects keep
    # input_kind=select.
    resolved = browser_use_runner.resolve_category_attribute_fields(
        {
            "102530": "100-150cm",
            "100413": "Brand New",
            "100174": "Round Neck",
            "100168": "1500",
        },
        {
            "fields": [
                {"field_id": "102530", "label": "尺寸", "type": "text", "options": []},
                {"field_id": "100413", "label": "条件", "type": "text", "options": []},
                {"field_id": "100174", "label": "衣领类型", "type": "single_select",
                 "options": [{"value": "1574", "label": "普通话衣领 (Mandarin Collar)"}]},
                {"field_id": "100168", "label": "套筒长度", "type": "single_select",
                 "options": [{"value": "1500", "label": "短袖 (Short Sleeves)"}]},
            ]
        },
    )
    by_id = {item["field_id"]: item for item in resolved}
    assert by_id["102530"]["input_kind"] == "text"
    assert by_id["102530"]["value_ids"] == ["100-150cm"]
    assert by_id["100413"]["input_kind"] == "text"
    # 衣领类型的值 Round Neck 不在枚举选项中 → select_custom：执行器按控件类型决定
    # （可编辑输入框就打字；readonly 下拉就走马帮的「添加自填选项」）。
    assert by_id["100174"]["input_kind"] == "select_custom"
    assert by_id["100168"]["input_kind"] == "select"


def test_save_draft_script_allows_exact_save_once_and_keeps_publish_guard(tmp_path: Path) -> None:
    task = browser_use_runner.build_browser_use_task_plan(_ready_payload(), tmp_path / "uploads", allow_save=True)

    script = browser_use_runner.build_browser_use_safe_fill_script(task)

    assert "click_save_draft_once" in script
    assert "verify_save_draft_result" in script
    assert "install_publish_guard" in script
    assert "SAVE_BUTTON_TEXT" in script
    assert "PUBLISH_BUTTON_TEXTS" in script
    assert "saveToken" in script
    encoded = re.search(r'b64decode\("([^"]+)"\)', script)
    assert encoded
    decoded_task = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert decoded_task["safety"]["allow_save"] is True
    assert decoded_task["safety"]["allow_publish"] is False


def test_playwright_executor_covers_complete_iframe_save_draft_flow() -> None:
    source = browser_use_runner.PLAYWRIGHT_EXECUTOR_PATH.read_text(encoding="utf-8")

    assert "connectOverCDP" in source
    assert "#iframeContent" in source
    assert "installPublishGuard" in source
    assert "openListingWorkspace" in source
    assert "selectStore" in source
    assert "selectCategory" in source
    assert "setCategoryAttributes" in source
    assert "fillBasicInfo" in source
    assert "uploadProductImages" in source
    assert "configureVariants" in source
    assert "fillSkuGrid" in source
    assert "selectDiscountActivity" in source
    assert "uploadVariantThumbnails" in source
    assert "validateBeforeSave" in source
    assert "clickSaveDraftOnce" in source
    assert "verifySavedDraft" in source
    assert "closeSavedDetailPage" in source
    assert "TEXT.closeThisPage" in source
    assert "TEXT.listingDraftBox" in source
    assert "visibleSuccessMessage" in source
    assert "stableClosureMatches" in source
    assert "exactVisibleLocator(dialog, ACTION_SELECTOR, TEXT.closeThisPage)" in source
    assert "page_closed: true" in source
    assert "close_method" in source
    assert "MABANG_PLAYWRIGHT_RESULT" in source
    assert "保存并刊登" not in source
    assert "browser.close()" not in source
    assert "productInputIndex" not in source
    assert "process.exit(" in source
    assert "process.exitCode" not in source
    assert "blockedLabels.some((label) => text.includes(label))" not in source
    assert "normalized === label" in source
    assert "normalized.startsWith(saveAndPrefix)" in source
    assert "__AI_LISTING_PUBLISH_GUARD_V2_INSTALLED__" in source
    assert "legacyLabels.splice" in source
    assert "function compactText" in source
    assert "compactText(await candidate.innerText" in source
    assert "normalize(element.innerText || element.textContent || element.value) === normalize(saveText)" in source
    assert "String(category.path || '').split('>')" in source
    assert "label.el-transfer-panel-item" in source
    assert "category_id_not_verified" in source
    assert "category_segment_timeout" in source
    assert "toLocaleLowerCase()" in source
    assert "categoryIdAll" in source
    assert "values.at(-1)" in source
    assert "method: 'already_selected'" in source
    assert "const selectedValue = selectedTexts.join(', ')" in source
    assert "targets.some((value) => selectedTexts.some" in source
    assert "const existingDetail = await detailFrameFromPage(candidate)" in source
    assert "waitForFormItemForLabels" in source
    assert "select_value_not_verified" in source
    assert "const optionDeadline = Date.now() + 5000" in source
    assert "stableMatches >= 5" in source
    assert "categorySettings.categoryIdAll" in source
    assert "method: 'already_selected_id'" in source
    assert "selectedTagTexts" in source
    assert ".el-input__suffix" in source
    assert 'input[type="file"][multiple]' in source
    assert ".upload-icon.el-popover__reference" in source
    assert "aria-describedby" in source
    assert ".natureBlock" in source
    assert "ensureVariantAxis" in source
    assert "waitForSkuRows" in source
    assert "current.includes(String(color))" not in source
    assert "current.includes(String(size))" not in source
    assert "size_chart_already_uploaded" in source
    assert "naturalWidth" in source
    assert ".tradeInfoTableRef-table.vxe-table" in source
    assert "String(row.discount_id || '')" in source
    assert "discount_name" in source
    assert "discount_activity_selected_by_id" in source
    assert ".custom-img .img-center" in source
    assert "variant_thumbnail_already_uploaded" in source
    assert "publish_guard_triggered_after_save" in source
    assert "if (evidence.publish_clicked)" in source
    # 保存证据里的网络那条来自 watchSaveResponses 的活数组：监听必须活到保存校验
    # 结束（verifySavedDraft 的 finally 里 stopWatchingSave），点完就摘会漏掉慢响应
    # → 只剩一条证据 → save_result_not_proven（现场 HN0012，草稿其实已经建好）。
    assert "watchSaveResponses" in source
    assert "stopWatchingSave" in source


def test_playwright_executor_can_select_a_mabang_size_chart_template_by_id() -> None:
    source = browser_use_runner.PLAYWRIGHT_EXECUTOR_PATH.read_text(encoding="utf-8")

    assert "selectSizeChartTemplate" in source
    assert "size_chart_template_unresolved" in source
    # 尺码图表表单项里第一个 input 是「上传图片 / 选择模板」的模式单选（radio 在 DOM 里
    # 排在下拉前面），回读和触发器都必须走 valueInputForItem 跳过它，否则模板在页面上
    # 选好了也会被判成 select_value_not_verified。
    assert "async function valueInputForItem(item)" in source
    assert 'input:not([type="radio"]):not([type="checkbox"]):not([type="file"])' in source
    assert "const input = await valueInputForItem(item);" in source
    assert "const trigger = await valueInputForItem(item);" in source


def test_playwright_executor_close_behavior_node_suite() -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    module_dir = project_root / "node_modules" / ".pnpm" / "node_modules"
    env["NODE_PATH"] = os.pathsep.join(filter(None, (str(module_dir), env.get("NODE_PATH", ""))))

    result = subprocess.run(
        ["node", "--test", str(project_root / "tests" / "mabang_playwright_executor.test.cjs")],
        cwd=project_root,
        capture_output=True,
        input="",
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_run_playwright_save_draft_passes_plan_over_stdin(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        output = {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
        }
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        cdp_url="http://127.0.0.1:9223",
        timeout_seconds=19,
    )

    assert result["ok"] is True
    assert result["status"] == "saved"
    assert result["save_clicked"] is True
    assert result["publish_clicked"] is False
    assert result["page_closed"] is True
    assert result["close_method"] == "success_dialog"
    assert calls["args"][0] == [
        "node.exe",
        str(browser_use_runner.PLAYWRIGHT_EXECUTOR_PATH),
        "--cdp-url",
        "http://127.0.0.1:9223",
    ]
    assert json.loads(calls["input"])["safety"]["allow_save"] is True
    assert calls["timeout"] == 19
    assert "NODE_PATH" in calls["env"]
    assert ".pnpm" in calls["env"]["NODE_PATH"] or calls["env"]["NODE_PATH"]
    assert calls.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", None)


def test_resolve_node_executable_hides_the_windows_console(monkeypatch) -> None:
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="v22.0.0\n", stderr="")

    monkeypatch.setattr(browser_use_runner.shutil, "which", lambda name: "C:\\Program Files\\nodejs\\node.exe")
    monkeypatch.setattr(browser_use_runner, "_NODE_PROBE_CACHE", {})
    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    node_path, version = browser_use_runner._resolve_node_executable("node", {})

    assert node_path == "C:\\Program Files\\nodejs\\node.exe"
    assert version == "v22.0.0"
    assert calls.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", None)


def test_resolve_node_executable_falls_back_to_the_standard_install_dir(monkeypatch) -> None:
    """node 不在 PATH 上时（精简环境拉起的 API）仍要能跑起来。

    2026-10-12：从一个只剩两条 PATH 条目的 shell 重启 API，`shutil.which("node")`
    返回 None，自动登录子进程 `[WinError 2]` 起不来，启动 Worker 只报
    「马帮登录未确认：failed」。
    """

    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="v22.0.0\n", stderr="")

    monkeypatch.setattr(browser_use_runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        browser_use_runner,
        "_node_fallback_candidates",
        lambda: ["C:\\Program Files\\nodejs\\node.exe"],
    )
    monkeypatch.setattr(browser_use_runner, "_NODE_PROBE_CACHE", {})
    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    node_path, version = browser_use_runner._resolve_node_executable("node", {})

    assert node_path == "C:\\Program Files\\nodejs\\node.exe"
    assert version == "v22.0.0"
    assert calls["args"][0][0] == "C:\\Program Files\\nodejs\\node.exe"


def test_resolve_node_executable_keeps_the_bare_name_when_nothing_is_found(monkeypatch) -> None:
    """PATH 和标准安装位置都没有 → 保留字面量 node，报错仍是「无法启动 Node」。"""

    monkeypatch.setattr(browser_use_runner.shutil, "which", lambda name: None)
    monkeypatch.setattr(browser_use_runner, "_node_fallback_candidates", lambda: [])
    monkeypatch.setattr(browser_use_runner, "_NODE_PROBE_CACHE", {})
    monkeypatch.setattr(
        browser_use_runner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("node")),
    )

    assert browser_use_runner._resolve_node_executable("node", {}) == ("node", None)


def test_node_fallback_candidates_only_returns_existing_files(monkeypatch, tmp_path: Path) -> None:
    """兜底只在文件真的存在时生效，避免把不存在的路径当命令发出去。"""

    existing = tmp_path / "nodejs"
    existing.mkdir()
    binary = existing / "node.exe"
    binary.write_bytes(b"stub")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert browser_use_runner._node_fallback_candidates() == [str(binary)]


def test_run_playwright_save_draft_passes_harness_resume_flags(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(), tmp_path / "uploads", allow_save=True,
    )
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(
            args[0], 0,
            stdout='MABANG_PLAYWRIGHT_RESULT {"ok":false,"status":"blocked","error":"resume_test","publish_clicked":false}',
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)
    browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        preserve_detail_on_failure=True,
        resume_from_stage="select_category",
    )
    assert "--preserve-detail-on-failure" in calls["args"][0]
    assert calls["args"][0][-2:] == ["--resume-from-stage", "select_category"]


def test_run_playwright_save_draft_timeout_closes_leftover_detail_pages(tmp_path: Path, monkeypatch) -> None:
    """超时被杀的执行器不会再跑 close_saved_detail_page：runner 必须补一次收尾并如实回报。

    现场 2026-09-27 22:21（任务 45fff5a5）：保存已经成功（页面上有「刊登信息保存成功」弹窗），
    但 executor 预算 480s 到点、node 被 taskkill，于是浏览器里留着详情页与成功弹窗。
    """
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )
    calls: dict[str, list[str]] = {}

    def fake_run(*args, **kwargs):
        command = args[0]
        if "--close-detail-pages" in command:
            calls["cleanup"] = command
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "MABANG_PLAYWRIGHT_RESULT "
                    '{"ok":true,"status":"closed_detail_pages","closed":1,"saved_dialog_pages":1,"kept":[]}\n'
                ),
                stderr="",
            )
        calls["executor"] = command
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        cdp_url="http://127.0.0.1:9223",
        timeout_seconds=480,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Playwright save-draft executor timed out"
    assert result["timeout_seconds"] == 480
    assert result["save_clicked"] is False
    # 收尾补跑过，并且把"这些页面里有一页带着保存成功弹窗"如实回报出来。
    assert "--close-detail-pages" in calls["cleanup"]
    assert result["page_closed"] is True
    assert result["leftover_detail_pages"]["closed"] == 1
    assert result["leftover_detail_pages"]["saved_dialog_pages"] == 1


def test_run_playwright_save_draft_surfaces_crash_exit_code_hint(tmp_path: Path, monkeypatch) -> None:
    """A node crash with no result marker must surface the NTSTATUS hint + evidence."""
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(
            args[0],
            3221225794,  # 0xC0000142 STATUS_DLL_INIT_FAILED
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        cdp_url="http://127.0.0.1:9223",
        timeout_seconds=19,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["exit_code"] == 3221225794
    assert "0xC0000142" in result["error"]
    assert "STATUS_DLL_INIT_FAILED" in result["error"]
    assert result["raw_output_tail"] == ""
    # The resolved command must still use the explicit node the caller pinned.
    assert calls["args"][0][0] == "node.exe"


def test_run_playwright_save_draft_detects_broken_default_node(tmp_path: Path, monkeypatch) -> None:
    """A default ``node`` that cannot even run --version is reported clearly."""
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )
    broken = tmp_path / "node.exe"
    broken.write_text("not a real exe", encoding="utf-8")
    monkeypatch.setattr(browser_use_runner.shutil, "which", lambda name: str(broken))
    monkeypatch.setattr(browser_use_runner, "_NODE_PROBE_CACHE", {})

    result = browser_use_runner.run_playwright_save_draft(
        task,
        cdp_url="http://127.0.0.1:9223",
        timeout_seconds=19,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "Node 无法启动" in result["error"]
    assert result["node_path"] == str(broken)


def test_run_mabang_page_import_uses_browser_use_session(tmp_path: Path, monkeypatch) -> None:
    calls = {}
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='MABANG_PAGE_IMPORT_RESULT {"source":"mabang_current_page"}',
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        browser_use_runner.storage,
        "get_runtime_configuration",
        lambda: {"worker": {"cdp_url": "http://127.0.0.1:9223"}},
    )

    result = browser_use_runner.run_mabang_page_import_audit(
        {"platform": "shopee", "country": "PH"},
        browser_use_exe=fake_exe,
        timeout_seconds=19,
    )

    assert result == {"ok": True, "status": "completed", "capture": {"source": "mabang_current_page"}}
    assert calls["args"][0] == [str(fake_exe)]
    assert "Page.getFrameTree" in calls["input"]
    assert "Runtime.evaluate" in calls["input"]
    assert "mabang_page_import_capture.js" in calls["input"]
    assert calls["timeout"] == 19
    assert calls["env"]["BU_CDP_URL"] == "http://127.0.0.1:9223"


def test_run_mabang_page_import_surfaces_executor_error(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")

    def fake_run(*args, **kwargs):
        output = {"ok": False, "status": "failed", "error": "未找到当前马帮商品编辑页"}
        return subprocess.CompletedProcess(
            args[0],
            1,
            stdout="MABANG_PAGE_IMPORT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_mabang_page_import_audit(
        {"platform": "shopee", "country": "PH"},
        browser_use_exe=fake_exe,
    )

    assert result == {"ok": False, "status": "failed", "error": "未找到当前马帮商品编辑页"}


def test_run_mabang_page_import_does_not_expose_browser_use_traceback(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        browser_use_runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr="Traceback (most recent call last):\nPermissionError: C:\\Users\\secret\\bu-default.pid",
        ),
    )

    result = browser_use_runner.run_mabang_page_import_audit(
        {"platform": "shopee", "country": "PH"},
        browser_use_exe=fake_exe,
    )

    assert result == {
        "ok": False,
        "status": "failed",
        "error": "Browser-Use 启动失败，请关闭残留进程后重试",
    }


def test_mabang_capture_skips_empty_models_and_keeps_real_promotion_options() -> None:
    capture = _run_mabang_capture({
        "transactionInformation": {},
        "formData": {"stock": 450},
        "promotionOptions": [],
        "activityOptions": [{"activity_id": "promo-1", "name": "店铺六折活动"}],
    })

    assert capture["transaction_information"]["stock"] == 450
    assert capture["promotion_options"] == [{
        "activity_id": "promo-1",
        "name": "店铺六折活动",
        "discount_rate": "",
        "start_at": "",
        "end_at": "",
        "status": "",
        "selected": False,
    }]


def test_mabang_capture_reads_all_activities_loaded_by_the_discount_dialog() -> None:
    capture = _run_mabang_capture({
        "promotionOptions": [],
        "dialogVms": [{
            "textContent": "选择折扣活动 活动名称 状态 操作 选择 关闭",
            "__vue__": {
                "tableData": [{
                    "activityId": "promo-40",
                    "activityName": "40%",
                    "discountRate": "40%",
                    "startTime": "2026-05-28 17:30:00",
                    "endTime": "2026-11-23 23:59:00",
                    "status": "进行中",
                }],
            },
        }],
    })

    assert capture["promotion_options"] == [{
        "activity_id": "promo-40",
        "name": "40%",
        "discount_rate": "40%",
        "start_at": "2026-05-28 17:30:00",
        "end_at": "2026-11-23 23:59:00",
        "status": "进行中",
        "selected": False,
    }]


def test_mabang_capture_reads_mabang_discount_dialog_field_names() -> None:
    capture = _run_mabang_capture({
        "promotionOptions": [],
        "dialogVms": [{
            "textContent": "选择折扣活动 活动名称 状态 操作 选择 关闭",
            "__vue__": {
                "tableData": [{
                    "id": 8547175,
                    "discount_id": 928232107737088,
                    "discount_name": "40%",
                    "start_time": "2026-05-28 17:30:00",
                    "end_time": "2026-11-23 23:59:00",
                    "flag": 2,
                }],
            },
        }],
    })

    assert capture["promotion_options"] == [{
        "activity_id": "928232107737088",
        "name": "40%",
        "discount_rate": "",
        "start_at": "2026-05-28 17:30:00",
        "end_at": "2026-11-23 23:59:00",
        "status": "进行中",
        "selected": False,
    }]


def test_mabang_capture_reads_live_shopee_transaction_table_logistics_and_discount() -> None:
    capture = _run_mabang_capture({
        "transactionInformation": {},
        "tradeInfoTableData": [{
            "color": "black",
            "size": "S",
            "stock": 300,
            "price": "89",
            "discount_price": 69,
            "package_weight": "",
            "package_length": "",
            "package_width": "",
            "package_height": "",
        }],
        "logisticsInformationModule": {
            "weight": 0.3,
            "length": 25,
            "width": 20,
            "height": 2,
        },
        "variantAttributeModule": {
            "mdDiscountActivity": {
                "discount_id": 928232107737088,
                "discount_name": "40%",
            },
        },
        "changeDiscountInfo": {"discountId": 928232107737088},
    })

    assert capture["sku_rows"] == [{
        "color": "black",
        "size": "S",
        "stock": 300,
        "weight_kg": "",
        "package_length_cm": "",
        "package_width_cm": "",
        "package_height_cm": "",
        "normal_price": "89",
        "sale_price": 69,
    }]
    assert capture["transaction_information"] == {
        "stock": "",
        "weight_kg": 0.3,
        "package_length_cm": 25,
        "package_width_cm": 20,
        "package_height_cm": 2,
    }
    assert capture["promotion_options"] == [{
        "activity_id": "928232107737088",
        "name": "40%",
        "discount_rate": "",
        "start_at": "",
        "end_at": "",
        "status": "",
        "selected": True,
    }]


def test_mabang_capture_reads_the_live_size_chart_metadata() -> None:
    capture = _run_mabang_capture({
        "productMaterialModule": {
            "sizeChartUrl": {
                "url": "https://publish-cos.mabangerp.com/upload/publish/shopee/chart.png",
                "id": "chart-1",
                "width": 808,
                "height": 720,
            },
        },
    })

    assert capture["size_chart"] == {
        "url": "https://publish-cos.mabangerp.com/upload/publish/shopee/chart.png",
        "id": "chart-1",
        "width": 808,
        "height": 720,
    }


def test_mabang_capture_reads_the_loaded_size_chart_template_catalog() -> None:
    capture = _run_mabang_capture({
        "productMaterialModule": {
            "sizeChartTemplateList": [
                {"id": "chart-template-1", "name": "男装尺码表", "url": "https://publish-cos.mabangerp.com/chart-1.png"},
                {"id": "chart-template-2", "name": "童装尺码表", "url": "https://publish-cos.mabangerp.com/chart-2.png"},
            ],
        },
    })

    assert capture["size_chart_template_catalog_loaded"] is True
    assert capture["size_chart_templates"] == [
        {"template_id": "chart-template-1", "name": "男装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-1.png"},
        {"template_id": "chart-template-2", "name": "童装尺码表", "preview_url": "https://publish-cos.mabangerp.com/chart-2.png"},
    ]


def test_mabang_capture_reads_size_chart_templates_from_the_live_select() -> None:
    capture = _run_mabang_capture({
        "selectVms": [{
            "options": [{"value": 106239228, "label": "男装T恤"}],
        }],
    })

    assert capture["size_chart_template_catalog_loaded"] is True
    assert capture["size_chart_templates"] == [{
        "template_id": "106239228",
        "name": "男装T恤",
        "preview_url": "",
    }]


def test_mabang_capture_reads_watermark_menu_options_and_thumbnails() -> None:
    capture = _run_mabang_capture({
        "dialogVms": [{
            "__vue__": {
                "watermarkMenu": {
                    "options": [
                        {"watermarkId": "wm-main", "watermarkName": "主图角标", "imgUrl": "https://cdn.example/main.png"},
                        {"watermarkId": "wm-detail", "watermarkName": "产品图角标", "imgUrl": "https://cdn.example/detail.png"},
                    ],
                },
            },
        }],
    })

    assert capture["watermark_catalog_loaded"] is True
    assert capture["watermark_templates"] == [
        {"template_id": "wm-main", "name": "主图角标", "preview_url": "https://cdn.example/main.png"},
        {"template_id": "wm-detail", "name": "产品图角标", "preview_url": "https://cdn.example/detail.png"},
    ]


def test_mabang_capture_reads_a_loaded_complete_watermark_catalog_without_applying_it() -> None:
    source = browser_use_runner.MABANG_PAGE_IMPORT_CAPTURE_PATH.read_text(encoding="utf-8")

    assert "readWatermarkTemplates" in source
    assert "watermark_catalog_loaded" in source
    assert "watermark_templates" in source
    assert "template_id" in source
    assert "preview_url" in source
    assert "保存模板" not in source


def test_mabang_capture_merges_vue_list_with_the_visible_open_watermark_menu() -> None:
    # 采集器在 prepare 阶段已用 hover/click 展开「添加水印」；可见菜单里比 Vue
    # options 多一个「关闭水印」占位和一条新模板时，DOM 行与 Vue 列表合并去重。
    capture = _run_mabang_capture({
        "dialogVms": [{
            "__vue__": {
                "watermarkMenu": {
                    "options": [
                        {"watermarkId": "wm-main", "watermarkName": "主图角标", "imgUrl": "https://cdn.example/main.png"},
                    ],
                },
            },
        }],
        "watermarkMenu": {
            "visible": True,
            "options": [
                {"template_id": "wm-main", "name": "主图角标", "preview_url": "https://cdn.example/main.png"},
                {"template_id": "wm-new", "name": "新角标", "preview_url": "https://cdn.example/new.png"},
            ],
        },
    })

    assert capture["watermark_catalog_loaded"] is True
    assert capture["watermark_templates"] == [
        {"template_id": "wm-main", "name": "主图角标", "preview_url": "https://cdn.example/main.png"},
        {"template_id": "wm-new", "name": "新角标", "preview_url": "https://cdn.example/new.png"},
    ]


def test_mabang_capture_hidden_watermark_menu_never_reports_loaded() -> None:
    # 菜单未展开（隐藏）时，即使 DOM 残留旧行也不得当作已加载目录；capture 返回
    # loaded=false，后端据此保留店铺上一份有效目录。
    capture = _run_mabang_capture({
        "watermarkMenu": {
            "visible": False,
            "options": [
                {"template_id": "wm-stale", "name": "残留角标", "preview_url": "https://cdn.example/stale.png"},
            ],
        },
    })

    assert capture["watermark_catalog_loaded"] is False
    assert capture["watermark_templates"] == []


def test_mabang_capture_requires_consistent_leaf_category_evidence() -> None:
    assert _run_mabang_capture({})["store_category_state"]["category"]["is_leaf"] is True
    assert _run_mabang_capture({"selectedCategory": {"has_children": True}})["store_category_state"]["category"]["is_leaf"] is False
    assert _run_mabang_capture({"moduleCategoryId": 100054})["store_category_state"]["category"]["is_leaf"] is False


def test_mabang_executor_contract_has_raw_sources_only() -> None:
    source = browser_use_runner.MABANG_PAGE_IMPORT_CAPTURE_PATH.read_text(encoding="utf-8")

    assert "store_category_state" in source
    assert "category_attributes" in source
    assert "sku_rows" in source
    assert "transaction_information" in source
    assert "promotion_options" in source
    assert "size_chart_templates" in source
    assert "document.querySelectorAll('.el-form-item,.ant-form-item,.form-item,tr')" not in source
    assert "const firstSku = skuRows[0]" not in source
    for forbidden in [".click(", "fetch(", "XMLHttpRequest", "location.href =", "window.open("]:
        assert forbidden not in source


def test_page_import_runner_prepares_dynamic_menus_outside_the_read_only_capture() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})

    assert "prepare_mabang_page_import_catalogs" in script
    assert "restore_mabang_page_import_catalogs" in script
    assert "const isVisible" in script
    assert "filter((node) => isVisible(node))" in script
    assert "document.querySelector('.change-discount')" in script
    assert "waitUntil" in script
    assert "timeout = 6000" in script
    assert "loadingMasks.some((node) => isVisible(node))" in script
    assert r"\u6dfb\u52a0\u6c34\u5370" in script
    assert r"\u9009\u62e9\u6a21\u677f" in script
    assert r"\u6298\u6263\u6d3b\u52a8" in script
    assert r"\u9009\u62e9\u6298\u6263\u6d3b\u52a8" in script
    assert "promotionOpenedByCollector" in script
    assert "capture_source" in script


def test_page_import_runner_reads_cnsc_store_picker_and_discount_catalogs_then_cancels() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})

    assert "#shops-wap" in script
    assert "sitePickerOpenedByCollector" in script
    assert "target_site_stores_loaded" in script
    assert "discount_catalog_loaded" in script
    assert "__AI_LISTING_CNSC_IMPORT_STATE__" in script
    assert r"\u53d6\u6d88" in script
    assert "new Set(targetCodes).size === requiredManualSites.length" in script
    assert "store.discount_catalog.length > 0 && selected.length === 1" in script
    assert "headerText.includes(item.name)" in script


def test_page_import_runner_collects_all_eight_cnsc_discount_catalogs_in_separate_cdp_calls() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})
    sites = ["TW", "PH", "SG", "TH", "MY", "MX", "VN", "BR"]
    calls = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if "const requestedSite" in kwargs["expression"]:
            return {"result": {"value": {"status": "started"}}}
        if "window.__AI_LISTING_CNSC_IMPORT_STATE__ =" in kwargs["expression"]:
            return {"result": {"value": True}}
        site = next(code for code in sites if f'["{code}"]' in kwargs["expression"])
        return {
            "result": {
                "value": {
                    "status": "completed",
                    "value": {
                        "site_code": site,
                        "discount_catalog_loaded": True,
                        "discount_catalog": [{"mabang_discount_id": f"discount-{site.lower()}"}],
                    },
                }
            }
        }

    collect = _generated_page_import_function(
        "collect_mabang_cnsc_discount_catalogs",
        {"json": json, "time": time, "context_id": 42, "cdp": fake_cdp},
    )
    state = {"cnscPage": True, "target_site_stores": [{"site_code": code} for code in sites]}
    result = collect(state)

    assert "def collect_mabang_cnsc_discount_catalogs(state):" in script
    assert "for store in state.get(\"target_site_stores\") or []:" in script
    assert "prepared_catalogs = collect_mabang_cnsc_discount_catalogs(prepared_catalogs)" in script
    assert "collectDiscountCatalogs = discountTriggers.length <= 2" not in script
    start_calls = [call for call in calls if "const requestedSite" in call[1]["expression"]]
    assert len(start_calls) == 8
    assert all(method == "Runtime.evaluate" for method, _ in calls)
    assert all(kwargs["contextId"] == 42 and kwargs["awaitPromise"] is False for _, kwargs in calls)
    assert [item["site_code"] for item in result["target_site_stores"]] == sites
    assert all(item["discount_catalog_loaded"] is True for item in result["target_site_stores"])


def test_page_import_runner_deduplicates_cnsc_headers_and_matches_selected_activity_id() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})

    assert "seenActivityColumns" in script
    assert "currentDiscountId" in script
    assert "apply_selected_activity(previous_store, previous_selected_id)" in script
    assert "normalizeActivityLabel" in script
    assert "normalizeActivityLabel(item.name) === normalizedHeaderLabel" in script
    assert "previousStoreId" in script
    assert "const staleStoreId = previousStoreId || headerStoreId" in script
    assert "changedStoreIds.length === 1" in script
    assert "optionStoreId === acceptedStoreId" in script
    assert "unchangedFirstStore" in script
    assert "__AI_LISTING_CNSC_ACTIVITY_TASKS__" in script
    assert "collect().then" in script
    assert "Date.now() - lastTriggerAt >= 1500" in script


def test_page_import_runner_surfaces_cnsc_discount_javascript_exceptions() -> None:
    def fake_cdp(_method, **_kwargs):
        return {
            "exceptionDetails": {"text": "Uncaught"},
            "result": {"type": "object", "subtype": "error", "description": "ReferenceError: broken dialog"},
        }

    collect = _generated_page_import_function(
        "collect_mabang_cnsc_discount_catalogs",
        {"json": json, "time": time, "context_id": 42, "cdp": fake_cdp},
    )

    with pytest.raises(RuntimeError, match=r"TW.*ReferenceError: broken dialog"):
        collect({"cnscPage": True, "target_site_stores": [{"site_code": "TW"}]})


def test_page_import_runner_applies_preclick_activity_id_to_previous_cnsc_site() -> None:
    sites = ["TW", "PH"]

    def fake_cdp(_method, **kwargs):
        if "const requestedSite" in kwargs["expression"]:
            return {"result": {"value": {"status": "started"}}}
        if "window.__AI_LISTING_CNSC_IMPORT_STATE__ =" in kwargs["expression"]:
            return {"result": {"value": True}}
        site = next(code for code in sites if f'["{code}"]' in kwargs["expression"])
        values = {
            "TW": {
                "site_code": "TW", "discount_catalog_loaded": False,
                "discount_catalog": [
                    {"mabang_discount_id": "tw-selected", "name": "40%"},
                    {"mabang_discount_id": "tw-other", "name": "40%"},
                ],
                "_previous_activity_id": "ph-selected",
            },
            "PH": {
                "site_code": "PH", "discount_catalog_loaded": True,
                "discount_catalog": [{"mabang_discount_id": "ph-selected", "name": "40%"}],
                "mabang_discount_id": "ph-selected", "discount_name": "40%",
                "_previous_activity_id": "tw-selected",
            },
        }
        return {"result": {"value": {"status": "completed", "value": values[site]}}}

    collect = _generated_page_import_function(
        "collect_mabang_cnsc_discount_catalogs",
        {"json": json, "time": time, "context_id": 42, "cdp": fake_cdp},
    )
    result = collect({"cnscPage": True, "target_site_stores": [{"site_code": code} for code in sites]})

    tw, ph = result["target_site_stores"]
    assert tw["discount_catalog_loaded"] is True
    assert tw["mabang_discount_id"] == "tw-selected"
    assert tw["discount_name"] == "40%"
    assert ph["mabang_discount_id"] == "ph-selected"
    assert all("_previous_activity_id" not in item for item in (tw, ph))


def test_page_import_runner_keeps_first_site_when_dialog_already_matches_it() -> None:
    def fake_cdp(_method, **kwargs):
        if "const requestedSite" in kwargs["expression"]:
            return {"result": {"value": {"status": "started"}}}
        if "window.__AI_LISTING_CNSC_IMPORT_STATE__ =" in kwargs["expression"]:
            return {"result": {"value": True}}
        return {
            "result": {
                "value": {
                    "status": "completed",
                    "value": {
                        "site_code": "TW", "discount_catalog_loaded": False,
                        "discount_catalog": [
                            {"mabang_discount_id": "tw-selected", "name": "40%"},
                            {"mabang_discount_id": "tw-other", "name": "40%"},
                        ],
                        "_current_activity_id": "tw-selected",
                    },
                }
            }
        }

    collect = _generated_page_import_function(
        "collect_mabang_cnsc_discount_catalogs",
        {"json": json, "time": time, "context_id": 42, "cdp": fake_cdp},
    )
    result = collect({"cnscPage": True, "target_site_stores": [{"site_code": "TW"}]})

    store = result["target_site_stores"][0]
    assert store["discount_catalog_loaded"] is True
    assert store["mabang_discount_id"] == "tw-selected"
    assert "_current_activity_id" not in store


def test_page_import_runner_handles_checkbox_store_picker_without_waiting_for_radios() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})

    assert ".shops-checkbox" in script
    assert "feat-content" in script
    assert "visibleRadios" not in script


def test_page_import_runner_reads_selected_cnsc_shops_from_vue_checkbox_model() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": ""})

    assert "const shopVm = picker?.__vue__ || picker?.__vueParentComponent?.proxy" in script
    assert "filter((shop) => shop?.isChecked === true)" in script
    assert "shop?.id" in script
    assert "shop?.name" in script


def test_page_import_runner_restores_ui_when_preparation_fails() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})

    preparation = script[script.index("prepared_catalogs ="):script.index("print(\"MABANG_PAGE_IMPORT_RESULT")]
    assert "try:" in preparation
    assert "restore_mabang_page_import_catalogs(prepared_catalogs)" in preparation
    assert preparation.index("try:") < preparation.index("prepared_catalogs = prepare_mabang_page_import_catalogs()")


def test_page_import_runner_always_closes_the_visible_promotion_dialog() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})

    restore = script[script.index("def restore_mabang_page_import_catalogs"):script.index("ensure_real_tab()")]
    assert "if (state.promotionOpenedByCollector)" not in restore
    assert ".el-dialog__headerbtn" in restore
    assert r"\u5173\u95ed" in restore
    assert r"\u53d6\u6d88" in restore


def test_mabang_executor_reads_current_mabang_category_shape_without_guessing_store() -> None:
    source = browser_use_runner.MABANG_PAGE_IMPORT_CAPTURE_PATH.read_text(encoding="utf-8")

    assert "categoryNameArr" in source
    assert "category_id_path" in source
    assert "attribute_value_list" in source
    assert "categorySettings?.shopId" in source
    assert "storeCategoryModule?.shopId" in source
    assert "shopList?.[0]" not in source


def test_mabang_capture_classifies_free_text_attributes_not_as_empty_selects() -> None:
    # Mabang marks free-text controls with attribute_info.input_type 3, and some
    # enum-style attributes (条件/尺寸/型号) have no option list at all. These
    # must be captured as text — never as single_select with zero options, which
    # makes the 维护 editor render an empty dropdown and lose the value.
    capture = _run_mabang_capture({
        "storeCategory": {
            "ProductAttributeInfo": {
                "attributesNew": [
                    {
                        "attribute_id": 102530, "name_cn": "尺寸", "mandatory": "",
                        "attribute_info": '{"input_type":"3"}',
                        "value": "100-150cm", "values": [], "attribute_value_list": "",
                    },
                    {
                        "attribute_id": 100413, "name_cn": "条件", "mandatory": "",
                        "attribute_info": '{"input_type":"2"}',
                        "value": "Brand New",
                        "values": [{"id": "custom", "label": "自填选项", "options": []}, {"id": "enumeration", "label": "", "options": []}],
                        "attribute_value_list": "",
                    },
                    {
                        "attribute_id": 101639, "name_cn": "型号", "mandatory": "",
                        "attribute_info": '{"input_type":"5"}',
                        "value": [],
                        "values": [{"id": "custom", "label": "自填选项", "options": []}, {"id": "enumeration", "label": "", "options": []}],
                        "attribute_value_list": "",
                    },
                    {
                        "attribute_id": 100174, "name_cn": "衣领类型", "mandatory": "",
                        "attribute_info": '{"input_type":"2"}',
                        "value": "Round Neck",
                        "values": [{"id": "custom", "label": "自填选项", "options": []}, {"id": "enumeration", "label": "", "options": [{"value": "1574", "label": "普通话衣领 (Mandarin Collar)"}]}],
                        "attribute_value_list": [{"value_id": "1574", "name_cn": "普通话衣领", "name": "Mandarin Collar"}],
                    },
                ]
            }
        },
    })
    by_id = {a["field_id"]: a for a in capture["category_attributes"]}
    assert by_id["102530"]["type"] == "text"
    assert by_id["102530"]["selected_value"] == "100-150cm"
    assert by_id["102530"]["options"] == []
    assert by_id["100413"]["type"] == "text"
    assert by_id["100413"]["selected_value"] == "Brand New"
    assert by_id["101639"]["type"] == "text"
    assert by_id["100174"]["type"] == "single_select"
    # The captured custom value is not in the enum — it must be kept as an option
    # so the 维护 dropdown can display it instead of falling back blank.
    assert {"value": "Round Neck", "label": "Round Neck"} in by_id["100174"]["options"]


def test_mabang_capture_types_attributes_per_category_not_by_field_id() -> None:
    # 不同类目属性会不同：同一个 Shopee attribute_id 在 A 类目可能是纯文本、
    # 在 B 类目可能是枚举下拉。类型推断必须只看“当前类目”的 input_type/枚举，
    # 不能按属性 ID 建任何跨类目的全局类型表。采集函数本身是无状态的——每个
    # run 都实时读取当前类目 attributesNew，绝不允许按 ID 缓存上次类型。
    text_attr = {
        "attribute_id": 102530, "name_cn": "尺寸", "mandatory": "",
        "attribute_info": '{"input_type":"3"}',
        "value": "100-150cm", "values": [], "attribute_value_list": "",
    }
    enum_attr = {
        "attribute_id": 102530, "name_cn": "尺码", "mandatory": "",
        "attribute_info": '{"input_type":"1"}',
        "value": "170",
        "values": [{"id": "enumeration", "label": "", "options": [{"value": "165", "label": "165"}, {"value": "170", "label": "170"}]}],
        "attribute_value_list": [{"value_id": "165", "name": "165"}, {"value_id": "170", "name": "170"}],
    }
    text_capture = _run_mabang_capture({"storeCategory": {"ProductAttributeInfo": {"attributesNew": [text_attr]}}})
    enum_capture = _run_mabang_capture({"storeCategory": {"ProductAttributeInfo": {"attributesNew": [enum_attr]}}})
    text_again = _run_mabang_capture({"storeCategory": {"ProductAttributeInfo": {"attributesNew": [text_attr]}}})

    assert text_capture["category_attributes"][0]["type"] == "text"
    assert text_capture["category_attributes"][0]["options"] == []
    assert enum_capture["category_attributes"][0]["type"] == "single_select"
    assert len(enum_capture["category_attributes"][0]["options"]) == 2
    # No cross-run bleed: 采完 B 类目再采 A 类目，102530 仍是 text。
    assert text_again["category_attributes"][0]["type"] == "text"


def test_mabang_page_import_uses_one_shared_capture_implementation() -> None:
    runner_source = Path(browser_use_runner.__file__).read_text(encoding="utf-8")
    executor_source = browser_use_runner.MABANG_PAGE_IMPORT_EXECUTOR_PATH.read_text(encoding="utf-8")

    assert "_build_legacy_mabang_page_import_audit_script" not in runner_source
    assert "_run_playwright_mabang_page_import_audit" not in runner_source
    assert "collectLegacy" not in executor_source
    assert "mabang_page_import_capture.js" in executor_source


def test_run_playwright_save_draft_rejects_saved_result_without_closed_page(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )

    def fake_run(*args, **kwargs):
        output = {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
        }
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(task, node_executable="node.exe")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["save_clicked"] is False
    assert result["publish_clicked"] is False
    assert result["page_closed"] is False
    assert result["error"] == "Playwright executor reported saved without closing the detail page"


def test_run_playwright_save_draft_rejects_saved_result_with_ok_false(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )

    def fake_run(*args, **kwargs):
        output = {
            "ok": False,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
        }
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(task, node_executable="node.exe")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Playwright executor reported saved with ok=false"


def test_run_playwright_save_draft_rejects_saved_result_with_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )

    def fake_run(*args, **kwargs):
        output = {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "success_dialog",
        }
        return subprocess.CompletedProcess(
            args[0],
            2,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(task, node_executable="node.exe")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Playwright executor reported saved with a nonzero exit code"


def test_run_playwright_save_draft_rejects_unknown_close_method(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )

    def fake_run(*args, **kwargs):
        output = {
            "ok": True,
            "status": "saved",
            "save_clicked": True,
            "publish_clicked": False,
            "page_closed": True,
            "close_method": "unknown",
        }
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(output),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(task, node_executable="node.exe")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Playwright executor reported saved with an unknown close method"


def test_run_playwright_save_draft_rejects_non_save_plan(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(_ready_payload(), tmp_path / "uploads")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("unsafe plan must not launch Node")

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fail_if_called)

    result = browser_use_runner.run_playwright_save_draft(task, node_executable="node.exe")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["save_clicked"] is False
    assert result["publish_clicked"] is False


def test_run_playwright_can_stop_after_complete_fill_before_save(tmp_path: Path, monkeypatch) -> None:
    task = browser_use_runner.build_browser_use_task_plan(
        _ready_payload(),
        tmp_path / "uploads",
        allow_save=True,
    )
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args[0]
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="MABANG_PLAYWRIGHT_RESULT " + json.dumps(
                {
                    "ok": True,
                    "status": "ready_to_save",
                    "save_clicked": False,
                    "publish_clicked": False,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_playwright_save_draft(
        task,
        node_executable="node.exe",
        stop_before_save=True,
    )

    assert calls["args"][-1] == "--stop-before-save"
    assert result["status"] == "ready_to_save"
    assert result["save_clicked"] is False
    assert result["publish_clicked"] is False


def test_safe_fill_script_contains_browser_guards_and_task_data(tmp_path: Path) -> None:
    source = tmp_path / "A黑色.jpg"
    source.write_bytes(b"black image")
    payload = _ready_payload()
    payload["browser_inputs"]["variant_image_paths"] = {"black": str(source)}
    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads")

    script = browser_use_runner.build_browser_use_safe_fill_script(task)

    assert "ensure_real_tab()" in script
    assert "list_tabs()" in script
    assert "productDetailShopee" in script
    assert "__AI_LISTING_SAFE_GUARD__" in script
    assert "FORBIDDEN_BUTTON_TEXTS" in script
    assert "\\u4fdd\\u5b58" in script
    assert "保存" not in script
    assert "保存并刊登" not in script
    assert "upload_variant_thumbnails" in script
    assert "stop_for_operator_review" in script
    assert ".click()" not in script
    encoded = re.search(r'b64decode\("([^"]+)"\)', script)
    assert encoded
    decoded_task = json.loads(base64.b64decode(encoded.group(1)).decode("utf-8"))
    assert decoded_task["sku_grid"]["skus"][0]["sku_code"] == "DL0001-BLACK-S"
    compile(script, "<browser-use-safe-fill>", "exec")


def test_safe_audit_script_does_not_fill_or_upload(tmp_path: Path) -> None:
    source = tmp_path / "A黑色.jpg"
    source.write_bytes(b"black image")
    payload = _ready_payload()
    payload["browser_inputs"]["variant_image_paths"] = {"black": str(source)}
    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads")

    script = browser_use_runner.build_browser_use_safe_audit_script(task)

    assert "productDetailShopee" in script
    assert "__AI_LISTING_SAFE_GUARD__" in script
    assert "install_task_payload()" in script
    assert "audit_page_state()" in script
    assert "SAFE_AUDIT_RESULT" in script
    assert "Mabang Shopee product detail tab not found" in script
    assert "ensure_ascii=True" in script
    assert "\\u4fdd\\u5b58" in script
    assert "保存" not in script
    assert "保存并刊登" not in script
    assert "run_sku_grid_safe_fill()" not in script
    assert "upload_variant_thumbnails" not in script
    assert ".click()" not in script
    compile(script, "<browser-use-safe-audit>", "exec")


def test_safe_audit_script_recognizes_base64_wrapped_go_publish_detail_url(tmp_path: Path) -> None:
    task = browser_use_runner.build_browser_use_task_plan(_ready_payload(), tmp_path / "uploads")

    script = browser_use_runner.build_browser_use_safe_audit_script(task)

    assert "base64.urlsafe_b64decode" in script
    assert "main.goPublish" in script
    assert "productDetailShopee" in script


def test_login_recovery_script_has_selectors_without_secret() -> None:
    script = browser_use_runner.build_mabang_login_recovery_script(
        account_env="MABANG_ACCOUNT",
        password_env="MABANG_PASSWORD",
    )

    assert "#account" in script
    assert "#password" in script
    assert "#account-btn" in script
    assert "MABANG_ACCOUNT" in script
    assert "MABANG_PASSWORD" in script
    assert "yang" not in script.lower()
    assert "LOGIN_RECOVERY_RESULT" in script
    compile(script, "<mabang-login-recovery>", "exec")


def test_mabang_page_import_script_reads_cross_origin_editor_with_shared_capture() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})

    assert "Page.getFrameTree" in script
    assert "Runtime.executionContextCreated" in script
    assert "Runtime.evaluate" in script
    assert "mabang_page_import_capture.js" in script
    assert "current_page_url" in script
    assert "len(detail_tabs) > 1" in script
    # Page import may temporarily open only the two catalog controls, then restores
    # the previous view. It must not write product fields or trigger save/publish.
    assert script.count(".click()") >= 7
    assert "sitePickerOpenedByCollector" in script
    assert r"\u6dfb\u52a0\u6c34\u5370" in script
    assert r"\u9009\u62e9\u6a21\u677f" in script
    assert r"\u6298\u6263\u6d3b\u52a8" in script
    assert r"\u9009\u62e9\u6298\u6263\u6d3b\u52a8" in script
    assert r"\u5173\u95ed" in script
    assert "clickForText(promotionDialog, '选择')" not in script
    assert ".replace(/\\s+/g, ' ')" in script
    assert '[role="button"]' in script
    assert 'input[type="radio"]' in script
    assert "\\\\s+" not in script
    assert '\\\\"button\\\\"' not in script
    assert "input.value =" not in script
    # 采集前用 hover 优先展开「添加水印」菜单，点击仅作为兼容回退。此脚本只负责
    # 临时展开目录菜单并随后恢复，不得写入商品字段或触发保存/刊登。
    assert "'mouseenter'" in script and "'mouseover'" in script
    assert "'mouseleave'" in script and "'mouseout'" in script
    assert "new MouseEvent(eventName" in script
    assert "state.watermarkMenuMethod" in script
    assert "watermarkOpenedByCollector" in script
    assert "保存并刊登" not in script
    assert "提交审核" not in script
    compile(script, "<mabang-page-import>", "exec")


def test_mabang_page_import_script_is_ascii_safe_for_browser_use_stdin() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})

    assert script.isascii()


def _run_generated_js_expression(expression: str) -> dict:
    """在 Node 里用最小 mock DOM 跑一段生成的 async IIFE，返回 {ok, state|error, dispatched}。

    ``watermarkButton`` 这类漏声明只在浏览器里炸（ReferenceError → 整个 IIFE 中断），
    Python 侧只做字符串断言是发现不了的，所以这里真的把 JS 跑一遍。
    """
    node_script = r"""
const expression = process.argv[1];
global.window = global;
global.location = {
  href: 'https://publish.mabangerp.com/publish-ui/?platform=shopee',
  origin: 'https://publish.mabangerp.com',
  hash: '',
};
global.getComputedStyle = () => ({ display: 'block', visibility: 'visible', opacity: '1' });
global.MouseEvent = class { constructor(type, init) { this.type = type; Object.assign(this, init || {}); } };
let menuOpen = false;
const dispatched = [];
const rect = { left: 10, top: 10, right: 60, bottom: 30, width: 50, height: 20 };
const watermarkTrigger = {
  textContent: '\u6dfb\u52a0\u6c34\u5370',
  isConnected: true,
  disabled: false,
  getAttribute: (name) => (name === 'aria-expanded' ? (menuOpen ? 'true' : 'false') : null),
  setAttribute: () => {},
  getBoundingClientRect: () => rect,
  dispatchEvent: (event) => { dispatched.push(event.type); if (event.type === 'mouseenter') menuOpen = true; return true; },
  click: () => { dispatched.push('click'); menuOpen = true; },
  closest: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  matches: () => false,
};
const menu = {
  textContent: '\u6dfb\u52a0\u6c34\u5370 \u5173\u95ed\u6c34\u5370 \u4e3b\u56fe\u6c34\u5370',
  isConnected: true,
  querySelector: () => ({}),
  querySelectorAll: (selector) => (String(selector).includes('.el-dropdown-menu__item') ? [{}] : []),
  getBoundingClientRect: () => rect,
};
global.document = {
  body: { isConnected: true, textContent: '', querySelector: () => null, querySelectorAll: () => [] },
  querySelector: () => null,
  querySelectorAll: (selector) => {
    const text = String(selector);
    if (text.includes('button,[role="button"]')) return [watermarkTrigger];
    if (text === '.el-dropdown-menu,.el-select-dropdown,.el-popper,[role="menu"]') return menuOpen ? [menu] : [];
    return [];
  },
};
(async () => {
  try {
    const value = await (0, eval)(expression);
    process.stdout.write(JSON.stringify({ ok: true, state: value, menuOpen, dispatched }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((error && error.message) || error), dispatched }));
  }
})();
"""
    result = subprocess.run(
        ["node", "-e", node_script, expression],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_page_import_prepare_opens_the_watermark_menu_without_undefined_references() -> None:
    """2026-10-01 现场回归：prepare 段改成 hover 优先打开水印菜单时漏了 ``watermarkButton``
    的声明，``watermarkMenuVisible()`` 一进来就 ReferenceError，异常被 cdp() 吞掉，
    于是水印菜单、尺码图来源、店铺选择器、折扣活动整段准备都不再执行，而测试全是
    字符串断言所以一直绿。这条把生成的脚本 exec 出来、把它的 JS 真跑一遍。
    """

    script = browser_use_runner.build_mabang_page_import_audit_script(
        {"platform": "shopee", "country": "PH", "store_id": "store-ph"}
    )
    expressions: list[str] = []

    def fake_cdp(method, **params):
        if method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {"id": "F1", "url": "https://publish.mabangerp.com/x"},
                    "childFrames": [],
                }
            }
        if method == "Runtime.evaluate":
            expressions.append(str(params.get("expression") or ""))
            return {"result": {"value": {"watermarkOpenedByCollector": True, "watermarkMenuMethod": "hover"}}}
        return {}

    namespace: dict = {
        "__name__": "mabang_page_import_script",
        "cdp": fake_cdp,
        "ensure_real_tab": lambda: None,
        "list_tabs": lambda: [],
        "page_info": lambda: {"url": "https://publish.mabangerp.com/x"},
        "switch_tab": lambda _target: None,
        "drain_events": lambda: [],
    }
    try:
        exec(compile(script, "<mabang-page-import>", "exec"), namespace)
    except Exception:  # noqa: BLE001 - 入口流程在测试环境里跑不完，只要函数定义出来就够
        pass

    prepare = namespace.get("prepare_mabang_page_import_catalogs")
    assert callable(prepare), "生成的脚本必须定义 prepare_mabang_page_import_catalogs()"
    namespace["context_id"] = "CTX"
    prepare()

    prep_js = next((item for item in expressions if "watermarkMenuVisible" in item), "")
    assert prep_js, "prepare 段应该通过 Runtime.evaluate 下发打开水印菜单的脚本"

    outcome = _run_generated_js_expression(prep_js)

    assert outcome["ok"] is True, outcome
    assert outcome["state"]["watermarkOpenedByCollector"] is True
    assert outcome["state"]["watermarkMenuMethod"] == "hover"
    assert "mouseenter" in outcome["dispatched"]


def test_mabang_page_import_script_prefers_current_detail_and_uses_latest_duplicate_tab() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})
    tabs = [
        {"targetId": "old", "url": "https://publish.mabangerp.com/#/productDetailShopee?id=duplicate"},
        {"targetId": "current", "url": "https://publish.mabangerp.com/#/productDetailShopee?id=duplicate"},
    ]
    selected = []
    environment = {
        "ensure_real_tab": lambda: None,
        "list_tabs": lambda: tabs,
        "page_info": lambda: {"url": tabs[1]["url"]},
        "switch_tab": selected.append,
        "cdp": lambda *args, **kwargs: {"frameTree": {}},
        "drain_events": lambda: [],
    }

    with pytest.raises(RuntimeError, match="editor iframe"):
        exec(script, environment)
    assert selected == ["old"]

    environment.update({"page_info": lambda: {"url": "http://127.0.0.1:8793/"}})
    with pytest.raises(RuntimeError, match="editor iframe"):
        exec(script, environment)
    assert selected[-1] == "current"


def test_mabang_page_import_script_blocks_different_product_detail_tabs() -> None:
    script = browser_use_runner.build_mabang_page_import_audit_script({"platform": "shopee", "country": "PH"})
    tabs = [
        {"targetId": "first", "url": "https://publish.mabangerp.com/#/productDetailShopee?id=first"},
        {"targetId": "second", "url": "https://publish.mabangerp.com/#/productDetailShopee?id=second"},
    ]
    environment = {
        "ensure_real_tab": lambda: None,
        "list_tabs": lambda: tabs,
        "page_info": lambda: {"url": "http://127.0.0.1:8794/"},
        "switch_tab": lambda _: None,
        "cdp": lambda *args, **kwargs: {"frameTree": {}},
        "drain_events": lambda: [],
    }

    with pytest.raises(RuntimeError, match="Multiple Mabang product detail tabs"):
        exec(script, environment)


def test_workspace_navigation_script_clicks_local_pending_and_new_listing() -> None:
    script = browser_use_runner.build_mabang_workspace_navigation_script()

    assert "open_mabang_workspace()" in script
    assert "click_local_pending_products()" in script
    assert "click_new_listing()" in script
    assert script.isascii()
    assert "\\u5f85\\u53d1\\u5e03\\u5546\\u54c1" in script
    assert "\\u65b0\\u589e\\u520a\\u767b" in script
    assert "WORKSPACE_NAVIGATION_RESULT" in script
    assert "\\u4fdd\\u5b58" not in script
    compile(script, "<mabang-workspace-navigation>", "exec")


def test_run_browser_use_safe_fill_passes_script_to_browser_use_stdin(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")
    payload = _ready_payload()
    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads")
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="SAFE_FILL_RESULT {}", stderr="")

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_browser_use_safe_fill(task, fake_exe, timeout_seconds=9)

    assert result["ok"] is True
    assert calls["args"][0] == [str(fake_exe)]
    assert calls["input"] == result["script"]
    assert calls["encoding"] == "utf-8"
    assert calls["errors"] == "replace"
    assert calls["timeout"] == 9
    assert "ensure_real_tab()" in result["script"]


def test_run_browser_use_safe_audit_passes_audit_script_to_browser_use_stdin(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")
    payload = _ready_payload()
    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads")
    calls = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="SAFE_AUDIT_RESULT {}", stderr="")

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_browser_use_safe_audit(task, fake_exe, timeout_seconds=9)

    assert result["ok"] is True
    assert calls["args"][0] == [str(fake_exe)]
    assert calls["input"] == result["script"]
    assert "SAFE_AUDIT_RESULT" in result["script"]
    assert "run_sku_grid_safe_fill()" not in result["script"]


def test_run_browser_use_safe_fill_reports_timeout_as_remote_debugging_prompt(tmp_path: Path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")
    payload = _ready_payload()
    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output="connecting to ws://127.0.0.1:9222", stderr="")

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_browser_use_safe_fill(task, fake_exe, timeout_seconds=1)

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["next_step"] == "allow_chrome_remote_debugging"
    assert "chrome://inspect/#remote-debugging" in result["output"]


def test_doctor_result_mentions_remote_debugging_when_devtools_is_missing() -> None:
    result = browser_use_runner.interpret_doctor_result(
        1,
        "fatal: DevToolsActivePort not found. enable chrome://inspect/#remote-debugging",
    )

    assert result["ok"] is False
    assert result["needs_remote_debugging"] is True
    assert result["next_step"] == "enable_chrome_remote_debugging"


def test_doctor_result_mentions_daemon_restart_when_daemon_is_down() -> None:
    result = browser_use_runner.interpret_doctor_result(
        1,
        "[FAIL] daemon alive — see install.md\n[FAIL] active browser connections — 0",
    )

    assert result["ok"] is False
    assert result["needs_remote_debugging"] is False
    assert result["next_step"] == "restart_browser_use_daemon"


def test_doctor_result_ignores_optional_cloud_auth_failure_when_exit_is_zero() -> None:
    result = browser_use_runner.interpret_doctor_result(
        0,
        "[ok  ] chrome running\n[ok  ] daemon alive\n[FAIL] Browser Use cloud auth — optional",
    )

    assert result["ok"] is True
    assert result["needs_remote_debugging"] is False
    assert result["next_step"] == ""


def test_run_doctor_uses_utf8_replacement_for_browser_titles(tmp_path, monkeypatch) -> None:
    fake_exe = tmp_path / "browser-use.exe"
    fake_exe.write_text("", encoding="utf-8")
    calls = {}

    def fake_run(*args, **kwargs):
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="active page: 🐴 马帮导出工作台", stderr="")

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)

    result = browser_use_runner.run_doctor(fake_exe)

    assert result["ok"] is True
    assert calls["encoding"] == "utf-8"
    assert calls["errors"] == "replace"
    assert "马帮导出工作台" in result["output"]


def test_main_reconfigures_stdout_for_unicode_doctor_output(monkeypatch) -> None:
    class FakeStdout:
        def __init__(self) -> None:
            self.encoding = None
            self.errors = None
            self.output = ""

        def reconfigure(self, **kwargs) -> None:
            self.encoding = kwargs.get("encoding")
            self.errors = kwargs.get("errors")

        def write(self, value) -> int:
            self.output += value
            return len(value)

        def flush(self) -> None:
            pass

    fake_stdout = FakeStdout()
    monkeypatch.setattr(browser_use_runner.sys, "stdout", fake_stdout)
    monkeypatch.setattr(
        browser_use_runner,
        "run_job",
        lambda job_id, include_doctor=False, execute_safe_fill=False, execute_safe_audit=False: {
            "dry_run": {"ok": True},
            "doctor": {"output": "active page: 🐴 马帮导出工作台"},
        },
    )

    exit_code = browser_use_runner.main(["--job-id", "job-1", "--doctor"])

    assert exit_code == 0
    assert fake_stdout.encoding == "utf-8"
    assert fake_stdout.errors == "replace"
    assert "马帮导出工作台" in fake_stdout.output


def test_parse_playwright_step_trail_extracts_ordered_unique_stages() -> None:
    """executor 的 MABANG_PLAYWRIGHT_EVENT stage 行按序解析成 steps：
    只保留 stage 事件、按出现顺序、同 stage 去重（重试会重发）、带 at 时间。"""
    lines = [
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "select_store", "ok": True, "at": "2026-09-07T01:00:00Z"}),
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "attempt", "attempt": 1, "max": 3, "at": "2026-09-07T01:00:01Z"}),
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "fill_basic_info", "ok": True, "at": "2026-09-07T01:00:05Z"}),
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "fill_sku_grid", "ok": True, "at": "2026-09-07T01:01:00Z"}),
        # 同 stage 重发（retry 重跑 fill_sku_grid）应去重
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "fill_sku_grid", "ok": False, "at": "2026-09-07T01:01:30Z"}),
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "fill_cnsc_site_prices_and_discounts", "ok": True, "at": "2026-09-07T01:02:00Z"}),
        'MABANG_PLAYWRIGHT_EVENT ' + json.dumps({"event": "stage", "stage": "save_draft", "ok": True, "at": "2026-09-07T01:03:00Z"}),
        'MABANG_PLAYWRIGHT_RESULT {"ok": true, "status": "saved"}',
        'unrelated line',
    ]
    steps = browser_use_runner.parse_playwright_step_trail("\n".join(lines))

    assert [step["stage"] for step in steps] == [
        "select_store",
        "fill_basic_info",
        "fill_sku_grid",
        "fill_cnsc_site_prices_and_discounts",
        "save_draft",
    ]
    assert steps[0]["at"] == "2026-09-07T01:00:00Z"
    assert steps[0]["ok"] is True
    # attempt 事件不入 steps
    assert all(step["kind"] == "stage" for step in steps)


def test_parse_playwright_step_trail_empty_without_stage_events() -> None:
    assert browser_use_runner.parse_playwright_step_trail("") == []
    assert browser_use_runner.parse_playwright_step_trail(
        "MABANG_PLAYWRIGHT_RESULT {}\nrandom noise\n"
    ) == []

def test_watermark_slots_carry_the_image_range_intent(tmp_path: Path) -> None:
    """水印槽位带「图片范围」意图：主图=first，产品图默认=from_second，自定义=explicit。

    操作员 2026-10-01 确认马帮那边选完水印模板还要选图片范围，所以范围要作为任务参数
    送到执行器（现在先回报到阶段结果，交互部分等真实页面结构确认后再接）。
    """

    images = [tmp_path / name for name in ["one.jpg", "two.jpg", "three.jpg", "four.jpg"]]
    for image in images:
        image.write_bytes(b"image")
    payload = deepcopy(_ready_payload())
    payload["browser_inputs"].update({
        "product_image_paths": [str(image) for image in images],
        "main_image_watermark": {"template_id": "wm-main", "name": "主图水印", "mode": "auto", "positions": []},
        "product_image_watermark": {"template_id": "wm-product", "name": "产品图水印", "mode": "auto", "positions": []},
    })

    task = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)

    assert task["watermarks"]["main_image"]["image_positions"] == [1]
    assert task["watermarks"]["main_image"]["image_range"] == {"kind": "first", "positions": [1]}
    assert task["watermarks"]["product_images"]["image_positions"] == [2, 3, 4]
    assert task["watermarks"]["product_images"]["image_range"] == {"kind": "from_second", "positions": [2, 3, 4]}

    # 自定义范围（只勾第 3 张）→ explicit + 具体张数
    payload["browser_inputs"]["product_image_watermark"] = {
        "template_id": "wm-product", "name": "产品图水印", "mode": "custom", "positions": [3],
    }
    custom = browser_use_runner.build_browser_use_task_plan(payload, tmp_path / "uploads", allow_save=True)
    assert custom["watermarks"]["product_images"]["image_positions"] == [3]
    assert custom["watermarks"]["product_images"]["image_range"] == {"kind": "explicit", "positions": [3]}

def test_watermark_collector_spawns_node_and_parses_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """水印采集走 Playwright/CDP：Python 侧只负责起 node + 解析结果标记。"""

    calls: dict = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls.update(kwargs)
        payload = {
            "ok": True,
            "status": "ok",
            "catalog_loaded": True,
            "templates": [{"template_id": "wm-1", "name": "主图角标", "preview_url": "https://cdn/1.png"}],
            "store": {"id": "store-a", "name": "POD PH"},
            "range_probe": {"candidates": []},
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="MABANG_WATERMARK_RESULT " + json.dumps(payload, ensure_ascii=False),
            stderr="",
        )

    monkeypatch.setattr(browser_use_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(browser_use_runner, "_resolve_node_executable", lambda exe, env: ("node", "v20.0.0"))

    result = browser_use_runner.collect_store_watermarks_via_playwright(cdp_url="http://127.0.0.1:9333")

    assert result["ok"] is True
    assert result["templates"][0]["template_id"] == "wm-1"
    assert str(browser_use_runner.MABANG_WATERMARK_COLLECT_PATH) in calls["command"]
    assert "--cdp-url" in calls["command"] and "http://127.0.0.1:9333" in calls["command"]
    assert calls["cwd"] == str(browser_use_runner.PROJECT_ROOT)
    # node_modules 要进 NODE_PATH，否则采集器 require 不到 playwright-core。
    assert calls["env"]["NODE_PATH"]


def test_watermark_collector_reports_a_missing_result_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        browser_use_runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="boom", stderr=""),
    )
    monkeypatch.setattr(browser_use_runner, "_resolve_node_executable", lambda exe, env: ("node", "v20.0.0"))

    result = browser_use_runner.collect_store_watermarks_via_playwright()

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"]


def test_watermark_collector_reports_node_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_use_runner, "_resolve_node_executable", lambda exe, env: ("node", None))

    result = browser_use_runner.collect_store_watermarks_via_playwright()

    assert result["ok"] is False
    assert "Node" in result["error"]


def test_variant_thumbnail_upload_downscales_oversized_images(tmp_path: Path) -> None:
    """马帮变种图区对超大原图不显示缩略图（实测 4752×5120 PNG 一直空预览 →
    variant_thumbnail_not_verified）。上传副本超过 2048px 时先缩到 2048，原图不动。"""
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "A红色.png"
    with Image.new("RGB", (3000, 2600), (200, 30, 40)) as image:
        image.save(source)

    uploads = browser_use_runner.prepare_variant_thumbnail_uploads(
        {"red": str(source)},
        tmp_path / "uploads",
    )
    upload_path = Path(uploads["red"]["upload_path"])

    with Image.open(source) as original:
        assert original.size == (3000, 2600), "原图不能被改"
    with Image.open(upload_path) as resized:
        assert max(resized.size) <= browser_use_runner.VARIANT_THUMBNAIL_MAX_SIDE


def test_variant_thumbnail_upload_keeps_small_images_byte_identical(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "A白色.png"
    with Image.new("RGB", (512, 512), (255, 255, 255)) as image:
        image.save(source)
    raw = source.read_bytes()

    uploads = browser_use_runner.prepare_variant_thumbnail_uploads(
        {"white": str(source)},
        tmp_path / "uploads",
    )

    assert Path(uploads["white"]["upload_path"]).read_bytes() == raw
