"""Mechanical transform classification for difficulty counting.

This Layer 2 module consumes canonical sub-technique keys produced by
``technique_taxonomy.resolve_sub_technique``. It knows nothing about the
difficulty rubric and deliberately has no chain or sequence API.
"""

from __future__ import annotations

MECHANICAL_CLASS = "encoding"

MECHANICAL_TRANSFORMS: frozenset[str] = frozenset(
    {
        "base64",
        "base32",
        "hex",
        "url",
        "rot",
        "caesar",
        "xor",
        "gzip",
        "zlib",
        "strings",
        "strings extraction",
        "strings on binary",
        "strings on the binary",
    }
)


def is_mechanical_transform(sub_technique: str) -> bool:
    """Return whether ``sub_technique`` is a pure decode/unwrap transform."""

    return sub_technique in MECHANICAL_TRANSFORMS
