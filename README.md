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
| `exact_sha256` | Streaming single-pass SHA-256 byte digest | Byte-identical re-submissions, renamed files | Instant (< 0.01s / clip) |
| `frame_phash` | 64-bit DCT perceptual hash (1 fps), chunked lookup (pigeonhole principle), temporal offset voting, **Speed-Invariant RANSAC Alignment (0.4× – 3.0× speed changes)**, horizontal mirror flip detection, junk-hash filtering | Re-encodes (H.264/H.265), resolutions (1080p -> 480p), head/tail trims, watermarks, brightness/contrast shifts, **speed manipulations (1.3×, 1.7×, 2.4×, etc.)**, horizontal mirroring | ~0.15s – 5.0s / clip |
| `audio_chromaprint` | AcoustID Chromaprint 32-bit sub-fingerprints, sliding bit error rate (BER) correlation | Catches audio re-encoding, volume shifts, audio track reuse across visual modifications | ~0.10s / clip |

### Combiner Policies
A `Combiner` synthesizes individual detector results into a final `Verdict`:
- **`any` (default):** Declares `duplicate` if any detector meets its `flag_threshold`; `review` if meeting `review_threshold`; else `clear`.
- **`corroborate`:** Promotes a borderline visual `review` to `duplicate` when audio independently matches the same original clip.
- **`weighted`:** Calculates a weighted confidence score across enabled detectors against configurable thresholds.

### Safe Ingestion Guard
To guarantee the cryptographic and visual integrity of the approved reference database, ClipGuard strictly enforces auto-registration **only when `verdict == "clear"`**. Flagged duplicates and borderline review items are never accidentally ingested into `dedupe.db` under pending status.

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
# or launch directly via Python / Batch script:
python -m src.dedupe.gui
.\run_gui.bat

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
register_after_check = true       # Automatically index novel clips (strictly when verdict is clear)

[source]
type = "local"                    # local | s3
path = "./data/incoming"
# S3 settings:
# bucket = "my-incoming-bucket"
# prefix = "approved-clips/"
# region = "us-east-1"
# endpoint_url = ""               # Optional for MinIO / LocalStack

[store]
type = "sqlite"                   # sqlite | postgres
path = "./dedupe.db"

[detectors.exact_sha256]
enabled = true

[detectors.frame_phash]
enabled = true
fps = 1                           # 1 frame per second sampling (set 0.5 for 50% storage reduction)
radius = 3                        # Max hamming distance per chunk
num_chunks = 4                    # 64-bit hash split into four 16-bit chunks
offset_bin_s = 2                  # Temporal voting bin size in seconds
flag_threshold = 0.80             # >= 0.80 confidence -> duplicate
review_threshold = 0.40           # >= 0.40 confidence -> review queue
speed_detection = true            # Enable speed-invariant RANSAC temporal alignment
speed_min = 0.4                   # Minimum playback speed ratio (slow motion down to 0.4x)
speed_max = 3.0                   # Maximum playback speed ratio (fast motion up to 3.0x)

[detectors.audio_chromaprint]
enabled = false                   # Disabled by default; enable after acoustic evaluation
ber_threshold = 0.35              # Bit error rate threshold
flag_threshold = 0.80
review_threshold = 0.50
```

---

## 6. Known Limitations and Risks

1. **Heavy Crops & Extreme Zooms:**
   While variable playback speed modifications (0.4× to 3.0×) and horizontal mirroring are fully detected via Speed-Invariant RANSAC alignment, extreme zoom-ins (>35%) or tight spatial cropping can degrade pHash recall.
2. **Distinct Takes of the Same Task:**
   In robotics training data, different takes of the same physical task (e.g. cutting vegetables on the same cutting board) share high visual background similarity. ClipGuard suppresses false positives through temporal offset voting and junk-hash filtering, but threshold tuning on a validation split remains essential.
3. **Muted or Silent Audio:**
   The audio detector requires audible sound. In environments where robots or cameras record silent tracks, acoustic matching is skipped.
4. **ETag vs SHA-256:**
   Amazon S3 multipart ETags are not SHA-256 digests. ClipGuard streams object content directly to compute true SHA-256 digests on ingest.

---

## 7. Desktop Graphical Interface (GUI)

ClipGuard provides a full-featured desktop interface for visual review, bulk audits, and library management:

```
┌────────────────────────────────────────────────────────────────────────┐
│ [🔍 Check Single Video] [📂 Batch Audit] [📥 Index Library] [🗄️ Database]│
└────────────────────────────────────────────────────────────────────────┘
```

1. **🔍 Check Single Video (Tab 1):**
   - Individual video inspection for local files or direct Amazon S3 URIs (`s3://bucket/prefix/clip.mp4`).
   - High-contrast visual verdict badge (`CLEAR`, `DUPLICATE`, `ALREADY_INDEXED`, `REVIEW`).
   - Matched reference video identification with clickable link, inline OS media player launch (`▶ Play Matched Video`), and file explorer reveal (`📂 Reveal in File Explorer`).
   - Detailed forensic evidence log with layer breakdown, seller intent assessment, and operational guidance.
   - **Smart Report Modal:** Generates an executive forensic report with copy-to-clipboard and Markdown export.

