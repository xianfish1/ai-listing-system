# AI Listing System Self-Run Gap Implementation Plan

> **For AI agents:** Use `executing-plans` or `subagent-driven-development` to execute this plan. Track progress with checkboxes. Keep the browser automation safety boundary: no save, no publish, stop for operator review.

**Goal:** Turn the current AL0071 / POD Philippines workflow from a set of working pieces into a repeatable self-run pipeline: start services, verify readiness, recover login when needed, create/open the Mabang draft, audit the iframe form, safely fill fields from product data, and stop with a clear operator review report.

**Architecture:** Keep product data, template data, category profiles, and automation jobs in the existing API/storage layer. Add a small local orchestration layer that runs health checks and invokes `scripts/browser_use_runner.py` in ordered phases. Browser automation remains guarded and iframe-aware, and every phase writes structured status without storing credentials or clicking save/publish.

**Tech Stack:** Python 3.12, pytest, local JSON storage, Browser-Use 0.13.3, Chrome CDP on `127.0.0.1:9223`, Mabang Shopee publish UI, existing static web app.

---

## Current State

- Product/template/category-profile data for AL0071 and `POD菲律宾` is ready.
- Automation job `c26ee694-cbc2-4d6d-bfca-252b6f704516` is ready and contains store ID, category ID, discount ID, 35 SKU rows, 9 product images, and color thumbnails.
- Dedicated Chrome CDP profile works on port `9223`.
- Login recovery has been proven once with operator-provided credentials, but credentials must not be stored or echoed.
- The automation can open the Shopee listing workspace, click local `待发布商品`, click `新增刊登`, and open the wrapped `main.goPublish` Shopee detail tab.
- Existing gap plan `2026-07-09-mabang-shopee-iframe-safe-fill-plan.md` covers iframe-level audit/fill.

## Remaining Gaps

1. No one-command local runner exists for API + Chrome CDP + Browser-Use health checks.
2. Login recovery is documented but not wired into a reusable safe phase.
3. Detail draft creation/opening is not yet a reusable Browser-Use phase.
4. Safe audit/fill still needs iframe helpers from the iframe plan.
5. There is no persistent run record that says exactly which phase passed, stopped, or needs manual work.
6. The web UI can generate jobs, but cannot yet show a complete "can self-run now" readiness panel.
7. Final self-run behavior is not verified end-to-end with one command.

## Safety Rules

- Do not store Mabang passwords in `.env`, docs, screenshots, payload JSON, progress logs, or generated scripts.
- Do not click save, update, submit, publish, or any equivalent final action in this plan.
- Save-only behavior is explicitly out of scope until the stuck-save behavior is separately diagnosed.
- If login needs SMS, slider captcha, device verification, or any second factor, stop with `needs_manual_login`.
- If category ID, discount ID, size chart, image file, iframe, selector, dropdown option, or SKU row count is missing or ambiguous, stop with `needs_manual`.
- Every live run must emit a final status: `ready`, `blocked`, `needs_manual`, `safe_audit_passed`, `safe_fill_completed`, or `stop_for_operator_review`.

## Files

- Create: `scripts/run_self_listing.ps1`
  - Starts or verifies the API, verifies Chrome CDP, optionally opens dedicated Chrome, runs dry-run, audit, and later safe-fill phases.
- Create: `scripts/self_run_listing.py`
  - Python orchestrator for structured phase execution and JSON status output.
- Modify: `scripts/browser_use_runner.py`
  - Expose reusable phase functions for login recovery, workspace navigation, local pending tab selection, draft creation, iframe audit, and guarded fill.
- Modify: `tests/test_browser_use_runner.py`
  - Cover generated Browser-Use phase scripts and safety guards.
- Create: `tests/test_self_run_listing.py`
  - Cover the self-run orchestrator without launching a real browser.
- Modify: `apps/web/app.js`
  - Add a read-only automation readiness panel for selected jobs.
- Modify: `services/api/main.py`
  - Add self-run status endpoints if the UI needs persisted run records.
- Modify: `services/api/storage.py`
  - Add `automation_run_records` storage if status persistence is implemented locally.
- Modify: `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`
  - Record the self-run phase order and stop conditions.
