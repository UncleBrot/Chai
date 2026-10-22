import os
import uuid
from urllib.parse import urlsplit
import boto3
from botocore.config import Config
import asyncio

_s3_client = None
_cached_creds = None


def _build_s3_config() -> Config:
    base_kwargs = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
    }
    try:
        # botocore >= 1.36.0 enables default CRC32 checksum headers that Cloudflare R2 can reject
        return Config(
            **base_kwargs,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    except TypeError:
        return Config(**base_kwargs)


def _get_s3_client(endpoint: str, access_key: str, secret_key: str):
    global _s3_client, _cached_creds
    creds = (endpoint, access_key, secret_key)
    if _s3_client is None or _cached_creds != creds:
        _s3_client = boto3.client(
            service_name="s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=_build_s3_config(),
        )
        _cached_creds = creds
    return _s3_client


def _clean_env(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip("'\"")


def upload_to_r2_sync(file_bytes: bytes, filename: str, content_type: str) -> str | None:
    """Synchronous upload to R2, called via asyncio.to_thread to avoid blocking."""
    endpoint = _clean_env(os.getenv("R2_ENDPOINT_URL"))
    access_key = _clean_env(os.getenv("R2_ACCESS_KEY_ID"))
    secret_key = _clean_env(os.getenv("R2_SECRET_ACCESS_KEY"))
    bucket = _clean_env(os.getenv("R2_BUCKET_NAME"))
    public_url = _clean_env(os.getenv("R2_PUBLIC_URL"))

    missing = [
        k
        for k, v in [
            ("R2_ENDPOINT_URL", endpoint),
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
            ("R2_BUCKET_NAME", bucket),
        ]
        if not v
    ]

    if missing:
        raise ValueError(f"Missing required R2 environment variables: {', '.join(missing)}")

    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    # Strip any accidental "/<bucket>" path copied from the Cloudflare R2 dashboard S3 API URL
    parsed_endpoint = urlsplit(endpoint)
    endpoint = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"

    # Generate a unique key for the file to prevent overwrites
    clean_filename = os.path.basename(filename).replace(" ", "_")
    unique_key = f"{uuid.uuid4().hex}_{clean_filename}"

    s3 = _get_s3_client(endpoint, access_key, secret_key)

    s3.put_object(
        Bucket=bucket,
        Key=unique_key,
        Body=file_bytes,
        ContentType=content_type,
    )
    print(f"[r2] Uploaded '{unique_key}' ({len(file_bytes)} bytes) to bucket '{bucket}'.")

    if public_url:
        if not public_url.startswith(("http://", "https://")):
            public_url = f"https://{public_url}"
        public_url = public_url.rstrip("/")
        return f"{public_url}/{unique_key}"

    return None


async def upload_image_to_r2(file_bytes: bytes, filename: str, content_type: str = "image/png") -> str | None:
    """Async wrapper for R2 upload"""
    return await asyncio.to_thread(upload_to_r2_sync, file_bytes, filename, content_type)

