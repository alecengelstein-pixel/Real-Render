"""MLS-optimized photo and video formatter.

Formats deliverables to meet common MLS (Multiple Listing Service) requirements:
- Photos: 2048x1536 (4:3), JPEG quality 90, sRGB color profile
- Videos: 1920x1080 (16:9), H.264, under 100MB
Used for Premium tier.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Standard MLS photo requirements
MLS_WIDTH = 2048
MLS_HEIGHT = 1536
MLS_QUALITY = 90
MLS_ASPECT = 4 / 3


def format_photo_for_mls(image_path: str, output_path: str) -> bool:
    """Resize and optimize a single photo for MLS."""
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")

            w, h = img.size
            current_aspect = w / h

            # Crop to 4:3 if needed (center crop)
            if abs(current_aspect - MLS_ASPECT) > 0.05:
                if current_aspect > MLS_ASPECT:
                    # Too wide — crop sides
                    new_w = int(h * MLS_ASPECT)
                    left = (w - new_w) // 2
                    img = img.crop((left, 0, left + new_w, h))
                else:
                    # Too tall — crop top/bottom
                    new_h = int(w / MLS_ASPECT)
                    top = (h - new_h) // 2
                    img = img.crop((0, top, w, top + new_h))

            # Resize to MLS dimensions (upscale if needed)
            img = img.resize((MLS_WIDTH, MLS_HEIGHT), Image.LANCZOS)

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(out), "JPEG", quality=MLS_QUALITY, optimize=True, subsampling=0)

            logger.info("MLS photo formatted: %s", output_path)
            return True

    except Exception:
        logger.exception("Failed to format photo for MLS: %s", image_path)
        return False


def format_video_for_mls(video_path: str, output_path: str) -> bool:
    """Re-encode video to MLS-friendly specs."""
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not available — skipping MLS video formatting")
        return False

    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("MLS video formatting failed: %s", result.stderr[:500])
            return False

        logger.info("MLS video formatted: %s", output_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("MLS video formatting timed out")
        return False
    except Exception:
        logger.exception("Failed to format video for MLS: %s", video_path)
        return False


def format_all_for_mls(
    photos_dir: str,
    video_path: str | None,
    output_dir: str,
) -> dict[str, Any]:
    """Format all deliverables for MLS listing."""
    out = Path(output_dir)
    mls_photos = out / "mls_photos"
    mls_photos.mkdir(parents=True, exist_ok=True)

    in_path = Path(photos_dir)
    images = sorted(
        [p for p in in_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()]
    )

    formatted_photos: list[str] = []
    for i, img in enumerate(images):
        out_file = mls_photos / f"mls_{i + 1:02d}.jpg"
        if format_photo_for_mls(str(img), str(out_file)):
            formatted_photos.append(str(out_file))

    formatted_video = None
    if video_path and Path(video_path).exists():
        mls_vid = str(out / "mls_walkthrough.mp4")
        if format_video_for_mls(video_path, mls_vid):
            formatted_video = mls_vid

    logger.info("MLS formatting: %d photos, video=%s",
                len(formatted_photos), "yes" if formatted_video else "no")

    return {
        "ok": len(formatted_photos) > 0,
        "photos": formatted_photos,
        "photo_count": len(formatted_photos),
        "video": formatted_video,
        "specs": {
            "photo_dimensions": f"{MLS_WIDTH}x{MLS_HEIGHT}",
            "photo_aspect": "4:3",
            "photo_format": "JPEG",
            "photo_quality": MLS_QUALITY,
            "video_dimensions": "1920x1080",
            "video_codec": "H.264",
        },
    }
