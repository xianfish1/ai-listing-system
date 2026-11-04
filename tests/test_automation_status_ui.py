from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_automation_table_shows_worker_runtime_and_save_evidence() -> None:
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    # Trimmed 13-column table down to 9: Worker/Lease/尝试次数/类型 moved into a
    # tooltip on the "更新时间" cell so the row stays scan-friendly. The tooltip
    # is still built from the same underlying fields.
    for label in ["店铺 / 主 SKU", "状态", "阶段", "缺口", "最后错误", "保存证据", "更新时间", "操作"]:
        assert label in html
    for field in [
        "current_phase",
        "attempt_count",
        "worker_id",
        "lease_expires_at",
        "last_error",
        "save_clicked",
        "publish_clicked",
    ]:
        assert field in script
    assert "已保存 / 未发布" in script
    assert 'colspan="9"' in script
    assert "自动领取" in html
    assert "只保存草稿" in html
    assert "发布按钮" not in html


def test_worker_activity_shows_the_page_import_standby_state() -> None:
    """「从马帮当前页导入」期间 Worker 让路：界面要说清楚是它主动让路，别让人以为卡了。"""

    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'waiting_for_page_import: { label: "页面导入中 · 已让路"' in script
    assert 'if (activity === "waiting_for_page_import")' in script
    # 让路状态不能被「自愈」当成卡死重启（重启会打断导入）
    assert script.index('if (activity === "waiting_for_page_import")') > script.index(
        'if (activity === "waiting_for_browser")'
    )


def test_automation_table_shows_saved_with_warnings_and_media_stages() -> None:
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'saved_with_warnings: "已保存（有提醒）"' in script
    assert 'new Set(["saved", "saved_with_warnings"])' in script
    for stage in ["apply_main_image_watermark", "apply_product_image_watermark", "video_upload"]:
        assert stage in script


def test_automation_timeline_labels_the_description_media_stage() -> None:
    """描述插图阶段（尺码表 + 全部产品图）要在执行时间线里有中文标签。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'append_description_media: "正在追加描述图片（尺码表 + 产品图）"' in script


def test_automation_task_details_show_persisted_execution_timeline() -> None:
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert "执行详情" in html
    assert 'id="automation-job-detail-dialog"' in html
    assert 'data-automation-job-details' in script
    assert "openAutomationJobDetails" in script
    assert "run_log" in script
    assert "result.stages" in script


def test_automation_table_offers_cancel_and_delete_actions() -> None:
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert "data-cancel-automation-job" in script
    assert "data-delete-automation-job" in script
    assert "cancelAutomationJob" in script
    assert "deleteAutomationJob" in script
    assert "/cancel" in script
    assert 'method: "DELETE"' in script


def test_top_navigation_separates_listing_copies_and_automation_jobs() -> None:
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'data-automation-panel="copies"' in html
    assert 'data-automation-panel="jobs"' in html
    assert "showAutomationPanel" in script
    automation = html[html.index('<section id="automation"'):html.index('<section id="file-management"')]
    assert automation.count('data-automation-panel="copies"') == 1
    assert automation.count('data-automation-panel="jobs"') == 1


def test_automation_tables_localize_timestamps_and_error_codes() -> None:
    """自动化任务表与详情把原始 UTC 时间戳显示成本地可读时间、
    英文错误码显示成中文说明（原文保留在 title）。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    # 时间本地化
    assert "formatLocalTime" in script
    assert "formatLocalTime(updatedAt)" in script
    # 错误码中文化映射（关键码必须有中文说明）
    assert "AUTOMATION_ERROR_LABELS" in script
    for code, label in [
        ("save_result_not_proven", "无法确认草稿已保存"),
        # 马帮拒绝保存的提示（描述图片宽度 < 700）必须有自己的中文说明，
        # 不能只剩「无法确认草稿已保存」这种看不出原因的兜底文案。
        ("description_image_width_too_small", "商品描述里的图片宽度小于"),
        # 保存前的自动修复（编辑图片 → 全选 → 1000px → 更改）也要有中文说明与步骤名。
        ("description_image_width_fix_failed", "描述图片自动修复没成功"),
        ("description_image_editor_not_found", "没找到「编辑图片」入口"),
        ("pre_save_validation_failed", "保存前校验未通过"),
        ("cnsc_payload_invalid", "CNSC 刊登内容校验未通过"),
        ("watermark_unconfigured", "水印未配置"),
        ("video_store_not_found", "未找到店铺视频"),
    ]:
        assert code in script
        assert label in script
    assert "automationErrorLabel(lastError)" in script
    # 表格错误格的提醒允许换行完整显示，不被单行省略截断
    assert ".automation-job-error-cell small" in styles
    assert "white-space: normal" in styles


