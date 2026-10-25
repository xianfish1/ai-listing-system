from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "apps" / "web" / "index.html"
APP = ROOT / "apps" / "web" / "app.js"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_store_template_ui_hides_legacy_ids_and_has_complete_controls():
    html = INDEX.read_text(encoding="utf-8")
    for forbidden in ["马帮店铺编号", "马帮类目编号", "马帮折扣编号", "产品线", "标题后缀", "审核人"]:
        assert forbidden not in html
    for required_id in [
        "mabang-page-import-btn", "store-template-category-dialog", "store-template-promotion",
        "store-template-stock", "store-template-weight-kg", "store-template-package-length-cm",
        "store-template-package-width-cm", "store-template-package-height-cm", "store-template-size-options",
        "store-template-custom-size", "store-template-size-chart-preview", "store-template-size-chart-upload",
        "store-template-description-profile", "copy-template-picker-dialog",
    ]:
        assert f'id="{required_id}"' in html


def test_base_product_maintenance_shows_video_status_and_upload_control():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in ["base-product-video-preview", "base-product-video-status", "base-product-video-upload"]:
        assert f'id="{control}"' in html
    assert "renderBaseProductVideo" in script
    assert "/api/base-products/" in script and "/video" in script


def test_base_product_maintenance_allows_uploading_own_product_images():
    """可选产品图区要能自己上传图片：按钮 + 多选文件输入 + 上传接口接线。"""
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert 'id="base-product-image-picker-btn"' in html
    assert 'data-open-file-picker="base-product-image-upload"' in html
    assert 'id="base-product-image-upload"' in html
    assert 'accept="image/png,image/jpeg,image/webp"' in html
    assert 'id="base-product-image-upload-result"' in html
    assert "uploadBaseProductProductImages" in script
    assert "/product-images" in script
    assert "base-product-image-upload" in script
    # 自上传的素材不在导入目录里，靠来源标记留在本商品的素材范围中。
    assert "manual_upload" in script


def test_base_product_maintenance_wires_local_folder_upload():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert 'id="create-local-base-product-btn"' in html
    assert 'id="local-base-product-folder-upload"' in html
    assert 'webkitdirectory' in html and 'multiple' in html
    assert 'local-folder-batch.js' in html
    # 入口文案 2026-09-28 改为「＋ 添加文件夹」：可连续点选、逐个累加进队列。
    assert '＋ 添加文件夹' in html
    assert "createBaseProductFromLocalFiles" in script
    assert "/api/local-folder-uploads" in script
    assert "planLocalFolderImport" in script
    assert "有效文件" in script
    assert "忽略文件" in script
    assert "批量导入完成" in script
    assert "失败" in script


def test_local_import_queue_lets_operators_queue_several_directories():
    """本地导入必须能排队：选下一个目录不再要求等上一个导入结束。"""
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    module = (ROOT / "apps" / "web" / "local-folder-batch.js").read_text(encoding="utf-8")

    # 队列核心：串行执行、可查看、可丢弃尚未开始的项。
    assert "function createImportQueue(" in module
    assert "whenIdle" in module and "removePending" in module and "clearPending" in module
    assert "createImportQueue" in script

    # 队列面板：进度、待处理数量、清空与逐项移除都在页面上。
    for required_id in ["local-import-queue", "local-import-queue-summary", "local-import-queue-list", "local-import-queue-clear"]:
        assert f'id="{required_id}"' in html
    assert "renderLocalImportQueue" in script
    assert "local-import-queue-summary" in script
    assert "data-local-import-remove" in script
    # 手动关闭 + 空闲自动收起 + 有新任务自动展开（现场 2026-09-28）。
    for required_id in ["local-import-queue-close", "local-import-queue-show"]:
        assert f'id="{required_id}"' in html
    assert "LOCAL_IMPORT_IDLE_COLLAPSE_MS" in script
    assert "localImportQueueCollapsed = true" in script
    assert 'target.id === "local-import-queue-close"' in script
    assert 'target.id === "local-import-queue-show"' in script
    # 自动展开：本地或 NAS 有等待/进行中的项都算（NAS 导入同样进这个队列）。
    assert "localImportQueueDepth(snapshot)\n    || (nasImportPanelState.items || []).some" in script
    assert "panel.hidden = depth === 0 || localImportQueueCollapsed;" in script
    # NAS 导入接进同一个队列面板 + 队列项重试（导入失败 / AI 识别出错）。
    assert "let nasImportPanelState = { taskId: \"\", items: [] };" in script
    assert "setNasImportPanel(task.id, state.items)" in script
    assert "data-import-retry" in script
    assert 'data-import-retry-kind="ai"' in script
    assert "async function retryImportQueueItem(value, kind)" in script
    assert "queue.removeResult?.(value);" in script
    assert "async function retryBaseProductAiFor(productId)" in script
    assert "removeResult" in module

    # 显式隐藏规则：带 display 的类会盖掉浏览器默认的 [hidden] 隐藏。
    styles = STYLES.read_text(encoding="utf-8")
    assert ".local-import-queue[hidden]" in styles
    assert ".local-import-queue-item--running" in styles and ".local-import-queue-remove" in styles

    # 入队即返回：不再 await 整批导入，也不再禁用按钮挡住下一次选目录。
    # app.js 是 CRLF，先归一化换行再按函数体边界切片。
    normalized = script.replace("\r\n", "\n")
    local_entry = normalized.split("async function createBaseProductFromLocalFiles(allFiles, options = {}) {", 1)[1].split("\n}\n", 1)[0]
    assert len(local_entry) < 3000, "函数体切片失败：后续断言会退化成整文件匹配"
    assert "queue.enqueue(" in local_entry
    assert "已加入" in local_entry and "个商品文件夹" in local_entry
    # 队列里已经有同名目录时提醒一次，避免重复创建基础商品。
    assert "重复创建" in local_entry
    assert ".disabled = true" not in local_entry
    assert "await runSequentialImport" not in local_entry


