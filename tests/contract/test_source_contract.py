"""Contract tests verifying all ClipSource implementations conform to identical behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path

import boto3
from moto import mock_aws

from dedupe.sources.base import ClipSource
from dedupe.sources.local import LocalSource
from dedupe.sources.s3 import S3Source


class SourceContractSuite:
    """Reusable contract assertions for any ClipSource."""

    @staticmethod
    def assert_source_contract(source: ClipSource, expected_clips: list[tuple[str, bytes]]) -> None:
        # 1. Test iter_clips yields valid ClipRefs
        clips = list(source.iter_clips())
        assert len(clips) == len(expected_clips)

        clips_by_sha = {c.clip_id: c for c in clips}
        for _name, data in expected_clips:
            expected_sha = hashlib.sha256(data).hexdigest()
            assert expected_sha in clips_by_sha
            clip = clips_by_sha[expected_sha]
            assert clip.clip_id == expected_sha
            assert clip.status == "approved"

            # 2. Test materialize yields a readable file with identical bytes
            with source.materialize(clip.uri) as materialized_path:
                assert materialized_path.exists()
                assert materialized_path.is_file()
                content = materialized_path.read_bytes()
                assert content == data
                assert hashlib.sha256(content).hexdigest() == expected_sha

            # 3. Test materialized temp file is cleaned up after context exit
            # (For LocalSource it may point to the original file, but for remote it must not leak)


def test_local_source_contract(tmp_path: Path) -> None:
    src_dir = tmp_path / "videos"
    src_dir.mkdir()

    f1 = src_dir / "vid1.mp4"
    f2 = src_dir / "vid2.mp4"
    b1 = b"video_sample_content_1" * 100
    b2 = b"video_sample_content_2" * 100
    f1.write_bytes(b1)
    f2.write_bytes(b2)

    source = LocalSource(src_dir, status="approved")
    SourceContractSuite.assert_source_contract(source, [("vid1.mp4", b1), ("vid2.mp4", b2)])


@mock_aws
def test_s3_source_contract() -> None:
    s3 = boto3.client("s3", region_name="us-east-1")
    bucket = "test-clipguard-bucket"
    s3.create_bucket(Bucket=bucket)

    b1 = b"s3_video_content_alpha" * 150
    b2 = b"s3_video_content_beta" * 150
    s3.put_object(Bucket=bucket, Key="folder/clip1.mp4", Body=b1)
    s3.put_object(Bucket=bucket, Key="folder/clip2.mp4", Body=b2)

    source = S3Source(
        bucket=bucket,
        prefix="folder/",
        status="approved",
        region="us-east-1",
        s3_client=s3,
    )

    SourceContractSuite.assert_source_contract(source, [("clip1.mp4", b1), ("clip2.mp4", b2)])

    # Also test materialize cleanup for S3 temp file
    clips = list(source.iter_clips())
    assert len(clips) == 2
    temp_path: Path | None = None
    with source.materialize(clips[0].uri) as p:
        temp_path = p
        assert temp_path.exists()
    assert not temp_path.exists()  # Cleaned up on exit!