def test_listing_copies_table_truncates_long_cells_and_keeps_actions_inline() -> None:
    """店铺刊登副本表：模板/店铺列限宽省略（否则「店铺名 - 类目」长串在窄列
    折行把整行撑到 200px+），操作按钮/状态徽章强制横排不竖排堆叠。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    # 模板列与店铺列带限宽省略 class + title（悬停看全名）
    assert "listing-copy-template-cell" in script
    assert "listing-copy-store-cell" in script
    assert ".listing-copy-template-cell" in styles
    assert ".listing-copy-store-cell" in styles
    assert "listing-copy-template-cell" in styles and "text-overflow: ellipsis" in styles
    # 操作按钮与状态标签横排
    assert ".table-actions" in styles and "white-space: nowrap" in styles
    assert ".row-status-label" in styles and "white-space: nowrap" in styles


def test_listing_copies_panel_has_select_all_button_below_batch_button() -> None:
    """副本面板头部：批量生成按钮下方有「全选新建」按钮 + 提示文字；
    全选可 toggle，按钮文案随勾选数变化（全选新建/取消全选新建/全选新建（已选 N））。"""
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'id="select-all-listing-copies-btn"' in html
    # 全选按钮在批量生成按钮之后（下方）
    batch_idx = html.index('id="batch-create-automation-jobs-btn"')
    select_idx = html.index('id="select-all-listing-copies-btn"')
    assert select_idx > batch_idx
    # 提示文字说明范围：只勾新建；已完成按时间分组可折叠
    assert "只勾选还没跑过、也没在执行/排队的副本" in html
    # 全选旁边还有批量删除副本（勾选后可一次删掉，正在执行的由后端跳过）。
    assert 'id="delete-selected-listing-copies-btn"' in html
    assert "批量删除副本" in html
    assert "已完成区按完成时间分组" in html
    # toggle 逻辑与文案更新函数存在
    assert "toggleSelectAllListingCopies" in script
    assert "updateListingCopySelectAllButton" in script
    assert "取消全选新建" in script
    # 勾选结果反馈（不再是「已全选 N 个副本」——全选只覆盖新建区）
    assert "已勾选" in script
    # 头部操作区上下布局样式
    assert ".listing-copy-head-actions" in styles
    assert ".listing-copy-head-actions-sub" in styles


def test_copy_and_automation_tables_group_new_failed_and_completed_tasks() -> None:
    """副本与自动化列表按新建、失败、已完成分段；已完成用横向分隔带收束。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    assert "TASK_SECTION_ORDER" in script
    assert 'label: "新建"' in script
    assert 'label: "失败"' in script
    assert 'label: "已完成"' in script
    assert "automationTaskSection(job)" in script
    assert "listingCopyTaskSection(job)" in script
    assert 'class="task-section-row task-section-row--${section.id}"' in script
    assert '.task-section-row--completed' in styles
    assert "border-top" in styles


def test_open_automation_detail_refreshes_live_through_save_evidence() -> None:
    """自动化详情打开时随 3 秒轮询更新，并把保存证据作为执行步骤末项。"""
    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="automation-job-detail-live"' in html
    assert "openAutomationJobId" in script
    assert "refreshOpenAutomationJobDetails()" in script
    assert "renderAutomationJobDetails(jobId)" in script
    poll_body = script[script.index("async function pollAutomationLive") : script.index("function startAutomationLivePoll")]
    assert "renderStoreListingCopies();" in poll_body
    assert 'automation-step-item--evidence' in script
    assert '保存证据' in script
    assert '已确认保存草稿，且未触发发布' in script


