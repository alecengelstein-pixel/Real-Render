# Real-Render — Open Door Cinematic

## Domain
- Production: opendoorcinematic.com
- Frontend: Lovable.dev (React + TypeScript + shadcn/ui)
- Backend: FastAPI on localhost:8000, exposed via Cloudflare quick tunnel

## Architecture
```
Frontend (Lovable) → Cloudflare Tunnel → localhost:8000 (FastAPI backend)
```

## Project Structure
```
app/
├── main.py              # Entry point — starts server, worker, inbox watcher
├── config.py            # Pydantic settings loaded from .env
├── db.py                # SQLite database (jobs table, CRUD)
├── routes/
│   ├── api.py           # REST API endpoints (/api/v1/*)
│   └── web.py           # HTML UI + admin dashboard
├── pipeline/
│   ├── core.py          # 4-phase agentic pipeline (compete → evaluate → refine → finalize)
│   ├── queue.py         # Background job queue (threaded worker)
│   └── ingest.py        # ZIP extraction, job creation, data dir setup
├── providers/
│   ├── base.py          # ProviderResult, VideoProvider protocol
│   ├── luma.py          # Luma Dream Machine (image-to-video)
│   └── veo.py           # Google Veo via Gemini API
├── services/
│   ├── storage.py       # S3 / Cloudflare R2 upload + presigned URLs
│   ├── notifications.py # Email via Gmail SMTP
│   ├── cost_tracker.py  # Monthly budget enforcement ($500)
│   ├── inbox_watcher.py # Watchdog-based .zip auto-ingest
│   ├── qc.py            # Input photo quality checks (focus, resolution)
│   └── video_utils.py   # ffmpeg keyframe extraction + video scoring
└── templates/           # Jinja2 HTML templates for admin UI
```

## Running
```bash
source .venv/bin/activate
python -m app.main
```

## Pipeline
- Dual-provider: Luma + Veo compete in parallel
- 4 phases: compete → evaluate → refine → finalize
- Progress: GET /api/v1/jobs/{id}/progress

## Config
- All keys in `.env` (Luma, Veo, S3, SMTP)
- Cloudflare quick tunnel URLs rotate on restart — update both `.env` (PUBLIC_BASE_URL) and Lovable frontend (`src/lib/api.ts`)
- ffmpeg required for refinement phase (keyframe extraction + quality scoring)

## Packages
| Package   | Price | Strategy  | Refine rounds |
|-----------|-------|-----------|---------------|
| Essential | $79   | luma_only | 1             |
| Signature | $139  | compete   | 2             |
| Premium   | $199  | compete   | 3             |
| Extra room| +$30  | —         | —             |