- Modify: `progress.md`
  - Log implementation and live verification results.

## Task 1: Add A Self-Run Orchestrator Skeleton

**Files:**
- Create: `scripts/self_run_listing.py`
- Create: `tests/test_self_run_listing.py`

- [ ] Step 1: Write failing tests for phase ordering.

```python
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
```

- [ ] Step 2: Run:

```powershell
python -m pytest tests/test_self_run_listing.py -q
```

Expected: FAIL because `scripts/self_run_listing.py` does not exist.

- [ ] Step 3: Implement the minimal skeleton.

```python
from __future__ import annotations

from typing import Any


SAFE_PHASES = [
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


def build_self_run_plan(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "mode": "guarded_self_run",
        "phases": [
            {"name": name, "can_save": False, "can_publish": False}
            for name in SAFE_PHASES
        ],
    }
```

- [ ] Step 4: Run:

```powershell
python -m pytest tests/test_self_run_listing.py -q
```

Expected: PASS.

## Task 2: Add Local Health Checks

**Files:**
- Modify: `scripts/self_run_listing.py`
- Test: `tests/test_self_run_listing.py`

- [ ] Step 1: Add tests for API and Chrome CDP health parsing.

```python
def test_health_summary_blocks_when_cdp_is_down() -> None:
    summary = self_run_listing.build_health_summary(
        api_ok=True,
        cdp_ok=False,
        browser_use_ok=True,
    )

    assert summary["ok"] is False
    assert summary["status"] == "blocked"
    assert summary["next_step"] == "start_dedicated_chrome_cdp_9223"
```

- [ ] Step 2: Implement `build_health_summary()`.

```python
def build_health_summary(*, api_ok: bool, cdp_ok: bool, browser_use_ok: bool) -> dict[str, Any]:
    if not api_ok:
        return {"ok": False, "status": "blocked", "next_step": "start_local_api"}
    if not cdp_ok:
        return {"ok": False, "status": "blocked", "next_step": "start_dedicated_chrome_cdp_9223"}
    if not browser_use_ok:
        return {"ok": False, "status": "blocked", "next_step": "restart_browser_use_daemon"}
    return {"ok": True, "status": "ready", "next_step": "run_job_dry_run"}
```

- [ ] Step 3: Add real check functions:

```python
def check_api_health(base_url: str = "http://127.0.0.1:8787") -> dict[str, Any]:
    ...


def check_chrome_cdp(cdp_url: str = "http://127.0.0.1:9223") -> dict[str, Any]:
    ...
```

Use `urllib.request.urlopen()` with a short timeout. Return structured JSON; do not throw on connection failure.

- [ ] Step 4: Run:

```powershell
python -m pytest tests/test_self_run_listing.py -q
python -m compileall -q scripts tests
```

## Task 3: Add A PowerShell Convenience Runner

**Files:**
- Create: `scripts/run_self_listing.ps1`
- Modify: `README.md`

- [ ] Step 1: Create a non-destructive runner that defaults to audit-only.

```powershell
param(
  [Parameter(Mandatory=$true)]
  [string]$JobId,

  [ValidateSet("health", "audit", "fill")]
  [string]$Mode = "audit"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$env:BU_CDP_URL = "http://127.0.0.1:9223"

python scripts\self_run_listing.py --job-id $JobId --mode $Mode
```

- [ ] Step 2: Add README usage.

```powershell
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode health
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode audit
```

- [ ] Step 3: Verify PowerShell syntax by running:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode health
```

Expected: health JSON is printed and no browser form action is performed.

## Task 4: Add Login Recovery Phase Without Storing Credentials

**Files:**
- Modify: `scripts/browser_use_runner.py`
- Test: `tests/test_browser_use_runner.py`
- Modify: `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`

- [ ] Step 1: Add tests asserting the generated login script references selectors but does not embed a password.

```python
def test_login_recovery_script_has_selectors_without_secret() -> None:
    script = browser_use_runner.build_mabang_login_recovery_script(
        account_env="MABANG_ACCOUNT",
        password_env="MABANG_PASSWORD",
    )

    assert "#account" in script
    assert "#password" in script
    assert "#account-btn" in script
    assert "MABANG_PASSWORD" in script
    assert "yang" not in script.lower()
