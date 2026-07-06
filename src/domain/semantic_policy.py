"""Policy-driven semantic consistency checks for generated challenges."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    text: str
    supports_required_evidence: bool = True


@dataclass(frozen=True)
class SemanticFamilyPolicy:
    name: str
    aliases: tuple[str, ...]
    forbidden_terms: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    required_evidence_terms: tuple[str, ...] = ()
    required_evidence_patterns: tuple[str, ...] = ()
    allowed_overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticCategoryPolicy:
    category: str
    families: tuple[SemanticFamilyPolicy, ...]


@dataclass(frozen=True)
class SemanticViolation:
    category: str
    declared_family: str
    code: str
    token: str
    source: str
    message: str
    observed_family: str = ""
    repair_action: str = ""

    def to_detail(self) -> dict[str, str]:
        detail = {
            "phase": "contract",
            "code": self.code,
            "status": "contract_failed",
            "message": self.message,
            "path": self.source,
            "semantic_category": self.category,
            "declared_family": self.declared_family,
            "conflict_token": self.token,
            "source": self.source,
            "hint": "Align metadata technique declarations with the final artifact evidence.",
        }
        if self.observed_family:
            detail["observed_family"] = self.observed_family
        if self.repair_action:
            detail["repair_action"] = self.repair_action
            detail["hint"] = self.repair_action
        return detail


_RET2WIN_FORBIDDEN_TERMS = (
    "ret2win",
    "rop_win",
    "win_func",
    "winfunction",
    "read_flag",
    "print_flag",
)
_RET2WIN_FORBIDDEN_PATTERNS = (
    r"\bwin\s*\(",
    r"\bread_flag\s*\(",
    r"\bprint_flag\s*\(",
)
_STRICT_RET2WIN_OVERRIDE = ("allow_ret2win",)


PWN_POLICY = SemanticCategoryPolicy(
    category="pwn",
    families=(
        SemanticFamilyPolicy(
            name="ret2libc",
            aliases=("ret2libc", "ret2libc_leak", "libc leak", "libc_base"),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(r"\b(?:libc\.address|libc_base|system|execve|/bin/sh)\b",),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="stack_canary_leak",
            aliases=(
                "stack_canary_leak",
                "stack canary leak",
                "canary_leak",
                "canary leak",
                "stack_canary_leak_via_print",
            ),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_terms=("canary", "leak"),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="stack_canary_fork_bruteforce",
            aliases=(
                "stack_canary_fork_bruteforce",
                "stack_canary_fork_brute_force",
                "fork brute force canary",
                "fork canary brute force",
                "fork bruteforce canary",
            ),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(
                r"\b(?:fork|forkserver|persistent child|child process|per-connection)\b",
                r"\b(?:oracle|crash|no-crash|stack smashing|connection reset|eof)\b",
                r"\b(?:byte[- ]by[- ]byte|byte_index|candidate byte|for\s+.*range\s*\(\s*256)\b",
                r"\b(?:flag\{|final_flag|extract\w*\s+flag|recv\w*\s+flag|print\w*\s*\([^)]*flag)\b",
            ),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="stack_canary_format_string",
            aliases=(
                "stack_canary_format_string",
                "stack canary format string",
                "format string canary",
                "format-string canary",
            ),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(r"\bcanary\b", r"%(?:\d+\$)?[px]"),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="srop",
            aliases=("srop", "sigreturn", "sigreturn oriented programming"),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(
                r"\b(?:SigreturnFrame|sigreturn|rt_sigreturn|SYS_rt_sigreturn|syscall)\b",
            ),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="orw",
            aliases=("orw", "open read write", "open/read/write"),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(r"\bopen(?:at)?\b", r"\bread\b", r"\bwrite\b"),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="got_overwrite",
            aliases=("got_overwrite", "got overwrite", "got hijack"),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(r"\b(?:got|GOT|write_primitive|overwrite)\b",),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
        SemanticFamilyPolicy(
            name="ret2win",
            aliases=("ret2win", "direct_win", "direct win", "win function", "rop_win"),
        ),
        SemanticFamilyPolicy(
            name="rop_chain",
            aliases=("rop_chain", "rop chain", "rop_chain_construction", "rop construction"),
            forbidden_terms=_RET2WIN_FORBIDDEN_TERMS,
            forbidden_patterns=_RET2WIN_FORBIDDEN_PATTERNS,
            required_evidence_patterns=(r"\b(?:ROP|rop|pop_rdi|pop rdi|gadget|chain)\b",),
            allowed_overrides=_STRICT_RET2WIN_OVERRIDE,
        ),
    ),
)


WEB_POLICY = SemanticCategoryPolicy(
    category="web",
    families=(
        SemanticFamilyPolicy(
            name="sql_injection",
            aliases=("sql injection", "sqli", "boolean blind sqli", "union sqli"),
            forbidden_terms=("hardcoded_admin_password", "static_admin_bypass"),
            required_evidence_patterns=(r"\b(?:select|union|where|sql)\b",),
        ),
    ),
)


SEMANTIC_POLICIES: dict[str, SemanticCategoryPolicy] = {
    policy.category: policy for policy in (PWN_POLICY, WEB_POLICY)
}


_TECHNIQUE_METADATA_FIELDS = (
    "primary_technique",
    "secondary_technique",
    "actual_solution_type",
    "learning_objective",
    "description",
)
_TEXT_EXTENSIONS = {
    "",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".s",
    ".S",
    ".asm",
    ".py",
    ".sh",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
}


def semantic_contract_error_messages(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    *,
    validation_result: Mapping[str, Any] | None = None,
) -> list[str]:
    return [
        violation.message
        for violation in semantic_contract_violations(
            challenge_dir,
            metadata,
            validation_result=validation_result,
        )
    ]


def semantic_contract_failure_details(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    *,
    validation_result: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    return [
        violation.to_detail()
        for violation in semantic_contract_violations(
            challenge_dir,
            metadata,
            validation_result=validation_result,
        )
    ]


def semantic_contract_violations(
    challenge_dir: Path,
    metadata: Mapping[str, Any],
    *,
    validation_result: Mapping[str, Any] | None = None,
) -> list[SemanticViolation]:
    category = str(metadata.get("category") or "")
    if category == "pwn" and not _strict_semantic_policy_requested(metadata):
        return []
    policy = SEMANTIC_POLICIES.get(category)
    if policy is None:
        return []

    declared = _declared_families(policy, metadata)
    if not declared:
        return []

    sources = collect_semantic_evidence(
        challenge_dir,
        metadata=metadata,
        validation_result=validation_result,
    )
    declared_names = {family.name for family in declared}
    violations: list[SemanticViolation] = []
    seen: set[tuple[str, str, str, str]] = set()

    for family in declared:
        if not _family_override_applies(family, metadata, declared_names):
            for source in sources:
                lower = source.text.lower()
                for term in family.forbidden_terms:
                    if term.lower() in lower:
                        key = (family.name, "forbidden_term", term, source.name)
                        if key not in seen:
                            seen.add(key)
                            violations.append(
                                _violation(
                                    category,
                                    family.name,
                                    "semantic_forbidden_term",
                                    term,
                                    source.name,
                                )
                            )
                for pattern in family.forbidden_patterns:
                    if re.search(pattern, source.text, re.IGNORECASE | re.MULTILINE):
                        key = (family.name, "forbidden_pattern", pattern, source.name)
                        if key not in seen:
                            seen.add(key)
                            violations.append(
                                _violation(
                                    category,
                                    family.name,
                                    "semantic_forbidden_pattern",
                                    pattern,
                                    source.name,
                                )
                            )

        required_sources = [source for source in sources if source.supports_required_evidence]
        for term in family.required_evidence_terms:
            if not any(term.lower() in source.text.lower() for source in required_sources):
                violations.append(
                    _missing_evidence_violation(category, family.name, f"term:{term}")
                )
        for pattern in family.required_evidence_patterns:
            if not any(
                re.search(pattern, source.text, re.IGNORECASE | re.MULTILINE)
                for source in required_sources
            ):
                violations.append(
                    _missing_evidence_violation(category, family.name, f"pattern:{pattern}")
                )
    return violations


def _strict_semantic_policy_requested(metadata: Mapping[str, Any]) -> bool:
    return bool(
        metadata.get("strict_semantic_contract")
        or metadata.get("enforce_declared_technique")
        or metadata.get("validate_declared_technique")
    )


def collect_semantic_evidence(
    challenge_dir: Path,
    *,
    metadata: Mapping[str, Any],
    validation_result: Mapping[str, Any] | None = None,
) -> list[EvidenceSource]:
    sources: list[EvidenceSource] = [
        EvidenceSource(
            "metadata.json",
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            supports_required_evidence=False,
        )
    ]
    for relative in (
        "challenge.yml",
        "README.md",
        "writenup/wp.md",
        "writenup/exp.py",
        "logs/report.json",
        "logs/repair-summary.md",
    ):
        _append_text_source(sources, challenge_dir / relative, relative)
    deploy_src = challenge_dir / "deploy" / "src"
    if deploy_src.is_dir() and not deploy_src.is_symlink():
        for path in sorted(deploy_src.rglob("*")):
            if path.is_file() and not path.is_symlink():
                rel = path.relative_to(challenge_dir).as_posix()
                _append_text_source(sources, path, rel)
    if validation_result is not None:
        candidate = validation_result.get("final_flag_candidate")
        if isinstance(candidate, str) and candidate:
            sources.append(EvidenceSource("validation_result.final_flag_candidate", candidate))
    return sources


def _append_text_source(sources: list[EvidenceSource], path: Path, name: str) -> None:
    if path.suffix not in _TEXT_EXTENSIONS:
        return
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if b"\0" in raw[:4096]:
        return
    text = raw.decode("utf-8", errors="replace")
    if text.strip():
        sources.append(EvidenceSource(name, text))


def _declared_families(
    policy: SemanticCategoryPolicy,
    metadata: Mapping[str, Any],
) -> list[SemanticFamilyPolicy]:
    text = _declared_technique_text(metadata)
    if not text:
        return []
    declared: list[SemanticFamilyPolicy] = []
    for family in policy.families:
        if any(_alias_in_text(alias, text) for alias in family.aliases):
            declared.append(family)
    return declared


def _declared_technique_text(metadata: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in _TECHNIQUE_METADATA_FIELDS:
        value = metadata.get(key)
        if isinstance(value, str):
            parts.append(value)
    techniques = metadata.get("techniques")
    if isinstance(techniques, Sequence) and not isinstance(techniques, (str, bytes)):
        parts.extend(str(item) for item in techniques if item)
    return " ".join(parts).lower()


def _alias_in_text(alias: str, text: str) -> bool:
    normalized_text = re.sub(r"[_\-/]+", " ", text.lower())
    normalized_alias = re.sub(r"[_\-/]+", " ", alias.lower()).strip()
    if not normalized_alias:
        return False
    return normalized_alias in normalized_text


def _family_override_applies(
    family: SemanticFamilyPolicy,
    metadata: Mapping[str, Any],
    declared_names: set[str],
) -> bool:
    if not family.allowed_overrides:
        return False
    if "ret2win" not in declared_names:
        return False
    return any(_truthy(metadata.get(name)) for name in family.allowed_overrides)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value == 1


def _violation(
    category: str,
    family: str,
    code: str,
    token: str,
    source: str,
) -> SemanticViolation:
    return SemanticViolation(
        category=category,
        declared_family=family,
        code=code,
        token=token,
        source=source,
        observed_family=_observed_family_for_token(token),
        repair_action=_repair_action_for_token(family, token, source),
        message=(
            "semantic contract failed: "
            f"category={category} declared_family={family} "
            f"conflict_token={token!r} source={source}"
        ),
    )


def _missing_evidence_violation(
    category: str,
    family: str,
    token: str,
) -> SemanticViolation:
    return SemanticViolation(
        category=category,
        declared_family=family,
        code="semantic_required_evidence_missing",
        token=token,
        source="semantic_evidence",
        repair_action=(
            f"Add concrete {family} evidence to the reference solve and writeup, "
            "or correct metadata if the declared family is wrong. Do not replace "
            "the challenge with an easier shortcut."
        ),
        message=(
            "semantic contract failed: "
            f"category={category} declared_family={family} "
            f"missing_required_evidence={token!r} source=semantic_evidence"
        ),
    )


def _observed_family_for_token(token: str) -> str:
    lowered = token.lower()
    if any(item in lowered for item in ("ret2win", "rop_win", "win", "read_flag", "print_flag")):
        return "ret2win"
    if "hardcoded_admin_password" in lowered or "static_admin_bypass" in lowered:
        return "static_bypass"
    return ""


def _repair_action_for_token(family: str, token: str, source: str) -> str:
    observed = _observed_family_for_token(token)
    if observed == "ret2win":
        return (
            f"`{source}` contains `{token}`, which indicates a ret2win/direct-win "
            f"shortcut but metadata declares `{family}`. Remove the shortcut from "
            "source/solver/writeup/report artifacts and implement the declared "
            f"`{family}` path, or explicitly declare ret2win with allow_ret2win=true "
            "only if that is the intended design."
        )
    if observed == "static_bypass":
        return (
            f"`{source}` contains `{token}`, which bypasses the declared `{family}` "
            "web technique. Replace the shortcut with evidence that the solver "
            "uses the declared web vulnerability path."
        )
    return (
        f"`{source}` contains `{token}`, which conflicts with declared family "
        f"`{family}`. Remove or rewrite that artifact so all final evidence "
        "matches the declared technique."
    )
