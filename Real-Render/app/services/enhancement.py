"""Photo enhancement service using PIL.

Applies auto-contrast, sharpness, brightness, and color correction
to produce listing-ready room imagery. Used for all tiers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def enhance_photo(image_path: str, output_path: str) -> bool:
    """Enhance a single photo. Returns True on success."""
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")

            # Auto white-balance via autocontrast
            img = ImageOps.autocontrast(img, cutoff=0.5)

            # Sharpness boost — makes details pop for listings
            img = ImageEnhance.Sharpness(img).enhance(1.3)

            # Slight brightness lift — brighter rooms look more inviting
            img = ImageEnhance.Brightness(img).enhance(1.05)

            # Slight color saturation boost
            img = ImageEnhance.Color(img).enhance(1.1)

            # Slight contrast boost
            img = ImageEnhance.Contrast(img).enhance(1.05)

            # Mild denoise via slight blur then sharpen
            img = img.filter(ImageFilter.SMOOTH_MORE)
            img = ImageEnhance.Sharpness(img).enhance(1.4)

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

            # Save at high quality
            if out.suffix.lower() == ".png":
                img.save(str(out), "PNG", optimize=True)
            else:
                img.save(str(out), "JPEG", quality=92, optimize=True)

            logger.info("Enhanced photo saved: %s", output_path)
            return True

    except Exception:
        logger.exception("Failed to enhance photo: %s", image_path)
        return False


def enhance_all_photos(input_dir: str, output_dir: str) -> dict[str, Any]:
    """Enhance all photos in input_dir, saving to output_dir."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    images = sorted(
        [p for p in in_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()]
    )

    if not images:
        return {"ok": False, "reason": "no_images", "enhanced": []}

    enhanced: list[dict[str, str]] = []
    failed: list[str] = []

    for img in images:
        out_file = out_path / f"enhanced_{img.name}"
        if enhance_photo(str(img), str(out_file)):
            enhanced.append({"original": str(img), "enhanced": str(out_file)})
        else:
            failed.append(str(img))

    logger.info("Enhancement complete: %d/%d enhanced", len(enhanced), len(images))

    return {
        "ok": len(enhanced) > 0,
        "enhanced_count": len(enhanced),
        "failed_count": len(failed),
        "enhanced": enhanced,
        "failed": failed,
    }
