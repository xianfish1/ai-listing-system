from __future__ import annotations

from pathlib import Path

import pytest

from scripts import bundle_chrome


def _make_fake_cache(root: Path, build: int) -> Path:
    """Create a fake Playwright chromium-<build> cache tree and return it."""

    chromium = root / f"chromium-{build}"
    exe = chromium / "chrome-win64" / "chrome.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("binary", encoding="utf-8")
    return chromium


def test_bundle_chrome_copies_newest_cache_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_root = tmp_path / "ms-playwright"
    _make_fake_cache(cache_root, 1200)
    newest = _make_fake_cache(cache_root, 1228)
    vendor = tmp_path / "vendor" / "chrome"
    monkeypatch.setattr(bundle_chrome, "VENDOR_CHROME_DIR", vendor)
    monkeypatch.setattr(bundle_chrome, "_playwright_cache_dirs", lambda: [cache_root])

    exe = bundle_chrome.bundle_chrome()

    # Highest build number wins, and the copy lands under the project vendor dir.
    assert exe.is_file()
    assert exe.name == "chrome.exe"
    assert (vendor / newest.name / "chrome-win64" / "chrome.exe").is_file()


def test_bundle_chrome_is_idempotent_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_root = tmp_path / "ms-playwright"
    _make_fake_cache(cache_root, 1228)
    vendor = tmp_path / "vendor" / "chrome"
    monkeypatch.setattr(bundle_chrome, "VENDOR_CHROME_DIR", vendor)
    monkeypatch.setattr(bundle_chrome, "_playwright_cache_dirs", lambda: [cache_root])

    first = bundle_chrome.bundle_chrome()
    # Second call must NOT hit the cache again; sabotage the copier to prove it short-circuits.
    monkeypatch.setattr(
        bundle_chrome,
        "_find_cached_chromium_dir",
        lambda: (_ for _ in ()).throw(AssertionError("should not re-copy")),
    )
    second = bundle_chrome.bundle_chrome()

    assert first == second


def test_bundle_chrome_falls_back_to_install_without_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = tmp_path / "vendor" / "chrome"
    monkeypatch.setattr(bundle_chrome, "VENDOR_CHROME_DIR", vendor)
    monkeypatch.setattr(bundle_chrome, "_playwright_cache_dirs", lambda: [])

    installed: list[bool] = []

    def fake_install() -> Path:
        exe = vendor / "chrome-win64" / "chrome.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("binary", encoding="utf-8")
        installed.append(True)
        return exe

    monkeypatch.setattr(bundle_chrome, "_install_via_playwright", fake_install)

    exe = bundle_chrome.bundle_chrome()

    assert installed == [True]
    assert exe.is_file()
