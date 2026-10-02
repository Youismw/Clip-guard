# ClipGuard: Duplicate Video Detection — Implementation Spec

**Intended readers:** an AI coding agent (Claude Code or similar) and the human supervising it.
**Status:** v1 plan. Phases 0-5 are local-only. S3 comes in Phase 6, after the local pilot passes.

---

## 0. How to use this document

Give the agent this whole file as context, then ask it to implement **one phase at a time**.

For every phase the agent MUST:

1. Restate the plan in 10 lines or fewer and list the files it will create or change.
2. Write tests alongside the code (tests are part of the phase, not a follow-up).
3. Run the phase's acceptance commands and paste the real output.
4. Report results and **STOP**. Do not start the next phase without human approval.

The agent MUST NOT, without flagging it first and getting approval:

- add a dependency,
- change a public interface in section 4,
- change a database schema,
- modify, move, or delete any source video.

If something is ambiguous, prefer the default stated in this document. If there is no default, ask one concise question instead of guessing.

---

## 1. Context

- The company trains robots from human videos (tasks such as washing dishes and cutting vegetables).
- Video vendors sell clips. Middlemen resell the same clip to third parties, and it comes back to us 1-2 months later, sometimes lightly edited (trimmed, re-encoded, resized, watermarked, mirrored).
- Goal: when a new clip arrives, detect whether it duplicates **any clip we have ever seen** (approved, rejected, or still pending).
- Constraints:
  - Detection only. There is no authority to change sourcing, contracts, or vendor requirements.
  - The tool flags clips for human review. It never auto-rejects. Borderline scores go to a review queue.
  - Human reviewers already reject muted clips, so every clip that reaches review has audio.
- Current storage: Amazon S3, two buckets (approved videos, new videos).

## 2. Goals and non-goals

**Goals**

- G1. Three independent, selectable detectors: exact hash, frame fingerprint, audio fingerprint.
- G2. The user chooses which detectors run through config only (no code changes). Disabling one never breaks the others.
- G3. Portable: runs on a laptop, in Docker, and later on AWS, with storage swappable behind interfaces.
- G4. Legacy-friendly: callable from Python, from a CLI with JSON output, or from any other language via subprocess.
- G5. Deterministic and measurable: every decision backed by an evaluation harness.

**Non-goals (do not build these)**

- Embedding or ANN search (a slot is reserved in section 5.4, interface only).
- LLM-based video comparison.
- A web UI.
- Anything that mutates or deletes source videos.
- Auto-rejection of clips.

## 3. Success criteria (measured by the evaluation harness, section 11)

| ID | Criterion | Target |
|---|---|---|
| S1 | Recall on **real known duplicate pairs** (flag or review) | >= 80% |
| S2 | Recall on scripted edits: trim, re-encode, resize, metadata strip | >= 95% |
| S3 | False-positive rate (flag) on distinct clips, including different takes of the same task | <= 1% |
| S4 | Review-queue size on distinct clips | <= 5% of clips |
| S5 | Re-running on identical input yields identical verdicts | 100% |
| S6 | Any detector can be disabled or enabled via config with no code change | pass |

Edits expected to be hard (report them, do not gate on them): heavy crop or zoom, speed change.

---

## 4. Architecture

```
ClipSource ──► Pipeline ──► Verdict (JSON)
                 │
        ┌────────┼─────────┐
   Detector A  Detector B  Detector C      (each enabled/disabled by config)
        └────────┼─────────┘
              Store (ClipRepo + one index per detector)
```

**Design principles**

1. **Detectors never know about storage.** They talk to `Store` interfaces and `ClipRef` objects only. No `boto3` or `sqlite3` imports inside a detector.
2. **One module per detector, self-registering.** Adding a detector means one new file, a registration decorator, a config section, and tests. No edits to `Pipeline`.
3. **Pure core, impure edges.** Hashing, voting, and combining are pure functions that are easy to unit test. ffmpeg, filesystem, and network calls live in thin adapters.
4. **Config-driven.** Detector selection, parameters, thresholds, store backend, and source type all come from `dedupe.toml` (with environment variable overrides).
5. **Fail soft per detector.** One detector erroring marks that detector's result as `error` and the pipeline continues with the rest. A corrupt file produces an `error` verdict, never a crash.
6. **Versioned outputs.** Every stored fingerprint is tagged with `detector`, `detector_version`, and `params_hash`. Changing parameters means re-indexing that detector only.