def test_sale_price_sizes_and_copy_picker_are_wired():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    # 「六折售价」可手改：输入框不再 readonly，六折只是初值（改原价时的自动预填
    # 在手填后停止，清空即恢复自动）。
    assert 'id="store-template-sale-price" type="number"' in html
    assert 'id="store-template-sale-price" readonly' not in html
    assert "calculateFortyPercentDiscount" in script
    assert "storeTemplateSalePriceIsManual" in script
    assert "applyStoreTemplateRatio" in script
    assert "STANDARD_SIZES" in script
    assert "openCopyTemplatePicker" in script
    assert "/api/uploads/size-chart" in script
    assert 'api("/api/mabang-page-imports"' in script
    assert "<code>${escapeHtml(category.category_id" not in script
    assert "MABANG_NEW_PRODUCT_URL" in script
    assert 'id="mabang-page-import-dialog"' in html
    assert 'id="open-mabang-new-product-link"' in html
    assert "openMabangNewProductPage" in script
    assert 'api("/api/mabang-browser/start"' in script
    assert 'id="collect-mabang-page-import-btn"' in html
    assert "/collect" in script
    assert "window.location.assign(MABANG_NEW_PRODUCT_URL)" not in script
    assert "window.open(MABANG_NEW_PRODUCT_URL" not in script
    assert '`ID: ${profile.mabang_category_id' not in script


def test_store_template_save_as_is_wired_without_a_duplicate_collection_entry():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in ["save-as-store-template-dialog", "save-as-store-template-btn"]:
        assert f'id="{control}"' in html
    assert "saveStoreTemplateAsCopy" in script
    assert "/api/store-templates/" in script and "/copy" in script


def test_store_template_management_has_delete_and_one_mabang_import_entry():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="mabang-page-import-btn"' in html
    assert 'id="collect-target-store-config-btn"' not in html
    assert "data-delete-store-template" in script
    assert "deleteStoreTemplate" in script
    assert 'method: "DELETE"' in script


def test_store_template_store_picker_does_not_hardcode_a_default_store():
    script = APP.read_text(encoding="utf-8")

    assert 'option.mabang_store_id === "2022625115"' not in script
    assert 'option.store_name === "POD菲律宾"' not in script


def test_cnsc_template_uses_store_multiselect_and_requires_a_price_preview():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="store-template-target-sites" multiple' in html
    for control in [
        "store-template-cnsc-standard-sizes",
        "store-template-cnsc-large-sizes",
        "store-template-cnsc-global-standard",
        "store-template-cnsc-global-large",
    ]:
        assert f'id="{control}"' in html
    assert "selectedCnscTargetSiteStores" in script
    assert "请先解析并应用站点价格表" in script


def test_store_template_has_explicit_size_chart_sources_and_two_watermark_roles():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in [
        "store-template-size-chart-source",
        "store-template-size-chart-template",
        "store-template-main-watermark",
        "store-template-product-watermark",
    ]:
        assert f'id="{control}"' in html
    for source in ["upload", "template"]:
        assert f'value="{source}"' in html
    assert 'value="mabang_capture"' not in html
    assert html.count('<div class="watermark-selection">') == 2
    assert html.count('data-watermark-picker="main"') == 1
    assert html.count('data-watermark-picker="product"') == 1
    assert html.count('class="watermark-trigger"') == 2
    assert html.count('role="listbox"') >= 2
    # 「关闭水印」选项由 JS 按店铺目录动态渲染（静态 HTML 无目录数据）。
    assert "关闭水印" in script
    assert "watermark-menu-close" in script
    assert "/api/store-watermarks" in script
    assert "/api/store-size-chart-templates" in script
    assert "loadStoreSizeChartTemplates" in script
    assert "loadStoreWatermarks" in script
    assert "renderWatermarkPicker" in script
    assert "openWatermarkMenu" in script
    assert "closeWatermarkMenu" in script
    assert "mouseenter" in script
    assert "watermarkRoleMenu" in script
    assert "main_image_watermark" in script
    assert "product_image_watermark" in script
    assert "mabang_size_chart_template_id" in script
    assert "size_chart_preview_url" in script
    for preview_id in ["mabang-import-size-chart-upload", "mabang-import-size-chart-templates", "mabang-import-watermarks"]:
        assert f'id="{preview_id}"' in html
    assert "focusFirstMissingStoreTemplateField" in script
    assert 'id="mabang-import-size-chart-templates"' in html
    assert "size_chart_template_catalog" in script
    source_handler = script.index('target instanceof HTMLSelectElement && target.id === "store-template-size-chart-source"')
    input_only_guard = script.index('if (!(target instanceof HTMLInputElement)) return;')
    assert source_handler < input_only_guard
    # 选择模板时表单页上没有可引用的尺码图地址（表单项里只有「上传图片 / 选择模板 / 同步」），
    # 自动化靠店铺模板里一并上传的那张图插到描述末尾：上传的图优先于模板自带预览图，
    # 并且两种模式都留着「选择图片」入口（2026-09-23）。
    assert 'size_chart_asset_id: state.sizeChartAsset?.id || selectedSizeChartTemplate?.asset_id || ""' in script
    assert 'size_chart_preview_url: state.sizeChartAsset?.preview_url || selectedSizeChartTemplate?.preview_url || ""' in script
    assert 'default_size_chart_path: state.sizeChartAsset?.file_path || selectedSizeChartTemplate?.file_path || ""' in script
    assert "picker.hidden = false;" in script
    confirm_import = script[script.index("async function confirmMabangPageImport"):script.index("function openCopyTemplatePicker")]
    assert "focusFirstMissingStoreTemplateField(confirmed.template)" in confirm_import
    assert confirm_import.index("confirmed = await api(") < confirm_import.index("state.mabangPageImportRun = confirmed.run")
    assert "模板已导入，但页面刷新失败" in confirm_import


