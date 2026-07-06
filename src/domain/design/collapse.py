"""Challenge fingerprinting and batch-level collapse detection.

"Challenge Design Collapse": nominal concepts A/B/C differ, but the actual
shortest solution of every challenge funnels back to one generic shortcut X
(XOR for re, weak-creds for web, win-function for pwn). Technique-name
diversity alone cannot see this — two tasks can use different sub-techniques
yet share the same entrypoint -> mechanism -> flag-access *shape*.

This module is pure/deterministic so it can run offline (no DB, no Docker):

- ``challenge_fingerprint`` reduces a design to its collapse-relevant shape.
- ``compute_batch_collapse`` reports semantic fingerprints / techniques
  / shapes across a batch and flags collapse when any single value dominates.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

# A batch is considered collapsed when any single mechanism / solution shape
# accounts for at least this share of the batch (operators can override).
DEFAULT_COLLAPSE_THRESHOLD = 0.5
# Below this batch size the share heuristic is too noisy to be meaningful.
MIN_BATCH_FOR_STATS = 4


def _asset_flow_shape(challenge: Mapping[str, Any]) -> list[str]:
    flow = challenge.get("asset_flow")
    shape: list[str] = []
    if isinstance(flow, list):
        for stage in flow:
            if isinstance(stage, Mapping):
                produced = stage.get("produced_asset_or_capability")
                if isinstance(produced, str) and produced.strip():
                    shape.append(produced.strip().lower())
    return shape


def _semantic_fingerprint_value(challenge: Mapping[str, Any]) -> str:
    flags = challenge.get("diversity_flags")
    if isinstance(flags, Mapping):
        for key in ("semantic_fingerprint", "chosen_mechanism", "core_mechanism"):
            value = flags.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    for key in ("semantic_fingerprint", "chosen_mechanism", "core_mechanism"):
        value = challenge.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "unknown"


def _primary_technique(challenge: Mapping[str, Any]) -> str:
    value = challenge.get("primary_technique")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "unknown"


def challenge_fingerprint(challenge: Mapping[str, Any]) -> str:
    """Stable short fingerprint of a design's collapse-relevant shape.

    Two challenges with the same category, difficulty, primary technique,
    semantic fingerprint, and asset-flow shape hash to the same fingerprint
    even if their labels differ.
    """
    parts = [
        str(challenge.get("category") or "").lower(),
        str(challenge.get("difficulty") or "").lower(),
        _primary_technique(challenge),
        _semantic_fingerprint_value(challenge),
        ">".join(_asset_flow_shape(challenge)),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _share_report(counter: Counter[str], total: int, threshold: float) -> dict[str, Any]:
    if total <= 0:
        return {"distribution": {}, "dominant": None, "dominant_share": 0.0,
                "collapsed": False}
    dominant, dominant_count = counter.most_common(1)[0]
    share = dominant_count / total
    return {
        "distribution": dict(counter),
        "dominant": dominant,
        "dominant_share": round(share, 3),
        "collapsed": share >= threshold,
    }


def compute_batch_collapse(
    challenges: Sequence[Mapping[str, Any]],
    *,
    threshold: float = DEFAULT_COLLAPSE_THRESHOLD,
) -> dict[str, Any]:
    """Summarize a batch and flag collapse along mechanism/technique/shape axes.

    Returns a report with per-axis distribution + dominant share, a list of
    fingerprint duplicate groups, and an overall ``collapsed`` flag (True when
    any axis exceeds ``threshold`` or any fingerprint repeats). Batches smaller
    than ``MIN_BATCH_FOR_STATS`` report ``collapsed=False`` (too small to judge)
    but still return the raw distributions.
    """
    total = len(challenges)
    semantic_fingerprints: Counter[str] = Counter()
    techniques: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    fingerprints: Counter[str] = Counter()
    fp_to_ids: dict[str, list[str]] = {}

    for ch in challenges:
        semantic_fingerprints[_semantic_fingerprint_value(ch)] += 1
        techniques[_primary_technique(ch)] += 1
        # Only non-trivial (multi-stage) flows participate in shape collapse:
        # a direct observe->flag flow is legitimate for easy tasks and must not
        # be counted as "everyone has the same shape".
        flow_key = ">".join(_asset_flow_shape(ch))
        if flow_key:
            shapes[flow_key] += 1
        fp = challenge_fingerprint(ch)
        fingerprints[fp] += 1
        fp_to_ids.setdefault(fp, []).append(str(ch.get("id") or "?"))

    duplicate_groups = [ids for fp, ids in fp_to_ids.items() if len(ids) > 1]

    shaped_total = sum(shapes.values())
    semantic = _share_report(semantic_fingerprints, total, threshold)
    tech = _share_report(techniques, total, threshold)
    shape = _share_report(shapes, shaped_total, threshold)

    small = total < MIN_BATCH_FOR_STATS
    # Shape collapse only when enough tasks actually declare a flow to judge.
    shape_collapsed = shape["collapsed"] and shaped_total >= MIN_BATCH_FOR_STATS
    collapsed = (not small) and (
        semantic["collapsed"]
        or tech["collapsed"]
        or shape_collapsed
        or bool(duplicate_groups)
    )

    reasons: list[str] = []
    if not small:
        if semantic["collapsed"]:
            reasons.append(
                f"semantic_fingerprint collapse: {semantic['dominant']!r} is "
                f"{int(semantic['dominant_share'] * 100)}% of the batch"
            )
        if tech["collapsed"]:
            reasons.append(
                f"primary_technique collapse: {tech['dominant']!r} is "
                f"{int(tech['dominant_share'] * 100)}% of the batch"
            )
        if shape_collapsed:
            reasons.append(
                f"asset_flow shape collapse: {shape['dominant']!r} is "
                f"{int(shape['dominant_share'] * 100)}% of multi-stage tasks"
            )
        for group in duplicate_groups:
            reasons.append(f"near-duplicate fingerprint: {', '.join(group)}")

    return {
        "total": total,
        "too_small_to_judge": small,
        "collapsed": collapsed,
        "semantic_fingerprint": semantic,
        "mechanism": semantic,
        "technique": tech,
        "asset_flow_shape": shape,
        "duplicate_groups": duplicate_groups,
        "reasons": reasons,
    }
