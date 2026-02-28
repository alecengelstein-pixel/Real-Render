from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db
from .api import router as api_router, set_enqueue_fn
from .config import settings
from .ingest import ensure_data_dirs, ingest_zip


def create_app(enqueue_job) -> FastAPI:  # type: ignore[no-untyped-def]
    ensure_data_dirs()
    db.init_db()

    app = FastAPI()

    # CORS
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # JSON API
    set_enqueue_fn(enqueue_job)
    app.include_router(api_router)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):  # type: ignore[no-untyped-def]
        jobs = db.list_jobs(limit=50)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "jobs": jobs,
                "inbox_dir": settings.mcp_inbox_dir,
                "luma_ok": bool(settings.luma_api_key and settings.luma_api_base_url),
                "veo_ok": bool(settings.veo_api_key and settings.veo_api_base_url),
            },
        )

    @app.get("/new", response_class=HTMLResponse)
    def new_job(request: Request):  # type: ignore[no-untyped-def]
        return templates.TemplateResponse(
            "new.html", {"request": request, "inbox_dir": settings.mcp_inbox_dir}
        )

    @app.post("/new")
    async def create_job_from_upload(  # type: ignore[no-untyped-def]
        request: Request,
        zip_file: UploadFile = File(...),
        customer_ref: str | None = Form(default=None),
    ):
        ensure_data_dirs()
        # Save upload to a temp file then ingest.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / (zip_file.filename or "upload.zip")
            with tmp.open("wb") as f:
                f.write(await zip_file.read())
            job_id = ingest_zip(str(tmp), customer_ref=customer_ref)

        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str):  # type: ignore[no-untyped-def]
        job = db.get_job(job_id)
        if not job:
            return HTMLResponse("Not found", status_code=404)
        return templates.TemplateResponse(
            "job.html",
            {
                "request": request,
                "job": job,
                "provider_json": json.dumps(job.provider, indent=2),
                "inbox_dir": settings.mcp_inbox_dir,
            },
        )

    @app.post("/jobs/{job_id}/options")
    def update_options(  # type: ignore[no-untyped-def]
        job_id: str,
        furnishing: str = Form(...),
        lighting: str = Form(...),
        reconstruction: str | None = Form(default=None),
        walkthrough_video: str | None = Form(default=None),
    ):
        job = db.get_job(job_id)
        if not job:
            return HTMLResponse("Not found", status_code=404)

        opts = dict(job.options)
        opts["furnishing"] = furnishing
        opts["lighting"] = lighting
        deliverables = dict(opts.get("deliverables") or {})
        deliverables["reconstruction"] = reconstruction == "1"
        deliverables["walkthrough_video"] = walkthrough_video == "1"
        opts["deliverables"] = deliverables

        db.update_job(job_id, options=opts)
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/process")
    def process(job_id: str):  # type: ignore[no-untyped-def]
        job = db.get_job(job_id)
        if not job:
            return HTMLResponse("Not found", status_code=404)
        enqueue_job(job_id)
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/download")
    def download_outputs(job_id: str):  # type: ignore[no-untyped-def]
        job = db.get_job(job_id)
        if not job:
            return HTMLResponse("Not found", status_code=404)

        outputs = Path(job.outputs_dir)
        outputs.mkdir(parents=True, exist_ok=True)
        tmp_zip = outputs / "outputs.zip"
        if tmp_zip.exists():
            tmp_zip.unlink()

        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for fp in outputs.rglob("*"):
                if fp.is_file() and fp.name != "outputs.zip":
                    z.write(fp, arcname=fp.relative_to(outputs).as_posix())

        return RedirectResponse(url=f"/jobs/{job_id}/outputs.zip", status_code=303)

    @app.get("/jobs/{job_id}/outputs.zip")
    def download_outputs_zip(job_id: str):  # type: ignore[no-untyped-def]
        job = db.get_job(job_id)
        if not job:
            return HTMLResponse("Not found", status_code=404)
        zip_path = Path(job.outputs_dir) / "outputs.zip"
        if not zip_path.exists():
            return HTMLResponse("No outputs zip yet. Click 'Download outputs zip' first.", status_code=404)
        return FileResponse(
            path=str(zip_path),
            filename=f"{job_id}-outputs.zip",
            media_type="application/zip",
        )

    return app


