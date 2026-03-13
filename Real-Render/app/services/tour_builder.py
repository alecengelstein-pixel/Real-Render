"""Interactive virtual tour HTML generator.

Creates a self-contained HTML page with room-by-room navigation,
smooth transitions, and a professional real estate presentation.
Used for Signature (photo-based) and Premium (3D model + photos).
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Standard room labels — matched to photos by order
_ROOM_LABELS = [
    "Living Room", "Kitchen", "Dining Room", "Primary Bedroom",
    "Bedroom 2", "Bedroom 3", "Bathroom", "Master Bath",
    "Home Office", "Laundry", "Hallway", "Patio",
    "Garage", "Exterior", "Pool", "Garden",
    "Den", "Foyer", "Basement", "Attic",
]


def _optimize_image_for_tour(image_path: str, max_width: int = 1600) -> str:
    """Load an image, resize if needed, return as base64 data URL."""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def build_tour(
    image_dir: str,
    output_html: str,
    property_info: dict[str, Any] | None = None,
    model_viewer_url: str | None = None,
) -> bool:
    """Build a self-contained interactive virtual tour HTML.

    Args:
        image_dir: Directory containing room photos (enhanced or staged).
        output_html: Where to write the HTML file.
        property_info: Dict with address, agent_name, etc.
        model_viewer_url: Optional URL to the 3D model viewer (Premium only).
    """
    in_path = Path(image_dir)
    images = sorted(
        [p for p in in_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()]
    )

    if not images:
        logger.warning("No images found for tour in %s", image_dir)
        return False

    info = property_info or {}
    address = info.get("address", "Property Tour")
    agent = info.get("agent_name", "")

    # Build room data (image + label)
    rooms_js = []
    for i, img_path in enumerate(images):
        label = _ROOM_LABELS[i] if i < len(_ROOM_LABELS) else f"Room {i + 1}"
        try:
            data_url = _optimize_image_for_tour(str(img_path))
            rooms_js.append(f'{{label:"{_esc(label)}",src:"{data_url}"}}')
        except Exception:
            logger.warning("Skipping unreadable image: %s", img_path)

    if not rooms_js:
        return False

    rooms_array = ",\n".join(rooms_js)

    model_btn = ""
    if model_viewer_url:
        model_btn = f"""
    <a href="{_esc(model_viewer_url)}" target="_blank" class="model-btn">View 3D Model</a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Virtual Tour — {_esc(address)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a0a;color:#fff;overflow:hidden;height:100vh;user-select:none}}
.viewer{{position:relative;width:100vw;height:100vh;overflow:hidden}}
.slide{{position:absolute;inset:0;opacity:0;transition:opacity 0.6s ease;background-size:cover;background-position:center;transform:scale(1.05)}}
.slide.active{{opacity:1;transform:scale(1);transition:opacity 0.6s ease,transform 8s ease-out}}
.header{{position:fixed;top:0;left:0;right:0;z-index:20;padding:20px 28px;background:linear-gradient(to bottom,rgba(0,0,0,0.75),transparent);display:flex;justify-content:space-between;align-items:flex-start}}
.header-left h1{{font-size:22px;font-weight:600;letter-spacing:-0.3px}}
.header-left p{{font-size:13px;opacity:0.6;margin-top:3px}}
.room-label{{position:fixed;top:50%;left:28px;transform:translateY(-50%);z-index:20}}
.room-label span{{display:block;font-size:28px;font-weight:700;letter-spacing:-0.5px;text-shadow:0 2px 20px rgba(0,0,0,0.8)}}
.room-label small{{font-size:13px;opacity:0.6}}
.nav{{position:fixed;bottom:0;left:0;right:0;z-index:20;background:linear-gradient(to top,rgba(0,0,0,0.8),transparent);padding:12px 28px 24px}}
.thumbnails{{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;padding:8px 0}}
.thumbnails::-webkit-scrollbar{{display:none}}
.thumb{{width:72px;height:48px;border-radius:6px;background-size:cover;background-position:center;cursor:pointer;opacity:0.5;transition:all 0.3s;flex-shrink:0;border:2px solid transparent}}
.thumb.active{{opacity:1;border-color:#2563eb;transform:scale(1.08)}}
.thumb:hover{{opacity:0.8}}
.nav-row{{display:flex;justify-content:space-between;align-items:center;margin-top:10px}}
.nav-btn{{background:rgba(255,255,255,0.1);backdrop-filter:blur(10px);border:none;color:#fff;width:40px;height:40px;border-radius:50%;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center;transition:background 0.2s}}
.nav-btn:hover{{background:rgba(255,255,255,0.2)}}
.counter{{font-size:13px;opacity:0.5;font-variant-numeric:tabular-nums}}
.badge{{display:inline-block;padding:5px 12px;border-radius:16px;background:rgba(255,255,255,0.1);backdrop-filter:blur(10px);font-size:11px;font-weight:500;letter-spacing:0.5px;text-transform:uppercase}}
.model-btn{{display:inline-block;padding:8px 16px;border-radius:8px;background:#2563eb;color:#fff;text-decoration:none;font-size:13px;font-weight:500;transition:background 0.2s}}
.model-btn:hover{{background:#1d4ed8}}
.autoplay-btn{{background:none;border:1px solid rgba(255,255,255,0.3);color:#fff;padding:4px 12px;border-radius:16px;font-size:12px;cursor:pointer}}
.autoplay-btn.playing{{border-color:#2563eb;color:#2563eb}}
@media(max-width:640px){{
  .header{{padding:14px 18px}}
  .header-left h1{{font-size:18px}}
  .room-label span{{font-size:22px}}
  .nav{{padding:10px 18px 20px}}
}}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <h1>{_esc(address)}</h1>
    <p>{_esc(f'Presented by {agent}' if agent else 'Virtual Tour')}</p>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    {model_btn}
    <span class="badge">Open Door Cinematic</span>
  </div>
</div>

<div class="room-label">
  <span id="roomName"></span>
  <small id="roomSub"></small>
</div>

<div class="viewer" id="viewer"></div>

<div class="nav">
  <div class="thumbnails" id="thumbs"></div>
  <div class="nav-row">
    <button class="nav-btn" id="prev">&#8249;</button>
    <span class="counter" id="counter"></span>
    <button class="autoplay-btn" id="autoplay">Auto</button>
    <button class="nav-btn" id="next">&#8250;</button>
  </div>
</div>

<script>
const rooms=[
{rooms_array}
];
let cur=0,autoId=null;
const viewer=document.getElementById('viewer');
const thumbsEl=document.getElementById('thumbs');

// Create slides and thumbnails
rooms.forEach((r,i)=>{{
  const s=document.createElement('div');
  s.className='slide'+(i===0?' active':'');
  s.style.backgroundImage='url('+r.src+')';
  s.dataset.idx=i;
  viewer.appendChild(s);
  const t=document.createElement('div');
  t.className='thumb'+(i===0?' active':'');
  t.style.backgroundImage='url('+r.src+')';
  t.onclick=()=>go(i);
  thumbsEl.appendChild(t);
}});

function go(i){{
  if(i<0)i=rooms.length-1;if(i>=rooms.length)i=0;
  document.querySelectorAll('.slide').forEach((s,idx)=>s.classList.toggle('active',idx===i));
  document.querySelectorAll('.thumb').forEach((t,idx)=>t.classList.toggle('active',idx===i));
  document.getElementById('roomName').textContent=rooms[i].label;
  document.getElementById('roomSub').textContent=(i+1)+' of '+rooms.length;
  document.getElementById('counter').textContent=(i+1)+' / '+rooms.length;
  cur=i;
  // Scroll thumb into view
  thumbsEl.children[i].scrollIntoView({{behavior:'smooth',block:'nearest',inline:'center'}});
}}
go(0);

document.getElementById('prev').onclick=()=>go(cur-1);
document.getElementById('next').onclick=()=>go(cur+1);
document.addEventListener('keydown',e=>{{
  if(e.key==='ArrowLeft')go(cur-1);
  if(e.key==='ArrowRight')go(cur+1);
  if(e.key===' '){{e.preventDefault();toggleAuto()}}
}});

// Touch swipe
let tx=0;
viewer.addEventListener('touchstart',e=>tx=e.touches[0].clientX);
viewer.addEventListener('touchend',e=>{{
  const dx=e.changedTouches[0].clientX-tx;
  if(Math.abs(dx)>50)go(cur+(dx<0?1:-1));
}});

// Autoplay
function toggleAuto(){{
  const btn=document.getElementById('autoplay');
  if(autoId){{clearInterval(autoId);autoId=null;btn.classList.remove('playing');btn.textContent='Auto'}}
  else{{autoId=setInterval(()=>go(cur+1),4000);btn.classList.add('playing');btn.textContent='Pause'}}
}}
document.getElementById('autoplay').onclick=toggleAuto;
</script>
</body>
</html>"""

    try:
        out = Path(output_html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        logger.info("Tour HTML written: %s (%d rooms)", output_html, len(rooms_js))
        return True
    except Exception:
        logger.exception("Failed to write tour HTML")
        return False
