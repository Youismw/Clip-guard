from __future__ import annotations

import random

import numpy as np

from dedupe.media.phash import (
    compute_phash64,
    hamming_distance,
    hash_to_chunks,
    is_low_information,
    to_signed64,
    to_unsigned64,
)


def test_phash_determinism() -> None:
    """Computing pHash multiple times on identical input produces identical hash."""
    rng = np.random.RandomState(42)
    frame = rng.randint(0, 256, size=(32, 32), dtype=np.uint8)

    h1 = compute_phash64(frame)
    h2 = compute_phash64(frame)

    assert isinstance(h1, int)
    assert h1 == h2
    assert 0 <= h1 < (1 << 64)


def test_phash_noise_robustness() -> None:
    """Slight modifications to image produce small Hamming distance."""
    rng = np.random.RandomState(123)
    frame = rng.randint(50, 200, size=(32, 32), dtype=np.uint8)

    # Slight brightness adjustment (+10)
    adjusted = np.clip(frame.astype(np.int16) + 10, 0, 255).astype(np.uint8)

    h1 = compute_phash64(frame)
    h2 = compute_phash64(adjusted)

    dist = hamming_distance(h1, h2)
    # Brightness shift should preserve low-frequency DCT relations
    assert dist <= 3


def test_phash_distinct_images() -> None:
    """Completely independent patterns have high Hamming distance."""
    f1 = np.zeros((32, 32), dtype=np.uint8)
    f1[:16, :16] = 255
    f1[16:, 16:] = 255

    f2 = np.zeros((32, 32), dtype=np.uint8)
    f2[8:24, 8:24] = 255

    h1 = compute_phash64(f1)
    h2 = compute_phash64(f2)

    assert hamming_distance(h1, h2) > 10


def test_signed64_roundtrip_with_top_bit_set() -> None:
    """Hashes with top bit set must round-trip correctly between Python unsigned

    and SQLite signed.
    """
    test_hashes = [
        0,
        1,
        (1 << 31) - 1,
        1 << 31,
        (1 << 63) - 1,
        1 << 63,  # Top bit set (0x8000000000000000)
        (1 << 63) + 42,
        0xFFFFFFFFFFFFFFFF,  # All bits set
    ]

    for u in test_hashes:
        s = to_signed64(u)
        # In signed 64-bit: -(2^63) <= s <= 2^63 - 1
        assert -(1 << 63) <= s <= (1 << 63) - 1
        u_recovered = to_unsigned64(s)
        assert u_recovered == u, f"Failed round-trip for 0x{u:016x}"


def test_chunk_pigeonhole_property() -> None:
    """Verify pigeonhole theorem: any two 64-bit hashes with Hamming distance <= 3

    MUST share at least one identical 16-bit chunk when split into 4 chunks.
    """
    rng = random.Random(999)

    for _ in range(500):
        # Generate random 64-bit hash
        h1 = rng.getrandbits(64)

        # Flip k bits where 0 <= k <= 3
        k = rng.randint(0, 3)
        bit_positions = rng.sample(range(64), k)
        mask = 0
        for pos in bit_positions:
            mask |= 1 << pos

        h2 = h1 ^ mask
        assert hamming_distance(h1, h2) == k

        chunks1 = hash_to_chunks(h1, num_chunks=4)
        chunks2 = hash_to_chunks(h2, num_chunks=4)

        # Pigeonhole: at least one chunk must be exactly identical
        shared_chunks = [c1 == c2 for c1, c2 in zip(chunks1, chunks2, strict=True)]
        assert any(shared_chunks), f"Pigeonhole failed for dist={k}: {chunks1} vs {chunks2}"


def test_low_information_detection() -> None:
    """Low standard deviation frames (solid black, blank wall) are flagged."""
    black_frame = np.zeros((32, 32), dtype=np.uint8)
    assert is_low_information(black_frame, min_frame_std=8.0)

    uniform_frame = np.full((32, 32), 128, dtype=np.uint8)
    assert is_low_information(uniform_frame, min_frame_std=8.0)

    # High information frame
    checker = np.zeros((32, 32), dtype=np.uint8)
    checker[::2, ::2] = 255
    checker[1::2, 1::2] = 255
    assert not is_low_information(checker, min_frame_std=8.0)
