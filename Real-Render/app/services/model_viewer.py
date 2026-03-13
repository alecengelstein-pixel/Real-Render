"""3D model viewer generator.

Creates a self-contained HTML page using Google's <model-viewer> web component.
The viewer supports rotation, zoom, pan, and AR on mobile devices.
Used for Premium tier 3D model delivery.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def build_model_viewer(
    glb_path: str,
    output_html: str,
    property_info: dict[str, Any] | None = None,
    embed_model: bool = False,
) -> bool:
    """Generate an interactive 3D viewer HTML page.

    Args:
        glb_path: Path to the .glb model file.
        output_html: Where to write the HTML file.
        property_info: Optional dict with address, agent_name, etc.
        embed_model: If True, base64-embed the .glb in the HTML (larger file, fully portable).
                     If False, reference it as a relative path (smaller HTML, needs co-located .glb).
    """
    glb = Path(glb_path)
    if not glb.exists():
        logger.error("GLB file not found: %s", glb_path)
        return False

    info = property_info or {}
    address = info.get("address", "Property")
    agent = info.get("agent_name", "")

    if embed_model:
        glb_bytes = glb.read_bytes()
        b64 = base64.b64encode(glb_bytes).decode()
        model_src = f"data:model/gltf-binary;base64,{b64}"
    else:
        model_src = glb.name

    subtitle = f"Presented by {agent}" if agent else "Interactive 3D Tour"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3D Tour — {_esc(address)}</title>
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0a; color: #fff; overflow: hidden; height: 100vh; }}
  .header {{ position: fixed; top: 0; left: 0; right: 0; z-index: 10; padding: 20px 28px; background: linear-gradient(to bottom, rgba(0,0,0,0.7), transparent); }}
  .header h1 {{ font-size: 22px; font-weight: 600; letter-spacing: -0.3px; }}
  .header p {{ font-size: 14px; opacity: 0.7; margin-top: 4px; }}
  model-viewer {{ width: 100vw; height: 100vh; background: #111; --poster-color: #111; }}
  .controls {{ position: fixed; bottom: 0; left: 0; right: 0; z-index: 10; padding: 16px 28px 24px; background: linear-gradient(to top, rgba(0,0,0,0.7), transparent); display: flex; align-items: center; justify-content: space-between; }}
  .controls .hint {{ font-size: 13px; opacity: 0.5; }}
  .badge {{ display: inline-block; padding: 6px 14px; border-radius: 20px; background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); font-size: 12px; font-weight: 500; letter-spacing: 0.5px; text-transform: uppercase; }}
  .ar-btn {{ background: #2563eb; color: white; border: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; }}
  .ar-btn:hover {{ background: #1d4ed8; }}
  @media (max-width: 640px) {{
    .header h1 {{ font-size: 18px; }}
    .header {{ padding: 14px 18px; }}
    .controls {{ padding: 12px 18px 20px; }}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>{_esc(address)}</h1>
  <p>{_esc(subtitle)}</p>
</div>

<model-viewer
  src="{model_src}"
  alt="3D model of {_esc(address)}"
  camera-controls
  touch-action="pan-y"
  auto-rotate
  auto-rotate-delay="3000"
  rotation-per-second="15deg"
  shadow-intensity="0.5"
  environment-image="neutral"
  exposure="1.1"
  ar
  ar-modes="webxr scene-viewer quick-look"
  camera-orbit="45deg 65deg auto"
  min-camera-orbit="auto auto auto"
  max-camera-orbit="auto 90deg auto"
  interpolation-decay="100"
  loading="eager"
>
  <button class="ar-btn" slot="ar-button">View in AR</button>
</model-viewer>

<div class="controls">
  <span class="badge">Open Door Cinematic</span>
  <span class="hint">Drag to rotate &bull; Pinch to zoom &bull; Two fingers to pan</span>
</div>
</body>
</html>"""

    try:
        out = Path(output_html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        logger.info("3D viewer HTML written: %s", output_html)
        return True
    except Exception:
        logger.exception("Failed to write 3D viewer HTML")
        return False


def _esc(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
