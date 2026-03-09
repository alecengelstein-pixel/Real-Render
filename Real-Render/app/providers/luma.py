from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .base import ProviderResult, VideoProvider

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}

# Camera motions good for real estate walkthroughs
_WALKTHROUGH_MOTIONS = ["Push In", "Orbit Left", "Pull Out", "Pan Right", "Crane Up"]


class LumaProvider(VideoProvider):
    """Luma Dream Machine — cinematic video from property photos."""

    name = "luma"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.luma_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _pick_best_image(self, input_dir: str) -> Path | None:
        """Pick the largest image file as the hero frame."""
        images = []
        for p in Path(input_dir).iterdir():
            if p.suffix.lower() in _IMAGE_EXTS and p.is_file():
                images.append(p)
        if not images:
            return None
        return max(images, key=lambda p: p.stat().st_size)

    def _image_url(self, job_id: str, image_path: Path, input_dir: str) -> str | None:
        """Build a public URL for an input image so Luma can fetch it."""
        public_base = settings.public_base_url
        if not public_base:
            logger.warning("PUBLIC_BASE_URL not set — cannot do image-to-video")
            return None
        relative = image_path.relative_to(Path(input_dir)).as_posix()
        url = f"{public_base.rstrip('/')}/api/v1/jobs/{job_id}/inputs/{relative}"
        logger.info("Image URL for Luma: %s", url)
        return url

    def _poll(self, client: httpx.Client, generation_id: str) -> dict[str, Any]:
        """Poll generation until completed or failed."""
        base = settings.luma_api_base_url.rstrip("/")
        url = f"{base}/dream-machine/v1/generations/{generation_id}"
        interval = settings.poll_interval_seconds
        deadline = time.monotonic() + settings.poll_max_wait_seconds

        while True:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            state = data.get("state", "")
            logger.info("Poll generation %s → state=%s", generation_id, state)

            if state == "completed":
                return data
            if state == "failed":
                reason = data.get("failure_reason", "unknown")
                raise RuntimeError(f"Luma generation failed: {reason}")

            if time.monotonic() + interval > deadline:
                raise TimeoutError(
                    f"Luma generation did not complete within {settings.poll_max_wait_seconds}s"
                )
            time.sleep(interval)
            interval = min(interval * 2, 60)

    def _build_prompt(self, options: dict[str, Any]) -> str:
        parts = ["Smooth cinematic walkthrough of a real estate property interior."]
        furnishing = options.get("furnishing", "as_is")
        lighting = options.get("lighting", "natural")

        if furnishing == "staged":
            parts.append("The rooms are virtually staged with modern furniture and decor.")
        elif furnishing == "empty":
            parts.append("The rooms are completely empty, showing bare floors and walls.")

        lighting_map = {
            "warm": "Warm golden-hour lighting fills the space.",
            "cool": "Cool natural daylight illuminates the rooms.",
            "night": "Evening ambiance with soft interior lighting.",
        }
        if lighting in lighting_map:
            parts.append(lighting_map[lighting])

        parts.append("Camera moves steadily through the space, showcasing layout, finishes, and natural light.")
        return " ".join(parts)

    def make_walkthrough(
        self,
        *,
        job_id: str,
        outputs_dir: str,
        options: dict[str, Any],
        reconstruction: dict[str, Any] | None,
        input_dir: str = "",
    ) -> ProviderResult:
        if not settings.luma_api_key or not settings.luma_api_base_url:
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={"error": "Luma not configured (set LUMA_API_KEY and LUMA_API_BASE_URL)."},
            )

        base = settings.luma_api_base_url.rstrip("/")
        out = Path(outputs_dir)
        out.mkdir(parents=True, exist_ok=True)

        try:
            prompt = self._build_prompt(options)
            logger.info("Luma prompt: %s", prompt)

            with httpx.Client(timeout=120) as client:
                # Build request body
                body: dict[str, Any] = {
                    "prompt": prompt,
                    "model": "ray-2",
                    "aspect_ratio": "16:9",
                    "duration": "5s",
                    "resolution": "720p",
                }

                # Image-to-video: use best property photo as starting frame
                hero = self._pick_best_image(input_dir) if input_dir else None
                if hero:
                    image_url = self._image_url(job_id, hero, input_dir)
                    if image_url:
                        body["keyframes"] = {
                            "frame0": {
                                "type": "image",
                                "url": image_url,
                            }
                        }
                        logger.info("Using hero image %s for image-to-video", hero.name)

                # Submit generation
                resp = client.post(
                    f"{base}/dream-machine/v1/generations",
                    headers=self._headers(),
                    json=body,
                )
                resp.raise_for_status()
                gen = resp.json()
                gen_id = gen.get("id", "")
                logger.info("Started Luma generation: %s", gen_id)

                # Poll until done
                result = self._poll(client, gen_id)

                # Download video
                video_url = (result.get("assets") or {}).get("video")
                if not video_url:
                    raise RuntimeError(f"No video URL in completed generation: {result}")

                mp4_path = out / f"{job_id}_walkthrough_luma.mp4"
                with client.stream("GET", video_url) as dl:
                    dl.raise_for_status()
                    with open(mp4_path, "wb") as f:
                        for chunk in dl.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                logger.info("Downloaded walkthrough video → %s (%d bytes)", mp4_path, mp4_path.stat().st_size)

            return ProviderResult(
                provider=self.name,
                ok=True,
                data={
                    "job_id": job_id,
                    "provider": "luma",
                    "generation_id": gen_id,
                    "artifacts": {"walkthrough_mp4": str(mp4_path)},
                },
                cost_usd=settings.cost_per_luma_generation,
            )

        except Exception as exc:
            logger.exception("Luma walkthrough failed for job %s", job_id)
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={"error": str(exc)},
            )
