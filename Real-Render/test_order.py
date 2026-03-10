#!/usr/bin/env python3
"""
Test the full order pipeline end-to-end.

Creates synthetic room images, packages them into a ZIP, submits an order
via the /api/v1/orders endpoint, then polls progress until completion.

Requirements: httpx, Pillow (both already in requirements.txt)

Usage:
    python test_order.py
"""

import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

BASE_URL = "http://127.0.0.1:8000"
POLL_INTERVAL = 15  # seconds

# Five synthetic "room" colors with labels
ROOMS = [
    {"color": (180, 160, 140), "label": "Living Room - Beige Walls"},
    {"color": (210, 210, 220), "label": "Kitchen - Light Gray"},
    {"color": (160, 190, 200), "label": "Bedroom - Soft Blue"},
    {"color": (200, 180, 170), "label": "Bathroom - Warm Taupe"},
    {"color": (170, 185, 160), "label": "Office - Sage Green"},
]


def create_test_images() -> list[tuple[str, bytes]]:
    """Generate 5 synthetic 1920x1080 room images as (filename, png_bytes) pairs."""
    images = []
    for i, room in enumerate(ROOMS, 1):
        img = Image.new("RGB", (1920, 1080), room["color"])
        draw = ImageDraw.Draw(img)

        # Draw some rectangles to simulate walls/floor/furniture
        w, h = 1920, 1080

        # Floor area (darker shade)
        floor_color = tuple(max(0, c - 40) for c in room["color"])
        draw.rectangle([0, h * 2 // 3, w, h], fill=floor_color)

        # Wall trim line
        draw.line([(0, h * 2 // 3), (w, h * 2 // 3)], fill=(100, 100, 100), width=3)

        # Window rectangle
        win_color = (200, 220, 240)
        draw.rectangle([w // 3, h // 6, w * 2 // 3, h // 2], outline=(80, 80, 80), width=4)
        draw.rectangle([w // 3 + 5, h // 6 + 5, w * 2 // 3 - 5, h // 2 - 5], fill=win_color)

        # Label text
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
        except (OSError, IOError):
            font = ImageFont.load_default()

        draw.text((50, 50), f"Test Image {i}: {room['label']}", fill=(40, 40, 40), font=font)
        draw.text((50, h - 80), "Real-Render Pipeline Test", fill=(120, 120, 120), font=font)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        images.append((f"room_{i:02d}.jpg", buf.getvalue()))

    return images


def create_zip(images: list[tuple[str, bytes]]) -> bytes:
    """Package images into an in-memory ZIP file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, data in images:
            zf.writestr(filename, data)
    return buf.getvalue()


def submit_order(client: httpx.Client, zip_data: bytes) -> dict:
    """POST /api/v1/orders with the ZIP file and order parameters."""
    print("\n=== Submitting Order ===")
    print(f"  Email:   test@opendoorcinematic.com")
    print(f"  Package: essential")
    print(f"  Rooms:   1")
    print(f"  ZIP size: {len(zip_data):,} bytes")

    resp = client.post(
        f"{BASE_URL}/api/v1/orders",
        files={"zip_file": ("test_rooms.zip", zip_data, "application/zip")},
        data={
            "email": "test@opendoorcinematic.com",
            "package": "essential",
            "rooms": "1",
        },
        timeout=60.0,
    )

    if resp.status_code != 201:
        print(f"\nERROR: Server returned {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    order = resp.json()
    print(f"\n  Order created successfully!")
    print(f"  Job ID:      {order['id']}")
    print(f"  Status:      {order['status']}")
    print(f"  Package:     {order.get('package')}")
    print(f"  Total Price: ${order.get('total_price_usd', 0):.2f}")
    return order


def poll_progress(client: httpx.Client, job_id: str) -> None:
    """Poll GET /api/v1/jobs/{id}/progress every POLL_INTERVAL seconds."""
    print(f"\n=== Polling Progress (every {POLL_INTERVAL}s) ===")

    terminal_statuses = {"done", "error", "failed"}
    last_phase = None
    poll_count = 0

    while True:
        poll_count += 1
        try:
            resp = client.get(f"{BASE_URL}/api/v1/jobs/{job_id}/progress", timeout=30.0)
            resp.raise_for_status()
            progress = resp.json()
        except httpx.HTTPError as e:
            print(f"  [Poll #{poll_count}] Request error: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        status = progress.get("status", "unknown")
        phase = progress.get("current_phase")
        strategy = progress.get("strategy")
        winner = progress.get("winner")
        cost = progress.get("total_cost_usd", 0.0)
        steps = progress.get("steps", [])
        scores = progress.get("scores", {})

        # Print update (always print on phase change, otherwise periodic)
        if phase != last_phase or poll_count % 4 == 0:
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] Poll #{poll_count}: status={status}, phase={phase}, "
                  f"strategy={strategy}, steps={len(steps)}, cost=${cost:.4f}")

            if scores:
                print(f"           Scores: {json.dumps(scores, indent=None)}")
            if winner:
                print(f"           Winner: {winner}")

        last_phase = phase

        if status in terminal_statuses:
            print(f"\n  Pipeline reached terminal status: {status}")
            break

        time.sleep(POLL_INTERVAL)


def print_final_detail(client: httpx.Client, job_id: str) -> None:
    """Fetch and display the final job detail."""
    print(f"\n=== Final Job Detail ===")

    try:
        resp = client.get(f"{BASE_URL}/api/v1/jobs/{job_id}", timeout=30.0)
        resp.raise_for_status()
        detail = resp.json()
    except httpx.HTTPError as e:
        print(f"  ERROR fetching final detail: {e}")
        return

    print(f"  ID:          {detail['id']}")
    print(f"  Status:      {detail['status']}")
    print(f"  Package:     {detail.get('package')}")
    print(f"  Email:       {detail.get('email')}")
    print(f"  Rooms:       {detail.get('rooms')}")
    print(f"  Price:       ${detail.get('total_price_usd', 0):.2f}")
    print(f"  Created:     {detail.get('created_at')}")
    print(f"  Updated:     {detail.get('updated_at')}")

    if detail.get("error"):
        print(f"  Error:       {detail['error']}")

    if detail.get("qc"):
        print(f"  QC:          {json.dumps(detail['qc'], indent=2)}")

    artifacts = detail.get("artifacts", [])
    if artifacts:
        print(f"  Artifacts ({len(artifacts)}):")
        for art in artifacts:
            print(f"    - {art['filename']}: {art['download_url']}")
    else:
        print(f"  Artifacts:   (none)")

    print(f"\n  Raw JSON:")
    print(f"  {json.dumps(detail, indent=2)}")


def main() -> None:
    print("=" * 60)
    print("  Real-Render Order Pipeline Test")
    print("=" * 60)

    # Step 1: Check server health
    print("\n--- Checking server health ---")
    with httpx.Client() as client:
        try:
            health = client.get(f"{BASE_URL}/api/v1/health", timeout=10.0)
            health.raise_for_status()
            print(f"  Server is up: {health.json()}")
        except httpx.ConnectError:
            print(f"  ERROR: Cannot connect to {BASE_URL}")
            print(f"  Make sure the server is running: uvicorn app.main:app --port 8000")
            sys.exit(1)
        except httpx.HTTPError as e:
            print(f"  ERROR: Health check failed: {e}")
            sys.exit(1)

        # Step 2: Create test images
        print("\n--- Generating test images ---")
        images = create_test_images()
        for fname, data in images:
            print(f"  Created {fname} ({len(data):,} bytes)")

        # Step 3: Package into ZIP
        print("\n--- Creating ZIP archive ---")
        zip_data = create_zip(images)
        print(f"  ZIP size: {len(zip_data):,} bytes ({len(images)} images)")

        # Step 4: Submit order
        order = submit_order(client, zip_data)
        job_id = order["id"]

        # Step 5: Poll progress
        poll_progress(client, job_id)

        # Step 6: Print final detail
        print_final_detail(client, job_id)

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