```

- [ ] Step 2: Implement `build_mabang_login_recovery_script()` using environment variables only.

```python
def build_mabang_login_recovery_script(
    *,
    account_env: str = "MABANG_ACCOUNT",
    password_env: str = "MABANG_PASSWORD",
) -> str:
    ...
```

- [ ] Step 3: Generated script behavior:

```text
If current URL is index.htm:
  read account/password from process environment
  fill #account and #password
  click #account-btn
  wait for workspace URL
If SMS/captcha/slider is detected:
  emit needs_manual_login and stop
If already logged in:
  emit already_logged_in
```

- [ ] Step 4: Run tests:

```powershell
python -m pytest tests/test_browser_use_runner.py -q
```

## Task 5: Make Workspace Navigation A Reusable Phase

**Files:**
- Modify: `scripts/browser_use_runner.py`
- Test: `tests/test_browser_use_runner.py`

- [ ] Step 1: Add tests for generated functions:

```python
def test_workspace_navigation_script_clicks_local_pending_and_new_listing() -> None:
    script = browser_use_runner.build_mabang_workspace_navigation_script()

    assert "open_mabang_workspace()" in script
    assert "click_local_pending_products()" in script
    assert "click_new_listing()" in script
    assert "待发布商品" in script
    assert "新增刊登" in script
    assert "保存" not in script
```

- [ ] Step 2: Implement the generated navigation script with these stages:

```text
open_mabang_workspace
find #iframeContent publishProductListV2 frame
click local 待发布商品
verify tab active
click 新增刊登
wait for a new main.goPublish detail tab
emit detail_tab_opened
```

- [ ] Step 3: Stop conditions:

```text
workspace iframe not found -> needs_manual_workspace
待发布商品 not found -> needs_manual_pending_tab
新增刊登 not found -> needs_manual_create_button
detail tab not opened -> needs_manual_detail_tab
```

## Task 6: Execute The Existing Iframe Safe-Fill Plan

**Files:**
- Modify: `scripts/browser_use_runner.py`
- Test: `tests/test_browser_use_runner.py`
- Modify: `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`

- [ ] Step 1: Execute `docs/superpowers/plans/2026-07-09-mabang-shopee-iframe-safe-fill-plan.md` Task 1 through Task 4 first.

Minimum output required before moving forward:

```text
safe audit selects wrapped main.goPublish detail tab
finds #iframeContent productDetailShopee frame
installs outer guard
installs inner guard
returns inner field discovery report
does not fill, upload, save, or publish
```

- [ ] Step 2: Run:

```powershell
python -m pytest tests/test_browser_use_runner.py tests/test_product_line_flow.py -q
python -m compileall -q scripts tests services workers
```

- [ ] Step 3: Live guarded audit:

```powershell
$env:BU_CDP_URL='http://127.0.0.1:9223'
python scripts\browser_use_runner.py --job-id c26ee694-cbc2-4d6d-bfca-252b6f704516 --execute-safe-audit
```

Expected: result contains `inner_audit.iframe_url` with `productDetailShopee`.

## Task 7: Add Persistent Run Records

**Files:**
- Modify: `services/api/storage.py`
- Modify: `services/api/main.py`
- Test: `tests/test_product_line_flow.py` or create `tests/test_automation_run_records.py`

- [ ] Step 1: Add test for run record persistence.

```python
def test_automation_run_record_can_store_phase_status(temp_store) -> None:
    record = storage.create_automation_run_record(
        {
            "job_id": "job-1",
            "mode": "audit",
            "status": "needs_manual",
            "phase": "iframe_safe_audit",
            "reason": "iframe_not_found",
        }
    )

    loaded = storage.list_automation_run_records("job-1")
    assert loaded[0]["id"] == record["id"]
    assert loaded[0]["status"] == "needs_manual"
    assert loaded[0]["reason"] == "iframe_not_found"
```

- [ ] Step 2: Implement storage collection:

```text
automation_run_records
```

Fields:

```text
id
job_id
mode
status
phase
reason
created_at
summary
```

- [ ] Step 3: Add endpoints:

```text
GET  /api/automation/jobs/{job_id}/runs
POST /api/automation/jobs/{job_id}/runs
```

- [ ] Step 4: Run:

```powershell
python -m pytest tests/test_product_line_flow.py tests/test_automation_run_records.py -q
```

## Task 8: Add Web Readiness Panel

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`
- Test: syntax check only unless UI tests are added.

