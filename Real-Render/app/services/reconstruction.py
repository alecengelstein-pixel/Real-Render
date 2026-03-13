"""3D reconstruction service.

Accepts walkthrough video, extracts frames, sends to Luma AI Captures API
for NeRF/Gaussian Splat reconstruction, downloads the resulting .glb mesh.
Used for Premium tier.

Provider abstraction: swap _reconstruct_luma() for another service if needed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_LUMA_CAPTURES_BASE = "https://webapp.engineeringlumalabs.com/api/v3"
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def reconstruction_available() -> bool:
    return bool(settings.luma_api_key) and ffmpeg_installed()


def ffmpeg_installed() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def extract_frames(
    video_path: str,
    output_dir: str,
    fps: float = 2.0,
    max_frames: int = 100,
) -> list[str]:
    """Extract frames from video at given FPS. Returns list of frame paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        "-frames:v", str(max_frames),
        "-q:v", "2",
        str(out / "frame_%04d.jpg"),
        "-y",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("ffmpeg frame extraction failed: %s", result.stderr[:500])
            return []
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg frame extraction timed out")
        return []

    frames = sorted(out.glob("frame_*.jpg"))
    logger.info("Extracted %d frames from %s", len(frames), video_path)
    return [str(f) for f in frames]


def _luma_headers() -> dict[str, str]:
    return {"Authorization": f"luma-api-key={settings.luma_api_key}"}


def _create_luma_capture(video_path: str, title: str) -> str | None:
    """Submit a video to Luma Captures API. Returns the capture slug."""
    try:
        with httpx.Client(timeout=60) as client:
            # Step 1: Create capture and get upload URL
            resp = client.post(
                f"{_LUMA_CAPTURES_BASE}/captures",
                headers=_luma_headers(),
                json={"title": title},
            )
            resp.raise_for_status()
            data = resp.json()
            slug = data.get("capture", {}).get("slug") or data.get("slug")
            upload_url = data.get("signedUrls", {}).get("source") or data.get("uploadUrl")

            if not slug:
                logger.error("Luma capture creation returned no slug: %s", data)
                return None

            # Step 2: Upload the video
            if upload_url:
                video_bytes = Path(video_path).read_bytes()
                up_resp = client.put(
                    upload_url,
                    content=video_bytes,
                    headers={"Content-Type": "video/mp4"},
                    timeout=300,
                )
                up_resp.raise_for_status()
                logger.info("Video uploaded to Luma for capture %s", slug)

            # Step 3: Trigger processing
            client.post(
                f"{_LUMA_CAPTURES_BASE}/captures/{slug}/trigger",
                headers=_luma_headers(),
            )
            logger.info("Luma capture triggered: %s", slug)
            return slug

    except Exception:
        logger.exception("Failed to create Luma capture")
        return None


def _poll_luma_capture(slug: str, timeout_secs: int = 3600) -> dict[str, Any] | None:
    """Poll Luma capture until complete. Returns capture info dict."""
    deadline = time.monotonic() + timeout_secs
    interval = 15

    with httpx.Client(timeout=30) as client:
        while time.monotonic() < deadline:
            try:
                resp = client.get(
                    f"{_LUMA_CAPTURES_BASE}/captures/{slug}",
                    headers=_luma_headers(),
                )
                resp.raise_for_status()
                data = resp.json()

                capture = data.get("capture", data)
                status = capture.get("status", "")
                progress = capture.get("progress", 0)

                logger.info("Luma capture %s: status=%s progress=%s%%", slug, status, progress)

                if status in ("complete", "done"):
                    return capture
                if status in ("failed", "error"):
                    logger.error("Luma capture failed: %s", capture.get("error", "unknown"))
                    return None

            except Exception:
                logger.exception("Error polling Luma capture %s", slug)

            time.sleep(interval)
            interval = min(interval * 1.5, 60)

    logger.error("Luma capture %s timed out after %ds", slug, timeout_secs)
    return None


def _download_luma_mesh(slug: str, output_path: str) -> bool:
    """Download the .glb mesh from a completed Luma capture."""
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            # Try the mesh download endpoint
            resp = client.get(
                f"{_LUMA_CAPTURES_BASE}/captures/{slug}/mesh",
                headers=_luma_headers(),
                params={"format": "glb"},
            )

            if resp.status_code == 200:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(resp.content)
                logger.info("Downloaded Luma mesh: %s (%d bytes)", output_path, len(resp.content))
                return True

            # Alternative: check capture info for download URLs
            info_resp = client.get(
                f"{_LUMA_CAPTURES_BASE}/captures/{slug}",
                headers=_luma_headers(),
            )
            info_resp.raise_for_status()
            capture = info_resp.json().get("capture", info_resp.json())

            # Look for mesh URL in artifacts
            artifacts = capture.get("artifacts", {})
            mesh_url = (
                artifacts.get("mesh_glb")
                or artifacts.get("mesh")
                or artifacts.get("textured_mesh")
            )

            if mesh_url:
                dl = client.get(mesh_url)
                dl.raise_for_status()
                Path(output_path).write_bytes(dl.content)
                logger.info("Downloaded Luma mesh from artifact URL: %s", output_path)
                return True

            logger.error("No mesh download available for capture %s", slug)
            return False

    except Exception:
        logger.exception("Failed to download Luma mesh for %s", slug)
        return False


def reconstruct_from_video(
    video_path: str,
    output_dir: str,
    job_id: str,
) -> dict[str, Any]:
    """Full 3D reconstruction pipeline: video → frames → Luma → .glb mesh.

    Returns dict with reconstruction results and paths.
    """
    if not reconstruction_available():
        return {"ok": False, "reason": "not_configured"}

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Find the video file
    vid = Path(video_path)
    if vid.is_dir():
        videos = [f for f in vid.iterdir() if f.suffix.lower() in _VIDEO_EXTS]
        if not videos:
            return {"ok": False, "reason": "no_video_found"}
        vid = max(videos, key=lambda f: f.stat().st_size)

    if not vid.exists():
        return {"ok": False, "reason": "video_not_found", "path": str(vid)}

    logger.info("Starting 3D reconstruction for job %s from %s", job_id, vid)

    # Step 1: Submit to Luma
    slug = _create_luma_capture(str(vid), title=f"Real-Render {job_id}")
    if not slug:
        return {"ok": False, "reason": "capture_creation_failed"}

    # Step 2: Poll until complete
    capture = _poll_luma_capture(slug)
    if not capture:
        return {"ok": False, "reason": "capture_processing_failed", "slug": slug}

    # Step 3: Download mesh
    glb_path = str(out / f"{job_id}_model.glb")
    if not _download_luma_mesh(slug, glb_path):
        return {"ok": False, "reason": "mesh_download_failed", "slug": slug}

    # Step 4: Extract preview frames for staging/enhancement
    frames_dir = str(out / "reconstruction_frames")
    frames = extract_frames(str(vid), frames_dir, fps=1.0, max_frames=30)

    return {
        "ok": True,
        "slug": slug,
        "glb_path": glb_path,
        "glb_size_bytes": Path(glb_path).stat().st_size,
        "frames": frames,
        "frames_dir": frames_dir,
        "cost_usd": settings.cost_per_reconstruction,
    }
