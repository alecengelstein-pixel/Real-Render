from __future__ import annotations

import logging
from typing import Any

import stripe

from ..config import settings
from .. import db

logger = logging.getLogger(__name__)

# Human-readable names for line items
_PACKAGE_NAMES = {
    "essential": "Essential Package",
    "signature": "Signature Package",
    "premium": "Premium Package",
}

_ADDON_NAMES = {
    "rush_delivery": "Rush Delivery",
    "extra_revision": "Extra Revision",
    "custom_staging": "Custom Virtual Staging",
    "instagram_carousel": "Instagram Carousel",
    "unique_request": "Custom Request",
}


def _usd_to_cents(amount: float) -> int:
    """Convert a USD float to Stripe's integer cents representation."""
    return int(round(amount * 100))


def calculate_price(
    package: str,
    rooms: int = 1,
    addons: list[str] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Calculate total price and build a list of line-item dicts.

    Returns (total_usd, line_items) where each line_item has
    'name', 'unit_amount_cents', and 'quantity'.
    """
    prices = settings.package_prices
    pkg = package.lower()
    if pkg not in prices:
        raise ValueError(f"Unknown package: {package}")

    line_items: list[dict[str, Any]] = []
    total = 0.0

    # Base package
    base = prices[pkg]
    total += base
    line_items.append({
        "name": _PACKAGE_NAMES.get(pkg, package.title()),
        "unit_amount_cents": _usd_to_cents(base),
        "quantity": 1,
    })

    # Extra rooms
    extra_rooms = max(0, rooms - 1)
    if extra_rooms > 0:
        room_rate = settings.price_per_extra_room.get(pkg, 30.0)
        room_total = extra_rooms * room_rate
        total += room_total
        line_items.append({
            "name": f"Additional Room ({pkg.title()})",
            "unit_amount_cents": _usd_to_cents(room_rate),
            "quantity": extra_rooms,
        })

    # Add-ons
    addon_list = addons or []
    for addon in addon_list:
        addon_price = settings.addon_prices.get(addon, 0.0)
        if addon_price > 0:
            total += addon_price
            line_items.append({
                "name": _ADDON_NAMES.get(addon, addon.replace("_", " ").title()),
                "unit_amount_cents": _usd_to_cents(addon_price),
                "quantity": 1,
            })

    return total, line_items


def create_checkout_session(
    *,
    job_id: str,
    package: str,
    rooms: int,
    addons: list[str],
    email: str,
    customer_ref: str | None = None,
) -> tuple[str, str, float]:
    """Create a Stripe Checkout session and return (checkout_url, session_id, total).

    Raises RuntimeError if Stripe is not configured.
    """
    if not settings.stripe_secret_key:
        raise RuntimeError("Stripe secret key is not configured")

    stripe.api_key = settings.stripe_secret_key

    total, line_items = calculate_price(package, rooms, addons)

    stripe_line_items = []
    for item in line_items:
        stripe_line_items.append({
            "price_data": {
                "currency": "usd",
                "product_data": {"name": item["name"]},
                "unit_amount": item["unit_amount_cents"],
            },
            "quantity": item["quantity"],
        })

    base_url = (settings.public_base_url or "http://localhost:8000").rstrip("/")

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=stripe_line_items,
        customer_email=email,
        success_url=f"{base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}&job_id={job_id}",
        cancel_url=f"{base_url}/checkout/cancel?job_id={job_id}",
        metadata={
            "job_id": job_id,
            "package": package,
            "rooms": str(rooms),
            "addons": ",".join(addons),
            "customer_ref": customer_ref or "",
        },
    )

    logger.info(
        "Created Stripe checkout session %s for job %s (total $%.2f)",
        session.id,
        job_id,
        total,
    )

    return session.url, session.id, total


def handle_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify and process a Stripe webhook event.

    Returns a dict with event details on success.
    Raises ValueError for invalid signatures or unhandled event types.
    """
    if not settings.stripe_webhook_secret:
        raise RuntimeError("Stripe webhook secret is not configured")

    stripe.api_key = settings.stripe_secret_key

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError as e:
        logger.warning("Stripe webhook signature verification failed: %s", e)
        raise ValueError("Invalid signature") from e

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session["id"]
        metadata = session.get("metadata", {})
        job_id = metadata.get("job_id")

        if not job_id:
            # Try to find by stripe_session_id
            job = db.get_job_by_stripe_session(session_id)
            if job:
                job_id = job.id

        if job_id:
            job = db.get_job(job_id)
            if job and job.status == "pending_payment":
                db.update_job(job_id, status="queued")
                logger.info(
                    "Payment completed for job %s (session %s) — status → queued",
                    job_id,
                    session_id,
                )
            else:
                logger.warning(
                    "Webhook for job %s but status is '%s', not 'pending_payment'",
                    job_id,
                    job.status if job else "NOT FOUND",
                )
        else:
            logger.warning(
                "checkout.session.completed but no job_id in metadata or DB (session %s)",
                session_id,
            )

        return {
            "event": "checkout.session.completed",
            "session_id": session_id,
            "job_id": job_id,
            "payment_status": session.get("payment_status"),
        }

    logger.debug("Ignoring Stripe event type: %s", event["type"])
    return {"event": event["type"], "status": "ignored"}