def test_store_template_editor_groups_workflow_fields_and_shows_watermark_thumbnails():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    for element_id in [
        "store-template-main-watermark-preview",
        "store-template-product-watermark-preview",
        "store-template-main-watermark-trigger",
        "store-template-product-watermark-trigger",
    ]:
        assert f'id="{element_id}"' in html
    for section in ["template-basics", "template-category", "template-transaction", "template-variants", "template-media"]:
        assert f'data-template-section="{section}"' in html
    assert "renderStoreWatermarkPreview" in script
    assert "renderWatermarkPicker" in script
    assert "setWatermarkChoice" in script
    assert "preview_url" in script
    assert ".store-template-workspace" in styles
    assert ".watermark-choice-preview" in styles
    assert ".watermark-menu" in styles
    assert ".watermark-trigger" in styles


def test_watermark_picker_menus_support_hover_click_and_keyboard_without_cross_store_leakage():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    # 每个角色都有「添加水印」触发器 + 悬浮菜单；菜单渲染关闭项、缩略图和名称。
    for role in ["main", "product"]:
        assert f'data-watermark-trigger="{role}"' in html
        assert f'data-watermark-menu="{role}"' in html
        assert f'data-watermark-picker="{role}"' in html
    assert "data-watermark-close" in script
    assert "关闭水印" in script
    assert "watermark-menu-thumb" in script
    assert "watermark-menu-name" in script
    # 悬停、点击、键盘绑定都存在。
    assert 'addEventListener("mouseenter"' in script
    assert 'addEventListener("mouseleave"' in script
    assert 'addEventListener("keydown"' in script
    assert "handleWatermarkTriggerKeydown" in script
    assert '["ArrowDown", "ArrowUp", "Enter", " ", "Escape"]' in script
    assert "aria-haspopup" in html
    # 每个角色独立保存选择，切换店铺时清空旧目录与旧选择。
    assert 'watermarkChoices: { main: "", product: "" }' in script
    assert "resetStoreWatermarkChoices" in script
    assert "state.watermarkCatalogStoreId !== \"\"" in script
    # 没有目录时提示「怎么把目录搞出来」（采集或手动填），且不允许误选。
    assert "watermark-empty-hint" in html
    assert "采集/更新水印" in html
    assert "手动填写模板" in html
    assert "trigger.disabled = !hasCatalog" in script


def test_store_template_basics_has_editable_fixed_description_template():
    # 模板基础 → 描述模板（固定描述）：可编辑填写，保存时带 description，
    # 打开既有模板时回填；不再被类目 profile 的 description 自动覆盖。
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="store-template-description"' in html
    assert "所有复制到该店铺模板的详情统一使用这份固定描述" in html
    assert "描述模板（固定描述）" in html
    # save payload carries description from the textarea
    assert 'description: $("store-template-description")?.value.trim() || ""' in script
    # open-edit prefills template.description
    assert 'descriptionInput.value = template.description || ""' in script
    # selecting a category profile must NOT clobber the fixed description
    assert '$("store-template-description").value = profile.description || ""' not in script


def test_confirm_import_separates_confirmation_failure_from_refresh_failure():
    script = APP.read_text(encoding="utf-8")
    confirm_import = script[script.index("async function confirmMabangPageImport"):script.index("function openCopyTemplatePicker")]

    assert confirm_import.index("confirmed = await api(") < confirm_import.index("state.mabangPageImportRun = confirmed.run")
    assert "模板已导入，但页面刷新失败" in confirm_import


def test_mabang_capture_uses_open_watermark_menu_as_a_fallback_source():
    capture = (ROOT / "scripts" / "mabang_page_import_capture.js").read_text(encoding="utf-8")

    assert "watermarkTemplateOptions" in capture
    assert "[data-watermark-id]" in capture
    assert "readWatermarkTemplatesFromDom" in capture


def test_size_specific_price_editor_is_visible_and_cache_busted():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="store-template-size-price-overrides"' in html
    assert 'app.js?v=' in html
    assert "renderSizePriceOverrides" in script
    assert "collectSizePriceOverrides" in script


