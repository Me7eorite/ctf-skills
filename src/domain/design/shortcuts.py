"""Per-category collapse shortcuts — the generic "X" that challenges funnel to.

These are the universal cheap solves that make a nominal concept decorative:
RE collapses to static XOR / direct run, Web to weak-creds / source leak /
direct flag route, PWN to win-function / direct shellcode. A design whose
DECLARED ``actual_solution_type`` is itself one of these is collapsed by
definition and must be rejected at design time; the runtime probes that prove
these shortcuts actually fail are a separate (build-host) concern.
"""

from __future__ import annotations

CATEGORY_FORBIDDEN_SHORTCUTS: dict[str, tuple[str, ...]] = {
    "re": (
        "static_xor_decrypt",
        "strings_plaintext_flag",
        "direct_run_get_flag",
        "hardcoded_license",
        "solver_hardcoded_enc_and_key",
        "patch_single_jump",
    ),
    "web": (
        "exposed_flag_route",
        "source_code_leak",
        "backup_file_leak",
        "default_credentials",
        "weak_password",
        "unrelated_sqli",
        "unrelated_lfi",
        "debug_endpoint_leak",
        "direct_db_flag",
        "admin_cookie_flip",
        "path_traversal_to_flag",
    ),
    "pwn": (
        "unintended_win_function",
        "direct_shellcode",
        "direct_stack_ret",
        "fmtstr_unrelated_leak",
        "reachable_flag_print",
        "one_gadget_shortcut",
        "no_aslr_when_required",
    ),
}


def normalize_token(value: str) -> str:
    """Canonicalize a free-text solution/shortcut label for comparison."""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def forbidden_shortcuts(category: str) -> frozenset[str]:
    return frozenset(CATEGORY_FORBIDDEN_SHORTCUTS.get(category, ()))


def is_forbidden_shortcut(category: str, solution_type: str) -> bool:
    """True if a declared solution type is itself a known collapse shortcut."""
    return normalize_token(solution_type) in forbidden_shortcuts(category)
