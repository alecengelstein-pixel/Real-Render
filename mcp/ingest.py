from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import db
from .config import settings
from .pipeline import DEFAULT_OPTIONS


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def ensure_data_dirs() -> None:
    _safe_mkdir(Path(settings.mcp_data_dir))
    _safe_mkdir(Path(settings.mcp_inbox_dir))
    _safe_mkdir(Path(settings.mcp_data_dir) / "jobs")


def _wait_for_stable_file(path: Path, *, timeout_s: float = 30.0) -> None:
    """
    macOS / browsers sometimes write zips gradually. Wait until size is stable.
    """
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            time.sleep(0.25)
            continue
        if size == last and size > 0:
            return
        last = size
        time.sleep(0.5)


def ingest_zip(
    zip_path: str,
    *,
    customer_ref: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    ensure_data_dirs()
    z = Path(zip_path)
    if not z.exists():
        raise FileNotFoundError(str(z))

    _wait_for_stable_file(z)

    job_id = uuid4().hex[:12]
    job_root = Path(settings.mcp_data_dir) / "jobs" / job_id
    input_dir = job_root / "input"
    outputs_dir = job_root / "outputs"
    _safe_mkdir(input_dir)
    _safe_mkdir(outputs_dir)

    # Extract
    with zipfile.ZipFile(z, "r") as zipf:
        zipf.extractall(input_dir)

    # Flatten nested single folder common in uploads
    children = [c for c in input_dir.iterdir() if c.name not in ("__MACOSX",)]
    if len(children) == 1 and children[0].is_dir():
        nested = children[0]
        for item in nested.iterdir():
            shutil.move(str(item), str(input_dir / item.name))
        shutil.rmtree(nested, ignore_errors=True)

    merged_options = {**DEFAULT_OPTIONS, **(options or {})}
    db.create_job(
        job_id=job_id,
        input_dir=str(input_dir),
        outputs_dir=str(outputs_dir),
        customer_ref=customer_ref,
        options=merged_options,
    )
    return job_id


def ingest_folder(
    folder_path: str,
    *,
    customer_ref: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    ensure_data_dirs()
    src = Path(folder_path)
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(str(src))

    job_id = uuid4().hex[:12]
    job_root = Path(settings.mcp_data_dir) / "jobs" / job_id
    input_dir = job_root / "input"
    outputs_dir = job_root / "outputs"
    _safe_mkdir(input_dir)
    _safe_mkdir(outputs_dir)

    for fp in src.iterdir():
        if fp.is_file():
            shutil.copy2(fp, input_dir / fp.name)

    merged_options = {**DEFAULT_OPTIONS, **(options or {})}
    db.create_job(
        job_id=job_id,
        input_dir=str(input_dir),
        outputs_dir=str(outputs_dir),
        customer_ref=customer_ref,
        options=merged_options,
    )
    return job_id



