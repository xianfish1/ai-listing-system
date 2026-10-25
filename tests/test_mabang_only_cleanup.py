from __future__ import annotations

from pathlib import Path

from services.api import project_plan, storage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TERMS = [
    "BigSeller",
    "bigseller",
    "waiting_browser",
    "Mabang IDs",
]
# "browser_harness" was removed from this list on 2026-08-30: the name now belongs
# to the supported constrained CDP + AI takeover harness (scripts/browser_harness.py,
# scripts/mabang_browser_harness.cjs), not the removed legacy flow this guard targeted.
# "manual_stop" and "商品列表" were removed on 2026-10-25: both now have legitimate
# current-feature usages the text scan can't tell apart from the removed BigSeller
# flow — `manual_stop` is a worker-hold action value (services/api/main.py), and
# 「商品列表」 is generic copy for the local-folder batch refresh flow. Keeping them
# made this guard permanently red (no signal) over false positives.
SCAN_GLOBS = [
    "README.md",
    ".env.example",
    "services/**/*.py",
    "apps/web/*.html",
    "apps/web/*.js",
    "apps/web/*.css",
    "scripts/*",
    "docs/**/*.md",
    "workers/**/*.py",
    "workers/**/*.js",
    "workers/**/*.html",
]


def test_project_plan_is_mabang_only() -> None:
    plan = project_plan.get_project_plan()
    text = str(plan)

    assert "马帮" in text
    assert "mabang" in text.lower()
    for term in FORBIDDEN_TERMS:
        assert term not in text
    assert plan["primary_output"] == "mabang_excel"
    assert [item["name"] for item in plan["runtime_chain"]] == [
        "NAS 素材库",
        "商品资料维护",
        "马帮导出预览",
        "马帮 Excel 下载",
    ]


def test_deployment_readiness_is_mabang_export_readiness(tmp_path: Path, monkeypatch) -> None:
    store_file = tmp_path / "store.json"
    monkeypatch.setattr(storage.settings, "STORE_FILE", store_file)
    monkeypatch.setattr(storage.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage.settings, "STORAGE_DIR", tmp_path / "storage")

    readiness = storage.deployment_readiness()
    text = str(readiness)

    assert readiness["target"] == "mabang_export"
    assert "template_available" in {item["code"] for item in readiness["checks"]}
    assert "exportable_products" in {item["code"] for item in readiness["checks"]}
    for term in FORBIDDEN_TERMS:
        assert term not in text


def test_deployment_readiness_handles_inaccessible_template_path(tmp_path: Path, monkeypatch) -> None:
    store_file = tmp_path / "store.json"
    inaccessible_template = tmp_path / "inaccessible-template.xlsx"
    monkeypatch.setattr(storage.settings, "STORE_FILE", store_file)
    monkeypatch.setattr(storage.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage.settings, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(storage.settings, "MABANG_TEMPLATE_PATH", inaccessible_template)
    # _safe_is_file 有 60 秒 TTL 探测缓存：前一个用例（或其它文件里的
    # deployment_readiness 调用）会把本地 fallback 模板缓存成 True，绕开下面的
    # Path.is_file 补丁。这里必须换一份空缓存，探测才真的走到补丁上。
    monkeypatch.setattr(storage, "_FILE_PROBE_CACHE", {})

    def broken_is_file(path: Path) -> bool:
        if Path(path) == inaccessible_template:
            raise PermissionError("simulated inaccessible network share")
        return False

    monkeypatch.setattr(Path, "is_file", broken_is_file)

    readiness = storage.deployment_readiness()

    template_check = next(item for item in readiness["checks"] if item["code"] == "template_available")
    assert template_check["ok"] is False
    assert readiness["status"] == "blocked"


def test_active_project_files_do_not_reference_removed_bigseller_flow() -> None:
    offenders: list[str] = []
    for pattern in SCAN_GLOBS:
        for path in PROJECT_ROOT.glob(pattern):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [term for term in FORBIDDEN_TERMS if term in text]
            if hits:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {', '.join(hits)}")

    assert offenders == []