def test_copy_and_automation_panel_switch_updates_page_title() -> None:
    """副本/自动化共用工作区时，顶部标题跟随当前任务区切换。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    show_panel = script[script.index("function showAutomationPanel") : script.index("let operationFeedbackTimer")]
    assert 'const title = $("view-title")' in show_panel
    assert 'title.textContent = id === "copies" ? "店铺刊登副本" : "自动化"' in show_panel


def test_listing_copy_task_detail_button_opens_detail_dialog() -> None:
    """详情弹窗只由详情触发器打开：副本行的「任务详情」按钮不被行点击保护吞掉，
    任务行的「再次尝试」等操作按钮也不能顺带弹出执行详情。"""
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    handler = script[script.index('const automationJobDetails = target.closest'):script.index('const cancelAutomationJobButton = target.closest')]

    assert 'target.closest("[data-automation-job-details]")' in handler
    assert "openAutomationJobDetails" in handler
    # 任务表把 data-automation-job-details 挂在 <tr> 上，行内每个按钮的 closest() 都会
    # 命中整行；放行条件必须是"没点到交互控件"或"点到的是详情触发器本身"，
    # 否则点一次「再次尝试」会先弹一遍执行详情。
    assert 'const control = target.closest("button, a, input, select")' in handler
    assert "if (!control || control === automationJobDetails)" in handler


def test_generating_automation_jobs_shows_progress_and_a_refresh_button() -> None:
    """现场反馈：点「生成自动化」没有反馈、也不知道在生成中。"""

    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    for required_id in ["refresh-automation-view-btn", "refresh-automation-jobs-btn"]:
        assert f'id="{required_id}"' in html, f"缺少刷新按钮 {required_id}"
    assert "async function refreshAutomationView()" in script
    assert "已刷新（" in script

    # 生成中：按钮上跑秒 + 禁用，结束后恢复
    assert "function startJobGenerationFeedback(button, { label = \"生成自动化任务\" } = {})" in script
    assert "target.textContent = `${label}… 已 ${seconds}s`;" in script
    assert 'startJobGenerationFeedback($("batch-create-automation-jobs-btn"), { label: "批量生成" })' in script
    assert 'startJobGenerationFeedback($("listing-copy-create-job"), { label: "生成自动化任务" })' in script
    assert "正在生成 ${ids.length} 个自动化任务…" in script
    assert 'target.id === "refresh-automation-view-btn" || target.id === "refresh-automation-jobs-btn"' in script


def test_refresh_buttons_are_unique_and_sit_left_of_the_batch_button() -> None:
    """现场反馈：副本面板出现了两个「刷新」；并把刷新放到「批量生成自动化任务」左侧。

    这条防的是补丁重复插入（同一个 id 出现两次）。
    """

    html = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    assert html.count('id="refresh-automation-view-btn"') == 1, "副本面板只允许一个刷新按钮"
    assert html.count('id="refresh-automation-jobs-btn"') == 1, "任务面板只允许一个刷新按钮"

    main_block = html[html.index('class="listing-copy-head-actions-main"'): html.index('class="listing-copy-head-actions-sub"')]
    assert 'id="refresh-automation-view-btn"' in main_block
    assert 'id="batch-create-automation-jobs-btn"' in main_block
    assert main_block.index('id="refresh-automation-view-btn"') < main_block.index('id="batch-create-automation-jobs-btn"'), "刷新要在批量生成左侧"

    assert ".listing-copy-head-actions-main {" in styles
    row = styles[styles.index(".listing-copy-head-actions-main {"):]
    row = row[: row.index("}")]
    assert "display: flex" in row and "justify-content: flex-end" in row

    # 现场要求（2026-10-01）：撤掉 `position: absolute; right: 230px` 那条定位覆盖，
    # 刷新按钮回到 flex 行内排布——所以这个 id 不该再有单独的样式规则。
    assert "#refresh-automation-view-btn" not in styles
    assert "right: 230px" not in styles


def test_automation_jobs_are_collapsible_and_grouped_by_time() -> None:
    """现场要求：自动化任务列表也做折叠，并按照时间分类。"""

    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    # 通用分桶（副本列表与任务列表共用）
    assert "function bucketByTime(items, stampOf, keyPrefix)" in script
    assert 'key: `${keyPrefix}:${bucket.id}`' in script

    # 任务列表：分区标题带 jobs: 前缀（可折叠，且与副本列表的折叠状态互不影响）
    jobs_render = script[script.index("function renderAutomationJobs()"):]
    jobs_render = jobs_render[: jobs_render.index("\nasync function cancelAutomationJob")]
    assert "const sectionKey = `jobs:${section.id}`;" in jobs_render
    assert "appendTaskSectionRow(tbody, section, jobs.length, 9, { collapsed: sectionCollapsed, key: sectionKey });" in jobs_render
    assert "if (sectionCollapsed) continue;" in jobs_render
    assert "const jobGroups = bucketByTime(jobs, (job) => job.updated_at || job.created_at, sectionKey);" in jobs_render
    assert "appendCopyBucketRow(tbody, group, 9, groupCollapsed);" in jobs_render

    # 折叠点击按前缀重画对应列表
    assert 'if (String(key).startsWith("jobs:")) renderAutomationJobs();' in script
    assert "else renderStoreListingCopies();" in script

    # 分区标题行支持自定义 key；分桶行按 items/copies 计数
    assert 'data-copy-section-collapse="${escapeHtml(options.key || section.id)}"' in script
    assert "(group.items || group.copies || []).length" in script
    assert ".copy-bucket-row" in styles


def test_automation_jobs_completed_section_collapses_on_first_open_and_sticks() -> None:
    """已完成区默认收起：只在首次访问播种一次，之后以操作员的手动展开/折叠为准。"""

    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'const JOBS_COLLAPSE_SEEDED_KEY = "ai_listing.automation_jobs_collapsed_seeded";' in script
    seeding = script[script.index("function ensureJobsCollapseDefaults()"):]
    seeding = seeding[: seeding.index("\n}\n")]
    assert 'keys.add("jobs:completed");' in seeding, "首次访问把已完成区种为收起"
    assert 'window.localStorage.setItem(JOBS_COLLAPSE_SEEDED_KEY, "1");' in seeding, "只播种一次"
    assert "if (window.localStorage.getItem(JOBS_COLLAPSE_SEEDED_KEY)) return;" in seeding

    jobs_render = script[script.index("function renderAutomationJobs()"):]
    jobs_render = jobs_render[: jobs_render.index("\nasync function cancelAutomationJob")]
    assert "ensureJobsCollapseDefaults();" in jobs_render, "渲染任务列表前播种默认折叠"


def test_automation_job_row_shows_main_image_next_to_sku() -> None:
    """现场要求：自动化任务列表里「主 SKU」旁边要能看到商品主图。

    取图复用副本列表那套 listingCopyThumbUrl()（副本快照 main_image → 基础商品主图），
    取不到时留虚线占位框，保证行内文字起点对齐。
    """

    script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "apps" / "web" / "styles.css").read_text(encoding="utf-8")

    jobs_render = script[script.index("function renderAutomationJobs()"):]
    jobs_render = jobs_render[: jobs_render.index("\nasync function cancelAutomationJob")]
    assert "const thumbUrl = listingCopyThumbUrl(copy);" in jobs_render
    assert 'class="automation-job-thumb"' in jobs_render
    assert "automation-job-thumb--empty" in jobs_render
    assert 'class="automation-job-identity-main"' in jobs_render
    assert 'class="automation-job-identity-text"' in jobs_render
    # 主 SKU 仍在同一个身份单元格里（图是「旁边」加，不是把 SKU 换成图）。
    identity_cell = jobs_render[jobs_render.index('class="automation-job-identity-cell"'):]
    identity_cell = identity_cell[: identity_cell.index("</td>")]
    assert "copy.main_sku" in identity_cell
    assert "${thumbHtml}" in identity_cell

    assert ".automation-job-identity-main {" in styles
    assert ".automation-job-thumb {" in styles
    assert ".automation-job-thumb--empty {" in styles
