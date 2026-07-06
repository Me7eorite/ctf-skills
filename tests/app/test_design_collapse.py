"""Unit tests for challenge fingerprinting and batch collapse detection."""

from __future__ import annotations

from domain.design.collapse import (
    challenge_fingerprint,
    compute_batch_collapse,
)


def _ch(cid, semantic_fingerprint, technique="t", shape=None, category="re", difficulty="easy"):
    challenge = {
        "id": cid,
        "category": category,
        "difficulty": difficulty,
        "primary_technique": technique,
        "diversity_flags": {"semantic_fingerprint": semantic_fingerprint},
    }
    if shape:
        challenge["asset_flow"] = [
            {"produced_asset_or_capability": s} for s in shape
        ]
    return challenge


def test_fingerprint_is_stable_and_shape_sensitive():
    a = _ch("re-1", "xor_keystream", "ptrace", shape=["key", "flag"])
    b = _ch("re-2", "xor_keystream", "ptrace", shape=["key", "flag"])
    c = _ch("re-3", "aes", "ptrace", shape=["key", "flag"])
    assert challenge_fingerprint(a) == challenge_fingerprint(b)  # same shape
    assert challenge_fingerprint(a) != challenge_fingerprint(c)  # mechanism differs


def test_batch_collapse_flags_dominant_semantic_fingerprint():
    # 5 of 6 use the same fingerprint -> semantic collapse.
    batch = [
        _ch("re-1", "xor_keystream", "ptrace"),
        _ch("re-2", "xor_keystream", "rdtsc"),
        _ch("re-3", "xor_keystream", "antivm"),
        _ch("re-4", "xor_keystream", "cff"),
        _ch("re-5", "xor_keystream", "packer"),
        _ch("re-6", "aes", "vm"),
    ]
    report = compute_batch_collapse(batch)
    assert report["collapsed"] is True
    assert report["semantic_fingerprint"]["dominant"] == "xor_keystream"
    assert any("semantic_fingerprint collapse" in r for r in report["reasons"])


def test_batch_diverse_mechanisms_not_collapsed():
    batch = [
        _ch("re-1", "xor_keystream", "ptrace"),
        _ch("re-2", "aes", "rdtsc"),
        _ch("re-3", "rc4", "antivm"),
        _ch("re-4", "tea_xtea", "cff"),
        _ch("re-5", "sbox_substitution", "packer"),
        _ch("re-6", "hash_compare", "vm"),
    ]
    report = compute_batch_collapse(batch)
    assert report["collapsed"] is False


def test_batch_flags_near_duplicate_fingerprints():
    batch = [
        _ch("re-1", "aes", "ptrace", shape=["key", "flag"]),
        _ch("re-2", "aes", "ptrace", shape=["key", "flag"]),  # identical shape
        _ch("re-3", "rc4", "rdtsc", shape=["t", "f"]),
        _ch("re-4", "tea_xtea", "antivm", shape=["a", "b"]),
    ]
    report = compute_batch_collapse(batch)
    assert report["collapsed"] is True
    assert ["re-1", "re-2"] in report["duplicate_groups"]


def test_small_batch_not_judged():
    batch = [_ch("re-1", "xor_keystream"), _ch("re-2", "xor_keystream")]
    report = compute_batch_collapse(batch)
    assert report["too_small_to_judge"] is True
    assert report["collapsed"] is False
    # raw distribution still available
    assert report["mechanism"]["distribution"]["xor_keystream"] == 2
