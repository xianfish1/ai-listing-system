from scripts.hybrid_automation_executor import HybridAutomationExecutor


class FakeHarness:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def takeover(self, task_plan, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_hybrid_executor_takes_over_once_then_resumes_from_failed_stage():
    calls = []

    def fake_playwright(task, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "ok": False, "status": "blocked", "error": "cnsc_target_site_not_found",
                "failed_stage": "select_cnsc_target_sites", "publish_clicked": False,
            }
        assert kwargs["resume_from_stage"] == "select_cnsc_target_sites"
        return {
            "ok": True, "status": "saved", "save_clicked": True,
            "publish_clicked": False, "page_closed": True,
            "close_method": "success_dialog", "exit_code": 0,
        }

    executor = HybridAutomationExecutor(enable_browser_use_fallback=False, playwright_executor=fake_playwright)
    executor.enable_diagnosis = True
    executor.harness = FakeHarness({"ok": True, "status": "recovered", "trace": []})
    result = executor.execute_save_draft({"job_id": "job-1"})

    assert result["status"] == "saved"
    assert len(calls) == 2
    assert calls[0]["preserve_detail_on_failure"] is True
    assert executor.harness.calls[0]["failed_stage"] == "select_cnsc_target_sites"


def test_hybrid_executor_keeps_failure_blocked_when_takeover_is_rejected():
    def fake_playwright(task, **kwargs):
        return {
            "ok": False, "status": "blocked", "error": "cnsc_target_site_not_found",
            "failed_stage": "select_cnsc_target_sites", "publish_clicked": False,
        }

    executor = HybridAutomationExecutor(enable_browser_use_fallback=False, playwright_executor=fake_playwright)
    executor.enable_diagnosis = True
    executor.harness = FakeHarness({"ok": False, "status": "harness_blocked", "error": "forbidden_action"})
    result = executor.execute_save_draft({"job_id": "job-1"})

    assert result["status"] == "harness_blocked"
    assert result["publish_clicked"] is False
    assert result["harness"]["error"] == "forbidden_action"
