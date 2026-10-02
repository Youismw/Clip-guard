"""Unit tests for AudioChromaprintDetector: offset voting, bit error rate, and thresholds."""

from __future__ import annotations

from pathlib import Path

from dedupe.detectors.audio_chromaprint import AudioChromaprintDetector
from dedupe.models import ClipRef
from dedupe.stores.sqlite import SqliteStore


def test_audio_detector_ber_exact_and_shifted(tmp_path: Path) -> None:
    db_file = tmp_path / "audio_detector.db"
    store = SqliteStore(db_file)

    detector = AudioChromaprintDetector(
        params={
            "ber_threshold": 0.35,
            "flag_threshold": 0.80,
            "review_threshold": 0.50,
            "min_overlap_frames": 5,
        },
        store=store,
    )

    # 20 distinct 32-bit subfingerprints
    orig_subfps = [0x11111111 * (i + 1) for i in range(20)]
    clip_id = "audio_clip_orig"

    store.clips.add(ClipRef(clip_id, "file:///orig.mp4", Path("orig.mp4"), "approved"))
    detector.register(clip_id, orig_subfps)

    # 1. Exact match test
    matches = detector.search(orig_subfps, exclude_clip_id="audio_clip_query")
    assert len(matches) == 1
    m = matches[0]
    assert m.matched_clip_id == clip_id
    assert m.confidence == 1.0
    assert m.evidence["ber"] == 0.0
    assert m.evidence["offset_s"] == 0.0

    # 2. Shifted audio test (shifted by +5 subfingerprints)
    shifted_subfps = orig_subfps[5:]  # length 15
    matches_shifted = detector.search(shifted_subfps, exclude_clip_id="audio_clip_query")
    assert len(matches_shifted) == 1
    ms = matches_shifted[0]
    assert ms.matched_clip_id == clip_id
    assert ms.confidence == 1.0
    assert ms.evidence["ber"] == 0.0
    # Offset: p_db - p_q = 5
    assert ms.evidence["offset_s"] == round(5 / 6.25, 2)

    # 3. Altered audio test within BER threshold:
    # Half of subfingerprints are identical (seeding candidate votes),
    # and half have 1 bit flip.
    altered_subfps = [sfp ^ 0b1 if i % 2 == 0 else sfp for i, sfp in enumerate(orig_subfps)]
    matches_altered = detector.search(altered_subfps, exclude_clip_id="audio_clip_query")
    assert len(matches_altered) == 1
    ma = matches_altered[0]
    assert ma.matched_clip_id == clip_id
    assert 0.0 < ma.evidence["ber"] <= 0.05
    assert ma.confidence >= 0.80

    # 4. Unrelated audio test (large BER > 0.40)
    unrelated_subfps = [0x99999999 ^ (i * 12345) for i in range(20)]
    matches_unrelated = detector.search(unrelated_subfps, exclude_clip_id="audio_clip_query")
    assert len(matches_unrelated) == 0

    store.close()
