# ClipGuard Expert Panel: Questions & Answers

Companion to `clipguard-implementation-plan.md`. This document records 168 cross-domain questions (7 experts × 4 questions × 6 recipients), the answers, and a final section that pulls out agreements, disagreements, gaps, and plan amendments.

## How to read this document

**The seven roles** (abbreviations are used in question IDs):

| Code | Role |
|---|---|
| **CV** | Computer Vision / Video Copy-Detection Engineer |
| **ML** | Applied ML / Evaluation Scientist |
| **MP** | Video / Media Pipeline Engineer |
| **BE** | Backend / Database Engineer |
| **CD** | Cloud / DevOps Engineer (AWS) |
| **QA** | QA / Test Automation Engineer |
| **FR** | Fraud / Marketplace-Integrity Analyst |

**IDs:** `ML→CV·2` means "the second question the ML Scientist asked the CV Engineer." Answers are grouped by who is answering (Parts 1-7), and within each part by who asked.

**Important caveats**

- These are **simulated roles**: one author answering as seven specialists. The answers reflect general engineering knowledge and reasoning, not verification by real specialists. Where a real person in your company can confirm or overrule an answer, they should.
- Numbers described as "rough" or "estimate" (decode speed, costs, row counts, rates of reseller behavior) are **starting guesses to benchmark**, not facts.
- **⚠** marks answers that depend on facts about your company that only your team can supply (data volumes, base rates, payment cycles, contracts).

## Contents

1. Part 1: Computer Vision Engineer answers
2. Part 2: ML / Evaluation Scientist answers
3. Part 3: Media Pipeline Engineer answers
4. Part 4: Backend / Database Engineer answers
5. Part 5: Cloud / DevOps Engineer answers
6. Part 6: QA / Test Automation Engineer answers
7. Part 7: Fraud / Marketplace-Integrity Analyst answers
8. Part 8: Conclusions (agreements, disagreements, gaps, plan amendments, next steps)

---

# Part 1: Computer Vision Engineer answers

## Questions from the ML Scientist

**ML→CV·1 Q:** Which public video copy-detection benchmarks could give me a baseline for a pHash approach?
**A:** The usual references are the Meta AI Video Similarity Challenge (VSC22) data, TRECVID's content-based copy detection task, and the older VCDB, CC_WEB_VIDEO and FIVR sets (check current availability and licenses). None is kitchen footage, so use them only to sanity-check the matching logic. Your own real duplicate pairs are the benchmark that matters.

**ML→CV·2 Q:** Which perceptual-hash failure modes would a naive eval set miss?
**A:** Low-texture, fixed-camera scenes where only hands move (different clips hash almost identically, which causes false positives), large overlays or watermarks, auto-exposure swings, heavy compression blur, and letterbox borders. Include same-kitchen, same-camera negatives and edits that cover a large part of the frame, or the eval will look better than reality.

**ML→CV·3 Q:** How should I construct hard negatives so they actually stress the detector?
**A:** Mine them: query your own library, collect clip pairs with the highest frame-hash similarity, and have a human confirm they are genuinely different footage (some will turn out to be real duplicates). Prefer same vendor, same session, same task. Add random negatives separately to measure the base false-positive rate.

**ML→CV·4 Q:** When recall plateaus, how do I tell a feature problem from a matching problem?
**A:** Look at known duplicate pairs frame by frame. If corresponding frames are far apart in Hamming distance (overlapping the distribution of random frame pairs), the feature is too fragile. If many frames match individually but there's no consistent offset or coverage is low, the voting rules or thresholds are the problem.

## Questions from the Media Pipeline Engineer