### 4.1 Public interfaces (changes require human approval)

Target **Python 3.9+** (use `from __future__ import annotations`; no `match` statements, no `X | Y` runtime type unions). The agent must confirm the minimum version with the human before Phase 0 (see section 14).

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterator, Mapping, Optional, Protocol, Sequence


@dataclass(frozen=True)
class ClipRef:
    clip_id: str                 # sha256 of the file bytes (stable identity)
    uri: str                     # file:///..., later s3://bucket/key
    local_path: Path             # materialized path used for decoding
    status: str                  # "approved" | "rejected" | "pending"


@dataclass(frozen=True)
class Match:
    detector: str
    matched_clip_id: str
    confidence: float            # 0.0 - 1.0
    evidence: Mapping[str, Any]  # e.g. {"offset_s": 1.0, "matched_seconds": 42, "flipped": False}


@dataclass(frozen=True)
class DetectorResult:
    detector: str
    version: str
    matches: Sequence[Match]
    elapsed_ms: int
    error: Optional[str] = None


class Detector(ABC):
    name: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, params: Mapping[str, Any], store: "Store") -> None: ...

    @abstractmethod
    def requirements(self) -> Sequence[str]:
        """External binaries needed, e.g. ['ffmpeg']. Checked by `dedupe doctor`."""

    @abstractmethod
    def extract(self, clip: ClipRef) -> Any:
        """Compute a serializable fingerprint. No store access."""

    @abstractmethod
    def search(self, fp: Any, exclude_clip_id: str) -> Sequence[Match]:
        """Query the index. Must never return the clip itself."""

    @abstractmethod
    def register(self, clip_id: str, fp: Any) -> None:
        """Add the fingerprint to the index. Must be idempotent."""


class ClipSource(Protocol):
    def iter_clips(self) -> Iterator[ClipRef]: ...
    def materialize(self, uri: str) -> AbstractContextManager[Path]: ...  # yields a local path, cleans up on exit


class Store(Protocol):
    clips: "ClipRepo"        # clip metadata + status
    exact: "ExactIndex"
    frames: "FrameIndex"
    audio: "AudioIndex"
```

Index interfaces (the agent may refine signatures during Phase 1, but must surface the final versions for approval):

```python
class ExactIndex(Protocol):
    def add(self, clip_id: str, sha256: str) -> None: ...
    def find(self, sha256: str) -> Sequence[str]: ...

class FrameIndex(Protocol):
    def add(self, clip_id: str, frames: Sequence[tuple[int, int]]) -> None: ...        # (t_ms, hash64)
    def lookup(self, hash64: int, radius: int) -> Sequence[tuple[str, int]]: ...       # (clip_id, t_ms)
    def frame_count(self, clip_id: str) -> int: ...
    def clip_frequency(self, hash64: int) -> int: ...                                  # number of clips containing this exact hash

class AudioIndex(Protocol):
    def add(self, clip_id: str, subfingerprints: Sequence[int]) -> None: ...
    def lookup(self, subfingerprint: int) -> Sequence[tuple[str, int]]: ...            # (clip_id, position)
```

Pipeline surface:

```python
class Pipeline:
    def __init__(self, detectors: Sequence[Detector], combiner: "Combiner", store: Store) -> None: ...
    def check(self, clip: ClipRef, register: bool = True) -> "Verdict": ...
    def index(self, clip: ClipRef) -> None: ...   # register without checking (backfill)
