#!/usr/bin/env python3
"""Create a project-local HERMES_HOME with terminal.backend forced to local.

Hermes' default backend is whatever the user picked globally (often docker
or modal). When it is a remote backend, files the agent "writes" end up
inside the sandbox container instead of on the host, and our generated
challenges never land in ``work/challenges/``.

To avoid touching the user's global config, this script builds a
project-local Hermes home at ``.hermes/`` that:

  - copies ``config.yaml`` from ``~/.hermes/`` and patches
    ``terminal.backend`` to ``local``;
  - symlinks the rest of the user's hermes home (auth.json, .env, skills,
    sessions, caches, ...) so credentials and providers keep working.

Run once before invoking the runner. The runner exports
``HERMES_HOME=<project>/.hermes`` automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_HOME = ROOT / ".hermes"

# Files to copy (we own these locally).
COPY_FILES = {"config.yaml"}

# Files / directories to symlink so credentials, providers, caches and
# skills keep working without duplication. Anything not listed is left
# untouched.
LINK_ENTRIES = [
    "auth.json",
    "shared",
    "skills",
    "memories",
    "models_dev_cache.json",
    "provider_models_cache.json",
    "ollama_cloud_models_cache.json",
    "cache",
    "SOUL.md",
    "bin",
]


def patch_backend_to_local(text: str) -> str:
    """Force terminal.backend to local without disturbing other keys."""

    def repl(match: re.Match) -> str:
        head = match.group(1)
        return f"{head}local"

    pattern = re.compile(
        r"(^terminal:\n(?:[ \t]+.*\n)*?[ \t]+backend:\s*)\w+",
        re.MULTILINE,
    )
    new_text, count = pattern.subn(repl, text)
    if count == 0:
        # No terminal block at all — append one.
        new_text = text.rstrip() + "\nterminal:\n  backend: local\n"
    return new_text


def link_or_copy(src: Path, dst: Path) -> str:
    if dst.exists() or dst.is_symlink():
        dst.unlink() if dst.is_symlink() or dst.is_file() else shutil.rmtree(dst)
    if not src.exists():
        return f"skip {src.name} (not in user home)"
    try:
        os.symlink(src, dst)
        return f"link {dst.name} -> {src}"
    except OSError as exc:
        # Fall back to copy if symlinks are not permitted.
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return f"copy {dst.name} ({exc.__class__.__name__})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user-home",
        type=Path,
        default=Path.home() / ".hermes",
        help="Source hermes home to mirror (default: ~/.hermes)",
    )
    parser.add_argument("--force", action="store_true", help="rebuild from scratch")
    args = parser.parse_args()

    if not args.user_home.exists():
        raise SystemExit(
            f"{args.user_home} does not exist. "
            "Run `hermes setup` first or pass --user-home."
        )

    if args.force and PROJECT_HOME.exists():
        shutil.rmtree(PROJECT_HOME)
    PROJECT_HOME.mkdir(parents=True, exist_ok=True)

    # 0. write project-local .env. We do NOT symlink the user's because it
    # commonly contains TERMINAL_ENV=docker, which silently overrides the
    # backend regardless of config.yaml.
    src_env = args.user_home / ".env"
    dst_env = PROJECT_HOME / ".env"
    env_pairs: dict[str, str] = {}
    if src_env.exists():
        for line in src_env.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            env_pairs[key.strip()] = value.strip()
    env_pairs["TERMINAL_ENV"] = "local"
    dst_env.write_text(
        "\n".join(f"{k}={v}" for k, v in env_pairs.items()) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {dst_env} (TERMINAL_ENV=local)")

    # 1. copy & patch config.yaml
    src_config = args.user_home / "config.yaml"
    dst_config = PROJECT_HOME / "config.yaml"
    if not src_config.exists():
        raise SystemExit(f"missing {src_config}; cannot derive project config")
    patched = patch_backend_to_local(src_config.read_text(encoding="utf-8"))
    dst_config.write_text(patched, encoding="utf-8")
    print(f"wrote {dst_config} (terminal.backend=local)")

    # 2. symlink the rest
    for entry in LINK_ENTRIES:
        src = args.user_home / entry
        dst = PROJECT_HOME / entry
        print(link_or_copy(src, dst))

    # Named custom credentials can silently override model.api_key when the
    # project uses a bare `provider: custom`. Keep OAuth/API provider entries,
    # but remove custom pools from this project-local copy.
    auth_path = PROJECT_HOME / "auth.json"
    if auth_path.exists() and not auth_path.is_symlink():
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            pool = auth.get("credential_pool")
            if isinstance(pool, dict):
                auth["credential_pool"] = {
                    key: value
                    for key, value in pool.items()
                    if not str(key).startswith("custom:")
                }
                auth_path.write_text(
                    json.dumps(auth, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                print("sanitized auth.json custom credential pools")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: could not sanitize auth.json ({exc})")

    # 3. local-only directories Hermes expects to exist
    for sub in (
        "sessions",
        "logs",
        "pastes",
        "cron",
        "hooks",
        "image_cache",
        "audio_cache",
    ):
        (PROJECT_HOME / sub).mkdir(exist_ok=True)

    print(f"\nproject hermes home ready at {PROJECT_HOME}")
    print("the runner sets HERMES_HOME automatically.")


if __name__ == "__main__":
    main()
