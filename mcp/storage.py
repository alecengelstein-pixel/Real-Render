from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from .config import settings

logger = logging.getLogger(__name__)

_CONTENT_TYPES: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".ply": "application/x-ply",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".zip": "application/zip",
}


def s3_configured() -> bool:
    return bool(
        settings.s3_endpoint_url
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
    )


def _get_s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _content_type(path: Path) -> str:
    ct = _CONTENT_TYPES.get(path.suffix.lower())
    if ct:
        return ct
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upload_file(local_path: Path, s3_key: str) -> None:
    client = _get_s3_client()
    client.upload_file(
        str(local_path),
        settings.s3_bucket_name,
        s3_key,
        ExtraArgs={"ContentType": _content_type(local_path)},
    )
    logger.info("Uploaded %s -> s3://%s/%s", local_path, settings.s3_bucket_name, s3_key)


def upload_job_outputs(job_id: str, outputs_dir: str) -> dict[str, str]:
    """Upload all files in outputs_dir to S3. Returns {filename: s3_key}."""
    result: dict[str, str] = {}
    outputs = Path(outputs_dir)
    if not outputs.exists():
        return result
    for fp in outputs.rglob("*"):
        if not fp.is_file() or fp.name == "outputs.zip":
            continue
        relative = fp.relative_to(outputs).as_posix()
        s3_key = f"jobs/{job_id}/{relative}"
        try:
            upload_file(fp, s3_key)
            result[relative] = s3_key
        except Exception:
            logger.exception("Failed to upload %s", fp)
    return result


def generate_presigned_url(s3_key: str) -> str:
    client = _get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": s3_key},
        ExpiresIn=settings.s3_presigned_url_expiry,
    )
