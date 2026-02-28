# Real-Render MCP (local production pipeline)

This repo contains a **local** (runs on your laptop) job runner + dashboard for turning a paid order's photos into deliverables by calling external providers (e.g. **Luma** for reconstruction, **Veo** for walkthrough video).

## What this is (MVP)
- Watches an **inbox folder** for a new `.zip` of photos
- Creates a **job** in SQLite
- Runs **photo QC** (count, resolution, blur-ish heuristic)
- Lets you set **options** per job: furnished/empty, lighting style, output formats
- Runs a **provider pipeline** (stubbed by default; plug in your API endpoints/keys)
- Produces outputs into `./data/jobs/<job_id>/outputs/`

## Important reality check (accuracy)
With **10–20 normal photos**, geometric accuracy is often **not reliable** for a full-home 3D model. This tool bakes in QC + capture guidance so you can reject/ask for more capture early.

If you want accuracy, consider changing intake to:
- a **continuous walkthrough video** (60–120s) OR
- **80–200 images** with overlap OR
- iPhone Pro **LiDAR** capture where possible

## Quickstart

### 1) Create venv + install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure env
```bash
cp .env.example .env
```
Edit `.env` and add your API keys.

### 3) Run the dashboard + worker
```bash
python -m mcp.main
```
Then open the dashboard at `http://127.0.0.1:8000`.

### 4) Drop a zip into the inbox
Put a file like `123-main-st.zip` into `./data/inbox/`.

## Folder layout
- `data/inbox/` – drop new jobs (zips)
- `data/jobs/<job_id>/input/` – extracted photos
- `data/jobs/<job_id>/outputs/` – deliverables
- `data/mcp.sqlite3` – local DB