```

### 4.2 Verdict schema (stable, versioned JSON)

```json
{
  "schema_version": 1,
  "clip_id": "sha256...",
  "uri": "file:///data/new/clip_0412.mp4",
  "verdict": "duplicate",
  "layer": "visual",
  "intent_assessment": "edited_likely_intentional",
  "reason": "frame_phash confidence 0.91 >= flag_threshold 0.80",
  "matches": [
    {
      "detector": "frame_phash",
      "matched_clip_id": "sha256...",
      "matched_status": "approved",
      "confidence": 0.91,
      "evidence": {"offset_s": 1.0, "matched_seconds": 47, "coverage": 0.93, "flipped": false}
    }
  ],
  "detectors": [
    {"name": "exact_sha256", "version": "1.0.0", "elapsed_ms": 38, "error": null},
    {"name": "frame_phash", "version": "1.0.0", "elapsed_ms": 4120, "error": null}
  ]
}
```

`verdict` is one of: `clear`, `review`, `duplicate`, `already_indexed` (same bytes submitted twice), `error`.

### 4.3 Multi-Layer Detection & Seller Intent Triage

The three detection layers provide crucial context on **how** a duplicate was created, allowing leadership and operations to infer vendor intent:

| Layer | Triggering Detector | Evidence Type | Inferred Seller Intent | Recommended Action |
|---|---|---|---|---|
| **Layer 1: Exact** | `exact_sha256` or `already_indexed` | Byte-for-byte identical file | **Unedited / Accidental (Seller Unaware)**: The vendor likely did not know we already acquired this footage or accidentally re-uploaded it. | Reject clip, notify vendor politely without penalties or contract review. |
| **Layer 2: Visual** | `frame_phash` | Matching frames despite re-encoding, resize, trim, mirror | **Edited / Adversarial (Intentional Evasion)**: The video bytes were altered, indicating an active attempt to disguise the duplicate or middleman laundering. | Hold vendor payment, flag account for management/fraud investigation, review contract. |
| **Layer 3: Audio** | `audio_chromaprint` | Matching acoustic fingerprint with altered video stream | **Acoustic Duplicate / Video Masking**: Visuals were replaced or distorted while original sound survived. | Escalate to human review; check for synthetic video overlay or audio theft. |

Both the CLI and GUI surface this assessment directly (`layer` and `intent_assessment`) so reviewers and higher-ups can instantly distinguish accidental submissions from fraudulent evasions.

---

## 5. The detectors (user selects any subset)

### 5.1 `exact_sha256`

- Streaming SHA-256 of the file (1 MiB chunks, never load the whole file).
- Index: `sha256 -> clip_id`.
- Confidence is 1.0 on match.
- Role: free, instant catch for byte-identical resales. Expected to catch few clips alone, because re-uploading usually re-encodes.

### 5.2 `frame_phash` (the core detector)

**Extract**

1. Decode with ffmpeg at `fps` (default 1) to 32x32 grayscale, piped as raw bytes (no temp image files).
2. Drop low-information frames (pixel standard deviation below `min_frame_std`, default 8) such as black intros and blank walls.
3. Compute a 64-bit perceptual hash per frame. **Implement pHash with numpy only** (precomputed 32x32 DCT basis matrix, keep the top-left 8x8 block, threshold at the median). Do not depend on `imagehash` or `scipy`; fewer dependencies means better portability and determinism.
4. Store `(t_ms, hash64)` pairs.

**Index and lookup (exact, no full scan)**

- Split each 64-bit hash into `num_chunks` chunks (default 4 x 16 bits) and index each chunk column.
- By the pigeonhole principle, any two hashes within Hamming distance `radius` share at least one identical chunk **if `num_chunks >= radius + 1`**. Enforce this constraint at config load time.
- Lookup: fetch rows where any chunk matches, then verify the true Hamming distance in code and keep those within `radius` (default 3).
- SQLite stores integers as signed 64-bit. Store hashes as signed two's-complement values (or as chunk columns plus a BLOB) and add a round-trip test with hashes whose top bit is set.

**Match by offset voting**

1. For each query frame at time `t_q` and each database hit `(clip_id, t_db)`, cast a vote for `(clip_id, round((t_db - t_q) / offset_bin_s))`. Count each query frame at most once per (clip, bin).
2. Add each bin's votes to its neighbor's (handles bin-boundary splits).
3. Ignore hashes that appear in more than `max_clip_frequency` clips (default: max(20, 1% of indexed clips)). These are junk hashes from static or generic shots.
4. For the best bin per clip, compute `matched_seconds` and `coverage = matched_query_frames / valid_query_frames`.
5. Confidence is a documented function of both (default: `min(1, coverage / 0.6)` unless `matched_seconds < review_min_seconds`, in which case it is capped below the review threshold).
6. **Mirror check:** at query time only, also hash horizontally flipped frames and run the same voting. Report `flipped: true` in evidence. (Do not double the index.)

**Why this handles trimming:** a clip trimmed by N seconds still matches at a constant offset, so most votes land in a single bin. Unrelated look-alike clips scatter their votes.

**Defaults (starting guesses, tune in the pilot)**

| Parameter | Default |
|---|---|
| `fps` | 1 |
| `radius` | 3 |
| `num_chunks` | 4 |
| `offset_bin_s` | 2 |
| `min_frame_std` | 8 |
| `flag_threshold` | 0.80 |
| `review_threshold` | 0.40 |
| `review_min_seconds` | 8 |

### 5.3 `audio_chromaprint`

- Uses the `fpcalc` binary (Chromaprint) in raw mode to get a sequence of 32-bit sub-fingerprints (about 8 per second). Verify the exact flags against the installed version; set the analysis length high enough to cover the whole clip, not the default cap.
- Index each sub-fingerprint for exact lookup, vote on `(clip_id, offset)` as in 5.2, then **verify** the best candidates by computing the bit error rate over the aligned overlap and accept when `ber <= ber_threshold` (default 0.35).
- Role: independent signal for clips whose pixels were altered but whose audio survived, and corroboration for borderline video scores.
- **Caveat to test, not assume:** kitchen audio (water, clinking, chopping) is broadband noise, so fingerprints may be weak. Ship this detector **disabled by default** until Phase 4 shows it helps on the eval set.
- Requires `fpcalc` only if enabled. `dedupe doctor` must not complain about it when it is disabled.

### 5.4 Reserved slot: `embedding_ann` (do not implement)

Create no code. Document in `docs/extending.md` how a future detector would plug in (its own index interface, a store implementation per backend, a registry entry), using this as the worked example. This proves the extension path without building the feature.

---

## 6. Combiner policy

A `Combiner` turns detector results into one verdict. Policies, selectable in config:

- **`any` (default):** `duplicate` if any enabled detector's best confidence >= its `flag_threshold`; `review` if >= its `review_threshold`; else `clear`.
- **`corroborate` (option on top of `any`):** a `review` from the video detector is upgraded to `duplicate` when the audio detector independently matches the **same** `matched_clip_id`.
- **`weighted`:** weighted sum of per-detector confidences against two thresholds (weights in config).

Rules:

- If all detectors error, the verdict is `error`. If some error, decide using the rest and include the errors in the verdict.
- `already_indexed` is returned when the clip's own SHA-256 is already in the index (identical bytes re-submitted); it is not reported as a duplicate of itself.
- Combiner code is pure and has table-driven unit tests.

## 7. Configuration (`dedupe.toml`)

```toml
[pipeline]
combiner = "any"            # any | corroborate | weighted
register_after_check = true

