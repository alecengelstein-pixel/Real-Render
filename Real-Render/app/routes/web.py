from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db
from .api import router as api_router, set_enqueue_fn
from ..config import settings
from ..pipeline.ingest import ensure_data_dirs, ingest_zip


def _build_stats(jobs: list[db.JobRow]) -> dict:
    by_package = {"essential": 0, "signature": 0, "premium": 0, "other": 0}
    revenue_by_package = {"essential": 0.0, "signature": 0.0, "premium": 0.0}
    for j in jobs:
        pkg = j.package or "other"
        if pkg in by_package:
            by_package[pkg] += 1
        else:
            by_package["other"] += 1
        if pkg in revenue_by_package:
            revenue_by_package[pkg] += j.total_price_usd

    return {
        "total": len(jobs),
        "queued": sum(1 for j in jobs if j.status == "queued"),
        "processing": sum(1 for j in jobs if j.status == "processing"),
        "done": sum(1 for j in jobs if j.status == "done"),
        "error": sum(1 for j in jobs if j.status == "error"),
        "revenue": sum(j.total_price_usd for j in jobs),
        "by_package": by_package,
        "revenue_by_package": revenue_by_package,
    }


def create_app(enqueue_job) -> FastAPI:  # type: ignore[no-untyped-def]
    ensure_data_dirs()
    db.init_db()

    app = FastAPI()

    # CORS
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # JSON API
    set_enqueue_fn(enqueue_job)
    app.include_router(api_router)

    templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):  # type: ignore[no-untyped-def]
        jobs = db.list_jobs(limit=50)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "jobs": jobs,
                "stats": _build_stats(jobs),
                "active_page": "orders",
                "luma_ok": bool(settings.luma_api_key and settings.luma_api_base_url),
                "veo_ok": bool(settings.veo_api_key and settings.veo_api_base_url),
                "smtp_ok": bool(settings.smtp_user and settings.smtp_password),
            },
        )

    @app.get("/new", response_class=HTMLResponse)
    def new_job(request: Request):  # type: ignore[no-untyped-def]
        return templates.TemplateResponse(
            "new.html", {"request": request, "active_page": "new"}
        )

    @app.post("/new-order")
    async def create_order_from_form(  # type: ignore[no-untyped-def]
        request: Request,
        zip_file: UploadFile = File(...),
        email: str = Form(...),
        package: str = Form(...),
        rooms: int = Form(default=1),
        addons: str = Form(default=""),
        customer_ref: str | None = Form(default=None),
    ):
        """Handle the new order form submission."""
        ensure_data_dirs()

        pkg = package.lower()
        prices = settings.package_prices
        if pkg not in prices:
            return HTMLResponse(f"Unknown package: {package}", status_code=400)

        addon_list = [a.strip() for a in addons.split(",") if a.strip()]
        base_price = prices[pkg]
        extra_rooms = max(0, rooms - 1)
        room_rate = settings.price_per_extra_room.get(pkg, 30.0)
        addon_total = sum(settings.addon_prices.get(a, 0.0) for a in addon_list)
        total = base_price + (extra_rooms * room_rate) + addon_total

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

        # Auto-start processing
        enqueue_job(job_id)

        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.post("/new")
    async def create_job_from_upload(  # type: ignore[no-untyped-def]
        request: Request,
        zip_file: UploadFile = File(...),
        customer_ref: str | None = Form(default=None),
    ):
        ensure_data_dirs()
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
                "active_page": "",
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
