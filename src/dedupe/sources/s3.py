"""Amazon S3 clip source implementation using boto3."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from dedupe.models import ClipRef
from dedupe.sources.base import ClipSource
from dedupe.sources.local import CHUNK_SIZE, KNOWN_VIDEO_EXTENSIONS


class S3Source(ClipSource):
    """Source that iterates over clips in AWS S3 buckets and materializes them locally.

    Safety: Strictly read-only; never writes to or deletes from either bucket.
    """

    def __init__(
        self,
        bucket: str,
        approved_bucket: Optional[str] = None,
        prefix: str = "",
        status: str = "pending",
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        s3_client: Optional[Any] = None,
        use_presigned_urls: bool = False,
    ) -> None:
        self.bucket = bucket
        self.approved_bucket = (
            approved_bucket if approved_bucket and approved_bucket != bucket else None
        )
        self.prefix = prefix
        self.status = status
        self.region = region
        self.endpoint_url = endpoint_url
        self.use_presigned_urls = use_presigned_urls

        if s3_client is not None:
            self.s3 = s3_client
        else:
            import boto3  # type: ignore[import-untyped]

            kwargs: dict[str, Any] = {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self.s3 = boto3.client("s3", **kwargs)

    def _compute_s3_sha256(self, bucket: str, key: str) -> str:
        """Stream object contents in 1 MiB chunks to compute true SHA-256.

        Note: The S3 ETag is NOT a SHA-256 (especially for multipart uploads).
        """
        response = self.s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        h = hashlib.sha256()
        try:
            while True:
                chunk = body.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
        finally:
            body.close()
        return h.hexdigest()

    def _iter_bucket_keys(self, bucket: str, prefix: str, clip_status: str) -> Iterator[ClipRef]:
        paginator = self.s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if not key or key.endswith("/"):
                    continue

                ext = Path(key).suffix.lower()
                if ext in KNOWN_VIDEO_EXTENSIONS or ext == "":
                    sha256 = self._compute_s3_sha256(bucket, key)
                    uri = f"s3://{bucket}/{key}"
                    yield ClipRef(
                        clip_id=sha256,
                        uri=uri,
                        local_path=Path(key),
                        status=clip_status,
                    )

    def iter_clips(self) -> Iterator[ClipRef]:
        """Iterate clips across the target bucket and optional approved bucket."""
        # 1. Yield clips from main target bucket (e.g. new clips with status)
        yield from self._iter_bucket_keys(self.bucket, self.prefix, self.status)

        # 2. If separate approved bucket configured, index those as approved
        if self.approved_bucket:
            yield from self._iter_bucket_keys(self.approved_bucket, self.prefix, "approved")

    @contextmanager
    def materialize(self, uri: str) -> Iterator[Path]:
        """Download S3 object to a local temporary file, cleaning up on exit."""
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError(f"S3Source can only materialize s3:// URIs, got: '{uri}'")

        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        ext = Path(key).suffix or ".mp4"

        # Create temporary file with appropriate extension for media tools
        fd, temp_path_str = tempfile.mkstemp(prefix="clipguard_s3_", suffix=ext)
        os.close(fd)
        temp_file = Path(temp_path_str)

        try:
            self.s3.download_file(bucket, key, str(temp_file))
            yield temp_file
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    def generate_presigned_url(self, uri: str, expiration_seconds: int = 3600) -> str:
        """Generate a presigned URL for direct streaming without downloading to disk."""
        parsed = urlparse(uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        return str(
            self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiration_seconds,
            )
        )
