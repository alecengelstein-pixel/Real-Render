from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


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


def get_conn() -> sqlite3.Connection:
    Path(settings.mcp_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.mcp_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(c["name"] == column for c in cols)


def init_db() -> None:
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
              total_price_usd REAL DEFAULT 0
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);")

        # Migrate existing tables that lack the new columns
        new_cols = [
            ("package", "TEXT"),
            ("email", "TEXT"),
            ("rooms", "INTEGER DEFAULT 1"),
            ("addons_json", "TEXT DEFAULT '[]'"),
            ("total_price_usd", "REAL DEFAULT 0"),
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
) -> None:
    now = _utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
              id, created_at, updated_at, status, customer_ref,
              input_dir, outputs_dir, options_json, qc_json, provider_json, error,
              package, email, rooms, addons_json, total_price_usd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                job_id,
                now,
                now,
                "queued",
                customer_ref,
                input_dir,
                outputs_dir,
                json.dumps(options),
                json.dumps({}),
                json.dumps({}),
                None,
                package,
                email,
                rooms,
                json.dumps(addons or []),
                total_price_usd,
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
    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [_utc_now_iso()]

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if options is not None:
        fields.append("options_json = ?")
        values.append(json.dumps(options))
    if qc is not None:
        fields.append("qc_json = ?")
        values.append(json.dumps(qc))
    if provider is not None:
        fields.append("provider_json = ?")
        values.append(json.dumps(provider))
    if error is not None:
        fields.append("error = ?")
        values.append(error)

    values.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?;"
    with get_conn() as conn:
        conn.execute(sql, tuple(values))


def get_job(job_id: str) -> JobRow | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,)).fetchone()
    return JobRow(**dict(row)) if row else None


def list_jobs(limit: int = 50) -> list[JobRow]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?;", (limit,)
        ).fetchall()
    return [JobRow(**dict(r)) for r in rows]



