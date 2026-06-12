"""LLM provider settings: read/write Hermes config files and probe providers.

Mirrors the on-disk layout Hermes already consumes:

- ``<hermes_home>/config.yaml`` — provider / base_url / model under ``model:``.
- ``<hermes_home>/auth.json`` — API keys under ``credential_pool``.

The plain-text API key is never returned to callers; only the masked form is
ever exposed. Use :func:`load_secret_settings` internally when an actual key
is required (for instance, :func:`test_connection`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.paths import ProjectPaths

PROVIDER_CHOICES: tuple[str, ...] = ("anthropic", "openai", "glm", "custom")

DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "custom": "",
}

TEST_CONNECTION_TIMEOUT = 10.0


@dataclass(frozen=True)
class _ResolvedSettings:
    provider: str
    base_url: str
    model: str
    api_key: str  # plain text; never returned to API callers


def mask_api_key(api_key: str | None) -> str:
    """Return the public mask for an API key.

    Keys longer than 8 characters mask to ``<first-3>***<last-4>``; shorter
    keys collapse to a fixed ``*****``. Empty or missing keys return ``""``.
    """
    if not api_key:
        return ""
    if len(api_key) > 8:
        return f"{api_key[:3]}***{api_key[-4:]}"
    return "*****"


def _config_path(paths: ProjectPaths) -> Path:
    return paths.hermes_home / "config.yaml"


def _auth_path(paths: ProjectPaths) -> Path:
    return paths.hermes_home / "auth.json"


def _read_config(paths: ProjectPaths) -> dict[str, Any]:
    path = _config_path(paths)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_config(paths: ProjectPaths, data: dict[str, Any]) -> None:
    path = _config_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    path.write_text(serialised, encoding="utf-8")


def _read_auth(paths: ProjectPaths) -> dict[str, Any]:
    path = _auth_path(paths)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_auth(paths: ProjectPaths, data: dict[str, Any]) -> None:
    path = _auth_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _resolve(paths: ProjectPaths) -> _ResolvedSettings:
    config = _read_config(paths)
    model_block = config.get("model") if isinstance(config.get("model"), dict) else {}
    provider = str(model_block.get("provider", "")).strip() or ""
    base_url = str(model_block.get("base_url", "")).strip()
    model = str(model_block.get("model", "")).strip()

    api_key = ""
    if provider == "custom":
        api_key = str(model_block.get("api_key", "")).strip()
    if not api_key:
        pool = _read_auth(paths).get("credential_pool")
        if isinstance(pool, dict) and provider:
            candidate = pool.get(provider)
            if isinstance(candidate, str):
                api_key = candidate

    return _ResolvedSettings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def load_secret_settings(paths: ProjectPaths) -> dict[str, str]:
    """Return resolved settings including the plain-text key. Internal use only."""
    resolved = _resolve(paths)
    return {
        "provider": resolved.provider,
        "base_url": resolved.base_url,
        "model": resolved.model,
        "api_key": resolved.api_key,
    }


def load_settings(paths: ProjectPaths) -> dict[str, str]:
    """Return the operator-safe view of current settings (no plain-text key)."""
    resolved = _resolve(paths)
    return {
        "provider": resolved.provider,
        "base_url": resolved.base_url,
        "model": resolved.model,
        "api_key_masked": mask_api_key(resolved.api_key),
    }


def save_settings(paths: ProjectPaths, payload: dict[str, Any]) -> dict[str, str]:
    """Persist provider / base_url / model and (optionally) api_key.

    If ``payload['api_key']`` is missing, empty, or equal to the current mask,
    the stored key is left unchanged. Otherwise the new key replaces it.
    Unrelated keys in either file are preserved.
    """
    provider = str(payload.get("provider", "")).strip()
    if provider not in PROVIDER_CHOICES:
        raise ValueError(f"unsupported provider: {provider!r}")
    base_url = str(payload.get("base_url", "")).strip()
    model = str(payload.get("model", "")).strip()
    incoming_key = payload.get("api_key")
    incoming_key = "" if incoming_key is None else str(incoming_key)

    current = _resolve(paths)
    current_mask = mask_api_key(current.api_key)
    is_placeholder = (
        not incoming_key
        or (current_mask and incoming_key == current_mask)
    )
    effective_key = current.api_key if is_placeholder else incoming_key

    config = _read_config(paths)
    model_block = config.get("model")
    if not isinstance(model_block, dict):
        model_block = {}
    model_block["provider"] = provider
    model_block["base_url"] = base_url
    model_block["model"] = model
    if provider == "custom":
        if effective_key:
            model_block["api_key"] = effective_key
        else:
            model_block.pop("api_key", None)
    else:
        model_block.pop("api_key", None)
    config["model"] = model_block
    _write_config(paths, config)

    auth = _read_auth(paths)
    pool = auth.get("credential_pool")
    if not isinstance(pool, dict):
        pool = {}
    if provider != "custom":
        if effective_key:
            pool[provider] = effective_key
    auth["credential_pool"] = pool
    _write_auth(paths, auth)

    return load_settings(paths)


def _scrub(message: str, *secrets: str) -> str:
    redacted = message
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = TEST_CONNECTION_TIMEOUT,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    for header_name, header_value in (headers or {}).items():
        request.add_header(header_name, header_value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def test_connection(paths: ProjectPaths) -> dict[str, Any]:
    """Perform a minimal probe against the configured provider.

    Returns ``{ok, latency_ms, model, error}``. ``error`` and any caller-visible
    string is scrubbed of the stored API key substring.
    """
    settings = _resolve(paths)
    if not settings.provider:
        return {"ok": False, "latency_ms": 0, "model": settings.model, "error": "no provider configured"}
    if not settings.api_key:
        return {"ok": False, "latency_ms": 0, "model": settings.model, "error": "no api key configured"}

    base_url = settings.base_url or DEFAULT_BASE_URLS.get(settings.provider, "")
    started = time.monotonic()
    try:
        if settings.provider == "anthropic":
            status, _body = _http_request(
                f"{base_url.rstrip('/')}/v1/messages",
                method="POST",
                headers={
                    "x-api-key": settings.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                body=json.dumps(
                    {
                        "model": settings.model or "claude-3-5-haiku-latest",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    }
                ).encode("utf-8"),
            )
        else:
            if not base_url:
                return {
                    "ok": False,
                    "latency_ms": 0,
                    "model": settings.model,
                    "error": "no base_url configured",
                }
            status, _body = _http_request(
                f"{base_url.rstrip('/')}/v1/models",
                method="GET",
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "latency_ms": elapsed_ms,
            "model": settings.model,
            "error": _scrub(f"HTTP {exc.code}", settings.api_key),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "latency_ms": elapsed_ms,
            "model": settings.model,
            "error": _scrub(str(exc) or exc.__class__.__name__, settings.api_key),
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": 200 <= status < 300,
        "latency_ms": elapsed_ms,
        "model": settings.model,
        "error": None if 200 <= status < 300 else f"HTTP {status}",
    }
