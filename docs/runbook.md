# ClipGuard: Operations and Runbook

This runbook provides operational instructions for threshold tuning, index re-building, backfills, and AWS production worker deployment.

---

## 1. Threshold Tuning Runbook

### Purpose
To optimize the trade-off between recall on duplicate clips (catching vendor resales) and false-positive flags on distinct clips (different takes of the same robotics task).

### Standard Targets (Section 3 & 13)
- **Duplicate Recall:** >= 80% on real pairs; >= 95% on scripted edits (trim, re-encode, resize, watermark).
- **False Positive Flag Rate:** <= 1% on distinct clips.
- **Review Queue Rate:** <= 5% of distinct clips.

### Step-by-Step Procedure:
1. **Prepare Ground Truth Data:**
   - Place known duplicate pairs in `data/known_pairs.csv` (`original_path,returned_path`).
   - Place distinct clips and alternate takes in `data/distinct/`.
2. **Execute Local Pilot / Evaluation:**
   ```bash
   # Run full pilot protocol (50/50 tuning and validation split)
   dedupe pilot --pairs data/known_pairs.csv --distinct data/distinct/ --config dedupe.toml
   ```
3. **Inspect Output Reports:**
   - Review `pilot_results/pilot_report.md` and `pilot_results/pilot_report.json`.
   - Inspect the threshold sweep table to locate the optimal confidence threshold where false positive rate remains under 1.0%.
4. **Update Configuration:**
   Adjust thresholds in `dedupe.toml`:
   ```toml
   [detectors.frame_phash]
   flag_threshold = 0.80      # Confidence >= 0.80 -> definite duplicate
   review_threshold = 0.40    # Confidence >= 0.40 -> human review queue
   ```
   Or set environment variable overrides:
   ```bash
   export DEDUPE_DETECTORS__FRAME_PHASH__FLAG_THRESHOLD=0.82
   export DEDUPE_DETECTORS__FRAME_PHASH__REVIEW_THRESHOLD=0.45
   ```
5. **Verify Determinism:**
   Re-run `dedupe pilot` to confirm 100% verdict repeatability.

---

## 2. Backfill and Re-Indexing Runbook

### When is Re-Indexing Required?
- When altering detector parameters (e.g., changing pHash `fps`, chunk `radius`, or audio `ber_threshold`).
- Changing detector versions or adding a new detector.
*Note: Because exact SHA-256 and pHash frames are tagged with detector name and params hash, changing parameters requires updating only the modified detector's index.*

### Step-by-Step Backfill:
1. **Upfront Cost & Footage Estimation (Dry-Run):**
   ```bash
   dedupe backfill --source s3://approved-videos-bucket/footage/ --dry-run
   ```
   Output:
   ```text
   ============================================================
   ClipGuard Backfill Estimation (Dry Run)
   ============================================================
   Discovered Videos:    4,250
   Total Footage Volume: 142.60 GB
   Total Video Duration: 85.40 hours
   Estimated Run Time:   2.85 hours (at ~1,500 clips/hr)
   ============================================================
   ```
2. **Execute Resumable Backfill:**
   ```bash
   dedupe backfill --source s3://approved-videos-bucket/footage/ --checkpoint /var/run/backfill_checkpoint.json
   ```
3. **Handling Interruptions:**
   - If the backfill process terminates unexpectedly (e.g., node spot termination), simply restart the same command with the same `--checkpoint` file.
   - The backfill manager automatically skips previously indexed keys and resumes from where it left off.
   - All database writes are idempotent; duplicate entries will not be created.

---

## 3. Production Cloud SQS Worker Runbook

### Architecture
- S3 Bucket Event Notification -> SQS Queue -> `dedupe worker` -> Verdict Output (Postgres / S3 / Queue).
- S3 Bucket permissions are strictly **read-only**. The worker never writes to, moves, or deletes source videos.

### Deployment Commands:
```bash
# Continuous polling mode (production service / container)
dedupe worker --queue-url https://sqs.us-east-1.amazonaws.com/123456789012/clipguard-incoming-queue \
              --results-bucket clipguard-verdicts-prod

# Single batch execution (cron / scheduled batch processing)
dedupe worker --queue-url https://sqs.us-east-1.amazonaws.com/123456789012/clipguard-incoming-queue \
              --once
```

### Worker Operational Best Practices:
1. **SQS Visibility Timeout:** Set queue visibility timeout to at least 15 minutes (900s) to allow video download, SHA-256 streaming, and detector extraction.
2. **Dead-Letter Queue (DLQ):** Configure redrive policy to send messages to a DLQ after 3 failed attempts (corrupt or unreadable files).
3. **Database Connections:** For Postgres, configure connection pooling (PgBouncer or RDS Proxy) when scaling multiple worker nodes.
4. **Temporary Storage:** Workers require fast ephemeral disk (NVMe SSD or `/tmp`) with at least 50 GB free space for materializing video chunks during inspection.

---

## 4. Operational Diagnostics and Healthchecks

### System Healthcheck:
```bash
dedupe doctor --all
```
Verifies:
- Python version (>= 3.9)
- `ffmpeg` binary and codec support (H.264, H.265)
- `ffprobe` video probing availability
- `fpcalc` (Chromaprint) if audio detector is enabled
- Database and S3 connectivity (configured endpoints)

### Viewing Index Statistics:
```bash
dedupe stats
```
Displays:
- Count of clips per status (`approved`, `rejected`, `pending`)
- Count of exact SHA-256 entries
- Count of pHash frame index entries and distinct video clips
- Database file size and index fragmentation
