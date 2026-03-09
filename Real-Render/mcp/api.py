from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from .config import settings
from .ingest import ensure_data_dirs, ingest_zip
from .storage import generate_presigned_url, s3_configured

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_enqueue_fn: Callable[[str], None] | None = None


def set_enqueue_fn(fn: Callable[[str], None]) -> None:
    global _enqueue_fn
    _enqueue_fn = fn


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ArtifactLink(BaseModel):
    filename: str
    download_url: str


class JobSummary(BaseModel):
    id: str
    status: str
    created_at: str
    updated_at: str
    customer_ref: str | None = None


class JobDetail(BaseModel):
    id: str
    status: str
    created_at: str
    updated_at: str
    customer_ref: str | None = None
    package: str | None = None
    email: str | None = None
    rooms: int = 1
    total_price_usd: float = 0.0
    options: dict[str, Any] = {}
    qc: dict[str, Any] = {}
    error: str | None = None
    artifacts: list[ArtifactLink] = []


class JobProgress(BaseModel):
    id: str
    status: str
    current_phase: str | None = None
    strategy: str | None = None
    steps: list[dict[str, Any]] = []
    scores: dict[str, Any] = {}
    winner: str | None = None
    total_cost_usd: float = 0.0


class HealthResponse(BaseModel):
    status: str = "ok"
    storage_configured: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _artifacts_from_job(job: db.JobRow) -> list[ArtifactLink]:
    """Generate download URLs for job artifacts.

    Uses presigned S3 URLs if configured, otherwise serves files directly
    from the local outputs directory.
    """
    artifacts: list[ArtifactLink] = []

    # Try S3 presigned URLs first
    if s3_configured():
        s3_keys: dict[str, str] = job.provider.get("s3_keys", {})
        for filename, s3_key in s3_keys.items():
            try:
                url = generate_presigned_url(s3_key)
                artifacts.append(ArtifactLink(filename=filename, download_url=url))
            except Exception:
                logger.exception("Failed to generate presigned URL for %s", s3_key)
        if artifacts:
            return artifacts

    # Fallback: serve local files via API
    outputs = Path(job.outputs_dir)
    if outputs.exists():
        base = (settings.public_base_url or "").rstrip("/")
        for fp in outputs.rglob("*"):
            if fp.is_file() and fp.name != "outputs.zip":
                relative = fp.relative_to(outputs).as_posix()
                download_url = f"{base}/api/v1/jobs/{job.id}/files/{relative}"
                artifacts.append(ArtifactLink(filename=relative, download_url=download_url))
    return artifacts


def _job_detail(job: db.JobRow) -> JobDetail:
    return JobDetail(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        customer_ref=job.customer_ref,
        package=job.package,
        email=job.email,
        rooms=job.rooms,
        total_price_usd=job.total_price_usd,
        options=job.options,
        qc=job.qc,
        error=job.error,
        artifacts=_artifacts_from_job(job),
    )


def _job_summary(job: db.JobRow) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        customer_ref=job.customer_ref,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", storage_configured=s3_configured())


@router.get("/jobs", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
    jobs = db.list_jobs(limit=50)
    return [_job_summary(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str) -> JobDetail:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_detail(job)


@router.post("/jobs", response_model=JobSummary, status_code=201)
async def create_job(
    zip_file: UploadFile = File(...),
    customer_ref: str | None = Form(default=None),
) -> JobSummary:
    ensure_data_dirs()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / (zip_file.filename or "upload.zip")
        with tmp.open("wb") as f:
            f.write(await zip_file.read())
        job_id = ingest_zip(str(tmp), customer_ref=customer_ref)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=500, detail="Job creation failed")
    return _job_summary(job)


@router.post("/orders", response_model=JobDetail, status_code=201)
async def create_order(
    zip_file: UploadFile = File(...),
    email: str = Form(...),
    package: str = Form(...),
    rooms: int = Form(default=1),
    addons: str = Form(default=""),          # comma-separated list
    customer_ref: str | None = Form(default=None),
) -> JobDetail:
    """Package-aware order intake. Calculates price, creates job, enqueues."""
    import json as _json

    pkg = package.lower()
    prices = settings.package_prices
    if pkg not in prices:
        raise HTTPException(status_code=400, detail=f"Unknown package: {package}")

    addon_list = [a.strip() for a in addons.split(",") if a.strip()] if addons else []
    base_price = prices[pkg]
    extra_rooms = max(0, rooms - 1)
    total = base_price + (extra_rooms * settings.price_per_extra_room)

    ensure_data_dirs()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / (zip_file.filename or "upload.zip")
        with tmp.open("wb") as f:
            f.write(await zip_file.read())
        job_id = ingest_zip(
            str(tmp),
            customer_ref=customer_ref,
            package=pkg,
            email=email,
            rooms=rooms,
            addons=addon_list,
            total_price_usd=total,
        )

    if _enqueue_fn is not None:
        _enqueue_fn(job_id)

    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=500, detail="Job creation failed")
    return _job_detail(job)


@router.post("/jobs/{job_id}/process", response_model=JobDetail)
def process_job(job_id: str) -> JobDetail:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if _enqueue_fn is None:
        raise HTTPException(status_code=503, detail="Processing not available")
    _enqueue_fn(job_id)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=500, detail="Job lost after enqueue")
    return _job_detail(job)


@router.post("/jobs/{job_id}/options", response_model=JobDetail)
def update_options(
    job_id: str,
    furnishing: str = Form(...),
    lighting: str = Form(...),
    reconstruction: str | None = Form(default=None),
    walkthrough_video: str | None = Form(default=None),
) -> JobDetail:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    opts = dict(job.options)
    opts["furnishing"] = furnishing
    opts["lighting"] = lighting
    deliverables = dict(opts.get("deliverables") or {})
    deliverables["reconstruction"] = reconstruction == "1"
    deliverables["walkthrough_video"] = walkthrough_video == "1"
    opts["deliverables"] = deliverables

    db.update_job(job_id, options=opts)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=500, detail="Job lost after update")
    return _job_detail(job)


@router.get("/jobs/{job_id}/progress", response_model=JobProgress)
def job_progress(job_id: str) -> JobProgress:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    prov = job.provider
    return JobProgress(
        id=job.id,
        status=job.status,
        current_phase=prov.get("current_phase"),
        strategy=prov.get("strategy"),
        steps=prov.get("steps", []),
        scores=prov.get("scores", {}),
        winner=prov.get("winner"),
        total_cost_usd=prov.get("total_cost_usd", 0.0),
    )


@router.get("/jobs/{job_id}/files/{file_path:path}")
def serve_output_file(job_id: str, file_path: str) -> FileResponse:
    """Serve a file from a job's outputs directory."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    fp = Path(job.outputs_dir) / file_path
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(fp), filename=fp.name)


@router.get("/jobs/{job_id}/inputs/{file_path:path}")
def serve_input_file(job_id: str, file_path: str) -> FileResponse:
    """Serve a file from a job's input directory (used by Luma API to fetch images)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    fp = Path(job.input_dir) / file_path
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(fp), filename=fp.name)
