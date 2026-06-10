"""Command-line interface for Challenge Factory."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from core.paths import ProjectPaths
from core.queue import ShardQueue, split_matrix
from core.state import STAGES, STATUSES, StateStore
from domain.reports import merge_reports
from domain.validation import ChallengeValidator
from hermes import HermesRunner
from packing import Packer, PackerOptions
from web.server import serve


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
    run.add_argument("--validate", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--max-shards", type=int, default=0)

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

    pack = commands.add_parser("pack", help="build the delivery format bundle")
    pack.add_argument("--out", type=Path)
    pack.add_argument("--include-pwn-attachments", action="store_true")
    pack.add_argument("--skip-docker", action="store_true")
    pack.add_argument("--require-docker", action="store_true")

    web = commands.add_parser("serve", help="start the dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=4173)
    return root


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
        result = HermesRunner(paths).run(
            args.worker,
            loop=args.loop,
            validate=args.validate,
            dry_run=args.dry_run,
            max_shards=args.max_shards,
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
        serve(paths, args.host, args.port)


if __name__ == "__main__":
    main()
