import os
import uuid
import boto3
from botocore.config import Config
import asyncio


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
        config=Config(signature_version='s3v4')
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
