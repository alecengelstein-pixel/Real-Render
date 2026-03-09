from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    """Return True if ffmpeg and ffprobe are on PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def extract_keyframe(
    mp4_path: str,
    output_path: str,
    timestamp: float = 2.5,
) -> bool:
    """Extract a single frame from *mp4_path* at *timestamp* seconds.

    Returns True on success, False if ffmpeg is missing or the command fails.
    """
    if not ffmpeg_available():
        logger.warning("ffmpeg not found — skipping keyframe extraction")
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", mp4_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        logger.info("Extracted keyframe → %s", output_path)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Keyframe extraction failed: %s", exc)
        return False


@dataclass
class VideoQuality:
    provider: str
    width: int = 0
    height: int = 0
    duration_secs: float = 0.0
    file_size_bytes: int = 0
    score: float = 0.0


def assess_video_quality(mp4_path: str, provider_name: str) -> VideoQuality:
    """Score a video file using ffprobe metadata.

    Weighted score: file_size 30%, duration 30%, resolution 40%.
    Raw values are normalised so they can be compared between two videos.
    """
    quality = VideoQuality(provider=provider_name)

    path = Path(mp4_path)
    if not path.exists():
        return quality

    quality.file_size_bytes = path.stat().st_size

    if not ffmpeg_available():
        # Without ffprobe we can only score on file size
        quality.score = float(quality.file_size_bytes)
        return quality

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        mp4_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        probe = json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        quality.score = float(quality.file_size_bytes)
        return quality

    # Extract video stream info
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            quality.width = int(stream.get("width", 0))
            quality.height = int(stream.get("height", 0))
            break

    # Duration from format
    fmt = probe.get("format", {})
    quality.duration_secs = float(fmt.get("duration", 0))

    # Weighted score (higher is better)
    resolution_score = quality.width * quality.height
    duration_score = quality.duration_secs * 1000  # scale up for weighting
    size_score = quality.file_size_bytes / 1024     # KB

    quality.score = (
        resolution_score * 0.4
        + duration_score * 0.3
        + size_score * 0.3
    )

    logger.info(
        "Quality [%s]: %dx%d, %.1fs, %.0f KB → score=%.1f",
        provider_name, quality.width, quality.height,
        quality.duration_secs, quality.file_size_bytes / 1024, quality.score,
    )
    return quality