- [ ] Step 1: Add read-only status display for the selected automation job:

```text
Job status
Store ID
Category ID
Discount ID
SKU rows
Product images
Size chart status
Color thumbnail status
Latest run phase
Latest run status
Next step
```

- [ ] Step 2: Use warning states:

```text
ready -> can run audit
needs_manual -> cannot self-run
safe_audit_passed -> can run safe fill
safe_fill_completed -> stop for operator review
```

- [ ] Step 3: Do not add any save/publish action button in this panel.

- [ ] Step 4: Run:

```powershell
node --check apps\web\app.js
```

## Task 9: Add One-Command Audit Mode

**Files:**
- Modify: `scripts/self_run_listing.py`
- Test: `tests/test_self_run_listing.py`

- [ ] Step 1: Add CLI tests by monkeypatching phase functions.

```python
def test_audit_mode_stops_after_iframe_safe_audit(monkeypatch, capsys) -> None:
    monkeypatch.setattr(self_run_listing, "run_phase", lambda phase, job_id: {"ok": True, "phase": phase["name"]})

    exit_code = self_run_listing.main(["--job-id", "job-1", "--mode", "audit"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "iframe_safe_audit" in output
    assert "iframe_safe_fill" not in output
```

- [ ] Step 2: Implement CLI modes:

```text
health -> api_health, chrome_cdp_health, browser_use_doctor
audit  -> health phases, job_dry_run, login_recovery, open_workspace, open_local_pending, open_or_create_detail_draft, iframe_safe_audit
fill   -> audit phases, iframe_safe_fill, operator_review
```

- [ ] Step 3: Ensure failures stop subsequent phases and return a non-zero exit code.

- [ ] Step 4: Run:

```powershell
python -m pytest tests/test_self_run_listing.py -q
```

## Task 10: Final AL0071 Self-Run Verification

**Files:**
- Modify: `progress.md`
- Modify: `docs/MABANG_SHOPEE_BROWSER_USE_WORKFLOW.md`

- [ ] Step 1: Run local regression:

```powershell
python -m pytest -q
python -m compileall -q services workers scripts tests
node --check apps\web\app.js
```

- [ ] Step 2: Run health mode:

```powershell
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode health
```

Expected:

```text
api_health ok
chrome_cdp_health ok
browser_use_doctor ok
```

- [ ] Step 3: Run audit mode:

```powershell
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode audit
```

Expected:

```text
login ready or already logged in
workspace opened
local 待发布商品 active
detail draft opened
outer save/publish guard installed
inner save/publish guard installed
iframe audit passed
no fill
no upload
no save
no publish
```

- [ ] Step 4: Run fill mode only after audit passes:

```powershell
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode fill
```

Expected:

```text
AL0071 draft fields are filled from listing copy payload.
9 product images are prepared and filled/uploaded only through guarded code.
35 SKU rows are verified.
Discount 40% is selected or verified.
Color thumbnails are mapped.
Final status is stop_for_operator_review.
No save action was clicked.
No publish action was clicked.
```

## Acceptance Criteria

- One command can show whether the project is ready to self-run.
- One command can run audit mode from job ID to iframe audit without manual code changes.
- Fill mode consumes the stored listing copy payload and category profile data; it does not hardcode POD fields in the browser script.
- Missing login, captcha, iframe, category ID, discount ID, size chart, image, dropdown option, or SKU mismatch stops with a clear status and next step.
- Web UI shows whether a job can self-run, why it cannot, and the latest run status.
- The final flow stops for operator review and never saves or publishes.

## Verification Commands

```powershell
python -m pytest tests/test_self_run_listing.py tests/test_browser_use_runner.py tests/test_product_line_flow.py -q
python -m pytest -q
python -m compileall -q services workers scripts tests
node --check apps\web\app.js
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode health
.\scripts\run_self_listing.ps1 -JobId c26ee694-cbc2-4d6d-bfca-252b6f704516 -Mode audit
```
