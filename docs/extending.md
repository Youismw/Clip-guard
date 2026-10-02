# ClipGuard: Extending Guide

This guide explains how to extend ClipGuard with new detectors, storage backends, and video sources following the clean architecture principles defined in the implementation plan.

---

## 1. Design Principles for Extensions

1. **Detectors Never Know About Storage:** Detectors only interact with `Store` protocols and `ClipRef` domain models. Never import `sqlite3`, `psycopg2`, or `boto3` inside a detector.
2. **Pure Core, Impure Edges:** Algorithmic computations (hashing, hamming distance, voting, combining) must remain pure functions that are deterministic and easily unit-tested. I/O, subprocesses, and network calls are confined to adapters.
3. **Self-Registering Detectors:** Adding a new detector requires only creating a new module with `@register_detector("<name>")`, adding its config schema, and writing tests. No changes to `Pipeline` are permitted.
4. **Contract-Tested Stores and Sources:** All new `Store` and `ClipSource` implementations must pass the standard contract test suite in `tests/contract/`.

---

## 2. Adding a New Detector

ClipGuard detectors implement the `Detector` abstract base class located in `src/dedupe/detectors/base.py`:

```python
from dedupe.detectors.base import Detector
from dedupe.models import ClipRef, DetectorResult
from dedupe.registry import register_detector
from dedupe.stores.base import Store

@register_detector("embedding_clip")
class VideoEmbeddingDetector(Detector):
    name: str = "embedding_clip"
    version: str = "1.0.0"

    def __init__(self, model_name: str = "ViT-B/32", flag_threshold: float = 0.85):
        self.model_name = model_name
        self.flag_threshold = flag_threshold

    @property
    def params_hash(self) -> str:
        # Return deterministic hash representing detector parameters
        import hashlib
        return hashlib.sha256(f"{self.model_name}:{self.flag_threshold}".encode()).hexdigest()[:16]

    def index(self, clip: ClipRef, store: Store) -> None:
        """Extract features/embeddings and store in the respective index."""
        # 1. Obtain clip video file path via clip.local_path
        # 2. Extract features
        # 3. Save into store.custom_index or detector table
        pass

    def check(self, clip: ClipRef, store: Store) -> DetectorResult:
        """Compare clip against store index and return Match list with confidence."""
        # 1. Extract features from incoming clip
        # 2. Query store index for candidates
        # 3. Calculate similarity score
        # 4. Return DetectorResult(detector=self.name, version=self.version, matches=matches, confidence=score)
        pass
```

### Steps to Integrate:
1. **Create the file:** `src/dedupe/detectors/<detector_name>.py`.
2. **Register it:** Apply `@register_detector("<detector_name>")`.
3. **Import in package init:** Ensure `src/dedupe/detectors/__init__.py` imports the module so the decorator runs on startup.
4. **Add Config Section:** Add configuration dataclass in `src/dedupe/config.py` under `DetectorsConfig`.
5. **Add Unit Tests:** Create `tests/unit/test_<detector_name>.py` testing extraction, scoring, empty clips, and corrupt files.

---

## 3. Adding a New Storage Backend

ClipGuard storage backends implement the `Store` protocol and detector index protocols defined in `src/dedupe/stores/base.py`:
- `ClipRepo`: Storing and looking up clip metadata (`upsert_clip`, `get_clip`, `get_clip_by_hash`, `count_clips`).
- `ExactIndex`: Exact SHA-256 hash lookup (`get_by_sha256`).
- `FrameIndex`: Chunked frame pHash lookup (`lookup_chunk_candidates`, `insert_frame_hashes`).
- `AudioIndex`: Sub-fingerprint bit error rate lookup (`lookup_audio_candidates`, `insert_audio_hashes`).

```python
from dedupe.stores.base import Store, ClipRepo, ExactIndex, FrameIndex, AudioIndex
from dedupe.models import ClipRef, Match, Verdict

class MongoStore:
    def __init__(self, uri: str, db_name: str):
        ...

    def initialize(self) -> None:
        """Create collections, indexes, and constraints."""
        ...

    def upsert_clip(self, clip: ClipRef) -> None:
        ...

    def get_clip_by_hash(self, sha256_hash: str) -> Optional[ClipRef]:
        ...

    def record_verdict(self, verdict: Verdict) -> None:
        ...

    def close(self) -> None:
        ...
```

### Verification:
Add the new store fixture to `tests/contract/test_store_contract.py`. The contract test automatically validates:
- Clip metadata upsert & retrieval
- Exact SHA-256 lookups
- Frame chunk indexing and hamming candidate queries
- Audio fingerprint indexing and range queries
- Verdict persistence

---

## 4. Adding a New Video Source

ClipGuard sources implement the `ClipSource` abstract base class defined in `src/dedupe/sources/base.py`:

```python
from contextlib import contextmanager
from typing import Iterator
from pathlib import Path
from dedupe.models import ClipRef
from dedupe.sources.base import ClipSource

class GoogleCloudStorageSource(ClipSource):
    def __init__(self, bucket: str, prefix: str = "", status: str = "approved"):
        self.bucket = bucket
        self.prefix = prefix
        self.status = status

    def iter_clips(self) -> Iterator[ClipRef]:
        """Yield ClipRef metadata for all video objects in the source."""
        ...

    @contextmanager
    def materialize(self, clip: ClipRef) -> Iterator[Path]:
        """Download video to a secure temporary file and yield local path."""
        # 1. Download blob to temporary file
        # 2. yield Path(temp_file)
        # 3. Ensure temporary file is safely unlinked on exit
        ...
```

### Verification:
Add the source fixture to `tests/contract/test_source_contract.py` to ensure:
- `iter_clips()` streams video metadata without downloading full content.
- `materialize()` yields a valid, readable local path and safely deletes temporary files on context exit.
