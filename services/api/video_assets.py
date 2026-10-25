from __future__ import annotations

import json
from services.api.subprocess_utils import no_console_window_kwargs
import subprocess
from pathlib import Path
from typing import Any


MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 60
MAX_DIMENSION_PIXELS = 1280
MAX_SIZE_BYTES = 30 * 1024 * 1024


def validate_video(path: Path, *, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    candidate = Path(path)
    # Locally verifiable checks (mp4 container, file size) are hard blocks — they
    # would always be rejected server-side, so there is no point attempting them.
    errors = _container_and_size_errors(candidate)
    # Metadata (duration/resolution) is best-effort: when ffprobe is available we
    # keep the checks as hard blocks (a known-bad clip would fail server-side too),
    # but when metadata cannot be read we DON'T block — we upload the original file
    # and let Mabang/Shopee's server-side transcode be the judge. A failed upload
    # then surfaces as a warning for a human to handle (never a silent skip).
    warnings: list[str] = []
    metadata: dict[str, float | int] = {"duration_seconds": 0, "width": 0, "height": 0}
    if "video_format_unsupported" not in errors:
        try:
            metadata = _probe_metadata(candidate, ffprobe_path=ffprobe_path)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
            warnings.append("video_metadata_unavailable")
        else:
            errors.extend(_metadata_errors(metadata))
    size_bytes = candidate.stat().st_size if candidate.is_file() else 0
    return {
        "mime_type": "video/mp4",
        "size_bytes": size_bytes,
        "duration_seconds": metadata["duration_seconds"],
        "width": metadata["width"],
        "height": metadata["height"],
        "validation_status": "invalid" if errors else "valid",
        "validation_errors": list(dict.fromkeys(errors)),
        "validation_warnings": list(dict.fromkeys(warnings)),
    }


def _container_and_size_errors(path: Path) -> list[str]:
    if not path.is_file() or path.suffix.lower() != ".mp4":
        return ["video_format_unsupported"]
    errors: list[str] = []
    header = path.read_bytes()[:32]
    if len(header) < 12 or header[4:8] != b"ftyp":
        errors.append("video_format_unsupported")
    if path.stat().st_size > MAX_SIZE_BYTES:
        errors.append("video_file_too_large")
    return errors


def _probe_metadata(path: Path, *, ffprobe_path: str) -> dict[str, float | int]:
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        **no_console_window_kwargs(),
    )
    if result.returncode != 0:
        raise ValueError("ffprobe failed")
    payload = json.loads(result.stdout or "{}")
    stream = next((item for item in payload.get("streams") or [] if item.get("width") and item.get("height")), {})
    return {
        "duration_seconds": float((payload.get("format") or {}).get("duration") or 0),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


def _metadata_errors(metadata: dict[str, float | int]) -> list[str]:
    errors: list[str] = []
    duration = float(metadata.get("duration_seconds") or 0)
    if duration < MIN_DURATION_SECONDS:
        errors.append("video_duration_too_short")
    if duration > MAX_DURATION_SECONDS:
        errors.append("video_duration_too_long")
    if max(int(metadata.get("width") or 0), int(metadata.get("height") or 0)) > MAX_DIMENSION_PIXELS:
        errors.append("video_resolution_too_large")
    if not metadata.get("width") or not metadata.get("height"):
        errors.append("video_metadata_unavailable")
    return errors
