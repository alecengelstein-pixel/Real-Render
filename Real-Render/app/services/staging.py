"""Virtual staging service using Gemini image generation.

Sends room photos to Gemini with staging prompts, receives back
furnished/decorated versions. Used for Signature + Premium tiers
and the Custom Themed Staging add-on.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

STYLE_PROMPTS: dict[str, str] = {
    "modern": (
        "Use contemporary furniture with clean lines and neutral tones — grays, whites, "
        "warm beiges. Add a sectional sofa or bed appropriate for the room, a coffee table "
        "or nightstands, a simple rug, and minimal wall art. Keep it airy and inviting."
    ),
    "rustic": (
        "Use warm wood-tone furniture, natural materials, and farmhouse style. "
        "Add a reclaimed-wood dining table or bed frame, woven textiles, potted plants, "
        "and earth-tone accents. Cozy but not cluttered."
    ),
    "minimalist": (
        "Use sparse, essential furniture only. One key piece per room — a low-profile "
        "sofa, a simple platform bed, a sleek dining table. Muted palette, lots of "
        "negative space. No clutter, no heavy patterns."
    ),
    "luxury": (
        "Use high-end designer furniture with rich materials — marble surfaces, velvet "
        "upholstery, brass or gold accents, statement lighting fixtures. "
        "Add a large plush rug, designer art pieces, and fresh flowers."
    ),
    "coastal": (
        "Light, breezy coastal style. White and blue palette, natural wicker and rattan "
        "furniture, linen textiles, sea-glass accents, driftwood decor."
    ),
    "mid_century": (
        "Mid-century modern style. Tapered wood legs, organic shapes, warm walnut tones, "
        "mustard and teal accent colors, iconic furniture silhouettes."
    ),
    "scandinavian": (
        "Scandinavian style. Light wood furniture, white walls, hygge textiles, "
        "functional minimalism, soft sheepskin throws, simple pendant lighting."
    ),
}

_BASE_PROMPT = (
    "You are a professional virtual staging artist for real estate photography. "
    "Take this room photograph and add realistic furniture and decor appropriate "
    "for the room type (living room, bedroom, kitchen, dining room, etc). "
    "CRITICAL RULES: "
    "1) Keep the exact room structure — walls, floors, windows, ceiling, doors UNCHANGED. "
    "2) Match the existing lighting and perspective perfectly. "
    "3) The result must look like an actual photograph, not a 3D render. "
    "4) Do NOT add people. "
    "5) Furniture should be proportionally correct for the room size. "
)


def staging_available() -> bool:
    return bool(settings.veo_api_key)


def _headers() -> dict[str, str]:
    return {"x-goog-api-key": settings.veo_api_key or ""}


def stage_photo(
    image_path: str,
    output_path: str,
    style: str = "modern",
) -> bool:
    """Stage a single room photo. Returns True on success."""
    img_path = Path(image_path)
    if not img_path.exists():
        logger.error("Image not found: %s", image_path)
        return False

    style_detail = STYLE_PROMPTS.get(style, STYLE_PROMPTS["modern"])
    prompt = f"{_BASE_PROMPT}\nStyle direction: {style_detail}"

    img_bytes = img_path.read_bytes()
    b64 = base64.b64encode(img_bytes).decode()

    suffix = img_path.suffix.lower()
    mime_map = {".png": "image/png", ".webp": "image/webp"}
    mime = mime_map.get(suffix, "image/jpeg")

    model = settings.gemini_image_model
    url = f"{_GEMINI_BASE}/v1beta/models/{model}:generateContent"

    body: dict[str, Any] = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime, "data": b64}},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
    }

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, headers=_headers(), json=body)
            resp.raise_for_status()
            result = resp.json()

        candidates = result.get("candidates", [])
        for candidate in candidates:
            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                inline = part.get("inlineData")
                if inline and inline.get("data"):
                    image_data = base64.b64decode(inline["data"])
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(image_data)
                    logger.info("Staged photo saved: %s", output_path)
                    return True

        logger.warning("Gemini returned no image for %s", image_path)
        return False

    except httpx.HTTPStatusError as exc:
        logger.error("Gemini staging error for %s: %s %s",
                     image_path, exc.response.status_code, exc.response.text[:500])
        return False
    except Exception:
        logger.exception("Failed to stage photo: %s", image_path)
        return False


def stage_all_rooms(
    input_dir: str,
    output_dir: str,
    style: str = "modern",
    max_photos: int = 20,
) -> dict[str, Any]:
    """Stage all room photos. Returns dict with paths and stats."""
    if not staging_available():
        return {"ok": False, "reason": "api_key_missing", "staged": []}

    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    images = sorted(
        [p for p in in_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )[:max_photos]

    if not images:
        return {"ok": False, "reason": "no_images", "staged": []}

    staged: list[dict[str, str]] = []
    failed: list[str] = []
    cost = 0.0

    def _do_stage(img: Path) -> tuple[Path, bool]:
        out_file = out_path / f"staged_{img.name}"
        ok = stage_photo(str(img), str(out_file), style=style)
        return img, ok

    max_workers = min(4, len(images))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do_stage, img): img for img in images}
        for future in as_completed(futures):
            img, ok = future.result()
            if ok:
                out_file = out_path / f"staged_{img.name}"
                staged.append({"original": str(img), "staged": str(out_file)})
                cost += settings.cost_per_staging_image
            else:
                failed.append(str(img))

    logger.info("Staging complete: %d/%d staged", len(staged), len(images))

    return {
        "ok": len(staged) > 0,
        "style": style,
        "staged_count": len(staged),
        "failed_count": len(failed),
        "staged": staged,
        "failed": failed,
        "cost_usd": cost,
    }
