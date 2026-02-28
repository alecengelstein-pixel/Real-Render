from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from . import db
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
    options: dict[str, Any] = {}
    qc: dict[str, Any] = {}
    error: str | None = None
    artifacts: list[ArtifactLink] = []


class HealthResponse(BaseModel):
    status: str = "ok"
    storage_configured: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _artifacts_from_job(job: db.JobRow) -> list[ArtifactLink]:
    """Generate fresh presigned URLs from stored s3_keys."""
    if not s3_configured():
        return []
    s3_keys: dict[str, str] = job.provider.get("s3_keys", {})
    artifacts: list[ArtifactLink] = []
    for filename, s3_key in s3_keys.items():
        try:
            url = generate_presigned_url(s3_key)
            artifacts.append(ArtifactLink(filename=filename, download_url=url))
        except Exception:
            logger.exception("Failed to generate presigned URL for %s", s3_key)
    return artifacts


def _job_detail(job: db.JobRow) -> JobDetail:
    return JobDetail(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        customer_ref=job.customer_ref,
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
