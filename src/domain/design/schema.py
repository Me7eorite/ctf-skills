"""Schema constants, dataclasses, and exceptions for challenge design validation.

Phase 3 split this out of the 896-line ``domain.challenge_design_validators``
module so that the schema lives in one place and the parser / validator /
quality gate can import only what they need.

The schema is intentionally **flat** — there is no SKILL.md → flat translation
layer. The agent is required to emit the field shape declared here directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

# ---------- Defaults ----------

DEFAULT_FLAG_FORMAT = "flag{...}"

# ---------- Length limits ----------

MAX_SUMMARY_CHARS = 280
MAX_IMPLEMENTATION_PLAN_CHARS = 4000
MAX_PLAN_STRING_CHARS = 500

# ---------- Artifact contracts ----------

COMMON_ARTIFACTS: tuple[str, ...] = (
    "README.md",
    "metadata.json",
    "validate.sh",
    "writenup/wp.md",
    "writenup/exp.py",
)

CONTAINER_BASE_ARTIFACTS: tuple[str, ...] = (
    "deploy/Dockerfile",
    "deploy/docker-compose.yml",
    "deploy/_files/start.sh",
)


@dataclass(frozen=True)
class RuntimeArtifactRule:
    """语言及运行配置的工件约束。"""

    required_exact: tuple[str, ...] = ()
    required_any_exact: tuple[tuple[str, ...], ...] = ()
    required_patterns: tuple[re.Pattern[str], ...] = ()
    required_any_patterns: tuple[tuple[re.Pattern[str], ...], ...] = ()
    required_service_user: str | None = None
    allowed_service_users: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeArtifactRequirements:
    """运行时声明。"""

    language: str | None
    profile: str | None
    service_user: str | None


def _pattern(expr: str) -> re.Pattern[str]:
    return re.compile(expr)


RUNTIME_ARTIFACT_RULES: dict[str, dict[str, RuntimeArtifactRule]] = {
    "python": {
        "default": RuntimeArtifactRule(
            required_exact=("deploy/src/app.py",),
        ),
    },
    "node": {
        "default": RuntimeArtifactRule(
            required_any_exact=(
                (
                    "deploy/src/server.js",
                    "deploy/src/app.js",
                    "deploy/src/index.js",
                ),
            ),
            required_exact=("deploy/src/package.json",),
            required_service_user="ctf",
        ),
    },
    "php": {
        "default": RuntimeArtifactRule(
            required_exact=("deploy/src/index.php",),
            allowed_service_users=("www-data", "apache", "ctf"),
        ),
    },
    "go": {
        "default": RuntimeArtifactRule(
            required_exact=("deploy/src/main.go",),
            required_service_user="ctf",
        ),
    },
    "rust": {
        "default": RuntimeArtifactRule(
            required_exact=("deploy/src/Cargo.toml",),
            required_any_exact=(
                (
                    "deploy/src/src/main.rs",
                    "deploy/src/main.rs",
                ),
            ),
            required_service_user="ctf",
        ),
    },
    "java": {
        "jar": RuntimeArtifactRule(
            required_patterns=(
                _pattern(r"^deploy/src/(?:src/main/java/.*\.java|Main\.java)$"),
            ),
            required_any_exact=(
                (
                    "deploy/_files/build.sh",
                    "deploy/_files/pom.xml",
                    "deploy/_files/build.gradle",
                ),
            ),
            required_service_user="ctf",
        ),
        "tomcat": RuntimeArtifactRule(
            required_exact=("deploy/src/src/main/webapp/WEB-INF/web.xml",),
            required_any_exact=(
                (
                    "deploy/_files/server.xml",
                    "deploy/_files/setup.sh",
                ),
            ),
            required_any_patterns=(
                (
                    _pattern(r"^deploy/src/src/main/java/.*Servlet\.java$"),
                    _pattern(r"^deploy/src/src/main/webapp/.*\.jsp$"),
                ),
            ),
            allowed_service_users=("tomcat",),
        ),
    },
    "pwn": {
        "binary": RuntimeArtifactRule(
            required_patterns=(
                _pattern(r"^deploy/src/(?:[A-Za-z0-9_.+/-]+)$"),
            ),
            required_any_patterns=(
                (
                    _pattern(
                        r"^deploy/src/(?:pwn|chal{1,2}|challenge|vuln|"
                        r"service|server)(?:\.[A-Za-z0-9_+.-]+)?$"
                    ),
                    _pattern(r"^deploy/src/bin/.*"),
                    _pattern(
                        r"^deploy/src/.*\.(?:c|cc|cpp|cxx|h|hpp|s|S|asm|rs|go)$"
                    ),
                    _pattern(r"^src/.*\.(?:c|cc|cpp|cxx|h|hpp|s|S|asm|rs|go)$"),
                ),
            ),
            required_any_exact=(
                (
                    "deploy/_files/start.sh",
                    "deploy/_files/entrypoint.sh",
                ),
            ),
            required_service_user="ctf",
        ),
        "kernel": RuntimeArtifactRule(
            required_any_exact=(
                (
                    "deploy/_files/run.sh",
                    "deploy/_files/start.sh",
                ),
            ),
            required_any_patterns=(
                (
                    _pattern(r"^deploy/src/.*\.(?:c|h|ko)$"),
                    _pattern(r"^deploy/src/(?:bzImage|vmlinux|initramfs\.cpio(?:\.gz)?)$"),
                    _pattern(r"^attachments/(?:bzImage|vmlinux|initramfs\.cpio(?:\.gz)?)$"),
                ),
            ),
            required_service_user="ctf",
        ),
        "xinetd": RuntimeArtifactRule(
            required_any_exact=(
                (
                    "deploy/_files/ctf.xinetd",
                    "deploy/_files/etc/xinetd.d/ctf",
                    "deploy/_files/etc/xinetd.d/chal",
                ),
            ),
            required_patterns=(
                _pattern(r"^deploy/src/(?:[A-Za-z0-9_.+/-]+)$"),
            ),
            required_service_user="ctf",
        ),
    },
}

DEFAULT_RUNTIME_LANGUAGE = "python"


def default_runtime_profile(language: str) -> str:
    profiles = RUNTIME_ARTIFACT_RULES.get(language)
    if not profiles:
        raise ValueError(f"unknown runtime language: {language}")
    return next(iter(profiles))


DEFAULT_RUNTIME_PROFILE = default_runtime_profile(DEFAULT_RUNTIME_LANGUAGE)


def default_container_artifacts() -> tuple[str, ...]:
    return (
        *CONTAINER_BASE_ARTIFACTS,
        *RUNTIME_ARTIFACT_RULES[DEFAULT_RUNTIME_LANGUAGE][DEFAULT_RUNTIME_PROFILE].required_exact,
    )


CONTAINER_ARTIFACTS: tuple[str, ...] = default_container_artifacts()


def resolve_runtime_artifact_rule(
    language: str | None,
    profile: str | None,
) -> RuntimeArtifactRule:
    """Return language/profile-specific artifact约束.

    未填写语言或 profile 时采用默认配置；未知语言按默认语言处理，
    未知 profile 则回落到该语言的默认 profile。
    """

    lang = (language or DEFAULT_RUNTIME_LANGUAGE).lower()
    profiles = RUNTIME_ARTIFACT_RULES.get(lang)
    if not profiles:
        lang = DEFAULT_RUNTIME_LANGUAGE
        profiles = RUNTIME_ARTIFACT_RULES[lang]
    prof = (profile or default_runtime_profile(lang)).lower()
    rule = profiles.get(prof)
    if rule is None:
        prof = default_runtime_profile(lang)
        rule = profiles[prof]
    return rule


RUNTIME_LANGUAGES: tuple[str, ...] = tuple(RUNTIME_ARTIFACT_RULES.keys())


def extract_runtime_requirements(challenge: Mapping[str, Any]) -> RuntimeArtifactRequirements:
    """从 challenge 中提取 language/profile 声明。"""

    runtime = challenge.get("implementation_plan") or {}
    language = None
    profile = None
    service_user = None
    if isinstance(runtime, Mapping):
        language = runtime.get("runtime_language") or runtime.get("language")
        profile = runtime.get("runtime_profile") or runtime.get("profile")
        service_user = runtime.get("service_user") or runtime.get("user")
        if not language:
            language = _infer_runtime_language(runtime.get("runtime"))
        if not profile:
            profile = _infer_runtime_profile(runtime.get("runtime"), runtime.get("framework"))
    language = challenge.get("language", language)
    language = challenge.get("runtime_language", language)
    profile = challenge.get("runtime_profile", profile)
    service_user = challenge.get("service_user", service_user)
    if not language:
        language = _infer_runtime_language(challenge.get("runtime"))
    if not profile:
        profile = _infer_runtime_profile(challenge.get("runtime"), challenge.get("framework"))
    category = str(challenge.get("category") or "").strip().lower()
    language, profile = _normalize_category_runtime(
        category=category,
        language=language,
        profile=profile,
        runtime=runtime,
        challenge=challenge,
    )
    return RuntimeArtifactRequirements(
        language=language if isinstance(language, str) else None,
        profile=profile if isinstance(profile, str) else None,
        service_user=service_user if isinstance(service_user, str) else None,
    )


def _normalize_category_runtime(
    *,
    category: str,
    language: Any,
    profile: Any,
    runtime: Any,
    challenge: Mapping[str, Any],
) -> tuple[Any, Any]:
    """Normalize category-specific runtime aliases before rule lookup."""

    if category != "pwn":
        return language, profile

    normalized_language = language.strip().lower() if isinstance(language, str) else ""
    native_aliases = {
        "",
        "pwn",
        "binary",
        "native",
        "c",
        "c11",
        "c17",
        "gcc",
        "clang",
        "cpp",
        "c++",
        "cxx",
        "g++",
        "asm",
        "assembly",
        "nasm",
        "rust",
        "rustc",
        "go",
        "golang",
        "zig",
    }
    if normalized_language in native_aliases:
        language = "pwn"

    normalized_profile = profile.strip().lower() if isinstance(profile, str) else ""
    if normalized_profile in {"", "default", "native", "binary", "tcp", "socat"}:
        profile = None
    if normalized_profile in {"xinetd_chroot", "xinetd-chroot", "chroot"}:
        profile = "xinetd"

    profile_text = " ".join(
        value.strip().lower()
        for value in (
            runtime.get("runtime") if isinstance(runtime, Mapping) else None,
            runtime.get("runtime_profile") if isinstance(runtime, Mapping) else None,
            runtime.get("service_model") if isinstance(runtime, Mapping) else None,
            runtime.get("framework") if isinstance(runtime, Mapping) else None,
            challenge.get("runtime"),
            challenge.get("target_format"),
            challenge.get("deployment"),
        )
        if isinstance(value, str)
    )
    if not profile and any(token in profile_text for token in ("kernel", "qemu", "bzimage", "initramfs")):
        profile = "kernel"
    if not profile and any(token in profile_text for token in ("xinetd", "chroot", "socket")):
        profile = "xinetd"
    if not profile:
        profile = "xinetd"

    return language, profile


def _infer_runtime_language(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    aliases = {
        "python": ("python", "flask", "fastapi", "django"),
        "node": ("node", "nodejs", "express", "fastify", "koa"),
        "php": ("php", "apache-php", "plain php", "slim", "laravel"),
        "java": ("java", "spring", "spring boot", "tomcat", "servlet", "jakarta"),
        "go": ("go", "golang", "gin", "fiber", "net/http"),
        "rust": ("rust", "axum", "actix", "actix web", "rocket"),
    }
    for language_name, prefixes in aliases.items():
        if any(normalized.startswith(prefix) for prefix in prefixes):
            return language_name
    return None


def _infer_runtime_profile(runtime: Any, framework: Any) -> str | None:
    text = " ".join(
        value.strip().lower()
        for value in (runtime, framework)
        if isinstance(value, str)
    )
    if "tomcat" in text or "servlet" in text or "jsp" in text:
        return "tomcat"
    if "jar" in text or "spring" in text:
        return "jar"
    return None

KNOWN_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "deploy/",
    "writenup/",
    "attachments/",
    "src/",
)

# ---------- Implementation-leakage guards ----------

# Top-level keys the design agent must NOT emit — they are build-phase
# artifacts. Detecting any of them in challenges[0] is a hard reject.
FORBIDDEN_IMPLEMENTATION_KEYS: frozenset[str] = frozenset(
    {
        "app_code",
        "compose_spec",
        "docker_compose",
        "dockerfile",
        "dockerfile_snippet",
        "exploit_code",
        "exploit_sketch",
        "files_content",
        "init_sql",
        "readme_body",
        "source_code",
        "writeup_body",
    }
)

# Substring markers that indicate the agent is smuggling code inside
# ``implementation_plan`` strings. Match is case-sensitive on purpose.
PLAN_CODE_MARKERS: tuple[str, ...] = (
    "```",
    "#!/bin/bash",
    "<?php",
    "CREATE TABLE",
    "FROM ",
    "RUN apt-get",
    "import requests",
    "services:",
)

# ---------- Required text fields (flat schema) ----------

REQUIRED_CHALLENGE_TEXT_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "category",
    "difficulty",
    "deployment",
    "primary_technique",
    "learning_objective",
    "prompt",
    "flag_location",
    "validation",
)

# ---------- URL detection ----------

URL_RE = re.compile(r"https?://", re.IGNORECASE)
HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>`)\]}]+", re.IGNORECASE)

# Loopback hosts the validator allows inside the ``validation`` string.
LOCAL_HTTP_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "host.docker.internal",
    }
)


# ---------- Exceptions ----------


class ChallengeDesignValidationError(ValueError):
    """Raised when the design agent's JSON output violates the schema."""


# ---------- Validated payload ----------


@dataclass(frozen=True)
class ValidatedDesignPayload:
    """A normalized, validated design ready for persistence.

    ``payload`` is the entire ``{event, challenges}`` object as returned to
    callers; ``challenge`` is the single challenge entry inside it.
    """

    payload: dict[str, Any]
    challenge: dict[str, Any]
    summary: str
    flag_format: str
    validation_notes: str