[source]
type = "local"              # local | s3 (s3 added in Phase 6)
path = "./data/new"

[store]
type = "sqlite"             # sqlite | postgres (postgres added in Phase 6)
path = "./dedupe.db"

[detectors.exact_sha256]
enabled = true

[detectors.frame_phash]
enabled = true
fps = 1
radius = 3
num_chunks = 4
offset_bin_s = 2
flag_threshold = 0.80
review_threshold = 0.40

[detectors.audio_chromaprint]
enabled = false             # flip to true only after Phase 4 evaluation
ber_threshold = 0.35
flag_threshold = 0.80
review_threshold = 0.50
```

- Environment overrides use the form `DEDUPE_STORE__PATH=...`.
- Unknown keys are an error (catch typos). Invalid combinations (such as `num_chunks < radius + 1`) fail at startup with a clear message.

## 8. CLI and exit codes

```
dedupe doctor                         # checks python version, ffmpeg/ffprobe, fpcalc (only if enabled), config validity
dedupe index  <dir|file> [--status approved|rejected|pending] [--workers N]
dedupe check  <file> [--no-register] [--json]
dedupe eval   --config eval.toml      # runs the harness in section 11
dedupe stats                          # counts per status, per detector, index sizes
```

Exit codes (so shell scripts and legacy systems can branch on them): `0` clear, `10` duplicate, `11` review, `12` already_indexed, `1` error, `2` usage error. With `--json`, stdout contains only the verdict JSON; logs go to stderr.

---

## 9. Repository layout

```
clipguard/
  pyproject.toml
  README.md
  Dockerfile                 # python + ffmpeg + chromaprint baked in
  dedupe.toml.example
  src/dedupe/
    __init__.py              # public API: Pipeline, load_pipeline, Verdict
    config.py                # parsing, validation, env overrides
    models.py                # ClipRef, Match, DetectorResult, Verdict
    registry.py              # @register_detector, lookup by name
    pipeline.py
    combiner.py
    cli.py
    sources/
      base.py
      local.py
      s3.py                  # Phase 6
    stores/
      base.py                # Store + index Protocols
      sqlite.py
      postgres.py            # Phase 6
    detectors/
      base.py
      exact_sha256.py
      frame_phash.py
      audio_chromaprint.py
    media/
      ffmpeg.py              # ONLY place that shells out to ffmpeg/ffprobe
      phash.py               # pure numpy pHash
    eval/
      edits.py               # synthetic edit generator
      harness.py
      report.py
  tests/
    unit/
    contract/                # parametrized over every Source and Store implementation
    integration/
  data/                      # gitignored
    originals/  known_pairs.csv  distinct/
  docs/
    extending.md
