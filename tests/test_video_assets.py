from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.api.video_assets import MAX_SIZE_BYTES, validate_video


def _fake_mp4(tmp_path: Path, *, size_bytes: int = 32) -> Path:
    path = tmp_path / "product.mp4"
    header = b"\x00\x00\x00\x18ftypisom"
    path.write_bytes(header + b"\x00" * max(0, size_bytes - len(header)))
    return path


def _fake_ffprobe(monkeypatch: pytest.MonkeyPatch, *, duration: float, width: int, height: int) -> None:
    payload = {"streams": [{"width": width, "height": height}], "format": {"duration": str(duration)}}
    monkeypatch.setattr(
        "services.api.video_assets.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )


@pytest.mark.parametrize(
    ("duration", "width", "height", "size_bytes"),
    [(10, 1280, 1280, 32), (60, 720, 1280, MAX_SIZE_BYTES)],
)
def test_mp4_boundary_values_are_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
    width: int,
    height: int,
    size_bytes: int,
) -> None:
    video = _fake_mp4(tmp_path, size_bytes=size_bytes)
    _fake_ffprobe(monkeypatch, duration=duration, width=width, height=height)

    result = validate_video(video)

    assert result["validation_status"] == "valid"
    assert result["validation_errors"] == []


@pytest.mark.parametrize(
    ("duration", "width", "height", "size_bytes", "error_code"),
    [
        (9.99, 720, 720, 32, "video_duration_too_short"),
        (60.01, 720, 720, 32, "video_duration_too_long"),
        (30, 1281, 720, 32, "video_resolution_too_large"),
        (30, 720, 720, MAX_SIZE_BYTES + 1, "video_file_too_large"),
    ],
)
def test_video_outside_a_boundary_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
    width: int,
    height: int,
    size_bytes: int,
    error_code: str,
) -> None:
    video = _fake_mp4(tmp_path, size_bytes=size_bytes)
    _fake_ffprobe(monkeypatch, duration=duration, width=width, height=height)

    result = validate_video(video)

    assert result["validation_status"] == "invalid"
    assert error_code in result["validation_errors"]


def test_non_mp4_is_a_hard_error(tmp_path: Path) -> None:
    wrong_format = tmp_path / "movie.mov"
    wrong_format.write_bytes(b"not-mp4")
    assert "video_format_unsupported" in validate_video(wrong_format)["validation_errors"]


def test_missing_ffprobe_keeps_original_uploadable_as_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A well-formed original .mp4 must stay upload-eligible when metadata cannot be
    # read (ffprobe missing). We do not transcode and we do not silently skip: the
    # original is uploaded and the server decides, with the unread metadata recorded
    # as a non-blocking warning for human follow-up.
    video = _fake_mp4(tmp_path)

    def missing_ffprobe(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("services.api.video_assets.subprocess.run", missing_ffprobe)
    result = validate_video(video)
    assert result["validation_status"] == "valid"
    assert result["validation_errors"] == []
    assert "video_metadata_unavailable" in result["validation_warnings"]
