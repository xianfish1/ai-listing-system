from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from services.api import mabang_export, settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SAMPLE = PROJECT_ROOT / "storage" / "samples" / "mabang-template.xlsx"


def test_resolve_template_path_uses_local_sample_when_network_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert LOCAL_SAMPLE.is_file(), f"Expected local sample at {LOCAL_SAMPLE}"
    fake_network = tmp_path / "missing-network-template.xlsx"
    monkeypatch.setattr(settings, "MABANG_TEMPLATE_PATH", fake_network)

    resolved = mabang_export._resolve_template_path()

    assert resolved == LOCAL_SAMPLE
    assert resolved.is_file()


def test_resolve_template_path_prefers_configured_path_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_copy = tmp_path / "explicit-template.xlsx"
    shutil.copy2(LOCAL_SAMPLE, local_copy)
    monkeypatch.setattr(settings, "MABANG_TEMPLATE_PATH", local_copy)

    resolved = mabang_export._resolve_template_path()

    assert resolved == local_copy


def test_resolve_template_path_raises_when_neither_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_network = tmp_path / "missing-network-template.xlsx"
    monkeypatch.setattr(settings, "MABANG_TEMPLATE_PATH", fake_network)
    monkeypatch.setattr(mabang_export, "_LOCAL_TEMPLATE_FALLBACK", tmp_path / "absent.xlsx")

    with pytest.raises(FileNotFoundError, match="Mabang template not found"):
        mabang_export._resolve_template_path()
