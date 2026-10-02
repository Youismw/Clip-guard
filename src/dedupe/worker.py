"""Production worker loop consuming S3 events from AWS SQS (Phase 6)."""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Optional
from urllib.parse import unquote_plus

from dedupe.models import ClipRef, Verdict
from dedupe.pipeline import Pipeline
from dedupe.sources.s3 import S3Source

logger = logging.getLogger("dedupe.worker")


class SqsWorker:
    """Worker processing video ingestion events from Amazon SQS."""

    def __init__(
        self,
        pipeline: Pipeline,
        queue_url: str,
        s3_source: Optional[S3Source] = None,
        results_bucket: Optional[str] = None,
        results_prefix: str = "verdicts/",
        sqs_client: Optional[Any] = None,
        s3_client: Optional[Any] = None,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self.pipeline = pipeline
        self.queue_url = queue_url
        self.results_bucket = results_bucket
        self.results_prefix = results_prefix.strip("/")
        self.region = region
        self.endpoint_url = endpoint_url

        if sqs_client is not None:
            self.sqs = sqs_client
        else:
            import boto3  # type: ignore[import-untyped]

            kwargs: dict[str, Any] = {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self.sqs = boto3.client("sqs", **kwargs)

        if s3_client is not None:
            self.s3 = s3_client
        elif s3_source is not None:
            self.s3 = s3_source.s3
        else:
            import boto3

            kwargs = {"region_name": self.region}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self.s3 = boto3.client("s3", **kwargs)

        self.s3_source = s3_source or S3Source(
            bucket="default",
            region=self.region,
            endpoint_url=self.endpoint_url,
            s3_client=self.s3,
        )

    def extract_s3_events(self, body_text: str) -> list[tuple[str, str]]:
        """Parse S3 bucket and key from direct SQS, S3 event, or SNS-wrapped event."""
        events: list[tuple[str, str]] = []
        try:
            data = json.loads(body_text)
            # 1. Check SNS wrapper
            if "Message" in data and isinstance(data["Message"], str):
                try:
                    data = json.loads(data["Message"])
                except Exception:
                    pass

            # 2. Check S3 Event Notification Records
            if "Records" in data and isinstance(data["Records"], list):
                for rec in data["Records"]:
                    s3_info = rec.get("s3", {})
                    b = s3_info.get("bucket", {}).get("name")
                    raw_k = s3_info.get("object", {}).get("key")
                    if b and raw_k:
                        k = unquote_plus(raw_k)
                        events.append((b, k))

            # 3. Direct JSON message payload { "bucket": "...", "key": "..." }
            elif "bucket" in data and "key" in data:
                events.append((data["bucket"], data["key"]))
        except Exception as exc:
            logger.warning("Failed to parse SQS body JSON: %s", exc)

        return events

    def process_s3_object(self, bucket: str, key: str) -> Verdict:
        """Download object, compute true streaming SHA-256, run check, and record verdict."""
        # 1. Compute true SHA-256 (not S3 ETag)
        sha256 = self.s3_source._compute_s3_sha256(bucket, key)
        uri = f"s3://{bucket}/{key}"

        # 2. Materialize video locally to run perceptual detectors
        with self.s3_source.materialize(uri) as local_temp_path:
            clip = ClipRef(
                clip_id=sha256,
                uri=uri,
                local_path=local_temp_path,
                status="pending",
            )
            verdict = self.pipeline.check(clip, register=True)

        # 3. Write verdict to database results table if supported
        if hasattr(self.pipeline.store, "record_verdict"):
            self.pipeline.store.record_verdict(
                clip_id=verdict.clip_id,
                verdict=verdict.verdict,
                layer=verdict.layer,
                intent=verdict.intent_assessment,
                reason=verdict.reason,
                verdict_json=verdict.to_json(),
            )

        # 4. Write verdict JSON to S3 results bucket/prefix if configured
        if self.results_bucket:
            out_key = f"{self.results_prefix}/{verdict.clip_id}.json"
            self.s3.put_object(
                Bucket=self.results_bucket,
                Key=out_key,
                Body=verdict.to_json().encode("utf-8"),
                ContentType="application/json",
            )

        return verdict

    def process_message(self, message: dict[str, Any]) -> bool:
        body = message.get("Body", "")
        receipt_handle = message.get("ReceiptHandle")
        events = self.extract_s3_events(body)

        if not events:
            logger.warning("No valid S3 event found in SQS message %s", message.get("MessageId"))
            if receipt_handle:
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
            return False

        all_success = True
        for bucket, key in events:
            try:
                verdict = self.process_s3_object(bucket, key)
                sys.stderr.write(
                    f"[WORKER] Processed s3://{bucket}/{key} -> {verdict.verdict.upper()} "
                    f"(layer: {verdict.layer}, reason: {verdict.reason})\n"
                )
            except Exception as exc:
                sys.stderr.write(f"[WORKER ERROR] Failed s3://{bucket}/{key}: {exc}\n")
                all_success = False

        if all_success and receipt_handle:
            self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)

        return all_success

    def run_once(self, max_messages: int = 10, wait_time_seconds: int = 5) -> int:
        """Fetch and process up to max_messages from SQS (useful for batch runs and tests)."""
        resp = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=min(max_messages, 10),
            WaitTimeSeconds=wait_time_seconds,
            VisibilityTimeout=300,
        )
        messages = resp.get("Messages", [])
        processed = 0
        for msg in messages:
            if self.process_message(msg):
                processed += 1
        return processed

    def run_loop(self, poll_interval: float = 1.0) -> None:
        """Run continuous worker listening loop."""
        sys.stderr.write(f"ClipGuard SQS worker listening on {self.queue_url}...\n")
        while True:
            try:
                count = self.run_once(max_messages=10, wait_time_seconds=10)
                if count == 0:
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                sys.stderr.write("Worker shutting down gracefully...\n")
                break
            except Exception as exc:
                sys.stderr.write(f"Worker polling error: {exc}\n")
                time.sleep(poll_interval)
