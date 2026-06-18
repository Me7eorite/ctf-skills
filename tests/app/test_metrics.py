"""Tests for ``domain.metrics.duration_breakdown``."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.state import InMemoryProgressStore, ProgressStore
from domain.metrics import duration_breakdown


@dataclass(frozen=True)
class _Paths:
    root: Path


def _make_store(tmp: Path) -> ProgressStore:
    return InMemoryProgressStore()


def _utc_at(offset_seconds: int) -> str:
    import time

    base = 1_700_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + offset_seconds))


class DurationBreakdownTests(unittest.TestCase):
    def test_no_history_returns_all_none(self):
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            durations = duration_breakdown(store, "c", "s.json")
            for stage in ("design", "implement", "build", "validate", "document"):
                self.assertIsNone(durations[stage])

    def test_passed_with_running_yields_duration(self):
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            timestamps = iter([_utc_at(0), _utc_at(1), _utc_at(11)])
            with patch("core.state.utc_now", side_effect=lambda: next(timestamps)):
                store.record(shard="s.json", stage="queued", status="running")
                store.record(
                    shard="s.json",
                    stage="design",
                    status="running",
                    challenge_id="c",
                )
                store.record(
                    shard="s.json",
                    stage="design",
                    status="passed",
                    challenge_id="c",
                )

            durations = duration_breakdown(store, "c", "s.json")
            self.assertEqual(durations["design"], 10.0)
            self.assertIsNone(durations["implement"])

    def test_carry_forward_only_returns_none(self):
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            # Only a passed event (no running) -> carry-forward shape
            store.record(
                shard="s.json",
                stage="design",
                status="passed",
                challenge_id="c",
            )
            durations = duration_breakdown(store, "c", "s.json")
            self.assertIsNone(durations["design"])

    def test_latest_passed_required(self):
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            store.record(
                shard="s.json",
                stage="design",
                status="running",
                challenge_id="c",
            )
            store.record(
                shard="s.json",
                stage="design",
                status="passed",
                challenge_id="c",
            )
            store.record(
                shard="s.json",
                stage="design",
                status="failed",
                challenge_id="c",
            )
            durations = duration_breakdown(store, "c", "s.json")
            self.assertIsNone(durations["design"])

    def test_old_window_events_excluded(self):
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            # First claim window with full design pair.
            store.record(shard="s.json", stage="queued", status="running")
            store.record(
                shard="s.json",
                stage="design",
                status="running",
                challenge_id="c",
            )
            store.record(
                shard="s.json",
                stage="design",
                status="passed",
                challenge_id="c",
            )
            # New claim window with no design events.
            store.record(shard="s.json", stage="queued", status="running")

            durations = duration_breakdown(store, "c", "s.json")
            self.assertIsNone(durations["design"])


if __name__ == "__main__":
    unittest.main()