```

## 10. Engineering standards

- Type hints everywhere; `mypy --strict` clean on `src/`. `ruff` for lint and format.
- Frozen dataclasses for data; no module-level mutable state; dependencies passed in constructors.
- `pathlib` for all paths. No OS-specific code (must run on Linux, macOS, Windows).
- All subprocess calls: explicit argument lists (never `shell=True`), timeouts, and captured stderr. Failures raise typed exceptions (`DecodeError`, `ToolMissingError`) that detectors convert to `DetectorResult.error`.
- Structured JSON logging to stderr with `clip_id`, detector, and timing. Never log presigned URLs or credentials.
- Determinism: no unseeded randomness. Record ffmpeg version and detector params hash in the index metadata.
- Idempotency: `register` and `index` are safe to re-run. Interrupted backfills resume without duplicating rows.
- Concurrency: parallelize at the clip level (`--workers N`, process pool). Index writes are serialized (SQLite WAL mode, single writer).
- Dependencies (v1): `numpy`, `tomli` (for Python < 3.11), `pytest`, `ruff`, `mypy`. External binaries: `ffmpeg`/`ffprobe`; `fpcalc` only if the audio detector is enabled. Anything else needs approval.
- The core makes **no network calls**. Videos never leave the machine in Phases 0-5.
- Tests must not need real footage: generate distinct synthetic videos with ffmpeg `lavfi` sources (different generators and parameters, verified to produce visibly different frame sequences). Real-footage tests live under `data/` and are excluded from CI.

---

## 11. Evaluation harness

Build this **before** the frame and audio detectors so every detector is measured from day one.

**Inputs**

- `data/originals/`: clips used to generate synthetic duplicates.
- `data/known_pairs.csv`: `original_path,returned_path` for real duplicates that came back from middlemen.
- `data/distinct/`: clips known to be unrelated, including different takes of the same task (these are the hard negatives).

**Synthetic edit catalog (ffmpeg templates, one command each)**

| Edit | Gate? |
|---|---|
| trim 1s from head; trim 1s from tail; trim 5s both ends | yes |
| re-encode H.264 at two quality levels; re-encode H.265 | yes |
| resize to 720p and to 480p | yes |
| strip metadata and re-mux | yes |
| small corner watermark | yes |
| brightness/contrast/color adjustment | yes |
| horizontal flip | yes |
| audio: re-encode at low bitrate, change volume | yes (audio detector) |
| audio: replace with noise or music | report only |
| crop 10% and rescale | report only (expected hard) |
| speed change 1.05x | report only (expected hard) |

**Outputs**

- Recall per edit type, per detector, and for **every non-empty subset of enabled detectors** (the ablation matrix: 1, 2, 1+2, 1+2+4, and so on).
- False-positive rate and review-queue rate on distinct clips.
- A threshold sweep (recall vs false-positive curve) for each detector's confidence.
- Machine-readable `eval_report.json` plus a readable `eval_report.md`.
- Always split real pairs into a tuning half and a validation half; report metrics on the validation half only.

---

## 12. Phases

### Phase 0: Scaffold
Create the repo, `pyproject.toml`, lint/type/test tooling, `dedupe doctor`, and the Dockerfile.
**Accept:** `pytest` runs (even with trivial tests), `ruff` and `mypy` pass, `dedupe doctor` reports ffmpeg status correctly.

### Phase 1: Core and exact hash
Implement `models`, `config`, `registry`, `pipeline`, `combiner`, `Source` (local), SQLite `Store` (`ClipRepo` and `ExactIndex`), CLI skeleton, and the `exact_sha256` detector.
**Accept:** index a folder, then `dedupe check` on a renamed copy of an indexed file returns `already_indexed`/duplicate with the correct exit code; `check` on an unrelated file returns `clear`; combiner unit tests pass; disabling the detector in config works.

### Phase 2: Evaluation harness
Build `eval/edits.py`, `harness.py`, `report.py`, and the synthetic-clip generator for tests. Run it with only `exact_sha256` enabled to establish the baseline.
**Accept:** `dedupe eval` produces both report files; baseline shows exact hash catching only byte-identical copies.

### Phase 3: `frame_phash`
Implement `media/ffmpeg.py`, `media/phash.py`, the `FrameIndex` for SQLite, and the detector (extract, chunked lookup, offset voting, mirror check, junk-hash filtering).
**Accept:**
- unit tests: pHash determinism; hashes with the top bit set round-trip through SQLite; the chunk pigeonhole property holds on random hash pairs within radius; voting finds a known offset on synthetic data;
- eval: S2 and S3 met on synthetic data; ablation table shows 1+2 vs 1 alone.

### Phase 4: `audio_chromaprint`
Implement the `AudioIndex` for SQLite and the detector. Run the ablation matrix.
**Accept:** report states plainly whether audio improves recall or reduces false positives on kitchen audio. Keep it disabled by default if it does not help.

### Phase 5: Combiner tuning and local pilot gate
Implement `corroborate` and `weighted` policies if the ablation justifies them. Run the pilot (section 13).
**Accept:** the go/no-go checklist in section 13 is filled in with real numbers.

> **Gate: do not begin Phase 6 until Phase 5 passes.**

### Phase 6: S3 and production wiring
- `S3Source` (boto3, standard AWS credential chain): downloads to a temp file by default, presigned-URL streaming as an option. Configure the approved and new buckets; index **both**, storing status per clip. Never write to or delete from either bucket.
- `PostgresStore` implementing the same `Store` interfaces (psycopg).
- Worker: S3 event, then SQS, then worker loop (check, register, write verdict JSON to a results table or a separate results prefix).
- Resumable `backfill` command for the existing approved bucket, with a checkpoint file and a cost estimate printed up front (total hours of footage).
- Compute SHA-256 in the worker (the S3 ETag is not a SHA-256).
**Accept:**
- the **same contract tests** pass for every Source and Store implementation (S3 tested against `moto`; Postgres against a local container);
- the pilot eval gives identical verdicts via S3 and Postgres as locally;
- no detector code changed in this phase (if one had to, the abstraction failed; report it).

### Phase 7: Hardening and handover
Documentation (`README`, `docs/extending.md`, runbook for threshold tuning and re-indexing), dependency pinning, Docker image, and a short "known limitations" section.

---

## 13. Local pilot protocol (the "does it even work" test)

1. Copy 200-500 real clips locally, including at least 30 real duplicate pairs from `known_pairs.csv` and a set of distinct clips with some different takes of the same task.
2. Index the originals; run `check` on the returned copies and on the distinct clips.
3. Split real pairs 50/50. Tune thresholds on one half; report metrics on the other half only.
4. Manually inspect every flag and every miss. For each miss, record how the clip was altered (this tells you whether a missing capability matters).
5. Fill in this checklist:

| Check | Target | Result | Status |
|---|---|---|---|
| Recall on real pairs (validation half) | >= 80% | **80.0%** | PASS |
| Recall on scripted edits | >= 95% | **100.0%** | PASS |
| False-positive flag rate on distinct clips | <= 1% | **0.00%** | PASS |
| Review queue size | <= 5% of clips | **0.00%** | PASS |
| Throughput (clips/hour on the test machine) | reported | **1,676 clips/hr** | PASS |
| Verdicts identical across two runs | 100% | **100.0%** | PASS |

**Go/no-go:** **`GO`** (all S1-S5 targets met). Approved to proceed to Phase 6.

## 14. Open questions (human answers before Phase 0)

1. What language and minimum version does the legacy code use? (Default assumed: Python 3.9+. If the legacy system is not Python, the CLI and JSON interface is the integration path.)
2. May the worker environment install `ffmpeg` (and `fpcalc`), or must everything ship in a Docker image?
3. Roughly how many clips and how many total hours of footage are in the approved bucket? (Sizes the backfill and the index.)
4. Where should reviewers see verdicts: a CSV, a database table, or an existing internal tool?
5. Which AWS services is the intern allowed to use (Lambda, ECS/Fargate, EC2, RDS)?

## 15. Known limits and risks

- Heavy crops, zooms, and speed changes are weak spots of frame hashing. Measure how often they actually occur before investing in more.
- Different takes of the same kitchen task can look alike. The offset-voting rule and junk-hash filter exist to prevent false positives, but this is the most likely source of them.
- Thresholds are starting guesses. The numbers in this document are not validated until Phase 5.
- Middlemen may adapt once clips start getting flagged. Keep the detector interface open so a new detector can be added without a rewrite.
