from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import settings
from ..db import get_conn

logger = logging.getLogger(__name__)


def get_monthly_spend() -> float:
    """Sum total_cost_usd from all jobs created this calendar month."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT provider_json FROM jobs WHERE created_at >= ?;",
            (month_start,),
        ).fetchall()

    total = 0.0
    for row in rows:
        prov = json.loads(row["provider_json"]) if row["provider_json"] else {}
        total += prov.get("total_cost_usd", 0.0)

    logger.info("Monthly spend so far: $%.2f", total)
    return total


def can_afford(estimated_cost: float) -> bool:
    """Check whether spending *estimated_cost* would exceed the monthly budget."""
    spent = get_monthly_spend()
    remaining = settings.monthly_budget_usd - spent
    if estimated_cost > remaining:
        logger.warning(
            "Budget check failed: need $%.2f but only $%.2f remaining (budget $%.2f, spent $%.2f)",
            estimated_cost, remaining, settings.monthly_budget_usd, spent,
        )
        return False
    return True
