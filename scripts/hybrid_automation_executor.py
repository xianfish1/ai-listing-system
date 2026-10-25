"""
混合自动化执行器（Playwright 为主，Browser-Use AI 仅做诊断）

设计原则（安全第一）：
1. Playwright 是唯一真正执行浏览器操作并产出“已保存”证据的执行器。
2. 当 Playwright 因页面结构类错误失败时，调用 AI 做【诊断】，把建议选择器
   记录到 data/learned_selectors.json，供人工/后续 Playwright 改进使用。
3. AI 诊断【绝不】改变执行结果，也【绝不】声称任务已保存：execute_save_draft
   永远原样返回 Playwright 的执行字典（含 error / system_failure 等字段），
   让 Worker 的 is_proven_saved_execution() 与 SYSTEM_FAILURE_ERRORS 逻辑
   在未被污染的数据上判定。这样系统失败仍能正确“暂停(paused)”。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import browser_use_runner
from scripts.browser_use_adaptive_executor import BrowserUseAdaptiveExecutor
from scripts.browser_harness import BrowserHarness

# 触发 AI 诊断的错误类型（页面结构类失败，选择器建议可能有帮助）
BROWSER_USE_FALLBACK_ERRORS = frozenset({
    "page_structure_changed",
    "exact_action_not_unique",
    "field_not_unique",
    "button_not_found_with_any_selector",
    "save_button_not_unique",
    "workspace_frame_not_unique",
    "mabang_detail_iframe_not_found",
    "category_dialog_not_visible",
    "trade_info_table_not_unique",
    "discount_dialog_not_visible",
    "cnsc_target_site_not_found",
    "cnsc_site_picker_not_ready",
    "playwright_timeout",
})

# 需要人工处理、不触发 AI 诊断的错误
NO_FALLBACK_ERRORS = frozenset({
    "needs_manual_login_or_verification",
    "publish_click_refused",
    "unsafe_or_incomplete_save_plan",
})


class HybridAutomationExecutor:
    """混合执行器：Playwright 执行 + AI 诊断（诊断不改变结果）。"""

    def __init__(
        self,
        *,
        enable_browser_use_fallback: bool = True,
        max_browser_use_cost: float = 0.25,
        cdp_url: str = "http://127.0.0.1:9223",
        playwright_executor: Any = None,
    ):
        """
        Args:
            enable_browser_use_fallback: 是否在失败时启用 AI 诊断
            max_browser_use_cost: AI 诊断单次最大成本（美元）
            cdp_url: Chrome DevTools Protocol URL
            playwright_executor: Playwright 执行函数（可注入，便于测试）。
                默认使用 browser_use_runner.run_playwright_save_draft。
        """
        self.enable_diagnosis = enable_browser_use_fallback
        self.max_cost = max_browser_use_cost
        self.cdp_url = cdp_url
        self.playwright_executor = playwright_executor or browser_use_runner.run_playwright_save_draft
        self.diagnoser: BrowserUseAdaptiveExecutor | None = None
        self.harness: BrowserHarness | None = None

        if self.enable_diagnosis:
            try:
                self.diagnoser = BrowserUseAdaptiveExecutor()
                self.harness = BrowserHarness(ai_executor=self.diagnoser)
                print(f"✓ Browser-Use AI 诊断已启用（最大成本: ${max_browser_use_cost}）")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Browser-Use AI 诊断初始化失败: {exc}")
                print("   将仅使用 Playwright 执行（不做 AI 诊断）")
                self.enable_diagnosis = False

    def execute_save_draft(
        self,
        task_plan: dict[str, Any],
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        """执行保存草稿任务。

        始终【原样返回】Playwright 执行结果字典。当遇到页面结构类失败时，
        额外触发 AI 诊断（best-effort），把建议选择器写入 learned_selectors.json，
        但【不会】修改返回结果，也不会声称任务已保存。
        """
        job_id = task_plan.get("job_id", "unknown")
        print(f"🚀 混合执行任务: {job_id}")

        execution = self.playwright_executor(task_plan, cdp_url=self.cdp_url, timeout_seconds=timeout_seconds, preserve_detail_on_failure=True)

        # 成功：原样返回，交由 Worker 用真实证据判定“已保存”
        if execution.get("ok"):
            print("✅ Playwright 执行成功")
            return execution

        error = str(execution.get("error") or "")
        print(f"⚠️ Playwright 执行失败: {error or '未知错误'}")

        # 仅对页面结构类错误做 AI 诊断（best-effort，不改变返回结果）
        if error in BROWSER_USE_FALLBACK_ERRORS and self.enable_diagnosis and self.harness is not None:
            failed_stage = str(execution.get("failed_stage") or "")
            if failed_stage:
                takeover = self.harness.takeover(
                    task_plan, playwright_error=error, failed_stage=failed_stage,
                    cdp_url=self.cdp_url, max_cost=self.max_cost,
                )
                if takeover.get("ok"):
                    resumed = self.playwright_executor(
                        task_plan, cdp_url=self.cdp_url, timeout_seconds=timeout_seconds,
                        resume_from_stage=failed_stage,
                    )
                    return {**resumed, "harness": takeover}
                return {**execution, "status": "harness_blocked", "harness": takeover}

        # 无论是否诊断，都原样返回 Playwright 的失败结果，
        # 保留 error / system_failure 等字段，确保 Worker 能正确暂停。
        return execution

    def _run_diagnosis(self, task_plan: dict[str, Any], playwright_error: str) -> None:
        """运行 AI 诊断并记录建议选择器。诊断失败不影响主流程。"""
        print("🔎 触发 Browser-Use AI 诊断（记录建议选择器，不操作浏览器）...")
        try:
            diagnosis = self.diagnoser.diagnose_failure(
                task_plan=task_plan,
                playwright_error=playwright_error,
                max_cost=self.max_cost,
            )
            if diagnosis.get("ok"):
                learned = diagnosis.get("learned_selectors", {})
                print(f"   诊断完成，记录 {len(learned)} 组建议选择器")
            else:
                print(f"   诊断未成功: {diagnosis.get('diagnosis', 'unknown')}")
        except Exception as exc:  # noqa: BLE001
            print(f"   诊断异常（忽略，不影响主流程）: {exc}")

    def get_stats(self) -> dict[str, Any]:
        """返回执行器配置统计。"""
        return {
            "diagnosis_enabled": self.enable_diagnosis,
            "max_cost": self.max_cost,
            "fallback_errors": list(BROWSER_USE_FALLBACK_ERRORS),
            "no_fallback_errors": list(NO_FALLBACK_ERRORS),
        }


def main():
    """命令行测试。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="混合自动化执行器测试")
    parser.add_argument("--task-plan", required=True, help="任务计划 JSON 文件路径")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223", help="Chrome CDP URL")
    parser.add_argument("--no-fallback", action="store_true", help="禁用 AI 诊断")
    parser.add_argument("--max-cost", type=float, default=0.25, help="AI 诊断最大成本")
    parser.add_argument("--timeout", type=int, default=900, help="超时时间（秒）")
    args = parser.parse_args()

    task_plan_path = Path(args.task_plan)
    if not task_plan_path.exists():
        print(f"错误: 任务计划文件不存在: {task_plan_path}")
        sys.exit(1)

    task_plan = json.loads(task_plan_path.read_text(encoding="utf-8"))
    executor = HybridAutomationExecutor(
        enable_browser_use_fallback=not args.no_fallback,
        max_browser_use_cost=args.max_cost,
        cdp_url=args.cdp_url,
    )
    result = executor.execute_save_draft(task_plan=task_plan, timeout_seconds=args.timeout)

    print("\n" + "=" * 60)
    print("执行结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
