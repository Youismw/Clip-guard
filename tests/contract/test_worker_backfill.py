"""Tests verifying SqsWorker and BackfillManager functionality."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
from moto import mock_aws

from dedupe.backfill import BackfillManager
from dedupe.eval.edits import generate_synthetic_clip
from dedupe.pipeline import load_pipeline
from dedupe.sources.local import LocalSource
from dedupe.sources.s3 import S3Source
from dedupe.stores.sqlite import SqliteStore
from dedupe.worker import SqsWorker


@mock_aws
def test_sqs_worker_e2e(tmp_path: Path) -> None:
    # 1. Setup S3 & SQS
    s3 = boto3.client("s3", region_name="us-east-1")
    sqs = boto3.client("sqs", region_name="us-east-1")

    video_bucket = "worker-video-bucket"
    results_bucket = "worker-results-bucket"
    s3.create_bucket(Bucket=video_bucket)
    s3.create_bucket(Bucket=results_bucket)

    q_resp = sqs.create_queue(QueueName="test-ingest-queue")
    q_url = q_resp["QueueUrl"]

    # 2. Upload video
    vid_path = tmp_path / "incoming_vid.mp4"
    generate_synthetic_clip(vid_path, duration_s=4.0, pattern="mandelbrot", frequency=440)
    s3.upload_file(str(vid_path), video_bucket, "new/incoming_vid.mp4")

    # 3. Setup Pipeline & Worker
    store = SqliteStore(tmp_path / "worker_test.db")
    pipeline = load_pipeline(store=store)
    s3_source = S3Source(bucket=video_bucket, region="us-east-1", s3_client=s3)

    worker = SqsWorker(
        pipeline=pipeline,
        queue_url=q_url,
        s3_source=s3_source,
        results_bucket=results_bucket,
        results_prefix="results",
        sqs_client=sqs,
        s3_client=s3,
    )

    try:
        # 4. Send S3 Event message to SQS
        s3_event_payload = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": video_bucket},
                        "object": {"key": "new/incoming_vid.mp4"},
                    }
                }
            ]
        }
        sqs.send_message(QueueUrl=q_url, MessageBody=json.dumps(s3_event_payload))

        # 5. Run worker batch
        processed_count = worker.run_once(max_messages=1, wait_time_seconds=1)
        assert processed_count == 1

        # 6. Verify result in S3 results bucket
        res_objects = s3.list_objects_v2(Bucket=results_bucket, Prefix="results/")
        assert "Contents" in res_objects
        assert len(res_objects["Contents"]) == 1

        verdict_key = res_objects["Contents"][0]["Key"]
        v_body = (
            s3.get_object(Bucket=results_bucket, Key=verdict_key)["Body"].read().decode("utf-8")
        )
        v_json = json.loads(v_body)
        assert v_json["verdict"] in {"clear", "duplicate"}
        assert v_json["schema_version"] == 1

        # 7. Verify SQS message was deleted
        empty_resp = sqs.receive_message(QueueUrl=q_url, WaitTimeSeconds=1)
        assert "Messages" not in empty_resp
    finally:
        store.close()


def test_backfill_manager_resumability(tmp_path: Path) -> None:
    # 1. Create 3 local video files
    src_dir = tmp_path / "approved"
    src_dir.mkdir()
    for i in range(3):
        p = src_dir / f"clip_{i}.mp4"
        generate_synthetic_clip(p, duration_s=3.0, frequency=300 + i * 50)

    store = SqliteStore(tmp_path / "backfill.db")
    pipeline = load_pipeline(store=store)
    source = LocalSource(src_dir, status="approved")
    checkpoint_file = tmp_path / "checkpoint.json"

    mgr = BackfillManager(
        pipeline=pipeline,
        source=source,
        checkpoint_path=checkpoint_file,
    )

    try:
        # Dry run does not index
        mgr.run(dry_run=True)
        assert store.clips.count() == 0

        # First run indexes all 3
        newly_indexed = mgr.run()
        assert newly_indexed == 3
        assert store.clips.count(status="approved") == 3
        assert checkpoint_file.exists()

        # Interrupted / repeated run resumes and does not duplicate
        repeat_run = mgr.run()
        assert repeat_run == 0
        assert store.clips.count(status="approved") == 3
    finally:
        store.close()
