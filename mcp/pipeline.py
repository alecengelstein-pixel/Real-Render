from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import db
from .providers.luma import LumaProvider
from .providers.veo import VeoProvider
from .qc import run_qc
from .storage import s3_configured, upload_job_outputs

logger = logging.getLogger(__name__)


DEFAULT_OPTIONS: dict[str, Any] = {
    "furnishing": "as_is",  # as_is | empty | staged
    "lighting": "natural",  # natural | warm | cool | night
    "deliverables": {
        "reconstruction": True,
        "walkthrough_video": True,
    },
}


def ensure_dirs(job_id: str) -> tuple[str, str]:
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")
    Path(job.input_dir).mkdir(parents=True, exist_ok=True)
    Path(job.outputs_dir).mkdir(parents=True, exist_ok=True)
    return job.input_dir, job.outputs_dir


def process_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")

    db.update_job(job_id, status="processing", error=None)
    input_dir, outputs_dir = ensure_dirs(job_id)

    qc = run_qc(input_dir)
    db.update_job(job_id, qc=qc)

    # If QC problems are severe, you might choose to stop early.
    # For now, we still attempt providers unless explicitly blocked.
    options = {**DEFAULT_OPTIONS, **job.options}

    provider_state: dict[str, Any] = {"steps": []}

    # Reconstruction
    reconstruction: dict[str, Any] | None = None
    if options.get("deliverables", {}).get("reconstruction", True):
        luma = LumaProvider()
        res = luma.reconstruct(
            job_id=job_id, input_dir=input_dir, outputs_dir=outputs_dir, options=options
        )
        provider_state["steps"].append({"provider": luma.name, "result": res.data, "ok": res.ok})
        if res.ok:
            reconstruction = res.data
        else:
            db.update_job(job_id, status="needs_config", provider=provider_state)
            return

    # Video walkthrough
    if options.get("deliverables", {}).get("walkthrough_video", True):
        veo = VeoProvider()
        res = veo.make_walkthrough(
            job_id=job_id,
            outputs_dir=outputs_dir,
            options=options,
            reconstruction=reconstruction,
        )
        provider_state["steps"].append({"provider": veo.name, "result": res.data, "ok": res.ok})
        if not res.ok:
            db.update_job(job_id, status="needs_config", provider=provider_state)
            return

    # Upload artifacts to S3/R2 if configured
    if s3_configured():
        try:
            s3_keys = upload_job_outputs(job_id, outputs_dir)
            provider_state["s3_keys"] = s3_keys
        except Exception:
            logger.exception("S3 upload failed for job %s; local files still available", job_id)

    db.update_job(job_id, status="done", provider=provider_state)



