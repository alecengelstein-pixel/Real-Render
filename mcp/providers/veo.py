from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .base import ProviderResult, VideoProvider

logger = logging.getLogger(__name__)

_GEMINI_BASE = "https://generativelanguage.googleapis.com"


class VeoProvider(VideoProvider):
    """Google Veo video generation via the Gemini REST API."""

    name = "veo"

    def _api_base(self) -> str:
        """Return the Gemini API base, overriding Vertex URLs to the
        key-authenticated Gemini endpoint."""
        base = (settings.veo_api_base_url or "").rstrip("/")
        if not base or "aiplatform.googleapis.com" in base:
            return _GEMINI_BASE
        return base

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": settings.veo_api_key or ""}

    @staticmethod
    def _build_prompt(options: dict[str, Any]) -> str:
        furnishing = options.get("furnishing", "as_is")
        lighting = options.get("lighting", "natural")

        parts = ["Smooth cinematic walkthrough of a real estate property interior."]
        if furnishing == "staged":
            parts.append("The rooms are virtually staged with modern furniture.")
        elif furnishing == "empty":
            parts.append("The rooms are completely empty, showing bare floors and walls.")

        lighting_map = {
            "warm": "Warm golden-hour lighting fills the space.",
            "cool": "Cool daylight illuminates the rooms.",
            "night": "Evening ambiance with soft interior lighting.",
        }
        if lighting in lighting_map:
            parts.append(lighting_map[lighting])

        parts.append("Camera moves steadily through each room, showcasing layout and finishes.")
        return " ".join(parts)

    def _find_reference_image(self, outputs_dir: str) -> str | None:
        """If reconstruction produced images, pick the first one for
        image-to-video mode."""
        out = Path(outputs_dir)
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            matches = sorted(out.glob(ext))
            if matches:
                return str(matches[0])
        return None

    def _poll_operation(self, client: httpx.Client, operation_name: str) -> dict[str, Any]:
        """Poll a long-running operation until done."""
        base = self._api_base()
        url = f"{base}/v1beta/{operation_name}"
        interval = settings.poll_interval_seconds
        deadline = time.monotonic() + settings.poll_max_wait_seconds

        while True:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

            logger.info("Poll %s → done=%s", operation_name, data.get("done"))

            if data.get("done"):
                if "error" in data:
                    raise RuntimeError(f"Veo operation failed: {data['error']}")
                return data

            if time.monotonic() + interval > deadline:
                raise TimeoutError(
                    f"Veo operation did not complete within {settings.poll_max_wait_seconds}s"
                )
            time.sleep(interval)
            interval = min(interval * 2, 60)

    def make_walkthrough(
        self,
        *,
        job_id: str,
        outputs_dir: str,
        options: dict[str, Any],
        reconstruction: dict[str, Any] | None,
    ) -> ProviderResult:
        if not settings.veo_api_key:
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={"error": "Veo provider not configured (set VEO_API_KEY)."},
            )

        base = self._api_base()
        model = settings.veo_model
        out = Path(outputs_dir)
        out.mkdir(parents=True, exist_ok=True)

        try:
            prompt = self._build_prompt(options)
            logger.info("Veo prompt: %s", prompt)

            # Build request body
            instances: list[dict[str, Any]] = [{"prompt": prompt}]

            # Image-to-video: attach a reference frame if available
            ref_image = self._find_reference_image(outputs_dir)
            if ref_image:
                img_bytes = Path(ref_image).read_bytes()
                b64 = base64.b64encode(img_bytes).decode()
                instances[0]["image"] = {
                    "bytesBase64Encoded": b64,
                    "mimeType": "image/png",
                }
                logger.info("Using reference image for image-to-video: %s", ref_image)

            body: dict[str, Any] = {
                "instances": instances,
                "parameters": {
                    "aspectRatio": "16:9",
                    "personGeneration": "dont_allow",
                },
            }

            with httpx.Client(timeout=120) as client:
                # 1. Submit generation request
                url = f"{base}/v1beta/models/{model}:predictLongRunning"
                resp = client.post(url, headers=self._headers(), json=body)
                resp.raise_for_status()
                op = resp.json()
                op_name = op.get("name", "")
                logger.info("Started Veo operation: %s", op_name)

                # 2. Poll until done
                result = self._poll_operation(client, op_name)

                # 3. Extract and download video
                response_body = result.get("response", {})
                predictions = response_body.get("predictions", [])
                if not predictions:
                    raise RuntimeError("Veo returned no predictions")

                mp4_path = out / f"{job_id}_walkthrough.mp4"

                # Predictions may contain base64 video or a URI
                pred = predictions[0]
                if "bytesBase64Encoded" in pred:
                    video_bytes = base64.b64decode(pred["bytesBase64Encoded"])
                    mp4_path.write_bytes(video_bytes)
                    logger.info("Wrote video from base64 (%d bytes)", len(video_bytes))
                elif "uri" in pred:
                    video_url = pred["uri"]
                    with client.stream("GET", video_url) as dl:
                        dl.raise_for_status()
                        with open(mp4_path, "wb") as f:
                            for chunk in dl.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                    logger.info("Downloaded video from %s", video_url)
                else:
                    raise RuntimeError(f"Unexpected prediction format: {list(pred.keys())}")

            return ProviderResult(
                provider=self.name,
                ok=True,
                data={
                    "job_id": job_id,
                    "provider": "veo",
                    "artifacts": {"walkthrough_mp4": str(mp4_path)},
                },
            )

        except Exception as exc:
            logger.exception("Veo walkthrough failed for job %s", job_id)
            return ProviderResult(
                provider=self.name,
                ok=False,
                data={"error": str(exc)},
            )
