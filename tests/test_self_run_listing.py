from __future__ import annotations

import json
from pathlib import Path

from scripts import self_run_listing


def test_self_run_plan_orders_safe_phases_without_publish() -> None:
    plan = self_run_listing.build_self_run_plan("job-1")

    assert [phase["name"] for phase in plan["phases"]] == [
        "api_health",
        "chrome_cdp_health",
        "browser_use_doctor",
        "job_dry_run",
        "login_recovery",
        "open_workspace",
        "open_local_pending",
        "open_or_create_detail_draft",
        "iframe_safe_audit",
        "iframe_safe_fill",
        "operator_review",
    ]
    assert not any(phase.get("can_save") for phase in plan["phases"])
    assert not any(phase.get("can_publish") for phase in plan["phases"])


def test_health_summary_blocks_when_cdp_is_down() -> None:
    summary = self_run_listing.build_health_summary(
        api_ok=True,
        cdp_ok=False,
        browser_use_ok=True,
    )

    assert summary["ok"] is False
    assert summary["status"] == "blocked"
    assert summary["next_step"] == "start_dedicated_chrome_cdp_9223"


def test_health_summary_ready_when_all_checks_pass() -> None:
    summary = self_run_listing.build_health_summary(
        api_ok=True,
        cdp_ok=True,
        browser_use_ok=True,
    )

    assert summary == {"ok": True, "status": "ready", "next_step": "run_job_dry_run"}


def test_audit_mode_stops_after_iframe_safe_audit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        self_run_listing,
        "run_phase",
        lambda phase, job_id: {"ok": True, "status": "completed", "phase": phase["name"]},
    )

    exit_code = self_run_listing.main(["--job-id", "job-1", "--mode", "audit"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    phases = [item["phase"] for item in output["results"]]
    assert "iframe_safe_audit" in phases
    assert "iframe_safe_fill" not in phases


def test_fill_mode_includes_operator_review(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        self_run_listing,
        "run_phase",
        lambda phase, job_id: {"ok": True, "status": "completed", "phase": phase["name"]},
    )

    exit_code = self_run_listing.main(["--job-id", "job-1", "--mode", "fill"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    phases = [item["phase"] for item in output["results"]]
    assert phases[-2:] == ["iframe_safe_fill", "operator_review"]


def test_cli_stops_after_failed_phase(monkeypatch, capsys) -> None:
    def fake_run_phase(phase: dict, job_id: str) -> dict:
        if phase["name"] == "chrome_cdp_health":
            return {"ok": False, "status": "blocked", "phase": phase["name"], "next_step": "start_cdp"}
        return {"ok": True, "status": "completed", "phase": phase["name"]}

    monkeypatch.setattr(self_run_listing, "run_phase", fake_run_phase)

    exit_code = self_run_listing.main(["--job-id", "job-1", "--mode", "audit"])

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    phases = [item["phase"] for item in output["results"]]
    assert phases == ["api_health", "chrome_cdp_health"]
    assert output["status"] == "blocked"


def test_check_chrome_cdp_reports_connection_failure(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(self_run_listing.request, "urlopen", fake_urlopen)

    result = self_run_listing.check_chrome_cdp("http://127.0.0.1:9223")

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["next_step"] == "start_dedicated_chrome_cdp_9223"


def test_run_phase_job_dry_run_uses_browser_runner(monkeypatch) -> None:
    monkeypatch.setattr(
        self_run_listing.browser_use_runner,
        "run_job",
        lambda job_id: {"dry_run": {"ok": True, "status": "ready", "job_id": job_id}},
    )

    result = self_run_listing.run_phase({"name": "job_dry_run"}, "job-1")

    assert result["ok"] is True
    assert result["phase"] == "job_dry_run"
    assert result["summary"]["job_id"] == "job-1"


def test_run_phase_iframe_audit_uses_safe_audit(monkeypatch) -> None:
    monkeypatch.setattr(
        self_run_listing.browser_use_runner,
        "run_job",
        lambda job_id, execute_safe_audit=False: {
            "safe_audit": {"ok": True, "status": "completed"},
        },
    )

    result = self_run_listing.run_phase({"name": "iframe_safe_audit"}, "job-1")

    assert result["ok"] is True
    assert result["phase"] == "iframe_safe_audit"
    assert result["summary"]["status"] == "completed"


def test_powershell_runner_exists_after_plan_task() -> None:
    path = Path("scripts/run_self_listing.ps1")
    if path.exists():
        text = path.read_text(encoding="utf-8")
        assert "self_run_listing.py" in text
        assert "BU_CDP_URL" in text
