"""UI：执行详情在弹窗里；面板「阶段」列只显示当前步骤（步骤 N · 名字）。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "web" / "app.js"
INDEX = ROOT / "apps" / "web" / "index.html"
STYLES = ROOT / "apps" / "web" / "styles.css"


def test_panel_shows_only_current_step_not_the_full_step_list() -> None:
    """现场澄清：外面（面板）只显示阶段=当前步骤，详细步骤只在「执行详情」弹窗里看。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    # 面板里不再有执行中步骤卡片
    assert "automation-live-card" not in html
    assert "renderAutomationLiveCard" not in script
    # 阶段列显示「步骤 N · 阶段名」
    assert "function automationCurrentStepLabel(job)" in script
    assert "return `步骤 ${steps.length} · ${label}`;" in script
    cell = [line for line in script.splitlines() if "automationCurrentStepLabel(job) || TASK_STATUS_LABELS" in line]
    assert cell, "阶段列应优先渲染当前步骤"
    assert "<th>阶段</th>" in html


def test_detail_dialog_hosts_steps_and_manual_confirmation() -> None:
    """弹窗里显示执行步骤（运行中优先实时步骤）+ 人工确认按钮；外面不摆这些按钮。"""

    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    dialog_start = html.index('id="automation-job-detail-dialog"')
    dialog = html[dialog_start:]
    assert 'id="automation-job-detail-steps"' in dialog
    assert 'id="automation-manual-saved-btn"' in dialog
    assert 'id="automation-manual-failed-btn"' in dialog
    assert 'id="automation-manual-note"' in dialog
    # 面板（弹窗之外）没有这些按钮
    panel = html[:dialog_start]
    assert "automation-manual-saved-btn" not in panel

    # 弹窗渲染：运行中优先 live_steps，跑完回退 steps；并展示人工确认记录
    details = script[script.index("function renderAutomationJobDetails(jobId)"):]
    details = details[: details.index("\n}\n")]
    assert "result.live_steps" in details
    assert "manual_confirmation" in details

    # 人工标记按钮作用于弹窗里正在看的那条作业
    assert 'markAutomationJobManually(state.openAutomationJobId || "", "saved")' in script
    assert 'markAutomationJobManually(state.openAutomationJobId || "", "failed")' in script

    styles = STYLES.read_text(encoding="utf-8")
    assert ".automation-manual-finish-actions" in styles


def test_dialog_layout_timeline_collapsed_at_bottom_and_sticky_confirm_bar() -> None:
    """现场要求：执行时间线默认收起并放到最下面；人工确认做成常驻底部确认栏（随时可确认）。"""

    html = INDEX.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    dialog_start = html.index('id="automation-job-detail-dialog"')
    dialog_end = html.index("</dialog>", dialog_start)
    dialog = html[dialog_start:dialog_end]

    # 时间线在 <details> 里（默认收起），且排在步骤/子步骤之后
    assert "<details class=\"automation-job-detail-section automation-job-detail-timeline-section\">" in dialog
    assert "<summary>执行时间线（默认收起，点开查看）</summary>" in dialog
    assert dialog.index("automation-job-detail-timeline") > dialog.index("automation-job-detail-stages")
    assert "<details" in dialog and "open" not in dialog.split("<details")[1].split(">")[0]

    # 人工确认是底部 footer 确认栏，且是弹窗最后一块
    assert 'class="automation-manual-finish"' in dialog
    assert dialog.rstrip().endswith("</footer>")
    assert "automation-manual-note" in dialog

    # 常驻底部：弹窗本身是滚动容器，用 sticky 固定确认栏
    assert ".automation-job-detail-dialog .automation-manual-finish {" in styles
    sticky = styles[styles.index(".automation-job-detail-dialog .automation-manual-finish {"):]
    sticky = sticky[: sticky.index("}")]
    assert "position: sticky" in sticky and "bottom: 0" in sticky


def test_media_stages_render_from_status_not_only_ok() -> None:
    """现场：模板默认视频其实附加成功了（status=attached），执行步骤却显示成未完成。

    执行器媒体阶段回的是 status（attached/skipped/warning），runner 现在连 code 一起
    透传；界面要按 status 三态渲染，不能只看 ok。"""

    script = (ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'MEDIA_DONE_STATUSES = new Set(["attached", "uploaded", "already_attached"' in script
    assert 'const skipped = stepStatus === "skipped";' in script
    assert 'step.ok === true || MEDIA_DONE_STATUSES.has(stepStatus)' in script
    # 媒体子步骤的那几种状态词必须有中文呈现，否则会显示灰色的英文原词。
    for status, label in [
        ("attached", "已附加"),
        ("uploaded", "已上传"),
        ("already_attached", "已存在"),
    ]:
        assert f'{status}: {{ tone: "done", label: "{label}" }}' in script


def test_runner_forwards_media_status_and_code_into_steps() -> None:
    """只透传 ok 会让界面拿不到 attached/skipped，也拿不到失败原因码。"""
    runner = (ROOT / "scripts" / "browser_use_runner.py").read_text(encoding="utf-8")

    assert '"status": str(event.get("status") or ""),' in runner
    assert '"code": str(event.get("code") or ""),' in runner
