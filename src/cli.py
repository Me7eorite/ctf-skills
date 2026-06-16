"""Command-line interface for Challenge Factory."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import UUID

from core.paths import ProjectPaths
from core.queue import ShardQueue, split_matrix
from core.state import STAGES, STATUSES, StateStore
from domain.metrics import duration_breakdown
from domain.reports import merge_reports
from domain.research import DIFFICULTY_LABELS, GenerationRequestStatus, ResearchRunStatus
from domain.research_validators import ResearchValidationError
from domain.validation import ChallengeValidator
from hermes import HermesRunner
from hermes.runner import DEFAULT_HERMES_TIMEOUT
from packing import Packer, PackerOptions
from web.server import serve

SHARD_BASENAME_RE = re.compile(r"^[a-z0-9_-]+\.json$")

# Fallback used when challenge_categories cannot be queried (DB unreachable
# or DATABASE_URL unset). Keeps `--help` and `--category` argparse choices
# working for users without DB access.
_FALLBACK_CATEGORY_CODES: tuple[str, ...] = ("web", "pwn", "re")


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
            f"Precedence: --timeout > HERMES_TIMEOUT env var > default {DEFAULT_HERMES_TIMEOUT} = 25min."
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

    _register_research_commands(commands)
    return root


# ---------------------------------------------------------------------------
# `research` subcommand group
# ---------------------------------------------------------------------------


def _register_research_commands(commands: argparse._SubParsersAction) -> None:
    """Attach the `research` subparser group with its six leaf commands."""
    research = commands.add_parser(
        "research", help="research queue operations (submit, worker, wait, show, list, categories)"
    )
    sub = research.add_subparsers(dest="research_command", required=True)
    category_choices = _fetch_category_choices()

    submit = sub.add_parser("submit", help="enqueue a generation request (returns immediately)")
    submit.add_argument("--category", required=True, choices=category_choices)
    submit.add_argument("--topic", required=True)
    submit.add_argument("--count", dest="target_count", type=_positive_int, required=True)
    submit.add_argument(
        "--difficulty",
        required=True,
        type=_parse_difficulty,
        help=(
            "comma-separated label:count pairs, e.g. easy:2,medium:3 "
            f"(labels: {'|'.join(DIFFICULTY_LABELS)})"
        ),
    )
    submit.add_argument(
        "--seed-url", dest="seed_urls", action="append", default=[],
        help="seed URL; may be passed multiple times",
    )
    submit.add_argument("--max-attempts", type=_positive_int, default=3)

    worker = sub.add_parser("worker", help="long-running research worker")
    worker.add_argument("--agent-id", required=True)
    worker.add_argument("--loop", action="store_true")
    worker.add_argument("--max-jobs", type=int, default=0)
    worker.add_argument("--poll-interval-seconds", type=float, default=5.0)
    worker.add_argument("--lease-seconds", type=_positive_int, default=900)
    worker.add_argument("--hermes-timeout-seconds", type=_positive_int, default=810)

    wait = sub.add_parser("wait", help="poll a run to terminal status")
    wait.add_argument("run_id")
    wait.add_argument("--timeout", type=_positive_int, default=None)
    wait.add_argument("--poll-interval-seconds", type=_positive_int, default=2)

    show = sub.add_parser("show", help="inspect a generation_request and its runs")
    show.add_argument("request_id")

    listing = sub.add_parser("list", help="list generation requests")
    listing.add_argument("--category", default=None)
    listing.add_argument("--status", default=None, choices=GenerationRequestStatus)

    sub.add_parser("categories", help="list the configured challenge categories")


def _fetch_category_choices() -> list[str]:
    """Read `challenge_categories.code` for argparse `choices`; safe fallback on DB miss."""
    try:
        from persistence.repositories import ResearchRepository
        from persistence.session import transaction

        with transaction() as session:
            codes = [cat.code for cat in ResearchRepository(session).list_categories()]
            return codes or list(_FALLBACK_CATEGORY_CODES)
    except Exception as exc:  # noqa: BLE001 — argparse build must not crash on any DB issue
        print(
            f"warning: could not query challenge_categories ({exc.__class__.__name__}); "
            f"falling back to {list(_FALLBACK_CATEGORY_CODES)}",
            file=sys.stderr,
        )
        return list(_FALLBACK_CATEGORY_CODES)


def _parse_difficulty(raw: str) -> dict[str, int]:
    """Parse `--difficulty easy:2,medium:3` into a dict."""
    if not raw:
        raise argparse.ArgumentTypeError("--difficulty must not be empty")
    distribution: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            raise argparse.ArgumentTypeError(
                f"--difficulty entries must be label:count, got {part!r}"
            )
        label, count_raw = part.split(":", 1)
        label = label.strip()
        if not label:
            raise argparse.ArgumentTypeError("--difficulty entries must include a label")
        try:
            count = int(count_raw.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"--difficulty count for {label!r} must be int, got {count_raw!r}"
            ) from exc
        if count <= 0:
            raise argparse.ArgumentTypeError(
                f"--difficulty count for {label!r} must be > 0, got {count}"
            )
        if label in distribution:
            raise argparse.ArgumentTypeError(f"--difficulty has duplicate label {label!r}")
        distribution[label] = count
    return distribution


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


def _handle_research(args: argparse.Namespace, paths: ProjectPaths) -> None:
    """Dispatch one of the six `research <subcmd>` operations."""
    try:
        if args.research_command == "submit":
            _research_submit(args)
        elif args.research_command == "worker":
            _research_worker(args, paths)
        elif args.research_command == "wait":
            _research_wait(args)
        elif args.research_command == "show":
            _research_show(args)
        elif args.research_command == "list":
            _research_list(args)
        elif args.research_command == "categories":
            _research_categories()
        else:  # pragma: no cover — argparse rejects this earlier
            print(f"error: unknown research command {args.research_command!r}", file=sys.stderr)
            sys.exit(2)
    except Exception as exc:  # noqa: BLE001 — translate any persistence-layer error
        from persistence.errors import (
            PersistenceConfigurationError,
            PersistenceConnectionError,
        )

        if isinstance(exc, (PersistenceConfigurationError, PersistenceConnectionError)):
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
        raise


def _research_submit(args: argparse.Namespace) -> None:
    from services import ResearchJobService

    try:
        request, run = ResearchJobService().submit_request(
            category=args.category,
            topic=args.topic,
            target_count=args.target_count,
            difficulty_distribution=args.difficulty,
            seed_urls=args.seed_urls,
            max_attempts=args.max_attempts,
        )
    except ResearchValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(
        json.dumps(
            {
                "request_id": str(request.id),
                "run_id": str(run.id),
                "category": request.category,
                "status": "queued",
            },
            ensure_ascii=False,
        )
    )


def _research_worker(args: argparse.Namespace, paths: ProjectPaths) -> None:
    # Spec 9.2b: argparse layer rejects `--hermes-timeout-seconds >= --lease-seconds`
    # AFTER applying defaults so a misconfigured default would also fail loudly.
    if args.hermes_timeout_seconds >= args.lease_seconds:
        print(
            f"error: --hermes-timeout-seconds ({args.hermes_timeout_seconds}) must be "
            f"less than --lease-seconds ({args.lease_seconds})",
            file=sys.stderr,
        )
        sys.exit(2)
    # Spec 9.2b: existing HERMES_TIMEOUT env var (used by shard execution) MUST NOT
    # influence the research worker — only the CLI flag or its default.
    from services import ResearchAgentExecutor, ResearchJobService, ResearchWorker

    job_service = ResearchJobService()
    executor = ResearchAgentExecutor(paths)
    worker = ResearchWorker(paths, job_service, executor)
    result = worker.run(
        args.agent_id,
        loop=args.loop,
        max_jobs=args.max_jobs,
        poll_interval_seconds=args.poll_interval_seconds,
        lease_seconds=args.lease_seconds,
        hermes_timeout_seconds=args.hermes_timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False))


def _research_wait(args: argparse.Namespace) -> None:
    from persistence.repositories import ResearchRepository
    from persistence.session import transaction

    try:
        run_uuid = UUID(args.run_id)
    except ValueError:
        print(f"error: {args.run_id!r} is not a valid uuid", file=sys.stderr)
        sys.exit(3)

    deadline = time.monotonic() + args.timeout if args.timeout else None
    while True:
        with transaction() as session:
            run = ResearchRepository(session).get_run(run_uuid)
        if run is None:
            print(f"error: run {run_uuid} not found", file=sys.stderr)
            sys.exit(3)
        if run.status in ("completed", "failed"):
            print(run.status)
            sys.exit(0 if run.status == "completed" else 1)
        if deadline is not None and time.monotonic() >= deadline:
            print(f"timeout after {args.timeout}s; last status: {run.status}")
            sys.exit(2)
        time.sleep(args.poll_interval_seconds)


def _research_show(args: argparse.Namespace) -> None:
    from persistence.repositories import ResearchRepository
    from persistence.session import transaction

    try:
        request_uuid = UUID(args.request_id)
    except ValueError:
        print(f"error: {args.request_id!r} is not a valid uuid", file=sys.stderr)
        sys.exit(2)

    with transaction() as session:
        repo = ResearchRepository(session)
        request = repo.get_generation_request(request_uuid)
        if request is None:
            print(f"error: generation_request {request_uuid} not found", file=sys.stderr)
            sys.exit(2)
        category_display = {cat.code: cat.display_name for cat in repo.list_categories()}.get(
            request.category, request.category
        )
        runs = repo.list_runs(generation_request_id=request_uuid)
        latest_run = max(runs, key=lambda r: r.created_at) if runs else None
        source_count = len(repo.list_sources(latest_run.id)) if latest_run else 0
        finding_kinds = Counter(
            finding.kind for finding in (repo.list_findings(latest_run.id) if latest_run else [])
        )

    print(f"request_id   : {request.id}")
    print(f"category     : {request.category} ({category_display})")
    print(f"topic        : {request.topic}")
    print(f"target_count : {request.target_count}")
    print(f"distribution : {dict(request.difficulty_distribution)}")
    print(f"seed_urls    : {list(request.seed_urls)}")
    print(f"max_attempts : {request.max_attempts}")
    print(f"status       : {request.status}")
    print(f"created_at   : {request.created_at.isoformat()}")
    print(f"runs ({len(runs)}):")
    for run in runs:
        last_err = (run.last_error or "")[:80]
        print(
            f"  - {run.id}  attempt={run.attempt}  status={run.status:9s}  "
            f"claimed_by={run.claimed_by or '-'}  "
            f"started={run.started_at.isoformat() if run.started_at else '-'}  "
            f"finished={run.finished_at.isoformat() if run.finished_at else '-'}  "
            f"error={last_err!r}"
        )
    print(f"latest source count  : {source_count}")
    print(f"latest finding kinds : {dict(finding_kinds)}")
    if latest_run and latest_run.hermes_log_path:
        print(f"latest log           : {latest_run.hermes_log_path}")


def _research_list(args: argparse.Namespace) -> None:
    from persistence.repositories import ResearchRepository
    from persistence.session import transaction

    with transaction() as session:
        repo = ResearchRepository(session)
        if args.category is not None:
            allowed = {cat.code for cat in repo.list_categories()}
            if args.category not in allowed:
                print(
                    f"error: unknown category {args.category!r}; allowed: {sorted(allowed)}",
                    file=sys.stderr,
                )
                sys.exit(2)
        requests = repo.list_generation_requests(category=args.category, status=args.status)

    if not requests:
        print("(no matching requests)")
        return
    for request in requests:
        print(
            f"{request.id}  {request.category:5s}  status={request.status:11s}  "
            f"count={request.target_count}  created={request.created_at.isoformat()}  "
            f"topic={request.topic!r}"
        )


def _research_categories() -> None:
    from persistence.repositories import ResearchRepository
    from persistence.session import transaction

    with transaction() as session:
        categories = ResearchRepository(session).list_categories()
    if not categories:
        print("(no categories)")
        return
    for cat in categories:
        print(f"{cat.code:8s}  {cat.display_name:20s}  {cat.description or ''}")


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
        serve(paths, args.host, args.port)
        return

    if args.command == "research":
        _handle_research(args, paths)


if __name__ == "__main__":
    main()
