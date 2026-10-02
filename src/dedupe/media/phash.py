"""Pure NumPy perceptual hash (pHash) implementation with 2D DCT."""

from __future__ import annotations

import numpy as np


def _compute_dct_basis(n: int = 32) -> np.ndarray:
    """Precompute the orthonormal 1D DCT-II basis matrix of size N x N."""
    basis = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            if i == 0:
                basis[i, j] = 1.0 / np.sqrt(n)
            else:
                basis[i, j] = np.sqrt(2.0 / n) * np.cos(np.pi * (2 * j + 1) * i / (2.0 * n))
    return basis


# Precompute 32x32 DCT basis matrix once at module load
DCT_BASIS_32: np.ndarray = _compute_dct_basis(32)


def is_low_information(frame: np.ndarray, min_frame_std: float = 8.0) -> bool:
    """Check if a frame is low-information (e.g. solid black, blank wall)."""
    return float(np.std(frame)) < min_frame_std


def compute_phash64(frame: np.ndarray) -> int:
    """Compute 64-bit perceptual hash for a 32x32 grayscale image.

    Steps:
    1. Transform 32x32 frame via precomputed 2D DCT: D = T @ frame @ T.T
    2. Extract top-left 8x8 frequency block
    3. Compute median of the 8x8 block
    4. Threshold at the median to produce 64 bits
    5. Pack into unsigned 64-bit integer
    """
    img = frame.astype(np.float64)
    # 2D DCT-II transform
    dct_2d = DCT_BASIS_32 @ img @ DCT_BASIS_32.T
    # Keep top-left 8x8 low-frequency block
    block_8x8 = dct_2d[:8, :8]

    # Median threshold
    med = float(np.median(block_8x8))
    bits = block_8x8 > med

    # Convert 64 boolean values to 64-bit unsigned integer
    val = 0
    for b in bits.ravel():
        val = (val << 1) | (1 if b else 0)
    return val


def to_signed64(u: int) -> int:
    """Convert unsigned 64-bit integer to signed two's complement 64-bit integer for SQLite."""
    if u >= (1 << 63):
        return u - (1 << 64)
    return u


def to_unsigned64(s: int) -> int:
    """Convert signed two's complement 64-bit integer from SQLite back to unsigned 64-bit."""
    if s < 0:
        return s + (1 << 64)
    return s


def hash_to_chunks(h: int, num_chunks: int = 4) -> tuple[int, ...]:
    """Split 64-bit hash into 4 x 16-bit chunks."""
    if num_chunks != 4:
        raise ValueError("Only 4 chunks (16 bits each) currently supported for 64-bit hashes.")
    return (
        (h >> 48) & 0xFFFF,
        (h >> 32) & 0xFFFF,
        (h >> 16) & 0xFFFF,
        h & 0xFFFF,
    )


def hamming_distance(h1: int, h2: int) -> int:
    """Compute Hamming distance between two 64-bit hashes."""
    return (h1 ^ h2).bit_count()
