import os
import uuid
import boto3
from botocore.config import Config
import asyncio

def _get_s3_config() -> Config:
    try:
        return Config(
            signature_version='s3v4',
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
        )
    except TypeError:
        return Config(signature_version='s3v4')

def upload_to_r2_sync(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Synchronous upload to R2, called via asyncio.to_thread to avoid blocking."""
    endpoint = os.getenv('R2_ENDPOINT_URL')
    access_key = os.getenv('R2_ACCESS_KEY_ID')
    secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    bucket = os.getenv('R2_BUCKET_NAME')
    public_url = os.getenv('R2_PUBLIC_URL')

    if not all([endpoint, access_key, secret_key, bucket, public_url]):
        raise ValueError("R2 environment variables are not fully configured in the stack.")

    # Generate a unique key for the file to prevent overwrites
    unique_key = f"{uuid.uuid4().hex}_{filename}"

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=_get_s3_config()
    )

    s3.put_object(
        Bucket=bucket,
        Key=unique_key,
        Body=file_bytes,
        ContentType=content_type
    )

    # Return the public URL
    return f"{public_url.rstrip('/')}/{unique_key}"

async def upload_image_to_r2(file_bytes: bytes, filename: str, content_type: str = "image/png") -> str:
    """Async wrapper for R2 upload"""
    return await asyncio.to_thread(upload_to_r2_sync, file_bytes, filename, content_type)


def get_r2_stats_sync() -> dict:
    """List objects in the configured R2 bucket and compute storage statistics."""
    endpoint = os.getenv('R2_ENDPOINT_URL')
    access_key = os.getenv('R2_ACCESS_KEY_ID')
    secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    bucket = os.getenv('R2_BUCKET_NAME')
    public_url = os.getenv('R2_PUBLIC_URL')

    if not all([endpoint, access_key, secret_key, bucket]):
        raise ValueError("R2 environment variables are not fully configured.")

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=_get_s3_config(),
    )

    total_objects = 0
    total_bytes = 0
    gif_count = 0
    png_count = 0
    other_count = 0
    latest_modified = None
    latest_key = None

    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            total_objects += 1
            size = obj.get('Size', 0)
            total_bytes += size
            key = obj.get('Key', '')
            lower_key = key.lower()
            if lower_key.endswith('.gif'):
                gif_count += 1
            elif lower_key.endswith('.png'):
                png_count += 1
            else:
                other_count += 1

            mod = obj.get('LastModified')
            if mod and (latest_modified is None or mod > latest_modified):
                latest_modified = mod
                latest_key = key

    return {
        "bucket": bucket,
        "public_url": public_url or "Not set",
        "total_objects": total_objects,
        "total_bytes": total_bytes,
        "gif_count": gif_count,
        "png_count": png_count,
        "other_count": other_count,
        "latest_key": latest_key,
        "latest_modified": latest_modified,
    }


async def get_r2_stats() -> dict:
    """Async wrapper for R2 bucket statistics."""
    return await asyncio.to_thread(get_r2_stats_sync)



