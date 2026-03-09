from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageStat


@dataclass(frozen=True)
class PhotoQC:
    path: str
    width: int
    height: int
    megapixels: float
    file_bytes: int
    focus_score: float


def _focus_score(img: Image.Image) -> float:
    """
    Cheap blur-ish heuristic: compare image to a GaussianBlur version.
    Higher scores tend to indicate sharper images.
    """
    gray = img.convert("L")
    blurred = gray.filter(ImageFilter.GaussianBlur(radius=2))
    diff = ImageChops.difference(gray, blurred)
    stat = ImageStat.Stat(diff)
    # RMS is a nice single-number "high frequency energy" proxy
    return float(stat.rms[0])


def run_qc(input_dir: str) -> dict[str, Any]:
    p = Path(input_dir)
    photos: list[PhotoQC] = []

    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG", "*.JPEG", "*.PNG", "*.WEBP"):
        for fp in sorted(p.glob(ext)):
            try:
                with Image.open(fp) as img:
                    w, h = img.size
                    mp = (w * h) / 1_000_000.0
                    score = _focus_score(img)
                photos.append(
                    PhotoQC(
                        path=str(fp),
                        width=w,
                        height=h,
                        megapixels=mp,
                        file_bytes=fp.stat().st_size,
                        focus_score=score,
                    )
                )
            except Exception:
                # Corrupt image or unsupported; skip but record later
                photos.append(
                    PhotoQC(
                        path=str(fp),
                        width=0,
                        height=0,
                        megapixels=0.0,
                        file_bytes=fp.stat().st_size if fp.exists() else 0,
                        focus_score=0.0,
                    )
                )

    focus_scores = [p.focus_score for p in photos if p.width and p.height]
    megapixels = [p.megapixels for p in photos if p.width and p.height]

    def pct(values: list[float], q: float) -> float | None:
        if not values:
            return None
        values = sorted(values)
        idx = int(round((len(values) - 1) * q))
        return float(values[idx])

    qc = {
        "photo_count": len(photos),
        "min_megapixels": min(megapixels) if megapixels else None,
        "p25_megapixels": pct(megapixels, 0.25),
        "median_megapixels": pct(megapixels, 0.50),
        "min_focus_score": min(focus_scores) if focus_scores else None,
        "p25_focus_score": pct(focus_scores, 0.25),
        "median_focus_score": pct(focus_scores, 0.50),
        "photos": [
            {
                "path": ph.path,
                "width": ph.width,
                "height": ph.height,
                "megapixels": ph.megapixels,
                "file_bytes": ph.file_bytes,
                "focus_score": ph.focus_score,
            }
            for ph in photos
        ],
    }

    # Simple gating guidance (tweak as you learn your pipeline)
    problems: list[str] = []
    if qc["photo_count"] < 20:
        problems.append("Low photo count (<20). Geometric reconstruction may be unreliable.")
    if qc["median_megapixels"] is not None and qc["median_megapixels"] < 2.0:
        problems.append("Low resolution (median <2MP).")
    if qc["median_focus_score"] is not None and qc["median_focus_score"] < 5.0:
        problems.append("Many images appear blurry (low focus score).")
    qc["problems"] = problems

    return qc