**MP→CV·1 Q:** Fixed fps or scene-change keyframes, and what are the tradeoffs?
**A:** Fixed, time-based sampling. It's reproducible across re-encodes, whereas scene-change and encoder keyframes move with compression noise and edits. My preference would be 2 fps in the index for extra margin, but index size scales linearly with fps, so I'd accept 1 fps if the query side oversamples (see MP's answer to CV→MP·3). Scene-change anchors can be revisited later.

**MP→CV·2 Q:** What resolution and color handling must I preserve for your features?
**A:** Very little. Features come from a 32×32 grayscale image after area-averaged downscaling, and median thresholding makes the hash robust to global brightness changes. What matters is consistency: the same scaler, the same handling of limited versus full color range, and no early low-resolution decode shortcuts.

**MP→CV·3 Q:** Does matching tolerate dropped or duplicated frames from variable-frame-rate conversion?
**A:** Mostly yes. Sampling by timestamp absorbs small timing jitter, and 2-second offset bins tolerate drift of a fraction of a second. Large speed changes break the constant offset, so those remain a known weak spot. There's no need to normalize to constant frame rate first, but record whether the source was variable-rate.

**MP→CV·4 Q:** Would cheaper features from the compressed stream (keyframes, motion vectors) be good enough?
**A:** I'd avoid them. Keyframe positions and motion vectors are encoder decisions, so a re-encoded copy has different ones, and the features don't survive the very edit we're trying to catch. Decoding to pixels is the price of robustness.

## Questions from the Backend Engineer

**BE→CV·1 Q:** How many frame hashes per minute should I budget for, and can keyframe-only sampling or winnowing shrink the index?
**A:** 60 per minute at 1 fps, so about 300 rows for a 5-minute clip. Keyframe-only is out for the reason above, and classic winnowing needs exact hash equality, which perceptual hashes don't provide. What does work is collapsing runs of near-identical consecutive hashes (static shots), which trims a meaningful share of rows at no recall cost.

**BE→CV·2 Q:** Is exact Hamming-radius search required, or is approximate search acceptable?
**A:** Per-frame exactness isn't essential, because a clip-level match needs only a modest number of consistent frames; even losing a third of per-frame matches still leaves a clear offset peak. I'd still stay exact for as long as possible, since it's simpler to reason about and test, and move to approximate only when latency forces it, re-measuring clip-level recall when I do.

**BE→CV·3 Q:** How many candidate hits per frame should I expect?
**A:** If hashes were uniform, about N/65,536 rows per 16-bit chunk lookup (times 4 chunks). Real hashes cluster, so expect several times that, and far more for dark or static frames. Measure the histogram on real data, cap candidates per lookup (say 500-1000), and log every truncation so you can see whether the cap hurts.

**BE→CV·4 Q:** Are 64-bit hashes enough at ten million or more clips?
**A:** Yes for now. For uniformly random hashes, the chance that two unrelated frames fall within radius 3 is 43,745 / 2^64, about 2.4e-15. Natural images cluster, so real rates are much higher, but a clip-level false match needs many frames agreeing on one offset. Move to 128-bit only if measured frame-level false matches become a problem.

## Questions from the Cloud / DevOps Engineer

**CD→CV·1 Q:** What's the compute cost per minute of video, and where would extra compute buy recall?
**A:** Cost is almost entirely decode; hashing 32×32 frames is negligible. Extra compute buys recall cheaply on the query side: a higher query fps and flip/rotation variants cost only hashing and lookups, not extra decode. Heavier features (embeddings) would be the next step up, and we've deferred them.

**CD→CV·2 Q:** Does any stage benefit from a GPU?
**A:** No. The pipeline is decode-bound at tiny output sizes, and GPU decode adds transfer overhead and bit-level differences from CPU decode. A GPU only becomes relevant if an embedding model is added later.

**CD→CV·3 Q:** Which components must be deterministic across deployments?
**A:** Frame extraction (ffmpeg build, flags, scaler), the downscale, and the hashing code. Matching can stay tolerant. Pin an exact ffmpeg build in the container image, record versions in index metadata, and keep golden-frame tests so a silent change is caught.

**CD→CV·4 Q:** How should we tolerate duplicates arriving during a long backfill?
**A:** Treat the index as append-only and stamp each verdict with the index high-water mark. After the backfill finishes, re-check every clip that was checked during it. For twins arriving in the same batch, run a short "late match" pass after registration so the second arrival still finds the first.

## Questions from the QA Engineer

**QA→CV·1 Q:** Which invariants of the algorithm can I assert in tests?
**A:** Identical file → match; trim of N seconds → match at offset ≈ N; moderate re-encode → match; symmetry (if A matches B, B matches A with negated offset); flip → match flagged as flipped; unrelated synthetic clips → no flag; and the same input always gives the same output.

**QA→CV·2 Q:** Which edge cases break perceptual hashing?
**A:** All-black or all-white frames, static single-image videos, very short clips (below the minimum matched seconds), slideshows, footage dominated by burned-in text, very dark footage, and low-texture scenes. Each deserves a fixture and an expected verdict.

**QA→CV·3 Q:** What outputs would signal a regression in the matching logic?
**A:** A drop in median coverage or matched seconds on the golden duplicate set, a widening gap between true and measured offsets, a rise in candidates per lookup, and a shift in the Hamming-distance distribution of true frame pairs. Any of these can appear before recall visibly falls.

**QA→CV·4 Q:** How far may hashes drift across library versions before a test should fail?
**A:** Within a pinned image I expect zero drift. Across ffmpeg or numpy upgrades, I'd tolerate a mean of at most 1 bit and a maximum of 3 bits on golden frames: the matching radius is 3, and larger drift eats the safety margin. I don't need bit-exact equality across versions, because matching is designed to tolerate small differences. Beyond that threshold, call it a new detector version and re-index.

## Questions from the Fraud Analyst

**FR→CV·1 Q:** If a reseller applies a random mix of edits, where does detection fail, and what does that cost them?
**A:** Rough estimates, to be measured: frame hashing holds through re-encoding, resizing, mild color changes, small watermarks, and trimming. It degrades past roughly 15-20% crop or zoom and fails with speed changes beyond a few percent, because the offset drifts. Each of those also visibly degrades the footage, which matters if buyers need clean clips.

**FR→CV·2 Q:** Can the detector report which edits were applied, so I learn what resellers actually do?
**A:** Partly. It can report estimated trim offset, flipped or not, duration ratio, and mean Hamming distance (a proxy for how heavily the visuals changed). It can't name the tool. Store those fields in each verdict; aggregated per vendor, they show how resellers are evolving.

**FR→CV·3 Q:** Can we detect partial reuse (sub-segments or stitched clips)?
**A:** Yes. Voting works per clip pair and matched seconds is an absolute number, so a shared 30-second segment of a long clip shows up as a clear bin with low coverage. Decide on absolute matched seconds (at least 10-15 s) plus two-way coverage, and return the top few bins per match so stitched clips show several sources.

**FR→CV·4 Q:** If the footage is re-filmed from a screen, how much detection power remains?
**A:** Little from frame hashes: moiré, perspective warp, and brightness shifts push corresponding frames well beyond radius 3. Audio might survive, but kitchen sound is noise-like, so I wouldn't count on audio fingerprints to rescue it. Catching this needs embeddings or local-feature matching, which is why it's the trigger for the deferred detector, not a v1 requirement.

---

# Part 2: ML / Evaluation Scientist answers

## Questions from the CV Engineer

**CV→ML·1 Q:** pHash voting gives raw counts (matched seconds, coverage), not probabilities. How do I turn them into a confidence score you'd trust, and how would you test its calibration?
**A:** Don't call it a probability yet. Build a labeled set of (query, candidate) pairs, true duplicates versus hard negatives, and fit a simple calibrator (logistic or isotonic) on matched seconds, coverage, and mean Hamming distance. Check a reliability diagram and Brier score on held-out data, and recalibrate when the index grows, because false matches scale with size.

**CV→ML·2 Q:** How many hard-negative pairs do I need before my false-positive estimate has a usable confidence interval?
**A:** Count queries, not pairs. Zero false positives in n independent queries bounds the rate at about 3/n with 95% confidence, so roughly 300 queries support "≤1%" and 3,000 support "≤0.1%". If errors occur, use Clopper-Pearson intervals. Split by vendor and session, since clips from the same session aren't independent and shrink your effective n.

**CV→ML·3 Q:** With only 30-50 real pairs, what is the minimum honest tuning protocol?
**A:** Freeze half the real pairs as an untouched validation set (split by vendor or session), tune no more than about three parameters on synthetic edits plus the other half, and report exact binomial intervals. For example, 16 of 20 caught is 80%, but the 95% interval is roughly 56-94%. Say plainly that you can't tell 80% from 90% at that size.

**CV→ML·4 Q:** A missed duplicate pollutes training data; a false flag costs reviewer time. Which single metric should drive threshold choice?
**A:** Recall at a fixed review budget. Set the review threshold so the queue matches what reviewers can handle (about 5% of incoming clips is my default), then maximize recall inside that budget. Set the flag threshold separately for a precision target (about 99% of auto-flags truly duplicate). A single global accuracy number hides this tradeoff.

## Questions from the Media Pipeline Engineer

**MP→ML·1 Q:** Which transcoding settings do you want represented in the test set so the eval reflects real re-encodes?
**A:** Derive them from reality: run ffprobe on the real original/returned pairs and see what changed (codec, bitrate ratio, resolution, fps, duration). Then cover H.264 and H.265 at two or three bitrate tiers (including messaging-app-level compression), resolution caps at 1080/720/480, fps changes, container swaps, and stripped metadata, weighted by how often each shows up in the real pairs.

**MP→ML·2 Q:** How many clips per codec, resolution, and fps bucket before per-bucket metrics mean anything?
**A:** For recall to within about ±6 points you need roughly 100 queries per bucket; ±10 points needs about 35-50. Use a few coarse buckets (codec family × resolution tier) and treat anything smaller as anecdote. Synthetic edits make counts cheap: apply many edits to each original.

**MP→ML·3 Q:** Should I produce aligned ground-truth timestamps for trims so you can score offset accuracy?
**A:** Yes. Have the edit generator write a manifest per variant: original id, trim head and tail, speed factor, flip, crop. Then score offset error as its own metric. It proves the matcher found the right segment, not just that it raised a flag.

**MP→ML·4 Q:** Do you want clips with unusual properties flagged for separate reporting?
**A:** Yes: variable frame rate, rotation tags, HDR or 10-bit, very short clips, and clips without audio each get their own row with raw counts and intervals. A headline average can hide a failing stratum; I'd rather see "0 of 6 caught" explicitly.

## Questions from the Backend Engineer

**BE→ML·1 Q:** Which per-query data should I persist for offline tuning, and for how long?
**A:** Per query: clip id, config and index-generation versions, the top-k matches with offset, matched seconds, coverage, mean Hamming and flipped flag, candidate counts, and the verdict. Skip raw per-frame hits except for a 1% sampled debug set. That's about a kilobyte per query; keep it 12 months or more, and the raw sample 30-90 days.

**BE→ML·2 Q:** What read/write patterns should I expect from eval runs?
**A:** Bursty, read-heavy lookups against a frozen index, plus bulk inserts when building the eval index. Run evals against a separate schema or snapshot so they never write into production, and rate-limit them: they can generate 10-100× normal lookup load.

**BE→ML·3 Q:** How will you need to join verdicts, reviewer labels, and features, and which keys should I design around?
**A:** Key everything on a verdict id (query clip × matched clip × config version). Tables: verdicts (features, thresholds, versions), reviews (decision, reviewer, time, confidence), and clips (vendor, session, properties). I'll join verdicts to reviews, and clips to vendors for vendor-split evaluation. Include the index generation so results are reproducible.

**BE→ML·4 Q:** What retention rules should apply to reviewer labels?
**A:** Keep them indefinitely and append-only: corrections are new rows, never overwrites. They are the only ground truth about production behavior and contain no video content. Pseudonymize reviewer identity, and follow company policy for vendor identifiers.

## Questions from the Cloud / DevOps Engineer

**CD→ML·1 Q:** What compute and storage do eval runs need, so I can budget them separately?
**A:** Modest. Say 200 originals × 15 edits × 5 minutes is about 250 hours of video; at roughly 10× realtime per core that's on the order of 25 core-hours, a few dollars on spot capacity. Since the edit generator is deterministic, delete edited files after fingerprinting and keep only the manifest, then cache fingerprints so threshold sweeps need no re-decode.

**CD→ML·2 Q:** Which production metrics should trigger alerts, and at what thresholds?
**A:** Flag rate, review rate, best-match score distribution, decode error rate, queue age, reviewer overturn rate (a proxy for false flags), and canary results. Page on canary failure; for the rest, open a ticket on a shift of roughly 30% or more versus the trailing baseline, and tune that after the first month.

**CD→ML·3 Q:** How often do you expect to retune and re-index?
**A:** Review thresholds monthly for the first quarter, then quarterly or on a trigger (index grew 5-10×, codec mix changed, canary dipped). Re-index only when extraction parameters change, which I'd aim to keep to once or twice a year, done blue/green.

**CD→ML·4 Q:** What would a safe rollout of new thresholds look like?
**A:** Thresholds are config over logged features, so replay them offline first. Then shadow for one to two weeks (verdicts written but not acted on), then apply to a slice or to the review band only, with one-click rollback by config version. Never change detector version and thresholds in the same release.

## Questions from the QA Engineer

**QA→ML·1 Q:** What statistical tolerance should regression tests use, and which metrics should gate?
**A:** Fixtures with deterministic expected outputs get exact equality. For metric gates use binomial intervals: fail only if the new value falls outside the baseline's interval, plus an absolute floor (for example synthetic recall ≥95%). Two stances I'd hold firmly: any threshold or detector change should be **blocked** unless it holds on the real held-out pairs, not just synthetic results, because synthetic edits are what the detector was tuned against. And the audio detector stays off by default until it adds measurable recall on real kitchen audio at no false-positive cost.

**QA→ML·2 Q:** How do I design test data so tuned examples can't leak into the test set?
**A:** Split by source: an original and all its edits go on the same side, and the same goes for a vendor or session. Record each file's split in a manifest, mount only the dev split in tuning jobs, and log whenever the golden set is evaluated.

**QA→ML·3 Q:** What's the minimum sample size per edit category for a trustworthy pass/fail?
**A:** With 60 clips and zero misses, the 95% lower bound on recall is about 95% (rule of three), so at least 60 per gated category supports a 95% claim. For a margin of about ±6 points at ~90% recall, use at least 100. Synthetic data makes this easy.

**QA→ML·4 Q:** How should I report results so a non-technical reviewer can decide whether to ship?
**A:** One page in plain numbers: "Of 100 resold clips we catch about X (range a-b); of 100 clean new clips we wrongly flag about Y; reviewers see about Z extra clips per 100." Add a traffic-light table by edit type, limitations in plain words, a trend line, and the decision requested. Skip ROC jargon.

## Questions from the Fraud Analyst

**FR→ML·1 Q:** How do I measure resilience to adaptive adversaries, not just a fixed edit catalog?
**A:** Run it as a red-team game: fix an adversary budget (say five minutes of scripted edits with a defined toolset), let someone attack held-out clips seeing only the verdict, and measure both success rate and quality lost. Also sweep edit strength (crop 0-30%, speed 0-15%) to find break points, and repeat whenever the detector changes.

**FR→ML·2 Q:** How would you estimate the unseen miss rate (duplicates nobody caught)?
**A:** Randomly audit a slice of "clear" verdicts using a more sensitive offline check (wider radius, rotation and crop variants) and label what it finds. As a rough second estimate, capture-recapture across two partly independent detectors (frame and audio, if audio proves useful) can bound total duplicates; the independence assumption is shaky, so treat it as a sanity check, not truth.

**FR→ML·3 Q:** How can reviewer decisions feed back as labels without biasing thresholds toward what they already catch?
**A:** Reviewers only see what the system surfaced, so labels over-represent what it already catches. Add a small random audit sample (1-2% of all clips, including low-score matches) to be labeled too, double-label about 10% to measure reviewer agreement, and use the labels mainly for evaluation rather than blind retuning.

**FR→ML·4 Q:** What would a monthly detection-effectiveness report look like for non-engineers?
**A:** Four panels in plain language: volume (checked, flagged, confirmed), estimated recall from the audit with intervals, false flags and reviewer hours spent, and a vendor table of repeat duplicates with the edit types seen. Add canary status and the top three risks.

---

# Part 3: Media Pipeline Engineer answers

## Questions from the CV Engineer

**CV→MP·1 Q:** Phone footage often has variable frame rate and rotation tags. How do I guarantee the same clip yields the same sampled frames?
**A:** Sample by timestamp with ffmpeg's fps filter, so variable-rate sources still produce frames at consistent times, and let ffmpeg apply the rotation tag (verify the autorotate behavior of your build). Log rotation and VFR status per clip, and add a test comparing a tag-rotated file with a pixel-rotated one. For copies physically rotated 90°, add optional query-side rotation variants.

**CV→MP·2 Q:** Which ffmpeg scaling and color settings keep 32×32 grayscale frames most stable across re-encodes?
**A:** One-step area-averaged downscale (`scale=32:32:flags=area`), explicit `format=gray` afterwards, a consistent color-range conversion, no audio/subtitle/data streams, a fixed thread count, and the bitexact flags where available. Deinterlace only when the stream is flagged interlaced. Pin the ffmpeg build so the flags mean the same thing everywhere.

**CV→MP·3 Q:** If I sample at 1 fps, a trimmed copy lands on different source frames. How much does that hurt, and can the decoder help?
**A:** It can hurt: the nearest sampled frame in the other copy may be up to half a second away, and fast hand motion can push some hashes past radius 3. The fix is cheap: index at 1 fps but sample the query at 3-4 fps, so some query frame in every second lands close to an indexed frame. Decode cost is unchanged because all frames are decoded anyway.

**CV→MP·4 Q:** Can ffmpeg detect and crop letterbox/pillarbox borders cheaply and reliably?
**A:** `cropdetect` works on a few seconds of samples, but it's a heuristic. Apply it only when the detected crop is stable across samples and the border is uniform and meaningfully sized (say at least 4%), and apply the same normalization to both indexed and query clips, otherwise you create mismatches. Dark footage is the main false-positive risk.

## Questions from the ML Scientist

**ML→MP·1 Q:** Which per-clip properties should I log so I can slice eval metrics?
**A:** Container, codec and profile, bitrate, resolution, fps and whether it's variable, duration, rotation, pixel format, audio presence and codec, file size, the encoder tag string, and vendor id. These let you see failures like "recall drops on 4K HEVC."

**ML→MP·2 Q:** How do I generate re-encodes that mimic real platforms and tools?
**A:** Reverse-engineer them from real pairs: run ffprobe on the original and the returned copy, tabulate what changed, then script ffmpeg presets that reproduce those signatures. Add a few messaging-app-like presets (low-bitrate H.264, capped at 720p or 480p), and check that the synthetic distribution resembles the real one.

**ML→MP·3 Q:** Is there any nondeterminism in decoding that could make the same clip yield different frames?
**A:** Decoding of conformant H.264/H.265 streams is spec-exact and independent of thread count, so the risk lies elsewhere: the scaler's SIMD paths can differ across CPU architectures and builds, and hardware decoders can differ outright. Pin the build, use bitexact flags, and keep indexing and querying on the same CPU architecture (x86_64) unless a golden test across architectures passes. I wouldn't trade that consistency for a modest compute saving.

**ML→MP·4 Q:** How do I automatically detect corrupted, truncated, or near-empty clips?
**A:** ffprobe failure; decoded frame count well below duration × fps (say under 90%); decode errors on stderr; a high share of blank frames; or duration under the minimum. Route these to an error verdict with a reason, keep them out of metrics, and list them in a quarantine report.

## Questions from the Backend Engineer

**BE→MP·1 Q:** What are typical clip durations and file sizes, so I can estimate rows per clip and index growth?
**A:** ⚠ Needs your data. The earlier description (skipping a minute at each end) implies clips of at least several minutes, so assume 5-15 minutes and 100 MB to 1.5 GB at 1080p. Run ffprobe over a 100-clip sample to get real distributions before sizing anything.

**BE→MP·2 Q:** How do I get a stable content identity (SHA-256) for large files without reading them twice?
**A:** Compute SHA-256 in the same pass that downloads or copies the file: stream chunks into both the hash and the temp file. When ffmpeg then reads the local temp file, you've read the network once. For files already on disk, a separate sequential hash pass is cheap relative to decode.

**BE→MP·3 Q:** Which ffprobe metadata is worth persisting, and which is noise?
**A:** Codec, resolution, fps, duration, bitrate, rotation, audio stream presence, creation time and encoder tag if present, plus the decoder fingerprint (ffmpeg version). Skip per-frame data and free-form tags unless investigators ask for them.

**BE→MP·4 Q:** If decoding differs across versions, how should I tag fingerprints so I know which decoder produced them?
**A:** Store the ffmpeg version string and build hash, scaler flags, and the detector's params hash with every fingerprint batch. A mismatch against current config means the rows belong to another index generation.

## Questions from the Cloud / DevOps Engineer

**CD→MP·1 Q:** What are realistic per-clip decode times and memory use, so I can size workers and timeouts?
**A:** Estimates to be benchmarked: software decode of 1080p H.264 usually runs roughly 5-15× realtime per core, so a 5-minute clip needs on the order of 20-60 CPU-seconds, and 4K or HEVC costs several times more. Memory is small when frames are piped (a few hundred MB); the big consumer is temp disk if you download the file first.

**CD→MP·2 Q:** Which ffmpeg build and codecs are required (GPL vs LGPL, H.265 support), and how should I package them?
**A:** A recent static build with H.264, H.265, VP9/AV1 decode and AAC; check licensing with your company (GPL builds are usually fine for internal use, but confirm). Verify against a corpus from real vendors. Bake one pinned build into the Docker image and ship the same binary everywhere.

**CD→MP·3 Q:** What corrupt-upload rate should I expect, and how should the queue handle poison messages?
**A:** ⚠ Unknown until measured; assume a low single-digit percent and track it. Give each job a retry cap (2-3), then send it to a dead-letter queue with the error reason; don't retry deterministic decode failures forever. Corrupt files should produce an error verdict a human can see.

**CD→MP·4 Q:** Do you recommend hardware-accelerated decode, and is it compatible with the determinism requirement?
**A:** No. It's faster per clip but produces slightly different pixels than software decode, and for a fingerprinting system consistency matters more than speed. If throughput becomes a problem, add CPU workers instead.

## Questions from the QA Engineer

**QA→MP·1 Q:** Which unusual-but-valid video types belong in the corpus?
**A:** Portrait and rotated clips, 4K, HDR/10-bit, variable frame rate, no audio track, multiple audio tracks, odd containers (MOV, MKV, WebM), very short clips, and clips with non-square pixels. One tiny fixture for each.

**QA→MP·2 Q:** How do I create deterministic, minimal test clips for CI without storing large binaries?
**A:** Generate them with ffmpeg lavfi sources (testsrc2, mandelbrot, cellauto with different parameters), 3-10 seconds at low resolution, then derive variants with scripted commands. Commit the generation script, not binaries, and cache the outputs in CI.

**QA→MP·3 Q:** What do ffmpeg errors look like for common failures, so I can assert specific error handling?
**A:** Truncated or corrupt files typically print messages such as "moov atom not found," "Invalid data found when processing input," or decoder errors mid-stream, with a nonzero exit code; zero-length files fail at probe. Assert on the exit code plus a mapped error category rather than exact message text, since wording varies by version.

**QA→MP·4 Q:** How do I verify that timeouts and resource limits actually kill runaway decodes?
**A:** Test with a fixture that stalls (a named pipe that never delivers, or a deliberately huge input) and assert that the subprocess is killed, no zombie remains, and the job returns an error verdict within the timeout plus a small grace. Run it inside the container with memory limits set.

## Questions from the Fraud Analyst

**FR→MP·1 Q:** Which technical traces survive resale (encoder tags, creation timestamps, device info)?
**A:** Often the encoder tag, bitrate profile, and resolution of the last tool used; sometimes original creation time or device info if metadata wasn't stripped. Treat them as supporting evidence only, since they're easy to alter and absence proves nothing.

**FR→MP·2 Q:** Can we tell from stream properties that a file was re-encoded, and roughly how many times?
**A:** Re-encoding often leaves signs (encoder string, quantization patterns, unusual GOP or bitrate for the claimed source), but "how many times" isn't reliably recoverable. Flag "likely re-encoded" as a weak signal and don't build decisions on it.

**FR→MP·3 Q:** Which cheap edits cause the most trouble for decoding-based features?
**A:** Speed changes, crops/zooms, heavy filters, and screen re-recording. Mirroring is trivial for us to handle on the query side, and trims, re-encodes, and resizes are the easy ones.

**FR→MP·4 Q:** Can the pipeline produce a human-readable evidence view (matched frames side by side with timestamps)?
**A:** Yes, cheaply: use the matched offsets to pull the same moment from both clips and tile the frames side by side (ffmpeg tile or a small script), five to ten pairs spread across the matched span, with timestamps. Generate it only for flagged and review items and store it with the verdict.

---

# Part 4: Backend / Database Engineer answers

## Questions from the CV Engineer

**CV→BE·1 Q:** At tens of millions of rows, are plain B-tree indexes enough for 4×16-bit chunk columns, or would you use multi-index hashing or an extension?
**A:** B-trees on four integer chunk columns are fine up to roughly 10M rows. Beyond that, fan-out dominates (about N/65k rows per chunk lookup if hashes were uniform, worse in practice). The options then are a five-chunk "two must match" scheme or an in-memory binary index with Postgres as the source of truth; I'd rather go in-memory than adopt a niche extension. Don't build either until measurements demand it, and I'd move at about 10M rows or a p95 lookup above 200 ms, whichever comes first.

**CV→BE·2 Q:** How would you cap or pre-filter junk hashes in SQL without losing real matches?
**A:** Maintain a stoplist table of hashes appearing in more than a threshold of clips (refreshed by a periodic job) and exclude them in lookups, plus a per-lookup LIMIT as a safety valve that logs truncations. Filter low-variance frames at ingest so most junk never enters.

**CV→BE·3 Q:** Should offset voting run in SQL, application memory, or a temp table?
**A:** Application memory. Fetch candidates in batches (`WHERE chunk = ANY(...)`), verify Hamming distance in numpy, and vote with a counter. Use a temp table only if candidate sets get too large to hold, which the cap should prevent.

**CV→BE·4 Q:** If I change fps or the hash algorithm, how do I version and migrate the index without mixed-parameter results?
**A:** Give every fingerprint row a generation id tied to a params hash. Build the new generation alongside the old (blue/green), validate it with the eval, then flip a config pointer so queries read one generation only. Delete the old one after a grace period.

## Questions from the ML Scientist

**ML→BE·1 Q:** Can the store log every query's candidates and votes so I can re-threshold offline without re-decoding?
**A:** Yes: a verdicts table with a JSON features column (top-k bins, matched seconds, coverage, mean Hamming, candidate counts, thresholds and generation in force). It's about a kilobyte per query. Raw per-frame hits are too big; keep them only for a sampled debug subset.

**ML→BE·2 Q:** How do I snapshot index state so eval results are reproducible months later?
**A:** Every row carries an ingest sequence number; an eval query takes an `as_of` value and ignores later rows, so results reproduce while production keeps growing. Add periodic database snapshots for milestone evals.

**ML→BE·3 Q:** What schema lets me join reviewer decisions to features for later tuning?
**A:** `verdicts(verdict_id, clip_id, matched_clip_id, features_json, config_version, generation)` and `reviews(review_id, verdict_id, reviewer, decision, decided_at, notes)`. Features are immutable once written, so a later label never gets joined to different features.

**ML→BE·4 Q:** Does the false-match rate scale with database size, and how should thresholds account for it?
**A:** Yes, roughly linearly in the number of candidate comparisons, though clip-level false matches stay rare because they need a consistent offset across many frames. Track the flag rate on known-distinct synthetic probes as the index grows, and revisit minimum matched seconds after each 5-10× growth.

## Questions from the Media Pipeline Engineer

**MP→BE·1 Q:** How should I store per-clip technical metadata so it's queryable alongside fingerprints?
**A:** A clips table with typed columns for the fields you filter on (codec, resolution, fps, duration, rotation, has_audio, size) and a JSON column for the rest, plus the extraction params hash. Index only what you actually query.

**MP→BE·2 Q:** Is it safe to run many ffmpeg subprocesses feeding a single-writer database?
**A:** Yes, if workers extract in parallel and send results to one writer (or use Postgres, which handles concurrent writers). Batch inserts per clip in a single transaction; on SQLite use WAL mode and a single writer to avoid lock contention.

**MP→BE·3 Q:** For failed decodes, what's the right record so retries stay idempotent?
**A:** One row in a `clip_errors` table (clip id, stage, error category, message, attempt count, timestamp) and no partial fingerprints; fingerprints for a clip are inserted atomically only on success. That keeps retries idempotent.

**MP→BE·4 Q:** How should very long videos be handled?
**A:** Extract in chunks with a checkpoint (last completed timestamp), and commit fingerprints per chunk with a "complete" flag set only at the end. A resumed job continues from the checkpoint, and incomplete clips are invisible to queries.

## Questions from the Cloud / DevOps Engineer

**CD→BE·1 Q:** What database throughput and storage growth should I plan for at a given daily clip volume?
**A:** At 1 fps and 5-minute clips, 100,000 clips is about 30M rows, roughly a few GB including indexes (a rough estimate; use integer surrogate clip ids in the frame table, not 64-character strings). Doubling the index fps doubles all of it, so I'd hold the index at 1 fps. Write load is small and bursty; reads dominate. ⚠ Your real clip counts will refine this.

**CD→BE·2 Q:** How should workers behave during database failover or brief unavailability without losing jobs?
**A:** Retry database calls with backoff for a bounded time, don't acknowledge the queue message until the verdict is committed, and rely on queue visibility timeouts for redelivery. Idempotent registration makes redelivery harmless.

**CD→BE·3 Q:** How do I run schema migrations safely while workers on the old version are still running?
**A:** Use expand/contract: first add columns or tables in a backward-compatible way, deploy new workers, then remove old structures in a later release. Never rename or drop in the same release that depends on it.

**CD→BE·4 Q:** What's the index-rebuild strategy (blue/green index) when parameters change?
**A:** Build the new generation in parallel from the stored clips, validate it with eval and shadow queries, switch the pointer, and keep the old generation for rollback for a couple of weeks.

## Questions from the QA Engineer

**QA→BE·1 Q:** Which concurrency scenarios must I test, and what is the correct outcome for each?
**A:** Two workers registering the same clip (one wins, the other becomes a no-op); a check during a backfill (verdict stamped with the high-water mark); two near-identical clips arriving together (the second finds the first); and a crash between "fingerprints written" and "clip marked complete" (the clip must stay invisible).

**QA→BE·2 Q:** Which database constraints guarantee idempotency?
**A:** A unique key on clip id (content SHA-256), a unique key on (clip, detector, generation) for fingerprint batches, and a "complete" status flipped in the same transaction as the last insert. Then re-registering can't create duplicate rows.

**QA→BE·3 Q:** How do I test migrations forward and backward with realistic data?
**A:** Apply each migration to a database loaded with realistic data, run the contract tests on the old and new schema versions with both old and new worker code, and test rollback where it's supported.

**QA→BE·4 Q:** What should happen when the index and the clip table disagree, and how do I test recovery?
**A:** Provide a `dedupe fsck` command: orphaned fingerprints (no clip row) are deleted; clips marked complete with missing fingerprints are re-queued. Test it by deliberately corrupting a fixture database and asserting recovery.

## Questions from the Fraud Analyst

**FR→BE·1 Q:** Can we store a match graph (clip A copies clip B, resold via vendor C)?
**A:** Yes: an `edges` table (clip_a, clip_b, offset, matched seconds, confidence, status, created_at) for confirmed duplicates and review-band matches only. I wouldn't store every low-score candidate: it would bloat the table with noise and make the graph misleading. Keep the vendor identity in a separate table with its own retention policy, so a policy change never forces edits to the graph or the fingerprints.

**FR→BE·2 Q:** How do I query which vendors generate the most duplicate flags without scanning everything?
**A:** Keep `vendor_id` as an indexed column on clips and aggregate over the confirmed edges (a materialized view refreshed daily is enough). Don't scan fingerprints for this.

**FR→BE·3 Q:** How do we keep an audit trail of every verdict and reviewer decision?
**A:** Append-only tables for verdicts and reviews (no updates or deletes), each row stamped with actor, time, and config version. Corrections are new rows.

**FR→BE·4 Q:** Can confirmed duplicates be linked into a family, so future copies attach to all earlier ones?
**A:** Yes, with a union-find style `family_id` assigned when an edge is confirmed; a new copy attaches to the family of its match, so every future copy sees all earlier members. Keep the earliest-seen clip as the family anchor.

---

# Part 5: Cloud / DevOps Engineer answers

## Questions from the CV Engineer

**CV→CD·1 Q:** What instance family and vCPU count would you size for decoding, and does GPU decode justify its cost?
**A:** CPU only. Compute-optimized instances with 4-8 vCPUs per worker, running one or two decodes at a time. I'd default to Graviton (ARM) instances, which are typically about 20% cheaper per vCPU, and ffmpeg runs well on them. GPU decode doesn't justify its cost here.

**CV→CD·2 Q:** Can ffmpeg read from S3 via presigned URLs with seeking, or should workers download first?
**A:** ffmpeg can read HTTP(S) with range requests, so presigned URLs work, but MP4s with the index at the end cause extra seeks, and retries are harder. Default to downloading to ephemeral disk in the same region (free transfer, simple retries) and treat streaming as an optimization.

**CV→CD·3 Q:** How should I package ffmpeg and numpy so local, Docker, and AWS runs use identical versions?
**A:** One Docker image with a pinned static ffmpeg (verified by checksum) and a lockfile with hashes for the Python dependencies; use the same image on laptops, CI, and workers. Record the image digest in every verdict's detector metadata.

**CV→CD·4 Q:** How would you estimate cost and wall-clock time for the backfill, and what parallelism limits should I expect?
**A:** Benchmark 100 clips to get CPU-seconds per clip, multiply by clip count, divide by workers, and price with spot rates (often 50-70% cheaper than on-demand). S3 request rates won't limit you; the practical limits are database write throughput and network bandwidth. Print the estimate before starting and make the job resumable.

## Questions from the ML Scientist

**ML→CD·1 Q:** Can eval jobs run cheaply and isolated from production workers?
**A:** Yes: run on spot instances (jobs are resumable per clip), give them read-only credentials to the clips they need, and write to a separate eval database or schema and bucket prefix. A separate account is better, but a separate role and namespace is the minimum.

**ML→CD·2 Q:** How do I version and store eval datasets and reports so every result traces to a code and config version?
**A:** Keep manifests (file list, SHA-256, labels, split) in git and the videos in an immutable or versioned S3 prefix. Each report records the git commit, config hash, image digest, and manifest hash. That's sufficient without a heavy data-versioning system.

**ML→CD·3 Q:** What monitoring would detect production drift automatically?
**A:** Daily dashboards of flag rate, review rate, score distribution, decode error rate, queue age, and clip property mix, with alerts on large shifts from a trailing baseline. Add nightly canaries: inject a known duplicate probe and a known distinct probe, and fail loudly if either is misclassified.

**ML→CD·4 Q:** Can we shadow-run new thresholds or detector versions on live traffic without affecting verdicts?
**A:** Yes. Thresholds can be replayed offline from logged features. New detector versions run as a second consumer on the same queue, writing to a shadow verdict table that nobody acts on, so you can compare disagreements before promotion.

## Questions from the Media Pipeline Engineer

**MP→CD·1 Q:** How much temp disk and memory should a decode worker have?
**A:** Disk is driven by the largest clip you'll download (plan 2-3× the biggest file, for example 20-50 GB ephemeral volumes if 4K appears); memory is a few GB per concurrent decode with piped frames. Set container limits and test with the largest file in the corpus.

**MP→CD·2 Q:** Can ffprobe/ffmpeg read from S3 efficiently with range requests, and how do I handle throttling?
**A:** Yes, with the caveats noted above. Throttling is rare at these volumes; handle 503/slow-down responses with exponential backoff, and prefer download-then-decode so a retry restarts a transfer rather than a decode.

**MP→CD·3 Q:** How do I enforce timeouts and kill hung ffmpeg processes in containers without leaving zombies?
**A:** Run each process in its own process group with a hard timeout, kill the whole group on expiry, and use an init process (tini or Docker's `--init`) so orphans get reaped. Set the queue visibility timeout above the decode timeout.

**MP→CD·4 Q:** Is a managed transcode service worth using, or is plain ffmpeg on compute cheaper?
**A:** Plain ffmpeg. You're not transcoding, only decoding to tiny frames, so spot compute is cheaper and keeps frame extraction fully under our control.

## Questions from the Backend Engineer

**BE→CD·1 Q:** RDS Postgres, Aurora, or self-managed for a read-heavy lookup index with bursty bulk loads?
**A:** Start with RDS Postgres: managed backups, point-in-time recovery, simple operations, and enough for tens of millions of rows. Aurora and self-managed add cost or burden you don't need yet. If an in-memory index is introduced later, keep Postgres as the system of record.

**BE→CD·2 Q:** How do we handle backups, PITR, and rebuild time if the index is lost but the videos survive?
**A:** Automated backups with PITR for the database. Since clips live in S3, the index is rebuildable, and the rebuild time equals the backfill time, so estimate it up front and test one restore. Enable S3 versioning on both buckets.

**BE→CD·3 Q:** What do you recommend for connection pooling and concurrency limits with many workers?
**A:** Use RDS Proxy or PgBouncer once you have more than a handful of workers, and cap per-worker connections at 1-2. Workers write once per clip, so the pool can be small.

**BE→CD·4 Q:** What monitoring should I set on index growth, slow queries, and lock contention?
**A:** CPU, free storage, connection count, backup health, a slow-query log for lookups beyond a latency threshold (for example p95 above 200 ms), lock waits, and table and index growth. Alert on storage trends well before they bite.

## Questions from the QA Engineer

**QA→CD·1 Q:** What end-to-end smoke test should run after every deployment?
**A:** Upload a tiny known duplicate pair and a distinct clip to a staging "new" bucket, then assert that the duplicate is flagged and the distinct one clears within the SLA, and that verdict rows and logs appear. Keep it under a couple of minutes.

**QA→CD·2 Q:** How do I test IAM permissions and failure paths without touching production?
**A:** In a sandbox account, assert that the worker role can read the buckets and write results but cannot delete or modify source videos, and test denial paths deliberately (revoke a permission, expect a clean error verdict and an alert, not a crash loop).

**QA→CD·3 Q:** Which failure-injection tests do you recommend for workers?
**A:** Kill a worker mid-job, deliver the same message twice, delay messages beyond the visibility timeout, stall the database briefly, and feed a poison file. Each should end in exactly one committed verdict or a dead-letter entry with a reason.

**QA→CD·4 Q:** How do I run integration tests against AWS services cheaply, and know when to trust them?
**A:** Use moto or LocalStack for unit and integration tests (S3, SQS), and a small real staging account for the handful of tests that need real IAM and timing behavior. Emulators don't model IAM or throttling faithfully, so don't trust them for those.

## Questions from the Fraud Analyst

**FR→CD·1 Q:** Can verdicts arrive fast enough that a suspicious upload is held before payment is released to the vendor?
**A:** Yes, as an asynchronous check: a typical clip should clear in minutes, and I'd set an SLA of roughly 10-15 minutes at p95 (sub-minute needs always-warm workers and costs more). Holding payment is a business-process integration outside this system: we expose a verdict API and a status.

**FR→CD·2 Q:** Who can access flagged clips and vendor data, and how is access logged?
**A:** Least privilege: reviewers see flagged items through the evidence view, engineers see fingerprints and verdicts but not vendor identities unless needed, and the worker role is read-only on videos. Enable CloudTrail and S3 access logging, and encrypt buckets and the database at rest.

**FR→CD·3 Q:** Can we alert on bursts (one vendor uploading many flagged clips) as a fraud signal?
**A:** Yes: a scheduled query counting flags per vendor per day, alerting on a jump versus that vendor's baseline, delivered through whatever channel the team already watches (email or Slack).

**FR→CD·4 Q:** How do we keep thresholds and detection logic confidential from vendors, since leaks help evasion?
**A:** Keep config in a private repo or secret store, don't expose scores or reasons to vendors (only the outcome, with no detail), restrict who can read verdict evidence, and avoid public dashboards.

---

# Part 6: QA / Test Automation Engineer answers

## Questions from the CV Engineer

**CV→QA·1 Q:** How do I build a synthetic duplicate suite (trim, re-encode, flip, watermark) whose labels I'm certain about?
**A:** Generate variants programmatically from each original with a recorded manifest (edit type and parameters), so the label is true by construction. Keep unrelated originals from different generators as negatives, and have a human verify any negative pairs drawn from real footage.

**CV→QA·2 Q:** What regression tests catch silent drift when ffmpeg or numpy versions change and hashes shift by a few bits?
**A:** Golden fixtures: a handful of frames and clips with stored expected hashes. In the pinned image, assert exact equality; when upgrading, run a drift report (bits changed per frame) and review it before accepting.

**CV→QA·3 Q:** How do I test that unrelated but visually similar clips don't match, without hand-labeling thousands?
**A:** Mine candidates automatically (the highest similarity between clips known to be different, such as different vendors or timestamps), hand-label only a few hundred of the top-scoring ones, and add them to the permanent hard-negative set. Also generate synthetic look-alikes (same background, different foreground).

**CV→QA·4 Q:** What property-based tests would you write for the chunk pigeonhole guarantee and for offset voting?
**A:** For the chunk guarantee: for random 64-bit pairs flipped in at most 3 bits, at least one of the four chunks matches. For voting: shifting a synthetic sequence by an arbitrary offset recovers that offset within one bin; adding random unrelated frames doesn't change the winning bin; and symmetry holds.

## Questions from the ML Scientist

**ML→QA·1 Q:** Which metrics should be hard CI gates and which advisory, and how do I set tolerance for statistical noise?
**A:** Hard gates on every merge: deterministic fixtures, invariants (identical file matches, trim offset recovered, distinct clips clear), and a floor on synthetic recall for gated edits. Advisory: real-footage metrics, report-only edits, and throughput. I wouldn't block merges on real-footage metrics, because they're slow, noisy, and the data can't live in CI; I'd run them as a scheduled job and a release checklist item.

**ML→QA·2 Q:** How do I test the evaluation code itself so a bug can't silently inflate recall?
**A:** Unit tests with hand-computed tiny cases; sanity detectors (a perfect oracle must score 100%, a random one near chance, an always-flag one 100% false positives); and a deliberate bug-injection check that the eval notices a broken detector.

**ML→QA·3 Q:** What flaky-test policy fits tests that depend on probabilistic thresholds?
**A:** Remove the randomness: seeded fixtures and margin-based assertions (score at least threshold plus margin). Statistical checks live in scheduled eval jobs, not unit tests. Quarantine any genuinely flaky test with a ticket and a deadline rather than adding silent retries.

**ML→QA·4 Q:** How do I keep a frozen golden test set that can still grow, without leaking tuned examples into it?
**A:** Make it versioned and append-only: new items arrive with a version tag, each tuning round records which golden version it used, and once a golden set has influenced tuning decisions repeatedly, rotate it into the dev set and cut a fresh one from newly labeled data.

## Questions from the Media Pipeline Engineer

**MP→QA·1 Q:** Which corrupt-media corpus should I test?
**A:** Truncated file, missing moov atom, zero-length, wrong extension, audio-only, video-only, a corrupt middle section, an unreadable first frame, and extremely small resolution. Generate them by mutating good fixtures (truncate bytes, flip bytes) so they're reproducible.

**MP→QA·2 Q:** How do I test rotation and VFR handling deterministically?
**A:** Create fixtures by remuxing a base clip with a rotation tag set and with timestamps rewritten to variable intervals, then assert that the extracted frames and hashes match the untagged constant-rate version within the expected tolerance.

**MP→QA·3 Q:** Which tests ensure the pipeline never hangs on a pathological file?
**A:** A stalling-input fixture and a very large input under a short timeout; assert termination, no leftover processes, and an error verdict. Run them under the container's memory and CPU limits.

**MP→QA·4 Q:** How do I verify decoded frames are consistent across machines, and what tolerance is acceptable?
**A:** Run the golden clips on each target environment (laptop, CI, worker architecture) and compare frame hashes. Inside the pinned image on the same architecture I require bit-exact equality: tolerances hide drift, so I'd rather fail loudly than accept "close enough." Across architectures, report the differences and make an explicit decision, and I'd keep indexing and querying on one architecture until that decision is made.

## Questions from the Backend Engineer

**BE→QA·1 Q:** How do I test that the SQLite and Postgres stores behave identically?
**A:** Write the contract tests once and parametrize them over every store implementation, covering add, lookup, idempotency, and ordering, plus signed 64-bit edge cases (hashes with the top bit set, chunk values at 0 and 65535).

**BE→QA·2 Q:** How do I test migrations, concurrent writers, and idempotent re-indexing?
**A:** Use a real Postgres container, run N workers inserting the same and overlapping clips, then assert row counts and uniqueness. For migrations, run old-code/new-code compatibility tests following the expand/contract approach.

**BE→QA·3 Q:** Which load tests would find the lookup-latency cliff as the index grows?
**A:** Grow a synthetic index in steps (1M, 5M, 10M, 30M rows) with realistic skew, including heavy junk-hash clusters, and plot p50 and p95 lookup latency and candidates per lookup at each size.

**BE→QA·4 Q:** How do I test data integrity after a crash mid-backfill?
**A:** Kill the process at random points (including between inserts and the completion flag), restart, and assert that no partial clips are visible, no duplicates exist, and the final index equals one built without a crash.

## Questions from the Cloud / DevOps Engineer

**CD→QA·1 Q:** How do I get a production-like environment for end-to-end tests without risking real buckets or paying for it constantly?
**A:** A staging account (or isolated prefixes with separate roles) with tiny synthetic clips, torn down or reset nightly. Costs stay minimal because the fixtures are seconds long.

**CD→QA·2 Q:** What retry and idempotency guarantees does SQS give, so I write correct tests for duplicate deliveries?
**A:** Standard queues are at-least-once, so duplicates and out-of-order delivery happen; FIFO queues add deduplication within a time window. Test duplicate delivery explicitly and make handlers idempotent regardless of queue type.

**CD→QA·3 Q:** How do I test deployment and rollback of the container image?
**A:** Deploy the new image to staging, run the smoke test, then redeploy the previous digest and rerun it. Track image digests, not tags, so rollback is exact.

**CD→QA·4 Q:** Which alerts should fire in failure-mode tests, so I can verify monitoring works too?
**A:** Every failure test should assert the alert it expects: queue age beyond the SLA, a non-empty dead-letter queue, an error-rate spike, a canary failure. An alert that never fires in a test won't fire in production.

## Questions from the Fraud Analyst

**FR→QA·1 Q:** Can you simulate a determined reseller as a test, using a fixed effort budget?
**A:** Yes: script a ladder of effort and record the detection result at each rung: trim only; re-encode plus resize; watermark and color shift; mirror plus 10% crop; speed 1.05×; combinations; sub-segment extraction; stitching two clips; and screen re-recording.

**FR→QA·2 Q:** How do we verify the review queue never silently drops items?
**A:** Reconcile counts: every verdict of type flag or review must have a queue item, and every queue item must reach a terminal decision or an age alert. Run that reconciliation as a scheduled check and as an assertion in the failure-injection tests.

**FR→QA·3 Q:** Which tests ensure a flagged clip can't slip into approval through a status race or timeout?
**A:** Make approval a guarded state transition (a flagged clip cannot move to approved without a recorded reviewer decision), then test concurrent updates, worker crashes mid-verdict, and timeouts to confirm the clip stays held.

**FR→QA·4 Q:** How do we confirm reviewers always see accurate evidence?
**A:** Test the evidence view against fixtures with known offsets: the frames shown must come from the matched clip at the stated timestamps. Add a regression test that the verdict, matched clip id, and evidence file always refer to the same pair, including after re-checks and reviews of multiple matches.

---

# Part 7: Fraud / Marketplace-Integrity Analyst answers

Answers here reflect general marketplace-fraud practice. Anything marked ⚠ depends on your company's real data, contracts, or processes and must be confirmed with your team.

## Questions from the CV Engineer

**CV→FR·1 Q:** Which edits do real middlemen apply: the lazy scripted ones versus what they'd escalate to?
**A:** In general marketplace practice, most resellers apply edits they can script in bulk: rename, re-export or re-compress, resize, trim a few seconds, sometimes add a small watermark. Audio usually passes through untouched, because stripping it makes a clip worthless to buyers (and your reviewers reject muted clips), so I'd enable the audio check early. Escalations such as mirror, crop/zoom, and speed change usually appear only after resellers learn they're being caught. ⚠ Confirm against your real returned pairs.

**CV→FR·2 Q:** If a reseller learns clips are fingerprinted, what's the cheapest evasion, and how fast do resellers adapt?
**A:** Mirroring, a 10-20% crop-and-zoom, and a mild speed change are the cheap options. Adaptation speed depends on margin: if flags cost them money, they test variants within weeks. Expect the edit mix to drift, and watch the share of detected duplicates that were mirrored or cropped as an early-warning signal.

**CV→FR·3 Q:** Do resellers cut sub-clips from long videos or stitch several sources together?
**A:** Splitting long videos into several sellable clips is common, so partial overlap is realistic; stitching several sources is rarer because it takes more effort. ⚠ Check against your real returned pairs whether returned clips tend to be shorter or longer than their originals.

**CV→FR·4 Q:** How do I tell a vendor who innocently resold a clip twice from a middleman laundering it, given that fingerprints can't tell intent?
**A:** Fingerprints can't show intent, but patterns can. A vendor with one duplicate among hundreds of clips is likely innocent; repeated duplicates across many different original sources suggest laundering. Report the evidence and the pattern; the judgment and any vendor action belong to operations and legal, not the detector.

## Questions from the ML Scientist

**ML→FR·1 Q:** Do you have labeled history of confirmed resales, and how was it labeled?
**A:** ⚠ I can't know your data, so ask operations how each known resale was discovered. If a person happened to recognize it, the set is biased toward obvious cases. Use it as a floor on difficulty and supplement it with synthetic edits and the random audit sample.

**ML→FR·2 Q:** How can I estimate duplicates that already slipped through?
**A:** Use sampling: review a random slice of previously approved clips with the new tool in its most sensitive setting. A retroactive scan of the whole library (comparing it against itself) also counts existing duplicates directly, which is more informative than any estimate.

**ML→FR·3 Q:** What base rate of duplicates should I assume in incoming batches?
**A:** ⚠ Ask operations for duplicates found divided by clips received over the last few months, split by month. It matters: with 90% recall and a 1% false-positive rate, about 65% of flags are real at a 2% base rate, but about 91% are real at a 10% base rate.

**ML→FR·4 Q:** Is the adversary stationary? How often should I refresh the test set?
**A:** No. Assume adaptation: refresh the adversarial test set quarterly, add every confirmed evasion to the edit catalog, and watch the edit-type mix of detected duplicates for drift.

## Questions from the Media Pipeline Engineer

**MP→FR·1 Q:** Which tools do resellers likely use (mobile editors, CapCut, HandBrake, ffmpeg scripts)?
**A:** Generally phone editors, free desktop converters, and ffmpeg or HandBrake scripts for bulk work; messaging apps and cloud drives re-encode as a side effect of moving files. ⚠ Verify from encoder tags on real returned copies, which will tell you more than my general impression.

**MP→FR·2 Q:** Do resellers usually strip or rewrite metadata?
**A:** Re-exporting usually does it as a side effect. Leftover metadata can occasionally serve as supporting evidence, but its absence proves nothing, so don't make decisions on it.

**MP→FR·3 Q:** Do they sometimes re-record by screen capture, and what does that do to the file's technical signature?
**A:** It happens in marketplaces when sellers want to defeat file-based checks, but it degrades quality enough that it's uncommon for high-value footage. If you see it in your data, it's the trigger for stronger visual features.

**MP→FR·4 Q:** Are there telltale encoder signatures that show a clip passed through a particular tool?
**A:** Often: encoder tag strings, characteristic bitrates, and resolution caps (messaging apps limit them). Collect them from real pairs into a small table; it won't be conclusive, but it helps trace which route a clip took.

## Questions from the Backend Engineer

**BE→FR·1 Q:** How long must we keep fingerprints of rejected or purged clips?
**A:** I'd keep them indefinitely: resellers recirculate clips after months or years, and fingerprints are small, derived, and can't be viewed as video. I'd also keep the match graph with vendor links for as long as policy allows, since repeat-offender analysis depends on it. The retention question really concerns vendor-linked data, not fingerprints.

**BE→FR·2 Q:** Do you need audit trails (who flagged, who overrode, when)?
**A:** Yes: who flagged, who confirmed or overrode, when, and on what evidence. Append-only is ideal, because disputes with vendors require reconstructing exactly what was known at the time.

**BE→FR·3 Q:** Which queries will investigators run, so I can index for them?
**A:** "Everything this vendor sent," "all clips in this family," "repeat offenders by month," "new flags this week," and "what did we decide about this clip and why." Index vendor, family, status, and date.

**BE→FR·4 Q:** Are there privacy or contractual limits on how long vendor-linked data can be kept?
**A:** ⚠ Likely, but I can't tell you the terms: check vendor contracts and local data-protection rules with legal. My starting point would be to keep vendor links for the length of the relationship plus the period needed to dispute payments.

## Questions from the Cloud / DevOps Engineer

**CD→FR·1 Q:** How quickly must a clip be checked (seconds, minutes, overnight)?
**A:** ⚠ It depends on your payment cycle. If vendors are paid in daily or weekly cycles, within an hour is plenty; if payment is released at upload, you need minutes. Ask operations which it is.

**CD→FR·2 Q:** What daily clip volume and burstiness should I plan for?
**A:** ⚠ I don't have your numbers; get the daily and peak counts for the last three months. Bursts around vendor batch uploads are typical in marketplaces, so plan for peaks well above the average.

**CD→FR·3 Q:** Who may see flagged clips and vendor information, and what logging is required?
**A:** Reviewers need the clips and evidence; analysts need vendor patterns; engineers rarely need vendor identity. Define these roles up front and log every access to flagged items.

**CD→FR·4 Q:** What does a missed duplicate cost versus a false flag?
**A:** ⚠ Quantify it with operations, but my instinct: a missed duplicate costs the clip's price plus training-data contamination and an invitation for further resale, while a false flag costs a few reviewer minutes and perhaps some vendor friction. So I'd tolerate a larger review queue (even 10-15%) during the first month, to learn what we miss, and tighten later.

## Questions from the QA Engineer

**QA→FR·1 Q:** Which adversarial scenarios should I script as tests?
**A:** A reseller with five minutes per clip, a scripted editor, and knowledge that detection exists: expect trim and re-export combinations first, then mirror, crop, and speed. Also resubmission under a new vendor identity, and splitting one long clip into several.

**QA→FR·2 Q:** Which false-negative cases are the worst for the business and must never regress?
**A:** Duplicates of the highest-priced or most-used clips, repeat resales from the same middleman, and duplicates that have already entered training sets.

**QA→FR·3 Q:** Which review-workflow edge cases need tests?
**A:** Vendor disputes and appeals, the same clip resold by two different middlemen, a legitimate re-submission (a vendor fixed a defect and re-uploaded), and conflicting reviewer decisions.

**QA→FR·4 Q:** What would a red-team exercise look like?
**A:** A fixed time budget, the real toolset resellers use, and a prize for the evasion that still produces usable footage. In the debrief, turn each successful evasion into a new regression case and a backlog item.

---

# Part 8: Conclusions

This section synthesizes all 168 answers. IDs in brackets point back to the exact question where each position was stated.

## 8.1 Decisions the experts converged on

| # | Decision | Experts | Source questions |
|---|---|---|---|
| 1 | Fixed, time-based frame sampling. No keyframe, scene-change, or compressed-stream features. | CV, MP | MP→CV·1, MP→CV·4, CV→MP·1 |
| 2 | Index at 1 fps, sample the query at 3-4 fps to cover sub-second trim shifts. | CV, MP, BE, CD | CV→MP·3, CD→BE·1, CD→CV·1 |
| 3 | CPU-only software decode. No GPU decode, hardware decode, or managed transcode service. | CV, MP, CD | CD→CV·2, CD→MP·4, MP→CD·4, CV→CD·1 |
| 4 | One pinned ffmpeg build in one Docker image; version metadata on every fingerprint batch; golden fixtures. | CV, MP, CD, BE, QA | CD→CV·3, CV→CD·3, BE→MP·4, QA→CV·2 |
| 5 | Decide at clip level from offset voting, using absolute matched seconds plus two-way coverage; return the top few bins per match (covers partial reuse and stitching). | CV, FR | FR→CV·3, FR→CV·1 |
| 6 | Mine hard negatives from your own library and have a human verify them; about 300 queries to support a "≤1% false positives" claim. | CV, ML, QA | ML→CV·3, CV→ML·2, CV→QA·3 |
| 7 | Statistical hygiene: split by source and vendor, report intervals, at least 60-100 clips per gated category, separate rows for unusual strata. | ML, QA, MP | CV→ML·3, QA→ML·2, QA→ML·3, MP→ML·2, MP→ML·4 |
| 8 | Persist per-verdict features and votes (immutable), a reviews table, and an `as_of` ingest sequence, so thresholds can be replayed offline. | ML, BE, FR | ML→BE·1-3, BE→ML·1-3, FR→BE·3 |
| 9 | Blue/green index generations tagged with a params hash; expand/contract schema migrations. | BE, CD, CV | CV→BE·4, CD→BE·3, CD→BE·4 |
| 10 | Postgres (RDS) as system of record, SQLite locally; offset voting in application memory; junk-hash stoplist and a per-lookup candidate cap. | BE, CV, CD | CV→BE·2, CV→BE·3, BE→CD·1 |
| 11 | Asynchronous queue processing: idempotent handlers, acknowledge only after commit, retry cap, dead-letter queue. | CD, BE, MP, QA | CD→BE·2, CD→MP·3, CD→QA·2, QA→CD·3 |
| 12 | Nightly canary probes, drift monitoring, and shadow rollout before any promotion. | ML, CD, QA | ML→CD·3, ML→CD·4, CD→ML·2, CD→ML·4 |
| 13 | A random audit sample of "clear" verdicts to estimate unseen misses and de-bias reviewer labels. | ML, FR | FR→ML·2, FR→ML·3, ML→FR·2 |
| 14 | An evidence contact sheet (matched frames side by side with timestamps) for reviewers, generated for flagged and review items. | MP, FR, QA | FR→MP·4, FR→QA·4 |
| 15 | The worker is read-only on S3; least-privilege roles; audit logging; scores and reasons never shown to vendors. | CD, FR, QA | FR→CD·2, FR→CD·4, QA→CD·2 |
| 16 | Corrupt or truncated files yield an error verdict plus a quarantine list; ffmpeg runs under process-group timeouts. | MP, QA, CD | ML→MP·4, QA→MP·1, QA→MP·3, MP→CD·3 |
| 17 | Mirroring is handled on the query side; 90° rotation variants are optional. | CV, MP | CV→MP·1, FR→MP·3 |
| 18 | Embeddings and local-feature matching stay deferred. The trigger is an audit showing misses are mostly crops, zooms, or screen re-recordings. | CV, FR, MP | FR→CV·4, MP→FR·3 |
| 19 | Two-threshold design: a flag threshold set by precision target, a review threshold set by reviewer capacity. | ML, QA, FR | CV→ML·4, ML→QA·1, CD→FR·4 |

## 8.2 Disagreements and which choice is better

### D1. Should the audio detector be on from day one?
- **Enable early:** FR argues scripted edits leave audio untouched and reviewers reject muted clips, so audio is a strong independent signal [CV→FR·1].
- **Keep it off until proven:** ML says it needs to add recall on real kitchen audio at no false-positive cost [QA→ML·1]; CV doubts that noise-like kitchen sound fingerprints well [FR→CV·4].
- **Better choice: off by default, but test it first and early.** An unvalidated detector adds false flags and reviewer load, so it shouldn't ship untested. But FR's premise is cheap to test (run the audio fingerprint on the real duplicate pairs during the feasibility spike, G4 below). Enable it only if it catches real duplicates the frame detector misses, or corroborates borderline ones, without raising flag-level false positives.

### D2. Which CPU architecture for workers?
- **Graviton (ARM):** about 20% cheaper per vCPU [CV→CD·1].
- **x86_64 only:** scaler code paths can differ across architectures, and a silent fingerprint change would force a re-index [ML→MP·3]; QA wants differences reported and decided explicitly [MP→QA·4].
- **Better choice: x86_64 for all index and query workers for now.** A 20% compute saving is small next to the cost of fingerprint drift that invalidates the index. Revisit after the backfill cost is known and only if golden hashes across architectures match.

### D3. Bit-exact or tolerant golden tests?
- **Bit-exact:** QA wants exact equality inside the pinned image, because tolerance hides drift [MP→QA·4].
- **Tolerant:** CV allows a mean of 1 bit and a maximum of 3 bits across versions, since matching itself tolerates radius 3 [QA→CV·4].
- **Better choice: both, in different places.** Exact equality is the blocking test inside the pinned image and architecture. The tolerance applies only to the drift report produced when deliberately upgrading ffmpeg or numpy, and a human reviews it before accepting.

### D4. What should block a release: real or synthetic metrics?
- **Real pairs must gate changes:** ML says synthetic edits are what the detector was tuned against [QA→ML·1].
- **Don't block merges on real data:** QA says real-footage metrics are slow, noisy, and can't live in CI [ML→QA·1].
- **Better choice: two tiers.** Merges are gated on deterministic fixtures and synthetic edits. Promotion to production is gated on the real held-out pairs, reported with intervals. With only 20-25 validation pairs, treat that gate as a smoke alarm rather than proof.

### D5. How large may the review queue be?
- **About 5%, set by capacity:** ML's default [CV→ML·4].
- **10-15% in month one:** FR argues a missed duplicate costs far more than a few reviewer minutes [CD→FR·4].
- **Better choice: FR's wider band during the pilot month, if reviewers can absorb it, with ML's capacity rule and a hard cap of 1% false positives on auto-flags.** The extra reviews buy labeled data and a measured miss rate. Tighten toward 5% as labels accumulate. If reviewers can't absorb it, capacity wins.

### D6. How long, and how broadly, should vendor-linked data be kept?
- **Long-term match graph with vendor links:** FR wants repeat-offender analysis [BE→FR·1, BE→FR·4].
- **Narrow scope and separable data:** BE stores edges only for confirmed and review-band matches and keeps vendor identity in a separate table with its own retention [FR→BE·1].
- **Better choice: BE's design.** Fingerprints are derived, non-personal data, so keep them indefinitely. Vendor identity sits in its own table whose retention legal can set or change without touching the index. Edges are stored only for confirmed and review-band matches. This still serves FR's analysis.

### D7. When to leave plain B-tree exact search?
- **Stay exact as long as possible:** CV prefers simplicity, accepting approximate search only if latency forces it [BE→CV·2].
- **Plan the exit at ~10M rows:** BE would move to an in-memory index or a five-chunk scheme at about 10M rows or p95 lookup above 200 ms [CV→BE·1].
- **Better choice: exact B-tree now, with a measured trigger.** At about 10M rows or p95 above 200 ms, first move to an in-memory exact multi-index; use approximate search only if that still isn't enough, and re-measure clip-level recall after any approximate method.

### D8. Index sampling density
- **2 fps in the index:** CV's preference for margin [MP→CV·1].
- **1 fps:** BE notes every doubling doubles rows and candidate fan-out [CD→BE·1].
- **Better choice: 1 fps in the index, 3-4 fps on the query.** It captures most of the benefit at no index cost [CV→MP·3]. Revisit only if misses are traced to sampling phase.

## 8.3 Gaps nobody covered, and the filled-in decisions

These are things the 168 questions did not address. The decisions below are what makes most sense given the plan and the answers, but items marked "sign-off" need a human decision from your team.

| # | Gap | Filled-in decision |
|---|---|---|
| G1 | **What counts as a duplicate?** (sign-off) | Same *footage*, not same task. Proposal: at least 10 s of consistent matched footage between two clips is a duplicate; shorter overlaps go to review; different takes of the same task are not duplicates. Operations must approve this, because it defines every metric. |
| G2 | **Duplicates already in the library.** | Once the index exists, run it as a self-comparison over the approved bucket. That finds existing duplicate families, quantifies the problem, and is the strongest demonstration of value. What to do about models already trained on them is a decision for the ML team, outside this tool. |
| G3 | **Data governance for the pilot.** (sign-off) | Get written permission before copying real clips to a laptop or outside the company's approved environment. Footage shows people, so treat it as sensitive: use a small approved sample, send nothing to external services, and keep processing inside company-controlled systems. |
| G4 | **Feasibility spike before building the framework.** | Spend 1-2 days on a throwaway script: take about 30 real duplicate pairs, compute 1-fps pHashes, and plot the Hamming distance of corresponding frames against random frame pairs (random 64-bit hashes sit near 32 bits apart). Proceed if true pairs sit well below that, roughly under 8 bits. In the same spike, run the audio fingerprint on the same pairs (resolves D1). |
| G5 | **Reviewer workflow and tooling.** | Minimum viable: a queue view (table or spreadsheet), the evidence contact sheet, a decision field (duplicate / not duplicate / unsure), and decisions written to the reviews table. Double-label about 10% to measure agreement, and set an age alert so nothing waits indefinitely. |
| G6 | **What happens when a clip is flagged.** (sign-off) | The tool outputs a verdict and evidence only. Decisions about holding payment, contacting vendors, or penalties belong to operations and legal, who should write a one-page policy. Never penalize a vendor on an automated result alone; distinguish a vendor re-uploading its own clip from a middleman. |
| G7 | **Ownership and handover.** | Because this is an internship, plan for the handover: README, a runbook for tuning and re-indexing, a short decisions log (this document works as the seed), a tagged release, and a demo to the team. Name an owner for thresholds. |
| G8 | **Realistic timeline.** | Rough estimate: week 1 for the spike, scaffold, exact hash, and eval harness; weeks 2-3 for the frame detector and tuning; week 4 for the local pilot and its one-page report; weeks 5-6 for S3, Postgres, and the worker; plus 1-2 weeks of buffer. This depends on data access and review turnaround. |
| G9 | **Security baseline.** | Encryption at rest and in transit, least-privilege roles, no public buckets, secrets in a secret manager (never in code), dependency scanning. Treat the fingerprint database as sensitive too, since it reveals which clips the company holds. |
| G10 | **Deletion requests and ended vendor relationships.** | Purge by clip id: delete fingerprints and edges for that clip; keep audit rows with the clip reference anonymized. Define this in the policy so it's a routine operation. |
| G11 | **Same-batch ordering.** | Within a batch, the clip with the earliest `received_at` is the anchor; later arrivals are duplicates of it. A late-match pass after registration ensures the second arrival finds the first. |
| G12 | **Definition of success and cost-benefit.** | Agree on what success means (for example, fewer duplicates accepted and reviewer minutes per clip), put it in the pilot report, and compare compute plus engineering cost with the cost of duplicates avoided. |
| G13 | **Alert ownership.** | An intern shouldn't be on call. Send alerts to a shared team channel or email list, and have a named owner for acknowledging them. |
| G14 | **Upgrade path to embeddings or local features.** | Keep the reserved detector slot. Build it only when the audit shows that crops, zooms, or screen re-recordings account for a meaningful share of confirmed misses. Evaluate existing video copy-detection models before building anything. |
| G15 | **Legacy language and integration.** | Unknown until confirmed. The CLI with JSON output and fixed exit codes is the integration path whichever language the legacy code uses. Confirm the Python version before Phase 0. |

## 8.4 Amendments to the implementation plan

These changes to `clipguard-implementation-plan.md` follow from the panel:

| # | Plan section | Amendment |
|---|---|---|
| 1 | §5.2 | Separate `index_fps` (default 1) from `query_fps` (default 4). Offset bins stay at 2 s. |
| 2 | §5.2 | Add absolute `min_matched_seconds`, two-way coverage, and return the top-k bins per matched clip. |
| 3 | §5.2 | Add `max_candidates_per_lookup` with truncation logging, a junk-hash stoplist table, and collapsing of consecutive near-identical hashes. |
| 4 | §5.2 | Optional border normalization (cropdetect), applied identically on index and query sides, off by default. |
| 5 | §4 | Frame tables use an integer surrogate `clip_pk`; the SHA-256 stays in the clips table. |
| 6 | §4 | Add index generations, a params hash on every fingerprint row, and an ingest sequence for `as_of` queries. |
| 7 | §4.2 | Extend the verdict schema: mean Hamming, duration ratio, flipped flag, image digest, config version, index generation, high-water mark, evidence path. |
| 8 | §4 | New tables: `verdicts`, `reviews`, `edges`, `families`, `clip_errors`; vendor identity in a separate table with its own retention. |
| 9 | §8 | New commands: `dedupe evidence <verdict>`, `dedupe fsck`, `dedupe audit-sample`, `dedupe replay` (re-threshold from logged features), `dedupe selfjoin` (library audit). |
| 10 | §10 | ffmpeg flags: area scaling, bitexact options, fixed threads; pinned static build; x86_64 workers; exact golden hashes inside the pinned image; a drift report on upgrades. |
| 11 | §11 | Eval manifests with true offsets; per-stratum reporting; splits by source and vendor; versioned golden set; two-tier gates (D4); a red-team script. |
| 12 | §12 | Insert a Phase 0.5 feasibility spike (G4), including an early audio experiment. Audio stays off by default (D1). |
| 13 | §13 | Pilot review band may reach about 10% in month one (D5) with the 1% auto-flag false-positive cap; add a 1-2% random audit sample. |
| 14 | §14 | Add open questions: data-governance approval, payment cycle, duplicate base rate, typical clip length, flagged-clip policy, definition of "duplicate." |
| 15 | §12 (Phase 6) | Add canary probes, a shadow verdict table, dead-letter queue and retry cap, a 10-15 minute p95 SLA, CloudTrail, and a worker role that is read-only on S3. |
| 16 | §12 (Phase 5) | Add the library self-comparison (G2) as a pilot deliverable. |
| 17 | §4.3 | Multi-Layer Fraud Intent Triage: Surface `layer` and `intent_assessment` in verdicts to distinguish unedited/accidental duplicates (seller unaware, reject without penalty) from edited duplicates (adversarial alteration/evasion, hold payment & escalate). |

## 8.5 Recommended next steps, in order

1. **Run the 1-2 day feasibility spike (G4)** on about 30 real duplicate pairs. It tests the central assumption cheaply before any framework exists.
2. **Ask operations five questions:** how duplicates were found, the duplicate base rate, the payment cycle, typical clip length and bucket size, and who decides what happens to a flagged clip (G1, G6).
3. **Get written permission** to copy a sample of clips for the pilot (G3).
4. **Run ffprobe on the real original/returned pairs** to see what resellers actually changed; this replaces the guessed edit mix with evidence.
5. **Apply the amendments in 8.4** and hand the updated plan to a coding agent, one phase at a time.
6. **Revisit the open disagreements (D1-D8)** with real numbers at the end of the pilot; most of them are settled by measurements you will have by then.
