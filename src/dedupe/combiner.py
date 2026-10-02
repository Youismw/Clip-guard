"""Pure decision combiner that maps detector results to verdicts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from dedupe.models import ClipRef, DetectorResult, Match, Verdict


class Combiner:
    """Combines multi-detector outputs into a single deterministic verdict."""

    def __init__(
        self,
        policy: str = "any",
        thresholds: Optional[Mapping[str, tuple[float, float]]] = None,
        weights: Optional[Mapping[str, float]] = None,
        global_flag_threshold: float = 0.80,
        global_review_threshold: float = 0.40,
    ) -> None:
        if policy not in {"any", "corroborate", "weighted"}:
            raise ValueError(f"Unknown combiner policy: '{policy}'")
        self.policy = policy
        # mapping of detector_name -> (flag_threshold, review_threshold)
        self.thresholds = dict(thresholds or {})
        self.weights = dict(weights or {})
        self.global_flag_threshold = global_flag_threshold
        self.global_review_threshold = global_review_threshold

    def get_thresholds(self, detector_name: str) -> tuple[float, float]:
        """Return (flag_threshold, review_threshold) for a detector with defaults."""
        return self.thresholds.get(detector_name, (0.80, 0.40))

    def combine(
        self,
        clip: ClipRef,
        results: Sequence[DetectorResult],
        is_already_indexed: bool = False,
    ) -> Verdict:
        detector_summaries: list[dict[str, Any]] = [
            {
                "name": r.detector,
                "version": r.version,
                "elapsed_ms": r.elapsed_ms,
                "error": r.error,
            }
            for r in results
        ]

        # 1. Byte-identical file already in the system
        if is_already_indexed:
            return Verdict(
                schema_version=1,
                clip_id=clip.clip_id,
                uri=clip.uri,
                verdict="already_indexed",
                layer="exact",
                intent_assessment="unedited_likely_unaware",
                reason=(
                    f"Clip {clip.clip_id[:16]} is already indexed in the system "
                    f"(identical bytes resubmitted)"
                ),
                matches=[],
                detectors=detector_summaries,
            )

        # 2. Check for detector errors
        if not results:
            return Verdict(
                schema_version=1,
                clip_id=clip.clip_id,
                uri=clip.uri,
                verdict="clear",
                layer="none",
                intent_assessment="clean",
                reason="No detectors were executed",
                matches=[],
                detectors=detector_summaries,
            )

        all_errors = all(r.error is not None for r in results)
        if all_errors:
            err_details = "; ".join(f"{r.detector}: {r.error}" for r in results if r.error)
            return Verdict(
                schema_version=1,
                clip_id=clip.clip_id,
                uri=clip.uri,
                verdict="error",
                layer="none",
                intent_assessment="error",
                reason=f"All detectors failed with errors: {err_details}",
                matches=[],
                detectors=detector_summaries,
            )

        # Gather matches from non-erroring detectors
        all_matches: list[Match] = []
        for r in results:
            if r.error is None:
                all_matches.extend(r.matches)

        # Sort matches by confidence descending
        all_matches.sort(key=lambda m: m.confidence, reverse=True)

        if not all_matches:
            # Check if partial errors occurred
            partial_errors = [r for r in results if r.error is not None]
            reason = "No candidate duplicates found"
            if partial_errors:
                warns = ", ".join(f"{r.detector} failed ({r.error})" for r in partial_errors)
                reason = f"{reason} (warning: {warns})"
            return Verdict(
                schema_version=1,
                clip_id=clip.clip_id,
                uri=clip.uri,
                verdict="clear",
                layer="none",
                intent_assessment="clean",
                reason=reason,
                matches=[],
                detectors=detector_summaries,
            )

        # 3. Decision by Policy
        if self.policy in {"any", "corroborate"}:
            verdict, reason, top_matches = self._combine_any(all_matches, results)
        else:
            verdict, reason, top_matches = self._combine_weighted(all_matches)

        # 4. Assess detection layer and vendor intent
        layer = "none"
        intent = "clean"
        if verdict in {"duplicate", "review"} and top_matches:
            top_det = top_matches[0].detector
            if top_det == "exact_sha256":
                layer = "exact"
                intent = "unedited_likely_unaware"
            elif "frame" in top_det:
                layer = "visual"
                intent = "edited_likely_intentional"
            elif "audio" in top_det:
                layer = "audio"
                intent = "edited_likely_intentional"
            else:
                layer = "unknown"
                intent = "edited_likely_intentional"

        return Verdict(
            schema_version=1,
            clip_id=clip.clip_id,
            uri=clip.uri,
            verdict=verdict,
            layer=layer,
            intent_assessment=intent,
            reason=reason,
            matches=top_matches,
            detectors=detector_summaries,
        )

    def _combine_any(
        self,
        matches: Sequence[Match],
        results: Sequence[DetectorResult],
    ) -> tuple[str, str, Sequence[Match]]:
        flag_matches: list[tuple[Match, float]] = []
        review_matches: list[tuple[Match, float]] = []

        for m in matches:
            flag_th, review_th = self.get_thresholds(m.detector)
            if m.confidence >= flag_th:
                flag_matches.append((m, flag_th))
            elif m.confidence >= review_th:
                review_matches.append((m, review_th))

        if flag_matches:
            best_match, th = flag_matches[0]
            reason = (
                f"{best_match.detector} confidence {best_match.confidence:.2f} "
                f">= flag_threshold {th:.2f}"
            )
            return "duplicate", reason, [m for m, _ in flag_matches]

        if review_matches:
            if self.policy == "corroborate":
                # Check if a video review match is corroborated by an audio match to the same clip
                video_review = [m for m, _ in review_matches if "frame" in m.detector]
                audio_matches = [m for m in matches if "audio" in m.detector]
                for vm in video_review:
                    for am in audio_matches:
                        if vm.matched_clip_id == am.matched_clip_id:
                            reason = (
                                f"Corroborated: {vm.detector} review match confirmed by "
                                f"{am.detector} on clip {vm.matched_clip_id[:12]}"
                            )
                            return "duplicate", reason, [vm, am]

            best_match, th = review_matches[0]
            reason = (
                f"{best_match.detector} confidence {best_match.confidence:.2f} "
                f">= review_threshold {th:.2f}"
            )
            return "review", reason, [m for m, _ in review_matches]

        best_m = matches[0]
        return "clear", f"Best match confidence {best_m.confidence:.2f} below review threshold", []

    def _combine_weighted(
        self,
        matches: Sequence[Match],
    ) -> tuple[str, str, Sequence[Match]]:
        # Group matches by matched_clip_id
        clip_scores: dict[str, float] = {}
        clip_matches: dict[str, list[Match]] = {}

        for m in matches:
            weight = self.weights.get(m.detector, 1.0)
            clip_scores[m.matched_clip_id] = (
                clip_scores.get(m.matched_clip_id, 0.0) + m.confidence * weight
            )
            clip_matches.setdefault(m.matched_clip_id, []).append(m)

        total_weight = sum(self.weights.values()) if self.weights else 1.0
        best_clip_id = max(clip_scores, key=lambda cid: clip_scores[cid])
        normalized_score = clip_scores[best_clip_id] / max(total_weight, 1.0)
        best_matches = clip_matches[best_clip_id]

        if normalized_score >= self.global_flag_threshold:
            th = self.global_flag_threshold
            msg = f"Weighted score {normalized_score:.2f} >= flag_threshold {th:.2f}"
            return "duplicate", msg, best_matches
        elif normalized_score >= self.global_review_threshold:
            th = self.global_review_threshold
            msg = f"Weighted score {normalized_score:.2f} >= review_threshold {th:.2f}"
            return "review", msg, best_matches

        th = self.global_review_threshold
        msg = f"Weighted score {normalized_score:.2f} below review threshold {th:.2f}"
        return "clear", msg, []
