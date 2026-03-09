# Real-Render — Open Door Cinematic

## Domain
- Production: opendoorcinematic.com
- Frontend: Lovable.dev (React + TypeScript + shadcn/ui)
- Backend: FastAPI on localhost:8000, exposed via Cloudflare quick tunnel

## Architecture
```
Frontend (Lovable) → Stripe Checkout → Cloudflare Tunnel → localhost:8000 (FastAPI backend)
```

## Project Structure
```
app/
├── main.py              # Entry point — starts server, worker, inbox watcher
├── config.py            # Pydantic settings loaded from .env
├── db.py                # SQLite database (jobs table, CRUD)
├── routes/
│   ├── api.py           # REST API + Stripe checkout/webhook endpoints
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
│   ├── payments.py      # Stripe checkout session + webhook handling
│   ├── cloud/
│   │   ├── storage.py   # S3 / Cloudflare R2 upload + presigned URLs
│   │   └── email.py     # Email via Gmail SMTP
│   ├── cost_tracker.py  # Monthly budget enforcement ($500)
│   ├── inbox_watcher.py # Watchdog-based .zip auto-ingest
│   └── media/
│       ├── qc.py        # Input photo quality checks (focus, resolution)
│       └── video.py     # ffmpeg keyframe extraction + video scoring
└── templates/           # Jinja2 HTML templates for admin UI
```

## Running
```bash
source .venv/bin/activate
python -m app.main
```

## Order Flow
1. Frontend calls `POST /api/v1/checkout` with package, rooms, addons, email
2. Backend creates Stripe Checkout session + job in `pending_payment` status
3. Customer pays on Stripe hosted checkout page
4. Stripe webhook (`POST /api/v1/webhooks/stripe`) fires → job moves to `queued`
5. Customer uploads photos (`POST /api/v1/jobs/{id}/upload`)
6. Upload triggers pipeline processing

## Pipeline
- Dual-provider: Luma + Veo compete in parallel
- 4 phases: compete → evaluate → refine → finalize
- Progress: GET /api/v1/jobs/{id}/progress

## Config (.env)
- Luma + Veo API keys
- Stripe keys (secret, publishable, webhook secret)
- S3/R2 credentials (for cloud video storage)
- SMTP credentials (for email delivery)
- PUBLIC_BASE_URL (Cloudflare tunnel — rotates on restart)
- ffmpeg required for refinement phase

## Pricing
| Package   | Base  | Per extra room | Strategy  | Refine rounds |
|-----------|-------|----------------|-----------|---------------|
| Essential | $79   | +$20           | luma_only | 1             |
| Signature | $139  | +$30           | compete   | 2             |
| Premium   | $199  | +$40           | compete   | 3             |

### Add-ons
| Add-on                | Price |
|-----------------------|-------|
| Rush Delivery (<12h)  | $140  |
| Extra Revision Round  | $35   |
| Custom Themed Staging | $70   |
| Instagram Carousel    | $35   |
| Unique Request        | Custom|
