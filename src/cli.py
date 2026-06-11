"""Command-line interface for Challenge Factory."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from core.paths import ProjectPaths
from core.queue import ShardQueue, split_matrix
from core.state import STAGES, STATUSES, StateStore
from domain.metrics import duration_breakdown
from domain.reports import merge_reports
from domain.validation import ChallengeValidator
from hermes import HermesRunner
from hermes.fake import FakeHermesRunner
from hermes.runner import DEFAULT_HERMES_TIMEOUT
from packing import Packer, PackerOptions
from web.server import serve

SHARD_BASENAME_RE = re.compile(r"^[a-z0-9_-]+\.json$")


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {raw!r}"
        ) from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"must be greater than zero, got {value}"
        )
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="challenge-factory")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create work directories")

    split = commands.add_parser("split", help="split a JSONL matrix into shards")
    split.add_argument("--matrix", type=Path, required=True)
    split.add_argument("--out", type=Path)
    split.add_argument("--size", type=int, default=5)

    claim = commands.add_parser("claim", help="claim one pending shard")
    claim.add_argument("--worker", default=socket.gethostname())

    run = commands.add_parser("run", help="run Hermes for pending shards")
    run.add_argument("--worker", required=True)
    run.add_argument("--loop", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--max-shards", type=int, default=0)
    run.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        help=(
            "Hermes subprocess wall-clock timeout in seconds. "
            f"Precedence: --timeout > HERMES_TIMEOUT env var > default {DEFAULT_HERMES_TIMEOUT}."
        ),
    )

    validate = commands.add_parser("validate", help="validate generated challenges")
    validate.add_argument("--filter", action="append", default=[])
    validate.add_argument("--timeout", type=int, default=120)
    validate.add_argument("--shell", default="bash")
    validate.add_argument("--quiet", action="store_true")

    progress = commands.add_parser("progress", help="record agent progress")
    progress.add_argument("--shard", required=True)
    progress.add_argument("--challenge", default="")
    progress.add_argument("--worker", default="")
    progress.add_argument("--stage", choices=STAGES, required=True)
    progress.add_argument("--status", choices=sorted(STATUSES), required=True)
    progress.add_argument("--message", default="")

    commands.add_parser("merge-reports", help="merge shard reports")

    commands.add_parser(
        "build-ui",
        help="build the Next.js dashboard static export",
        description="build the Next.js dashboard static export",
    )

    durations = commands.add_parser(
        "durations",
        help="show the per-stage duration breakdown for one challenge in the latest claim window",
    )
    durations.add_argument("--challenge", required=True)
    durations.add_argument(
        "--shard",
        required=True,
        help=(
            "Original shard basename like web-0001-0005.json. Paths, "
            "worker-suffixed names, or names missing .json are rejected."
        ),
    )

    pack = commands.add_parser("pack", help="build the delivery format bundle")
    pack.add_argument("--out", type=Path)
    pack.add_argument("--include-pwn-attachments", action="store_true")
    pack.add_argument("--skip-docker", action="store_true")
    pack.add_argument("--require-docker", action="store_true")

    web = commands.add_parser("serve", help="start the dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=4173)
    web.add_argument("--demo", action="store_true", help="run the self-contained dashboard demo replay")
    return root


def _resolve_run_timeout(cli_value: int | None) -> tuple[int, str]:
    if cli_value is not None:
        return cli_value, "cli"
    env_raw = os.environ.get("HERMES_TIMEOUT")
    if env_raw:
        try:
            env_value = int(env_raw)
        except ValueError:
            print(
                f"error: HERMES_TIMEOUT must be a positive integer, got {env_raw!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        if env_value <= 0:
            print(
                f"error: HERMES_TIMEOUT must be greater than zero, got {env_raw!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        return env_value, "env"
    return DEFAULT_HERMES_TIMEOUT, "default"


def main() -> None:
    args = parser().parse_args()
    paths = ProjectPaths.discover()

    if args.command == "init":
        for directory in paths.initialize():
            print(f"ok {directory.relative_to(paths.root)}")
        return

    if args.command == "split":
        output = args.out or paths.shards / "pending"
        for path in split_matrix(args.matrix, output, args.size):
            print(f"wrote {path}")
        return

    if args.command == "claim":
        shard = ShardQueue(paths).claim(args.worker)
        print(shard or "no pending shard")
        return

    if args.command == "run":
        if args.dry_run and args.loop:
            print("error: --dry-run and --loop are mutually exclusive", file=sys.stderr)
            sys.exit(2)
        effective_timeout, source = _resolve_run_timeout(args.timeout)
        print(f"effective_timeout={effective_timeout} source={source}", flush=True)
        result = HermesRunner(paths).run(
            args.worker,
            loop=args.loop,
            dry_run=args.dry_run,
            max_shards=args.max_shards,
            timeout=effective_timeout,
        )
        print(json.dumps(result, indent=2))
        if result["failed"]:
            sys.exit(1)
        return

    if args.command == "validate":
        summary = ChallengeValidator(
            paths, timeout=args.timeout, shell=args.shell
        ).validate(args.filter)
        if not args.quiet:
            for result in summary["results"]:
                print(f"[{result['status'].upper()}] {result['id']}")
            print(
                f"{summary['status_counts'].get('passed', 0)}/"
                f"{summary['total']} challenges passed"
            )
        if summary["status_counts"].get("passed", 0) != summary["total"]:
            sys.exit(1)
        return

    if args.command == "progress":
        event = StateStore(paths).record(
            shard=args.shard,
            challenge_id=args.challenge,
            worker=args.worker,
            stage=args.stage,
            status=args.status,
            message=args.message,
        )
        print(json.dumps(event, ensure_ascii=False))
        return

    if args.command == "merge-reports":
        print(merge_reports(paths.reports))
        return

    if args.command == "build-ui":
        script = paths.root / "scripts" / "build_frontend.sh"
        if not script.is_file():
            print(f"error: frontend build script not found: {script}", file=sys.stderr)
            sys.exit(1)
        try:
            result = subprocess.run([str(script)], cwd=paths.root, check=False)
        except FileNotFoundError as exc:
            print(f"error: failed to start frontend build: {exc}", file=sys.stderr)
            sys.exit(127)
        sys.exit(result.returncode)

    if args.command == "durations":
        if not SHARD_BASENAME_RE.match(args.shard):
            print(
                "error: --shard must be an original shard basename like "
                f"web-0001-0005.json, got {args.shard!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        state = StateStore(paths)
        breakdown = duration_breakdown(state, args.challenge, args.shard)
        print(json.dumps(breakdown, ensure_ascii=False))
        return

    if args.command == "pack":
        result = Packer(
            paths,
            PackerOptions(
                include_pwn_attachments=args.include_pwn_attachments,
                skip_docker=args.skip_docker,
                require_docker=args.require_docker,
            ),
        ).pack(args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["errors"]:
            sys.exit(1)
        return

    if args.command == "serve":
        if args.demo:
            FakeHermesRunner(paths).start()
        serve(paths, args.host, args.port, demo=args.demo)


if __name__ == "__main__":
    main()
