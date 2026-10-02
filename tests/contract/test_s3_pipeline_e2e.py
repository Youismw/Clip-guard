"""End-to-end integration and contract test verifying Pipeline with S3Source."""

from __future__ import annotations

from pathlib import Path

import boto3
from moto import mock_aws

from dedupe.eval.edits import generate_synthetic_clip
from dedupe.models import ClipRef
from dedupe.pipeline import load_pipeline
from dedupe.sources.s3 import S3Source
from dedupe.stores.sqlite import SqliteStore


@mock_aws
def test_s3_pipeline_e2e_lifecycle(tmp_path: Path) -> None:
    # 1. Setup Moto S3
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "company-robot-training-footage"
    s3.create_bucket(Bucket=bucket)

    # 2. Generate local synthetic test clips
    orig_path = tmp_path / "orig.mp4"
    dup_path = tmp_path / "dup.mp4"
    distinct_path = tmp_path / "distinct.mp4"

    from dedupe.eval.edits import EditCatalog

    generate_synthetic_clip(orig_path, duration_s=4.0, pattern="mandelbrot", frequency=440)
    # Duplicate with visual modification (trimmed head) -> different SHA-256 but matching frames
    cat = EditCatalog()
    v_edit = cat.apply_single_edit(orig_path, tmp_path, "trim_head_1s", duration_s=4.0)
    dup_path = v_edit.output_path
    # Distinct with completely different pattern
    generate_synthetic_clip(distinct_path, duration_s=4.0, pattern="testsrc2", frequency=880)

    # 3. Upload to S3
    s3.upload_file(str(orig_path), bucket, "approved/orig.mp4")
    s3.upload_file(str(dup_path), bucket, "incoming/dup.mp4")
    s3.upload_file(str(distinct_path), bucket, "incoming/distinct.mp4")

    # 4. Instantiate S3Source
    s3_source = S3Source(bucket=bucket, region="us-east-1", s3_client=s3)

    # 5. Setup Pipeline with isolated test DB
    store = SqliteStore(tmp_path / "s3_e2e.db")
    pipeline = load_pipeline(store=store)

    try:
        # Index approved clip from S3
        orig_sha = s3_source._compute_s3_sha256(bucket, "approved/orig.mp4")
        with s3_source.materialize(f"s3://{bucket}/approved/orig.mp4") as p:
            orig_clip = ClipRef(
                clip_id=orig_sha,
                uri=f"s3://{bucket}/approved/orig.mp4",
                local_path=p,
                status="approved",
            )
            pipeline.index(orig_clip)

        assert store.clips.exists(orig_sha)
        assert store.clips.count(status="approved") == 1

        # Check duplicate clip from S3
        dup_sha = s3_source._compute_s3_sha256(bucket, "incoming/dup.mp4")
        with s3_source.materialize(f"s3://{bucket}/incoming/dup.mp4") as p:
            dup_clip = ClipRef(
                clip_id=dup_sha,
                uri=f"s3://{bucket}/incoming/dup.mp4",
                local_path=p,
                status="pending",
            )
            v_dup = pipeline.check(dup_clip, register=False)

        assert v_dup.verdict == "duplicate"
        assert v_dup.layer == "visual"
        assert v_dup.intent_assessment == "edited_likely_intentional"
        assert len(v_dup.matches) == 1
        assert v_dup.matches[0].matched_clip_id == orig_sha

        # Check distinct clip from S3
        dist_sha = s3_source._compute_s3_sha256(bucket, "incoming/distinct.mp4")
        with s3_source.materialize(f"s3://{bucket}/incoming/distinct.mp4") as p:
            dist_clip = ClipRef(
                clip_id=dist_sha,
                uri=f"s3://{bucket}/incoming/distinct.mp4",
                local_path=p,
                status="pending",
            )
            v_dist = pipeline.check(dist_clip, register=False)

        assert v_dist.verdict == "clear"
        assert v_dist.layer == "none"

        # Check already_indexed condition
        with s3_source.materialize(f"s3://{bucket}/approved/orig.mp4") as p:
            recheck_clip = ClipRef(
                clip_id=orig_sha,
                uri=f"s3://{bucket}/approved/orig.mp4",
                local_path=p,
                status="pending",
            )
            v_recheck = pipeline.check(recheck_clip, register=False)

        assert v_recheck.verdict == "already_indexed"
        assert v_recheck.layer == "exact"
        assert v_recheck.intent_assessment == "unedited_likely_unaware"
    finally:
        store.close()