def test_listing_copy_editor_supports_save_retry_and_manual_task_creation():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in [
        "listing-copy-editor-dialog", "listing-copy-description", "listing-copy-description-retry",
        "listing-copy-save", "listing-copy-create-job", "listing-copy-readiness-errors",
    ]:
        assert f'id="{control}"' in html
    assert "/description/generate" in script
    assert "saveListingCopy" in script
    assert "createAutomationJobForListingCopy" in script
    styles = (ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")
    assert "dialog:not([open])" in styles


def test_listing_copy_editor_is_a_complete_product_maintenance_form():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in [
        "listing-copy-description-profile",
        "listing-copy-promotion",
        "listing-copy-product-images",
        "listing-copy-size-chart-preview",
        "listing-copy-size-chart-upload",
        "listing-copy-sku-table",
        "listing-copy-stock",
        "listing-copy-weight-kg",
        "listing-copy-package-length-cm",
        "listing-copy-package-width-cm",
        "listing-copy-package-height-cm",
        "listing-copy-save-result",
        "listing-copy-video-preview",
        "listing-copy-video-upload",
        "listing-copy-video-remove",
        "listing-copy-video-status",
    ]:
        assert f'id="{control}"' in html
    for behavior in [
        "loadListingCopyDetail",
        "renderListingCopyProductImages",
        "renderListingCopySkuTable",
        "moveListingCopyImage",
        "uploadListingCopySizeChart",
        "collectListingCopyPayload",
        "uploadListingCopyVideo",
        "removeListingCopyVideo",
        "renderListingCopyVideo",
    ]:
        assert behavior in script
    assert 'data-listing-copy-normal-price' in script
    assert 'data-listing-copy-sale-price' in script
    assert 'app.js?v=' in html
    assert 'accept="video/mp4,.mp4"' in html


def test_listing_copy_media_uploads_have_explicit_picker_buttons_and_feedback():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    for control in [
        "listing-copy-size-chart-picker",
        "listing-copy-size-chart-file-name",
        "listing-copy-video-picker",
        "listing-copy-video-file-name",
    ]:
        assert f'id="{control}"' in html
    assert "openListingCopyFilePicker" in script
    assert 'data-open-file-picker' in script
    assert ".listing-copy-media-upload" in styles
    assert ".file-input-native" in styles
    click_handler = script[script.index('document.addEventListener("click"'):script.index('document.addEventListener("dragstart"')]
    assert "openListingCopyFilePicker(filePicker.dataset.openFilePicker" in click_handler


def test_listing_copy_warnings_do_not_disable_automation_creation():
    script = APP.read_text(encoding="utf-8")
    assert "copy.readiness?.warnings" in script
    assert '$("listing-copy-create-job").disabled = !!missing.length' in script
    assert "job.warnings" in script


def test_promotion_warning_labels_distinguish_expired_unresolved_and_unconfigured():
    script = APP.read_text(encoding="utf-8")
    assert 'promotion_expired: "促销活动已过期，请重新选择"' in script
    assert 'promotion_unresolved: "促销活动未解析（促销目录中未找到该活动），执行时可能失败"' in script
    assert 'promotion_unconfigured: "活动未配置"' in script
    for code in ["promotion_expired", "promotion_unresolved", "promotion_unconfigured"]:
        assert f'{code}: "listing-copy-basic-section"' in script


def test_readiness_labels_preserve_required_category_attribute_ids():
    script = APP.read_text(encoding="utf-8")
    assert 'key.startsWith("category_attributes.")' in script
    assert '必填类目属性未填写：${String(field).split(".").slice(1).join(".")}' in script
    # 枚举外的自选值（原产地=Philippines）另给一条提醒文案，不再显示成「未填写」。
    assert 'key.startsWith("category_custom_option.")' in script
    assert "使用了枚举外的自选值" in script


def test_listing_copy_table_only_requires_size_chart_for_local_copies():
    script = APP.read_text(encoding="utf-8")
    # 行渲染 2026-09-28 起抽到 appendListingCopyRow（列表要按完成时间分桶折叠），判据跟着搬过去。
    section = script[script.index("function appendListingCopyRow"):script.index("// 已完成区的时间分组行")]
    assert "const cnsc = isCNSCListingCopy(copy);" in section
    assert "if (!cnsc &&" in section


def test_all_task_statuses_have_chinese_labels():
    script = APP.read_text(encoding="utf-8")
    for label in ["待领取", "已领取", "执行中", "已保存", "执行失败", "需要人工确认", "需要重新生成", "已取消"]:
        assert label in script


def test_ui_has_global_operation_feedback_and_no_dead_category_review_controls():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert 'id="global-operation-feedback"' in html
    assert "showOperationFeedback" in script
    click_handler = script[script.index('document.addEventListener("click"'):]
    assert "reviewCategoryProfileVersion" not in click_handler
    assert "publishVersionButton" not in click_handler
    assert "rejectVersionButton" not in click_handler


def test_store_template_save_validates_required_business_fields():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert "validateStoreTemplateForm" in script
    assert "模板名" in script
    for control in [
        "store-template-store", "store-template-name", "store-template-normal-price",
        "store-template-stock", "store-template-weight-kg", "store-template-package-length-cm",
        "store-template-package-width-cm", "store-template-package-height-cm",
    ]:
        assert f'id="{control}"' in html


def test_store_template_does_not_implicitly_select_a_category_profile():
    script = APP.read_text(encoding="utf-8")
    assert 'categoryProfile($("store-template-category-profile")?.value || "")' in script
    assert '$("store-template-category-profile")?.value || "pod-ph-mens-tee"' not in script


def test_existing_store_template_can_be_loaded_and_updated():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    assert "editingStoreTemplateId" in script
    assert "openStoreTemplateEditor" in script
    assert "data-edit-store-template" in script
    assert 'method: state.editingStoreTemplateId ? "PUT" : "POST"' in script
    assert "保存模板" in html


def test_mabang_page_import_has_preview_then_explicit_confirmation():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    for control in [
        "mabang-import-preview",
        "mabang-import-store-category",
        "mabang-import-attributes",
        "mabang-import-variation",
        "mabang-import-transaction",
        "mabang-import-promotions",
        "mabang-import-warnings",
        "confirm-mabang-page-import-btn",
    ]:
        assert f'id="{control}"' in html
    assert 'id="collect-mabang-page-import-btn" type="button" disabled>采集当前页' in html
    assert 'id="confirm-mabang-page-import-btn" type="button" disabled>确认导入' in html
    assert "renderMabangImportPreview" in script
    assert "confirmMabangPageImport" in script
    assert "/confirm" in script
    assert 'normal_price: "原价(PHP)"' in script
    assert 'sale_price: "售价(PHP)"' in script
    assert "snapshot.price_defaults || {}" in script
    # Native (site) import preview: differing per-SKU prices keep a majority default
    # instead of a blank template, so the operator sees a notice, not 待补充.
    assert "uniform === false" in script
    assert "部分 SKU 价格不同，默认按多数价" in script
    for loader in [
        "loadMabangStoreOptions()",
        "loadStoreTemplates()",
        "loadCategoryProfiles()",
        "loadStorePromotions()",
        "loadReadiness()",
        "openStoreTemplateEditor",
    ]:
        assert loader in script


def test_cnsc_import_preview_and_template_cards_expose_mode_and_configuration_state():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'id="mabang-import-mode"' in html
    assert 'id="mabang-import-promotions-group"' in html
    assert '已识别：CNSC 全球商品' in script
    assert '全球价（CNY）' in script
    assert '泰国仓' in script and '广州仓' in script and '菲律宾仓' in script
    assert 'function cnscTemplateMissingFields' in script
    assert 'template-mode-badge' in script
    assert 'template-readiness-badge' in script
    assert 'template_needs_configuration' in script
    assert '.template-mode-badge' in styles
    assert '.template-readiness-badge' in styles


def test_cnsc_template_editor_configures_discount_activity_and_percent_per_store():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    for control in [
        "store-template-default-site-discount-percent",
        "store-template-target-store-config-body",
        "store-template-price-matrix",
    ]:
        assert f'id="{control}"' in html
    assert "价格 / 折扣价" in html
    assert "function renderCnscTargetStoreConfigs" in script
    assert "mabang_discount_id" in script
    assert "discount_percent" in script
    assert "default_site_discount_percent" in script
    assert 'placeholder="继承 ${escapeHtml(defaultDiscount)}"' in script
    assert 'target.id === "store-template-default-site-discount-percent"' in script
    assert "function recalculateCnscSitePrices" in script
    assert "recalculateCnscSitePrices(siteCode" in script
    assert "本次仅保存，不提交站点" in html


def test_cnsc_import_summary_does_not_show_default_discount_for_unloaded_store_activity():
    script = APP.read_text(encoding="utf-8")
    summary_start = script.index("const cnscLines = [")
    summary_end = script.index("setGroup(", summary_start)
    summary = script[summary_start:summary_end]

    assert "item.discount_catalog_loaded" in summary
    assert "if (!item.discount_catalog_loaded) return" in summary
    assert "snapshot.default_site_discount_percent" not in summary


def test_delegated_navigation_accepts_clicks_that_land_on_svg_icons():
    script = APP.read_text(encoding="utf-8")
    click_handler = script[script.index('document.addEventListener("click"'):]

    assert "if (!(target instanceof Element)) return;" in click_handler
    assert 'target.closest(".rail-item, .pipeline-stage")' in click_handler


def test_hidden_attribute_cannot_be_overridden_by_component_display_rules():
    styles = STYLES.read_text(encoding="utf-8")

    assert "[hidden] { display: none !important; }" in styles


def test_mabang_import_preview_is_single_column_without_overlays_on_mobile():
    styles = STYLES.read_text(encoding="utf-8")
    base_preview = styles.index(".mabang-import-preview {")
    assert "display: grid" in styles[base_preview:styles.index("}", base_preview)]
    mobile_start = styles.rfind("@media (max-width: 560px)")

    assert mobile_start > base_preview
    mobile = styles[mobile_start:]
    assert ".mabang-import-preview" in mobile
    assert "grid-template-columns: 1fr" in mobile
    assert ".workflow-dialog .panel-head" in mobile
    assert "flex-direction: row" in mobile
    assert "#mabang-page-import-dialog .actions" in mobile
    assert "position: static" in mobile


def test_unselected_category_fields_stay_blank_and_are_not_submitted():
    script = APP.read_text(encoding="utf-8")
    render = script[script.index("function renderStoreTemplateCategoryFields"):script.index("async function loadCategoryProfileVersions")]
    collect = script[script.index("function collectStoreTemplateCategoryValues"):script.index("const baseProductImageTypes")]

    assert 'empty.textContent = "请选择"' in render
    assert 'control.value = value == null ? "" : String(value)' in render
    assert "if (selected.length) values[fieldId] = selected" in collect
    # 「＋ 添加自选值…」哨兵值不能落库（单选/多选都要过滤）
    assert 'else if (control.value !== "" && control.value !== CUSTOM_ATTRIBUTE_OPTION) values[fieldId] = control.value' in collect


def test_stale_select_category_field_with_free_text_value_falls_back_to_text_input():
    # Old imports captured free-text Mabang attributes (条件/尺寸) as
    # single_select with empty options. When the 维护 editor re-renders such a
    # field whose stored value is not among (empty) options, it must render a
    # text input pre-filled with the value instead of an empty dropdown.
    script = APP.read_text(encoding="utf-8")
    render = script[script.index("function renderStoreTemplateCategoryFields"):script.index("async function loadCategoryProfileVersions")]

    assert "valueMissingFromOptions" in render
    assert 'selectOptions.length === 0' in render
    assert "dataset.categoryFieldInput = fieldId" in render
    assert 'control.type = field.type === "number" ? "number" : "text"' in render


def test_runtime_settings_page_is_wired_and_publish_policy_is_read_only():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    for control in [
        "settings", "settings-ai-connections", "settings-mabang-url",
        "settings-worker-max-attempts", "settings-worker-status",
        "worker-start-btn", "worker-pause-btn", "worker-restart-btn", "worker-logs-btn",
        "add-ai-connection-btn", "ai-connection-dialog", "ai-connection-protocol",
        "ai-connection-api-key", "save-ai-connection-btn",
        "settings-description-profiles", "add-description-profile-btn", "description-profile-dialog",
        "description-profile-mode", "save-description-profile-btn",
        "settings-nas-scan-mode", "settings-nas-max-files", "settings-collection-enabled",
        "settings-collection-interval", "settings-collection-max-attempts",
        "settings-nas-root", "settings-auto-focus-import", "settings-save-draft-only",
        "settings-publish-allowed", "save-settings-btn",
    ]:
        assert f'id="{control}"' in html
    assert 'id="settings-publish-allowed" type="checkbox" disabled' in html
    assert "loadRuntimeSettings" in script and 'api("/api/settings")' in script
    assert 'api("/api/worker/status")' in script
    assert 'api(`/api/worker/${action}`' in script
    assert 'api("/api/ai/model-connections"' in script
    assert 'api("/api/description-profiles")' in script
    assert "data-edit-description-profile" in script
    assert "scan_mode" in script and "interval_minutes" in script
    assert "openai_chat_completions" in script
    assert 'value="chat_completions"' in html
    assert "Chat Completions（/chat/completions）" in html
    assert "data-edit-ai-connection" in script and "rotate-key" in script
    assert 'method: "PUT"' in script
    assert "mabangBrowserPort" in script
    assert "mabangBrowserCdpUrl" in script
    assert "采集浏览器端口" in html
    assert 'id="settings-worker-auto-start"' not in html
    assert '$("settings-worker-auto-start")' not in script
    assert "worker-status-dot" in script
    assert "worker-status-running" in styles
    assert "worker-status-warning" in styles
    assert "worker-status-error" in styles
    assert 'running: { label: "运行中", tone: "running" }' in script
    assert 'paused: { label: "已暂停", tone: "warning" }' in script
    assert 'failed: { label: "运行失败", tone: "error" }' in script
    assert 'id="ai-connection-version-field" hidden' in html
    save_connection = script[script.index("async function saveAiConnection"):script.index("async function updateAiConnection")]
    assert 'api_version: ""' in save_connection
    assert "catch (error)" in save_connection and "finally" in save_connection
    assert "saveButton.disabled = false" in save_connection
    assert ".settings-layout" in styles and ".settings-group.active" in styles
    main = html[html.index("<main"):html.index("</main>")]
    assert main.index('<section id="settings"') > main.index('<section id="plan"')
    assert html.index('<section id="settings"') < html.index("</main>")


def test_mabang_auto_login_settings_are_wired():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    for control in ["settings-mabang-account", "settings-mabang-password", "settings-mabang-login-status"]:
        assert f'id="{control}"' in html
    assert 'id="settings-mabang-password" type="password"' in html
    assert 'autocomplete="new-password"' in html
    assert "自动登录账号" in html and "自动登录密码" in html
    assert "密码加密存储" in html

    render = script[script.index("function renderRuntimeSettings"):script.index("function formatStorageBytes")]
    assert 'config.mabang?.login_account' in render
    assert 'config.mabang?.login_configured' in render
    assert "已绑定自动登录账号" in render

    save = script[script.index("async function saveRuntimeSettings"):script.index("async function controlWorker")]
    assert "login_account:" in save and "login_password:" in save

    launch = script[script.index("async function launchAutomationBrowser"):script.index("async function testRuntimeConnection")]
    assert "launch.auto_login?.status" in launch
    assert "captcha_required" in launch and "no_credentials" in launch


def test_worker_controls_and_job_creation_surface_login_gate_results():
    script = APP.read_text(encoding="utf-8")
    control = script[script.index("async function controlWorker"):script.index("async function refreshWorkerLogs")]
    create_one = script[
        script.index("async function createAutomationJobForListingCopy"):
        script.index("async function saveStoreTemplateIds")
    ]
    create_many = script[
        script.index("async function createAutomationJobsFromSelection"):
        script.index("async function scanAssets")
    ]

    assert "正在准备自动化浏览器并检查马帮登录" in control
    assert "result.auto_login?.status" in control
    assert 'data.worker_start?.status === "waiting_for_login"' in create_one
    assert 'data.worker_start?.status === "waiting_for_login"' in create_many


def test_storage_cleanup_settings_are_wired():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for control in [
        "settings-storage-cleanup",
        "settings-storage-usage",
        "settings-storage-refresh-btn",
        "settings-storage-clean-btn",
        "settings-storage-auto-enabled",
        "settings-storage-temp-retention",
        "settings-storage-screenshot-retention",
        "settings-storage-export-retention",
        "settings-storage-log-retention",
    ]:
        assert f'id="{control}"' in html
    assert 'data-settings-group="storage-cleanup"' in html
    assert '/api/settings/storage/usage' in script
    assert '/api/settings/storage/cleanup' in script
    assert "last_cleanup" in script
    assert "settings-storage-clean-btn" in script
    assert ".storage-cleanup" in styles


def test_base_product_cards_offer_safe_delete():
    script = APP.read_text(encoding="utf-8")

    assert "data-delete-base-product" in script
    assert "deleteBaseProduct" in script
    assert 'method: "DELETE"' in script
    assert "历史自动化记录会保留" in script
def test_cnsc_price_paste_has_editable_site_recognition_order():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for control in [
        "cnsc-site-order-dialog", "cnsc-site-order-chips", "cnsc-site-order-save",
        "cnsc-site-order-reset", "listing-copy-cnsc-site-order", "store-template-cnsc-site-order",
        "listing-copy-cnsc-site-order-display", "store-template-cnsc-site-order-display",
    ]:
        assert f'id="{control}"' in html
    # 站点顺序用可拖动排序的站点块（不再手填文本），顺序存 localStorage。
    assert 'id="cnsc-site-order-input"' not in html
    assert "$(\"cnsc-site-order-input\")" not in script
    assert "站点顺序（拖动站点块调整顺序" in html
    assert "renderCnscSiteOrderChips" in script
    assert "attachCnscSiteChipDragHandlers" in script
    assert "cnscSiteOrderChipsOrder" in script
    assert 'class="size-chip size-chip--selected" draggable="true" data-cnsc-site=' in script
    assert ".cnsc-site-order-chips" in styles
    # Default recognition order follows the CNSC page (TW SG TH MY MX VN PH BR)
    # and differs from the editor's target order, which is the whole point.
    assert 'CNSC_DEFAULT_SITE_ORDER = ["TW", "SG", "TH", "MY", "MX", "VN", "PH", "BR"]' in script
    assert "ai_listing.cnsc_site_order" in script
    assert "orderedCnscSites" in script
    assert "saveCnscSiteOrder" in script


def test_cnsc_size_tiers_use_exclusive_chips_and_hide_raw_json():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 识别顺序按钮移到「新增价格档」右侧并缩小。
    assert html.index('id="listing-copy-cnsc-site-order"') > html.index('id="listing-copy-cnsc-add-tier"')
    assert 'id="listing-copy-cnsc-site-order" type="button" class="secondary-button mini"' in html
    assert ".secondary-button.mini" in styles

    # 按尺码分档：尺码改为互斥点选块，表格上方显示未分配提示，保存前校验覆盖。
    assert 'id="listing-copy-cnsc-tier-coverage"' in html
    assert "listingCopyCnscSizeChips" in script
    assert "toggleListingCopyCnscTierSize" in script
    assert "renderListingCopyCnscTierCoverage" in script
    assert "listingCopyCnscTierCoverageError" in script
    assert 'class="cnsc-size-chip' in script and 'data-cnsc-tier-size=' in script
    assert "cnsc-size-chip--on" in styles and ".cnsc-tier-size-chips" in styles
    # 旧的尺码自由文本输入不再渲染。
    assert 'placeholder="如 S, M, L"' not in html

    # 解析结果不再输出原始 JSON，改为友好摘要（含警告中文说明）。
    assert "cnscPriceParseSummary" in script
    assert "最后一行已自动填充剩余档位" in script
    assert "JSON.stringify(result, null, 2)" not in script


def test_cnsc_binding_mode_has_two_modes_and_batch_warehouse_stock():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 价格绑定模式精简为两种：colors（按颜色分行，每行覆盖该颜色全部尺码）
    # 与 sizes（按尺码分档）。旧的 uniform 全覆盖模式已移除。
    assert 'value="colors"' in html and 'value="sizes"' in html
    assert 'value="uniform"' not in html
    assert 'id="listing-copy-cnsc-apply-uniform"' not in html
    assert "applyListingCopyCnscUniform" not in script
    # 模式从副本持久化字段读取（不再从档位形状推断），colors 行为每颜色一档。
    assert "copy?.cnsc_binding_mode" in script
    assert "renderListingCopyCnscColorRows" in script
    assert "listingCopyCnscColorUniverse" in script
    # 每颜色行带「全覆盖」按钮：把本行价格应用到所有行。
    assert "data-listing-copy-cnsc-color-original" in script
    assert "data-listing-copy-cnsc-apply-row" in script
    assert "applyListingCopyCnscRowToAll" in script
    assert "本行价格已应用到全部颜色/款式行" in script

    # CNSC SKU 明细支持按仓库批量修改库存（泰国/广州/菲律宾），与普通统一库存互斥。
    assert 'id="listing-copy-warehouse-batch"' in html
    for code in ["TH", "CN_GZ", "PH"]:
        assert f'id="listing-copy-batch-stock-{code}"' in html
    assert 'id="listing-copy-warehouse-batch-apply"' in html
    assert "listing-copy-warehouse-batch-apply" in script
    assert "已批量更新仓库库存" in script
    assert 'warehouseBatch.hidden = !cnsc' in script and "applyStockButton.hidden = cnsc" in script
    assert ".listing-copy-warehouse-batch" in styles


def test_base_product_maintenance_prices_use_color_row_grid() -> None:
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    # Color/款式 row grid replaces the tier editor on the base maintenance PRICES section.
    assert 'id="base-price-color-table"' in html
    assert 'id="base-price-collapse-btn"' in html and 'id="base-price-collapsible"' in html
    assert "BASE_PRICES_COLLAPSED_KEY" in script and "toggleBasePriceCollapsed" in script
    assert 'id="base-price-apply-all"' in html
    assert 'id="base-price-add-color"' not in html
    assert "data-base-price-cost" in script
    # 颜色价格行保留 原价 + 售价 两个输入（售价手填尊重，留空由 %OFF 在复制时推导）。
    assert "data-base-price-sale" in script
    assert "data-base-price-original" in script
    assert "站点价格（原价 / 售价）" in html
    # No tier-editor controls remain on the base page.
    assert 'id="base-price-binding-mode"' not in html
    assert 'id="base-price-add-tier"' not in html
    assert 'id="base-price-tier-table"' not in html
    # Copy-editor parse flow is reused: same endpoint + no template dropdown.
    assert "previewBasePriceMatrix" in script
    assert '"/api/cnsc-price-matrix/preview"' in script
    assert "base-price-order-template" not in html
    assert "collectBasePriceColorRows" in script and "saveBaseImageOrder" in script
    # Size-chart section: pick image from product images + AI recognize sizes.
    # Size-chart controls now live inside the PRICES section (no standalone sizes section).
    assert 'id="maintenance-section-sizes"' not in html
    assert 'id="maintenance-section-prices"' in html
    assert 'id="base-size-chart-image"' in html
    assert 'id="base-recognize-sizes"' in html
    assert "recognize-sizes" in script and "recognizeBaseSizeChart" in script
    # Size checkboxes + reusable size templates (localStorage) + copy reminder.
    assert "data-base-size-check" in script
    assert "base-size-template-select" in html
    assert "base-size-template-save" in html
    assert "BASE_SIZE_TEMPLATES_KEY" in script and "saveCurrentSizesAsTemplate" in script
    assert "template-size-mismatch" in script and "copySelectedBaseProductToTemplates" in script


def test_base_product_list_has_category_and_time_filters():
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    # Big-chip filter bar sits above the base product cards; 时间默认选中「今天」。
    assert 'id="base-product-filter-category"' in html
    assert 'id="base-product-filter-time"' in html
    assert 'data-base-time-filter="today"' in html
    assert 'data-base-time-filter="yesterday"' in html
    assert 'data-base-time-filter="week"' in html
    # 「全部」不限时间窗，和 分类 行的「全部」一样是常驻入口。
    assert 'data-base-time-filter="all"' in html
    assert "base-product-cards" in html
    for label in ["今天", "昨天", "最近一周", "全部", "分类", "时间"]:
        assert label in html
    assert "chip--active" in html

    # Client-side chip wiring + predicates exist.
    assert "baseProductCategoryFilter" in script
    assert "baseProductTimeFilter" in script
    assert 'baseProductTimeFilter: "today"' in script  # 默认今天
    assert "BASE_PRODUCT_TIME_FILTER_LABELS" in script
    assert "baseProductTimeFilterLabel" in script
    assert "matchesBaseProductFilters" in script
    assert "dateFallsInPreset" in script
    # 时间窗判定要显式放行「全部」，不能靠 fallthrough 的默认 true。
    assert 'if (!preset || preset === "all") return true;' in script
    assert "renderBaseProductFilterBar" in script
    assert "setBaseTimeFilter" in script
    assert "toggleBaseCategoryFilter" in script
    assert "data-base-time-filter" in script
    assert "data-base-category-filter" in script


def test_selected_product_card_keeps_highlight_on_hover():
    """选中(.selected)/勾选(.checked)的商品卡片 hover 时不能被 kraft 主题的
    hover 边框盖掉：kraft 的 .product-card:hover{border-color:kraft-line} 写在
    .selected 之后会覆盖 indigo 边框 → 已改为 :not(.selected):not(.checked)。"""
    styles = STYLES.read_text(encoding="utf-8")

    # kraft hover 边框规则必须豁免选中/勾选卡片（顺序靠后、同特异性会覆盖高亮）
    assert ".product-card:hover:not(.selected):not(.checked)" in styles
    # 原始全覆盖版本不应存在
    assert ".product-card:hover { border-color: var(--kraft-line); }" not in styles
    # 选中/勾选高亮本身仍在
    assert ".product-card.selected" in styles
    assert ".product-card.checked" in styles


def test_card_single_select_also_checks_the_card():
    """点卡片以勾选状态为准 toggle：
    - 未勾选的卡 → 勾选 + 设为单选（复选框同步勾上）
    - 已勾选的卡再点 → 取消勾选；若它恰是当前单选卡，单选一并取消
    （点 A 勾选 → 点 B → 再点 A：A 取消勾选、单选留在 B）"""
    script = APP.read_text(encoding="utf-8")

    handler_start = script.index("card.addEventListener")
    # 以勾选状态为准：先判 has(product.id)
    assert "state.selectedBaseProducts.has(product.id)" in script[handler_start:]
    # 已勾选 → 从勾选集合移除；若还是当前单选卡则清单选
    assert "state.selectedBaseProducts.delete(product.id);" in script[handler_start:]
    assert 'state.selectedBaseProductId = "";' in script[handler_start:]
    # 未勾选 → 加入勾选集合 + 设为单选
    idx_add = script.index("state.selectedBaseProducts.add(product.id);", handler_start)
    idx_sel = script.index("state.selectedBaseProductId = product.id;", idx_add)
    assert idx_sel > idx_add
    # 勾选框仍由 change 委托独立处理多选，不能被卡片点击破坏
    assert "target.dataset.baseProductSelect" in script


def test_multi_select_category_fields_offer_add_custom_value() -> None:
    """现场：型号 在模板里是 multi_select（下拉），但「＋ 添加自选值」只对单选做过。

    现在多选也有该入口，且自选值会被自动化当作"枚举外的自选值"走马帮「添加自填选项」。
    """

    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert "category-field-custom-btn" in script
    assert 'customButton.textContent = "＋ 添加自选值";' in script
    assert 'if (field.type === "multi_select") {' in script
    # 自选值 = 替换当前选择（枚举里没有我要的）
    assert "[...control.options].forEach((option) => { option.selected = false; });" in script
    # 「＋ 添加自选值…」哨兵值不能被当成属性值存进模板/商品
    assert script.count(".filter((item) => item && item !== CUSTOM_ATTRIBUTE_OPTION)") == 2
    assert 'control.value !== CUSTOM_ATTRIBUTE_OPTION' in script
    assert ".category-field-custom-btn" in styles


def test_copy_template_picker_has_site_and_cnsc_cards_grouped_by_store() -> None:
    """现场要求：复制到店铺模板用两张卡片切换——左边本土（默认），点右边切跨境；模板按店铺分组。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    # 两张卡片 + 默认本土激活
    assert 'id="copy-template-mode-site"' in html and 'id="copy-template-mode-cnsc"' in html
    assert 'data-copy-template-mode="site"' in html and 'data-copy-template-mode="cnsc"' in html
    assert html.index('id="copy-template-mode-site"') < html.index('id="copy-template-mode-cnsc"'), "本土卡片在左"
    assert 'class="copy-template-mode-card is-active" id="copy-template-mode-site"' in html, "默认选中本土"
    assert 'data-copy-template-mode="cnsc"' in html and 'aria-selected="false"' in html

    # 模式切换 + 按模式过滤 + 按店铺分组
    assert "function copyTemplatePickerMode()" in script
    assert 'return state.copyTemplatePickerMode === "cnsc" ? "cnsc" : "site";' in script
    assert "function templateMatchesPickerMode(template, mode)" in script
    # 跨境模式开关（界面显示）：关闭时强制本土模式并隐藏跨境卡片。
    assert 'const mode = crossBorderModeEnabled() ? copyTemplatePickerMode() : "site";' in script
    assert '$("copy-template-mode-cnsc")?.toggleAttribute("hidden", !crossBorderModeEnabled())' in script
    assert "template-picker-group" in script and "template-picker-group-head" in script
    assert 'const key = String(item.store_name || "未指定店铺");' in script
    assert 'copyModeCard.dataset.copyTemplateMode' in script
    assert "copy-template-mode-card.is-active" in styles


def test_store_template_card_can_be_duplicated_in_place() -> None:
    """卡片「复制」= 同店再生成一份「- 副本」并直接进编辑器，不走换店铺的 /copy。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'data-copy-store-template="${escapeHtml(template.id)}">复制</button>' in script
    assert "async function copyStoreTemplate(templateId)" in script
    assert "/duplicate`" in script
    assert "已复制模板" in script
    assert "data-copy-store-template" in script
    # 复制入口要和维护/删除一起出现在卡片操作区里。
    actions = script[script.index('<div class="template-card-actions">'):script.index("</div>", script.index('<div class="template-card-actions">'))]
    assert "data-edit-store-template" in actions
    assert "data-copy-store-template" in actions
    assert "data-delete-store-template" in actions
    # 缓存版本要让浏览器拿到新脚本。
    assert "feature=store-template-duplicate-v1" in html


def test_store_templates_are_scoped_to_a_mabang_account() -> None:
    """模板按马帮账号隔离：页面切账号，店铺下拉与列表只列该账号。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'id="store-template-account"' in html
    assert "马帮账号" in html
    assert "feature=store-template-account-v1" in html
    for behavior in [
        "currentStoreTemplateAccountId",
        "renderStoreTemplateAccountOptions",
        "switchStoreTemplateAccount",
        "storeTemplateScopedStoreOptions",
        "storeTemplateById",
        "storeTemplatesAll",
    ]:
        assert behavior in script, behavior
    # 列表请求带账号；保存模板带账号；店铺下拉按账号收窄。
    assert "/api/store-templates${scopedQuery}" in script
    assert "account_id: currentStoreTemplateAccountId()" in script
    assert "state.storeTemplatesAll = all.items || [];" in script
    assert "template-card-account" in script and ".template-card-account" in styles
    assert ".template-account-field" in styles