2. **📂 Batch Folder & S3 Audit (Tab 2):**
   - Audit an entire local directory or Amazon S3 prefix against the approved reference database all at once.
   - **Executive Metrics Dashboard:** Real-time counters for `SCANNED`, `CLEAN / ORIGINALS`, `DUPLICATES`, `ALREADY INDEXED`, and `UNDER REVIEW`.
   - **Master-Detail File Inspector:** Color-coded table with quick filter chips (`All`, `Duplicates Only`, `Clean Only`, `Already Indexed`, `Review Only`) and search filter.
   - Click any scanned item to immediately inspect findings, launch the matched reference clip, open the folder, or jump directly into single check.
   - **Per-Item Smart Report:** Generate and export a forensic report modal for any individual clip in the batch.
   - **Batch Export:** One-click `Export Full Batch Report` (formatted summary) and `Export CSV` (tabular audit spreadsheet).

3. **📥 Index Library (Tab 3):**
   - Index approved reference footage from local directories or Amazon S3 buckets.
   - **Multi-Worker Concurrency (`Workers: 2 | 4 | 8 | 12 | 16`):** Multi-core parallel extraction pool automatically sized for your CPU cores.
   - **Amazon S3 Integration:** Built-in modal dialog for configuring credentials, region, custom endpoints (MinIO/LocalStack), and live connection testing.

4. **🗄️ Database & Library Inspector (Tab 4):**
   - Search and inspect all registered reference clips and pHash fingerprints.
   - Multi-select deletion with confirmation dialog and index wipe.
   - Live statistics refresh tracking total clips, exact SHA hashes, frame pHashes, and storage footprint.

---

## 8. High-Throughput Performance & Enterprise Scaling

Indexing high-resolution video footage is computationally intensive: a 7-minute 1080p 60 FPS video contains over **25,000 raw frames**. ClipGuard optimizes this workload via four key architectural enhancements:

1. **Multi-Core Worker Pool (`ThreadPoolExecutor`):** Fingerprint extraction is parallelized across all available CPU cores. On a 16-core system, 9 large videos (~5 GB) index concurrently in **~35 to 45 seconds total** instead of 4.5 minutes sequentially.
2. **Fast Bilinear Frame Downsampling (`:flags=fast_bilinear`):** FFmpeg hardware-friendly downsampling eliminates bicubic convolution overhead while preserving 100% perceptual hash accuracy.
3. **Single-Pass Hash Caching:** The SHA-256 digest computed during discovery is retained and reused across all downstream detectors, completely eliminating redundant disk reads of 500MB–700MB video files.
4. **Decoupled Producer-Consumer Storage:** CPU-bound video decoding runs in parallel worker threads, while SQLite registration commits in lightweight, non-blocking WAL mode without lock contention.

### Enterprise Throughput Projections (100,000 Videos, 7-Min Each)

| Architecture | Concurrency | Time for 100,000 Clips |
|---|---|---|
| Single-Threaded Sequential | 1 worker | ~34.7 Days |
| Multi-Core Workstation (16 cores) | 12 workers | ~2.8 Days |
| 4 Cloud Virtual Machines (16 vCPUs each) | 64 workers | ~13.0 Hours |
| Distributed AWS Batch / ECS Cluster | 128 workers | ~6.5 Hours |

---

## 9. Extending and Operations

- **Extending Guide:** See [docs/extending.md](docs/extending.md) for adding custom detectors, stores, and sources.
- **Operations Runbook:** See [docs/runbook.md](docs/runbook.md) for threshold tuning, resumable backfills, SQS worker management, concurrency tuning, and backups.
