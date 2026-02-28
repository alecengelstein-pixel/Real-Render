from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .base import ProviderResult, ReconstructionProvider

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}


class LumaProvider(ReconstructionProvider):
    """Luma AI 3-D capture → reconstruction → artifact download."""

    name = "luma"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"luma-api-key={settings.luma_api_key}"}

    def _zip_images(self, input_dir: str) -> bytes:
        """Create an in-memory zip of all image files in *input_dir*."""
        buf = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(Path(input_dir).iterdir()):
                if p.suffix.lower() in _IMAGE_EXTS and p.is_file():
                    zf.write(p, p.name)
                    count += 1
        if count == 0:
            raise ValueError(f"No images found in {input_dir}")
        logger.info("Zipped %d images from %s (%d bytes)", count, input_dir, buf.tell())
        buf.seek(0)
        return buf.read()

    def _poll(self, client: httpx.Client, url: str) -> dict[str, Any]:
        """Poll *url* until the resource reaches a terminal state."""
        interval = settings.poll_interval_seconds
        deadline = time.monotonic() + settings.poll_max_wait_seconds

        while True:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "").lower()
            logger.info("Poll %s → status=%s", url, status)

            if status in ("complete", "completed", "done"):
                return data
            if status in ("failed", "error"):
                raise RuntimeError(f"Luma capture failed: {data}")

            if time.monotonic() + interval > deadline:
                raise TimeoutError(
                    f"Luma capture did not complete within {settings.poll_max_wait_seconds}s"
                )
            time.sleep(interval)
            # Exponential backoff capped at 60s
            interval = min(interval * 2, 60)

    def _download(self, client: httpx.Client, url: str, dest: Path) -> Path:
        """Stream-download a file to *dest*."""
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        logger.info("Downloaded %s → %s (%d bytes)", url, dest, dest.stat().st_size)
        return dest

    def reconstruct(
        self,
        *,
        job_id: str,
        input_dir: str,
        outputs_dir: str,
        options: dict[str, Any],
    ) -> ProviderResult:
        if not settings.luma_api_key or not settings.luma_api_base_url:
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={
                    "error": "Luma provider not configured (set LUMA_API_KEY and LUMA_API_BASE_URL).",
                },
            )

        base = settings.luma_api_base_url.rstrip("/")
        out = Path(outputs_dir)
        out.mkdir(parents=True, exist_ok=True)

        try:
            zip_bytes = self._zip_images(input_dir)

            with httpx.Client(timeout=120) as client:
                # 1. Create capture
                resp = client.post(
                    f"{base}/api/v3/captures",
                    headers=self._headers(),
                    files={"file": ("images.zip", zip_bytes, "application/zip")},
                    data={
                        "title": f"real-render-{job_id}",
                        "camera_model": options.get(
                            "camera_model", settings.luma_camera_model
                        ),
                    },
                )
                resp.raise_for_status()
                capture = resp.json()
                capture_slug = capture.get("slug") or capture.get("uuid") or capture.get("id")
                logger.info("Created Luma capture: %s", capture_slug)

                # 2. Poll until complete
                poll_url = f"{base}/api/v3/captures/{capture_slug}"
                result = self._poll(client, poll_url)

                # 3. Download artifacts
                artifacts: dict[str, str | None] = {
                    "mesh_glb": None,
                    "pointcloud_ply": None,
                    "splat": None,
                    "viewer_url": None,
                }

                # Luma exposes artifacts under various response shapes; try common keys
                exports = result.get("exports", result.get("artifacts", {}))
                artifact_map = {
                    "mesh_glb": ("glb", "mesh_glb"),
                    "pointcloud_ply": ("ply", "pointcloud_ply"),
                    "splat": ("splat", "gs", "gaussian_splat"),
                }

                for key, candidates in artifact_map.items():
                    for c in candidates:
                        url = exports.get(c)
                        if url:
                            ext = key.split("_")[-1]
                            dest = out / f"{job_id}.{ext}"
                            self._download(client, url, dest)
                            artifacts[key] = str(dest)
                            break

                # Viewer URL (not downloaded, just recorded)
                viewer = result.get("viewer_url") or result.get("url")
                if viewer:
                    artifacts["viewer_url"] = viewer

            return ProviderResult(
                provider=self.name,
                ok=True,
                data={
                    "job_id": job_id,
                    "provider": "luma",
                    "capture_slug": capture_slug,
                    "artifacts": artifacts,
                },
            )

        except Exception as exc:
            logger.exception("Luma reconstruction failed for job %s", job_id)
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={"error": str(exc)},
            )
