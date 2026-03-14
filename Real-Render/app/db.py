from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class JobRow:
    id: str
    created_at: str
    updated_at: str
    status: str
    customer_ref: str | None
    input_dir: str
    outputs_dir: str
    options_json: str
    qc_json: str
    provider_json: str
    error: str | None
    package: str | None = None
    email: str | None = None
    rooms: int = 1
    addons_json: str = "[]"
    total_price_usd: float = 0.0
    stripe_session_id: str | None = None

    @property
    def options(self) -> dict[str, Any]:
        return json.loads(self.options_json) if self.options_json else {}

    @property
    def qc(self) -> dict[str, Any]:
        return json.loads(self.qc_json) if self.qc_json else {}

    @property
    def provider(self) -> dict[str, Any]:
        return json.loads(self.provider_json) if self.provider_json else {}

    @property
    def addons(self) -> list[str]:
        return json.loads(self.addons_json) if self.addons_json else []


# ---------------------------------------------------------------------------
# Supabase backend (direct REST API via httpx — no supabase-py dependency)
# ---------------------------------------------------------------------------

def _use_supabase() -> bool:
    return bool(settings.supabase_url and settings.supabase_key)


def _sb_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_url(path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}/rest/v1/{path}"


def _sb_get(path: str, params: dict | None = None) -> list[dict]:
    resp = httpx.get(_sb_url(path), headers=_sb_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _sb_post(path: str, data: dict) -> list[dict]:
    resp = httpx.post(_sb_url(path), headers=_sb_headers(), json=data, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _sb_patch(path: str, data: dict, params: dict | None = None) -> list[dict]:
    resp = httpx.patch(_sb_url(path), headers=_sb_headers(), json=data, params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _row_from_supabase(row: dict) -> JobRow:
    return JobRow(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status=row["status"],
        customer_ref=row.get("customer_ref"),
        input_dir=row.get("input_dir", ""),
        outputs_dir=row.get("outputs_dir", ""),
        options_json=row.get("options_json", "{}"),
        qc_json=row.get("qc_json", "{}"),
        provider_json=row.get("provider_json", "{}"),
        error=row.get("error"),
        package=row.get("package"),
        email=row.get("email"),
        rooms=row.get("rooms", 1),
        addons_json=row.get("addons_json", "[]"),
        total_price_usd=row.get("total_price_usd", 0.0),
        stripe_session_id=row.get("stripe_session_id"),
    )


# ---------------------------------------------------------------------------
# SQLite backend (fallback)
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    Path(settings.mcp_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.mcp_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(c["name"] == column for c in cols)


# ---------------------------------------------------------------------------
# Public API — routes to Supabase or SQLite
# ---------------------------------------------------------------------------

def init_db() -> None:
    if _use_supabase():
        try:
            rows = _sb_get("jobs", {"select": "id", "limit": "1"})
            logger.info("Supabase connection OK (%d rows)", len(rows))
        except Exception as e:
            logger.warning("Supabase connection check failed: %s", e)
        return

    # SQLite fallback
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              status TEXT NOT NULL,
              customer_ref TEXT,
              input_dir TEXT NOT NULL,
              outputs_dir TEXT NOT NULL,
              options_json TEXT NOT NULL,
              qc_json TEXT NOT NULL,
              provider_json TEXT NOT NULL,
              error TEXT,
              package TEXT,
              email TEXT,
              rooms INTEGER DEFAULT 1,
              addons_json TEXT DEFAULT '[]',
              total_price_usd REAL DEFAULT 0,
              stripe_session_id TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);")

        new_cols = [
            ("package", "TEXT"),
            ("email", "TEXT"),
            ("rooms", "INTEGER DEFAULT 1"),
            ("addons_json", "TEXT DEFAULT '[]'"),
            ("total_price_usd", "REAL DEFAULT 0"),
            ("stripe_session_id", "TEXT"),
        ]
        for col_name, col_type in new_cols:
            if not _table_has_column(conn, "jobs", col_name):
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type};")


def create_job(
    *,
    job_id: str,
    input_dir: str,
    outputs_dir: str,
    customer_ref: str | None,
    options: dict[str, Any],
    package: str | None = None,
    email: str | None = None,
    rooms: int = 1,
    addons: list[str] | None = None,
    total_price_usd: float = 0.0,
    status: str = "queued",
    stripe_session_id: str | None = None,
) -> None:
    now = _utc_now_iso()

    if _use_supabase():
        _sb_post("jobs", {
            "id": job_id,
            "created_at": now,
            "updated_at": now,
            "status": status,
            "customer_ref": customer_ref,
            "input_dir": input_dir,
            "outputs_dir": outputs_dir,
            "options_json": json.dumps(options),
            "qc_json": json.dumps({}),
            "provider_json": json.dumps({}),
            "error": None,
            "package": package,
            "email": email,
            "rooms": rooms,
            "addons_json": json.dumps(addons or []),
            "total_price_usd": total_price_usd,
            "stripe_session_id": stripe_session_id,
        })
        return

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
              id, created_at, updated_at, status, customer_ref,
              input_dir, outputs_dir, options_json, qc_json, provider_json, error,
              package, email, rooms, addons_json, total_price_usd, stripe_session_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                job_id, now, now, status, customer_ref,
                input_dir, outputs_dir,
                json.dumps(options), json.dumps({}), json.dumps({}), None,
                package, email, rooms,
                json.dumps(addons or []),
                total_price_usd, stripe_session_id,
            ),
        )


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    options: dict[str, Any] | None = None,
    qc: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    updates: dict[str, Any] = {"updated_at": _utc_now_iso()}

    if status is not None:
        updates["status"] = status
    if options is not None:
        updates["options_json"] = json.dumps(options)
    if qc is not None:
        updates["qc_json"] = json.dumps(qc)
    if provider is not None:
        updates["provider_json"] = json.dumps(provider)
    if error is not None:
        updates["error"] = error

    if _use_supabase():
        _sb_patch("jobs", updates, params={"id": f"eq.{job_id}"})
        return

    fields: list[str] = []
    values: list[Any] = []
    for k, v in updates.items():
        fields.append(f"{k} = ?")
        values.append(v)

    values.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?;"
    with get_conn() as conn:
        conn.execute(sql, tuple(values))


def get_job(job_id: str) -> JobRow | None:
    if _use_supabase():
        rows = _sb_get("jobs", {"select": "*", "id": f"eq.{job_id}"})
        return _row_from_supabase(rows[0]) if rows else None

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,)).fetchone()
    return JobRow(**dict(row)) if row else None


def get_job_by_stripe_session(session_id: str) -> JobRow | None:
    if _use_supabase():
        rows = _sb_get("jobs", {"select": "*", "stripe_session_id": f"eq.{session_id}"})
        return _row_from_supabase(rows[0]) if rows else None

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE stripe_session_id = ?;", (session_id,)
        ).fetchone()
    return JobRow(**dict(row)) if row else None


def list_jobs(limit: int = 50) -> list[JobRow]:
    if _use_supabase():
        rows = _sb_get("jobs", {
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit),
        })
        return [_row_from_supabase(r) for r in rows]

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?;", (limit,)
        ).fetchall()
    return [JobRow(**dict(r)) for r in rows]
