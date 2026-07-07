"""Technique family and sub-technique classification helpers.

This module is the Layer 1 taxonomy for design planning. It only normalizes
labels into coarse families and fine sub-technique keys; it deliberately avoids
difficulty scoring, chain folding, service orchestration, and web concerns.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

LOGGER = logging.getLogger(__name__)


class TechniqueFamily(StrEnum):
    AUTH = "auth"
    INJECTION = "injection"
    SERVER_SIDE = "server_side"
    CLIENT_SIDE = "client_side"
    UPLOAD = "upload"
    NODE_API = "node_api"
    STACK = "stack"
    FORMAT_STRING = "format_string"
    HEAP = "heap"
    INTEGER_OOB = "integer_oob"
    SANDBOX = "sandbox"
    KERNEL = "kernel"
    CRACKME = "crackme"
    VM_BYTECODE = "vm_bytecode"
    RUNTIME = "runtime"
    LANGUAGE = "language"
    PLATFORM = "platform"
    VISUAL_GAME = "visual_game"
    OTHER = "other"


CATEGORY_TECHNIQUE_FAMILIES: dict[str, tuple[str, ...]] = {
    "web": (
        TechniqueFamily.AUTH,
        TechniqueFamily.INJECTION,
        TechniqueFamily.SERVER_SIDE,
        TechniqueFamily.CLIENT_SIDE,
        TechniqueFamily.UPLOAD,
        TechniqueFamily.NODE_API,
        TechniqueFamily.OTHER,
    ),
    "pwn": (
        TechniqueFamily.STACK,
        TechniqueFamily.FORMAT_STRING,
        TechniqueFamily.HEAP,
        TechniqueFamily.INTEGER_OOB,
        TechniqueFamily.SANDBOX,
        TechniqueFamily.KERNEL,
        TechniqueFamily.OTHER,
    ),
    "re": (
        TechniqueFamily.CRACKME,
        TechniqueFamily.VM_BYTECODE,
        TechniqueFamily.RUNTIME,
        TechniqueFamily.LANGUAGE,
        TechniqueFamily.PLATFORM,
        TechniqueFamily.VISUAL_GAME,
        TechniqueFamily.OTHER,
    ),
}
CATEGORY_TECHNIQUE_FAMILIES = {
    category: tuple(str(family) for family in families)
    for category, families in CATEGORY_TECHNIQUE_FAMILIES.items()
}

_ALL_FAMILIES: frozenset[str] = frozenset(
    family
    for families in CATEGORY_TECHNIQUE_FAMILIES.values()
    for family in families
)

_SEPARATOR_RE = re.compile(r"[\s_-]+")
_WORD_RE = re.compile(r"\b[\w+/.#]+\b")
_QUALIFIER_TOKENS: frozenset[str] = frozenset(
    {
        "decode",
        "decoding",
        "decrypt",
        "decryption",
        "encrypt",
        "encryption",
        "cipher",
        "attack",
        "technique",
        "vuln",
        "bug",
    }
)

SUB_TECHNIQUE_ALIASES: dict[str, str] = {
    "boolean blind sqli": "sqli",
    "cross site scripting": "xss",
    "dom xss": "xss",
    "reflected xss": "xss",
    "stored xss": "xss",
    "path traversal": "path traversal",
    "prototype pollution": "prototype pollution",
    "server side template injection": "ssti",
    "ssti": "ssti",
    "sql inj": "sqli",
    "sql injection": "sqli",
    "blind sqli": "sqli",
    "second order sqli": "sqli",
    "buffer overflow": "ret2libc",
    "stack overflow": "ret2libc",
    "canary leak": "ret2libc",
    "canary leak then buffer overflow": "ret2libc",
    "stack canary leak": "ret2libc",
    "rop": "ret2libc",
    "rop chain": "ret2libc",
    "return oriented programming": "ret2libc",
    "libc leak": "ret2libc",
    "glibc leak": "ret2libc",
    "libc base leak": "ret2libc",
    "ret2plt": "ret2libc",
    "return to plt": "ret2libc",
    "stack pivot": "stack_pivot",
    "stack pivot with leave ret gadget": "stack_pivot",
    "ret2csu flow": "ret2csu",
    "ret2csu": "ret2csu",
    "ret2dlresolve": "ret2dlresolve",
    "one gadget": "ret2libc",
    "one_gadget": "ret2libc",
    "glibc heap": "heap_uaf_tcache",
    "heap": "heap_uaf_tcache",
    "heap exploitation": "heap_uaf_tcache",
    "heap overflow": "heap_uaf_tcache",
    "fastbin": "heap_uaf_tcache",
    "fastbin attack": "heap_uaf_tcache",
    "fastbin dup": "heap_uaf_tcache",
    "unsorted bin": "heap_uaf_tcache",
    "unsorted bin attack": "heap_uaf_tcache",
    "unlink attack": "heap_uaf_tcache",
    "tcache poisoning": "heap_uaf_tcache",
    "tcache dup": "heap_uaf_tcache",
    "use after free": "heap_uaf_tcache",
    "use after free primitive": "heap_uaf_tcache",
    "uaf": "heap_uaf_tcache",
    "format string": "format_string_got",
    "bss variable modification": "global_bss_write",
    "bss variable write": "global_bss_write",
    "bss overwrite": "global_bss_write",
    "global variable modification": "global_bss_write",
    "global variable write": "global_bss_write",
    "global overwrite": "global_bss_write",
    "anti debug": "anti_debug",
    "anti debugging": "anti_debug",
    "bytecode vm": "bytecode_vm",
    "vm bytecode": "bytecode_vm",
}

FAMILY_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "web": {
        "auth": (
            "auth",
            "authorization",
            "authentication",
            "idor",
            "jwt",
            "oauth",
            "oidc",
            "saml",
            "session",
            "login bypass",
            "role check",
        ),
        "injection": (
            "sqli",
            "sql injection",
            "nosql",
            "operator injection",
            "command injection",
            "template injection",
            "ssti",
            "xpath injection",
            "ldap injection",
        ),
        "server_side": (
            "ssrf",
            "lfi",
            "rfi",
            "path traversal",
            "deserialization",
            "request smuggling",
            "archive parser",
            "server side",
        ),
        "client_side": (
            "xss",
            "dom",
            "csp",
            "postmessage",
            "xs leak",
            "browser bot",
            "cache poisoning",
            "client side",
        ),
        "upload": (
            "upload",
            "export",
            "polyglot",
            "pdf renderer",
            "extension bypass",
            "file read",
        ),
        "node_api": (
            "node",
            "api",
            "prototype pollution",
            "sandbox escape",
            "express",
            "npm",
        ),
    },
    "pwn": {
        "stack": (
            "stack",
            "ret2win",
            "ret2libc",
            "canary",
            "rop",
            "srop",
            "gadget",
        ),
        "format_string": (
            "format string",
            "printf",
            "got overwrite",
            "partial write",
            "stack offset",
            "offset determination",
            "byte by byte leak",
            "pointer leak",
            "libc base leak",
            "canary leak",
            "return address overwrite",
        ),
        "heap": ("heap", "uaf", "use after free", "tcache", "fsop", "allocator"),
        "integer_oob": (
            "integer",
            "signedness",
            "oob",
            "out of bounds",
            "bounds",
            "overflow",
            "bss",
            "global variable",
            "global write",
        ),
        "sandbox": ("sandbox", "seccomp", "restricted shell", "vm escape", "syscall filter"),
        "kernel": ("kernel", "kaslr", "krop", "cross cache", "namespace"),
    },
    "re": {
        "crackme": (
            "crackme",
            "strings",
            "xor",
            "layered transform",
            "layered encoding",
            "symbolic constraint",
            "oracle",
        ),
        "vm_bytecode": ("vm", "bytecode", "instruction set", "lifting", "opcode"),
        "runtime": (
            "ltrace",
            "strace",
            "hook",
            "memory dump",
            "anti debug",
            "anti-debug",
            "signal handler",
            "timing",
            "jit",
        ),
        "language": ("python bytecode", ".net", "go", "rust", "swift", "packed", "packer"),
        "platform": ("wasm", "apk", "jni", "firmware", "driver", "architecture"),
        "visual_game": ("visual", "game", "asset", "shader", "game state", "custom format"),
    },
}

FAMILY_DERIVATION_ORDER: dict[str, tuple[str, ...]] = {
    "web": (
        "injection",
        "server_side",
        "client_side",
        "upload",
        "node_api",
        "auth",
    ),
    "pwn": (
        "format_string",
        "stack",
        "heap",
        "integer_oob",
        "sandbox",
        "kernel",
    ),
    "re": CATEGORY_TECHNIQUE_FAMILIES["re"][:-1],
}

FAMILY_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "web": {
        "auth": "Auth",
        "injection": "Injection",
        "server_side": "Server-side",
        "client_side": "Client-side",
        "upload": "Upload/export",
        "node_api": "Node/API",
        "other": "Other",
    },
    "pwn": {
        "stack": "Stack",
        "format_string": "Format string",
        "heap": "Heap",
        "integer_oob": "Integer/OOB",
        "sandbox": "Sandbox",
        "kernel": "Kernel",
        "other": "Other",
    },
    "re": {
        "crackme": "Crackme",
        "vm_bytecode": "VM/bytecode",
        "runtime": "Runtime",
        "language": "Language",
        "platform": "Platform",
        "visual_game": "Visual/game",
        "other": "Other",
    },
}


def families_for_category(category: str) -> tuple[str, ...]:
    """Return the closed family lane set for a category."""

    return CATEGORY_TECHNIQUE_FAMILIES.get(category, (str(TechniqueFamily.OTHER),))


def render_family_vocabulary(category: str) -> str:
    """Render the category vocabulary for prompt injection."""

    lanes = families_for_category(category)
    display_names = FAMILY_DISPLAY_NAMES.get(category, {"other": "Other"})
    rendered = [
        f"- `{family}` — {display_names.get(family, family.replace('_', ' ').title())}"
        for family in lanes
    ]
    return "\n".join(rendered)


def resolve_family(finding: Any, category: str | None = None) -> str:
    """Resolve a finding's effective technique family.

    Stored valid values win. Unknown stored values are accepted but coerced to
    ``other`` with a warning. If no usable stored value exists, the label is
    matched against the category keyword map and then falls back to ``other``.
    """

    valid_families = frozenset(families_for_category(category)) if category else _ALL_FAMILIES
    stored_family = _read_field(finding, "technique_family")
    if stored_family is not None:
        normalized = _normalize_family_value(stored_family)
        if normalized in valid_families:
            return normalized
        LOGGER.warning(
            "unknown technique_family %r for category %r; using other",
            stored_family,
            category,
        )
        return str(TechniqueFamily.OTHER)

    label_key = _canonical_label(_read_field(finding, "label") or "")
    if not label_key:
        return str(TechniqueFamily.OTHER)

    category_maps = (
        ((category, FAMILY_KEYWORDS.get(category, {})),)
        if category
        else FAMILY_KEYWORDS.items()
    )
    for _category, family_keywords in category_maps:
        family_order = FAMILY_DERIVATION_ORDER.get(_category, tuple(family_keywords))
        for family in family_order:
            keywords = family_keywords.get(family, ())
            if any(_keyword_matches(label_key, keyword) for keyword in keywords):
                return family
    return str(TechniqueFamily.OTHER)


def resolve_sub_technique(finding: Any) -> str:
    """Resolve a finding label to a conservative canonical sub-technique key."""

    label = _canonical_label(_read_field(finding, "label") or "")
    if not label:
        return "unknown"
    tokens = [token for token in _WORD_RE.findall(label) if token not in _QUALIFIER_TOKENS]
    key = " ".join(tokens).strip()
    if not key:
        key = label
    key = SUB_TECHNIQUE_ALIASES.get(key, key)
    if key != label:
        LOGGER.warning("normalized sub_technique raw=%r normalized=%r", label, key)
    return key


def _read_field(finding: Any, key: str) -> Any:
    if isinstance(finding, Mapping):
        return finding.get(key)
    return getattr(finding, key, None)


def _normalize_family_value(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _canonical_label(label: str) -> str:
    lowered = str(label).strip().lower()
    return _SEPARATOR_RE.sub(" ", lowered).strip()


def _keyword_matches(label: str, keyword: str) -> bool:
    normalized_keyword = _canonical_label(keyword)
    if not normalized_keyword:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)", label) is not None
