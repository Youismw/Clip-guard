# ClipGuard: Duplicate Video Detection System

ClipGuard is a modular, high-throughput duplicate video detection system designed to catch resold, reused, and lightly altered video clips (trimmed, re-encoded, resized, mirrored, watermarked, or re-muxed).

Built for robotic learning pipelines and large-scale video ingestion, ClipGuard processes both local filesystems and cloud-native Amazon S3/SQS workflows with zero modification to source videos.

---

## 1. System Architecture

```
ClipSource (Local / S3) ──► Pipeline ──► Verdict (JSON)
                               │
               ┌───────────────┼───────────────┐
         exact_sha256     frame_phash     audio_chromaprint  (Config-driven)
               └───────────────┼───────────────┘
                               │
                Store (SqliteStore / PostgresStore)
```

### Core Design Principles
- **Detectors Never Know About Storage:** Detectors interact exclusively with `Store` protocols and `ClipRef` domain models.
- **Pure Core, Impure Edges:** Algorithmic computations (pHash, hamming distance, voting, combining) are pure functions. Media decoding and I/O are isolated in adapters.
- **Fail-Soft Pipeline:** If a detector encounters a corrupt file, it yields an `error` result without terminating the pipeline. Other detectors continue unaffected.
- **Never Mutate Source Data:** ClipGuard has strictly read-only access to video sources.

---

## 2. Detectors & Algorithms

ClipGuard features three independent, pluggable detectors:

| Detector | Method | Strengths | Typical Latency |
|---|---|---|---|
| `exact_sha256` | Streaming SHA-256 byte digest | Byte-identical re-submissions, renamed files | < 0.05s / clip |
| `frame_phash` | 64-bit DCT perceptual hash (1 fps), chunked lookup (pigeonhole principle), temporal offset voting, mirror flip detection, junk-hash filtering | Re-encodes (H.264/H.265), resolutions (1080p -> 480p), head/tail trims, watermarks, brightness/contrast shifts, horizontal mirroring | ~0.15s - 0.4s / clip |
| `audio_chromaprint` | AcoustID Chromaprint 32-bit sub-fingerprints, sliding bit error rate (BER) correlation | Catches audio re-encoding, volume shifts, audio track reuse across visual modifications | ~0.10s / clip |

### Combiner Policies
A `Combiner` synthesizes individual detector results into a final `Verdict`:
- **`any` (default):** Declares `duplicate` if any detector meets its `flag_threshold`; `review` if meeting `review_threshold`; else `clear`.
- **`corroborate`:** Promotes a borderline visual `review` to `duplicate` when audio independently matches the same original clip.
- **`weighted`:** Calculates a weighted confidence score across enabled detectors against configurable thresholds.

---

## 3. Installation & Prerequisites

### Prerequisites
- Python 3.9+
- `ffmpeg` and `ffprobe` (required for video decoding and frame extraction)
- `fpcalc` / Chromaprint (required only if `audio_chromaprint` is enabled)

### Local Setup
```bash
# Clone and install in virtual environment
python -m venv venv
venv\Scripts\activate       # On Windows (or 'source venv/bin/activate' on Linux/macOS)
pip install -e .

# Verify environment & external dependencies
dedupe doctor
```

### Docker
```bash
# Build production Docker container
docker build -t clipguard:latest .

# Run diagnostic check
docker run --rm clipguard:latest doctor
```

---

## 4. CLI Reference & Exit Codes

```bash
# Verify system and configuration
dedupe doctor [--config PATH] [--all] [--json]

# Index approved reference videos
dedupe index <path|file> [--status approved|rejected|pending] [--workers N]

# Check incoming video clip against known database
dedupe check <path> [--no-register] [--json]

# View index statistics and counts
dedupe stats

# Launch functional desktop graphical interface
dedupe gui

# Run synthetic evaluation harness
dedupe eval --config eval.toml

# Run local pilot evaluation protocol (Section 13)
dedupe pilot --pairs data/known_pairs.csv --distinct data/distinct/

# Cloud backfill for existing approved S3 bucket
dedupe backfill [--source s3://bucket/prefix] [--checkpoint file.json] [--dry-run]

# Run SQS cloud worker event loop
dedupe worker --queue-url <SQS_URL> [--results-bucket <BUCKET>] [--once]
```

### Exit Codes
To integrate into automated scripts, pipelines, and CI/CD, ClipGuard returns standardized exit codes:

| Code | Status | Meaning |
|---|---|---|
| `0` | **CLEAR** | No duplicates detected; clip is novel |
| `10` | **DUPLICATE** | Confirmed duplicate detected exceeding flag threshold |
| `11` | **REVIEW** | Borderline match detected; routed to human review queue |
| `12` | **ALREADY_INDEXED** | Exact byte match of a previously indexed clip |
| `1` | **ERROR** | Pipeline or decoding failure occurred |
| `2` | **USAGE ERROR** | Invalid arguments or configuration error |

---

## 5. Configuration Guide (`dedupe.toml`)

ClipGuard is configured via TOML with support for environment variable overrides (`DEDUPE_<SECTION>__<KEY>`):

```toml
[pipeline]
combiner = "any"                  # any | corroborate | weighted
register_after_check = true       # Automatically index novel clips after checking

[source]
type = "local"                    # local | s3
path = "./data/incoming"
# S3 settings (Phase 6):
# bucket = "my-incoming-bucket"
# approved_bucket = "my-approved-bucket"
# region = "us-east-1"

[store]
type = "sqlite"                   # sqlite | postgres
path = "./dedupe.db"
# Postgres settings (Phase 6):
# host = "localhost"
# port = 5432
# database = "clipguard"
# user = "postgres"
# password = "secretpassword"

[detectors.exact_sha256]
enabled = true

[detectors.frame_phash]
enabled = true
fps = 1                           # 1 frame per second sampling
radius = 3                        # Max hamming distance per chunk
num_chunks = 4                    # 64-bit hash split into four 16-bit chunks
offset_bin_s = 2                  # Temporal voting bin size in seconds
flag_threshold = 0.80             # >= 0.80 confidence -> duplicate
review_threshold = 0.40           # >= 0.40 confidence -> review queue

[detectors.audio_chromaprint]
enabled = false                   # Disabled by default; enable after acoustic evaluation
ber_threshold = 0.35              # Bit error rate threshold
flag_threshold = 0.80
review_threshold = 0.50
```

---

## 6. Known Limitations and Risks

1. **Heavy Crops, Zooms, and Speed Changes:**
   Perceptual frame hashing relies on spatial frequencies and temporal alignment. Substantial zoom-ins (>25%), tight spatial cropping, or playback speed modifications (>5%) degrade pHash recall.
2. **Distinct Takes of the Same Task:**
   In robotics training data, different takes of the same physical task (e.g. cutting vegetables on the same cutting board) share high visual background similarity. ClipGuard suppresses false positives through temporal offset voting and junk-hash filtering, but threshold tuning on a validation split remains essential.
3. **Muted or Silent Audio:**
   The audio detector requires audible sound. In environments where robots or cameras record silent tracks, acoustic matching is skipped.
4. **ETag vs SHA-256:**
   Amazon S3 multipart ETags are not SHA-256 digests. ClipGuard streams object content directly to compute true SHA-256 digests on ingest.

---

## 7. Extending and Operations

- **Extending Guide:** See [docs/extending.md](docs/extending.md) for adding custom detectors, stores, and sources.
- **Operations Runbook:** See [docs/runbook.md](docs/runbook.md) for threshold tuning, resumable backfills, SQS worker management, and backups.
