"""
Python wrapper for Remotion rendering.

Calls `npx remotion render` (or the ts-node CLI script) from the remotion/
directory to produce branded walkthrough videos and Instagram carousel clips.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Absolute path to the remotion project directory (sibling of app/)
REMOTION_DIR = Path(__file__).resolve().parent.parent.parent / "remotion"
RENDER_SCRIPT = REMOTION_DIR / "src" / "render.ts"


def remotion_available() -> bool:
    """Check whether Node.js / npx is available on this system."""
    if not REMOTION_DIR.exists():
        logger.debug("Remotion directory not found: %s", REMOTION_DIR)
        return False
    # Check for node_modules — need deps installed
    if not (REMOTION_DIR / "node_modules").exists():
        logger.debug("Remotion node_modules not found; run 'npm install' in %s", REMOTION_DIR)
        return False
    for cmd in ("node", "npx"):
        if shutil.which(cmd) is None:
            logger.debug("%s not found on PATH", cmd)
            return False
    return True


def _get_video_duration(mp4_path: str) -> float:
    """Probe video duration in seconds using ffprobe. Returns 10.0 as fallback."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                mp4_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        dur = float(result.stdout.strip())
        if dur > 0:
            return dur
    except Exception:
        pass
    return 10.0


def render_branded_video(
    raw_mp4: str,
    output_mp4: str,
    address: str,
    agent_name: str = "",
) -> bool:
    """
    Render a branded walkthrough video with intro/outro overlays.

    Args:
        raw_mp4: Path to the raw AI-generated walkthrough MP4.
        output_mp4: Path where the polished MP4 should be written.
        address: Property address to display in the intro.
        agent_name: Optional agent name for the intro card.

    Returns:
        True if rendering succeeded, False otherwise.
    """
    if not remotion_available():
        logger.warning("Remotion not available — skipping branded render")
        return False

    raw_path = Path(raw_mp4)
    if not raw_path.exists():
        logger.error("Raw video file not found: %s", raw_mp4)
        return False

    duration = _get_video_duration(raw_mp4)

    cmd = [
        "npx", "ts-node", str(RENDER_SCRIPT),
        "--input", str(raw_path.resolve()),
        "--output", str(Path(output_mp4).resolve()),
        "--address", address,
        "--type", "walkthrough",
        "--duration", str(duration),
    ]
    if agent_name:
        cmd.extend(["--agent", agent_name])

    logger.info("Running Remotion walkthrough render: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REMOTION_DIR),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for rendering
        )

        if result.returncode != 0:
            logger.error(
                "Remotion render failed (exit %d):\nstdout: %s\nstderr: %s",
                result.returncode,
                result.stdout[-2000:] if result.stdout else "",
                result.stderr[-2000:] if result.stderr else "",
            )
            return False

        if Path(output_mp4).exists():
            logger.info("Branded video rendered successfully: %s", output_mp4)
            return True
        else:
            logger.error("Remotion render completed but output file not found: %s", output_mp4)
            return False

    except subprocess.TimeoutExpired:
        logger.error("Remotion render timed out after 600s")
        return False
    except Exception:
        logger.exception("Unexpected error during Remotion render")
        return False


def render_instagram_carousel(
    raw_mp4: str,
    output_dir: str,
    address: str,
) -> list[str]:
    """
    Render 5 square Instagram carousel clips from the walkthrough video.

    Args:
        raw_mp4: Path to the raw AI-generated walkthrough MP4.
        output_dir: Directory where carousel clips should be written.
        address: Property address to display on each clip.

    Returns:
        List of paths to the rendered carousel MP4 clips, or empty list on failure.
    """
    if not remotion_available():
        logger.warning("Remotion not available — skipping carousel render")
        return []

    raw_path = Path(raw_mp4)
    if not raw_path.exists():
        logger.error("Raw video file not found: %s", raw_mp4)
        return []

    duration = _get_video_duration(raw_mp4)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "npx", "ts-node", str(RENDER_SCRIPT),
        "--input", str(raw_path.resolve()),
        "--output", str(out_path.resolve()),
        "--address", address,
        "--type", "carousel",
        "--duration", str(duration),
    ]

    logger.info("Running Remotion carousel render: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REMOTION_DIR),
            capture_output=True,
            text=True,
            timeout=900,  # 15 minute timeout for 5 clips
        )

        if result.returncode != 0:
            logger.error(
                "Remotion carousel render failed (exit %d):\nstdout: %s\nstderr: %s",
                result.returncode,
                result.stdout[-2000:] if result.stdout else "",
                result.stderr[-2000:] if result.stderr else "",
            )
            return []

        # Collect rendered clips
        clips = sorted(out_path.glob("carousel_*.mp4"))
        clip_paths = [str(c) for c in clips]

        if clip_paths:
            logger.info("Carousel rendered: %d clips in %s", len(clip_paths), output_dir)
        else:
            logger.warning("Carousel render completed but no clips found in %s", output_dir)

        return clip_paths

    except subprocess.TimeoutExpired:
        logger.error("Remotion carousel render timed out after 900s")
        return []
    except Exception:
        logger.exception("Unexpected error during Remotion carousel render")
        return []
