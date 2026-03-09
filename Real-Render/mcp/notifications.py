from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import db
from .config import settings

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_password)


def send_completion_email(job_id: str) -> None:
    """Send a completion notification email for a finished job.

    Silently skips if SMTP is not configured or the job has no email.
    """
    if not _smtp_configured():
        logger.debug("SMTP not configured — skipping email for job %s", job_id)
        return

    job = db.get_job(job_id)
    if not job or not job.email:
        return

    if job.status != "done":
        return

    base_url = (settings.public_base_url or "http://localhost:8000").rstrip("/")
    job_url = f"{base_url}/api/v1/jobs/{job.id}"

    # Build artifact links
    from .api import _artifacts_from_job
    artifacts = _artifacts_from_job(job)
    artifact_lines = "\n".join(
        f"  - {a.filename}: {a.download_url}" for a in artifacts
    ) or "  (no artifacts yet)"

    package_label = (job.package or "standard").capitalize()
    subject = "Your Open Door Cinematic order is ready"
    body = f"""\
Hi there,

Great news — your {package_label} order is complete!

Order reference: {job.customer_ref or job.id}
Package: {package_label}
Job ID: {job.id}

Your deliverables:
{artifact_lines}

View full job details: {job_url}

Thanks for choosing Open Door Cinematic!
"""

    msg = MIMEMultipart()
    msg["From"] = settings.notification_from
    msg["To"] = job.email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)  # type: ignore[arg-type]
            server.send_message(msg)
        logger.info("Completion email sent to %s for job %s", job.email, job_id)
    except Exception:
        logger.exception("Failed to send email to %s for job %s", job.email, job_id)
