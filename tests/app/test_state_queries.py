"""Tests for the resume-safe ProgressStore query and snapshot APIs."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from core.state import InMemoryProgressStore, ProgressStore


@dataclass(frozen=True)
class _Paths:
    root: Path


def make_store(tmp: Path) -> ProgressStore:
    return InMemoryProgressStore()


class ProgressStoreQueryTests(unittest.TestCase):
    def test_record_returns_event_id(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            first = store.record(shard="web-0001.json", stage="queued", status="running")
            second = store.record(shard="web-0001.json", stage="queued", status="running")
            self.assertIn("event_id", first)
            self.assertIn("event_id", second)
            self.assertIsInstance(first["event_id"], int)
            self.assertGreater(second["event_id"], first["event_id"])

    def test_events_for_shard_returns_ascending_full_stream(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            a = store.record(shard="web-0001.json", stage="queued", status="running")
            b = store.record(
                shard="web-0001.json",
                stage="design",
                status="passed",
                challenge_id="web-0001",
            )
            store.record(shard="web-0002.json", stage="queued", status="running")

            stream = store.events_for_shard("web-0001.json")
            ids = [event["id"] for event in stream]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual({event["shard"] for event in stream}, {"web-0001.json"})
            self.assertIn(a["event_id"], ids)
            self.assertIn(b["event_id"], ids)

    def test_events_for_shard_before_id_is_exclusive(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            a = store.record(shard="s.json", stage="queued", status="running")
            b = store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c"
            )
            store.record(
                shard="s.json", stage="implement", status="passed", challenge_id="c"
            )

            window = store.events_for_shard("s.json", before_id=b["event_id"])
            ids = [event["id"] for event in window]
            self.assertEqual(ids, [a["event_id"]])

    def test_events_for_challenge_excludes_shard_level(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            challenge = store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c-1"
            )
            store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c-2"
            )

            window = store.events_for_challenge("s.json", "c-1")
            ids = [event["id"] for event in window]
            self.assertEqual(ids, [challenge["event_id"]])
            self.assertTrue(all(event["challenge_id"] == "c-1" for event in window))

    def test_events_for_challenge_requires_non_empty_id(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            with self.assertRaises(ValueError):
                store.events_for_challenge("s.json", "")

    def test_events_for_challenge_after_id_inclusive_before_id_exclusive(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            a = store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c"
            )
            b = store.record(
                shard="s.json", stage="implement", status="passed", challenge_id="c"
            )
            c = store.record(
                shard="s.json", stage="build", status="passed", challenge_id="c"
            )

            window = store.events_for_challenge(
                "s.json", "c", after_id=b["event_id"], before_id=c["event_id"]
            )
            ids = [event["id"] for event in window]
            self.assertEqual(ids, [b["event_id"]])
            self.assertNotIn(a["event_id"], ids)
            self.assertNotIn(c["event_id"], ids)

    def test_latest_claim_event_returns_most_recent(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c"
            )
            second_claim = store.record(shard="s.json", stage="queued", status="running")

            latest = store.latest_claim_event("s.json")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["id"], second_claim["event_id"])

    def test_latest_claim_event_before_id_finds_previous_claim(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            first_claim = store.record(shard="s.json", stage="queued", status="running")
            second_claim = store.record(shard="s.json", stage="queued", status="running")

            previous = store.latest_claim_event(
                "s.json", before_id=second_claim["event_id"]
            )
            self.assertIsNotNone(previous)
            assert previous is not None
            self.assertEqual(previous["id"], first_claim["event_id"])

    def test_latest_claim_event_ignores_challenge_level_events(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(
                shard="s.json", stage="queued", status="running", challenge_id="c"
            )
            latest = store.latest_claim_event("s.json")
            self.assertIsNone(latest)

    def test_reset_snapshots_preserves_events(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(shard="s.json", stage="queued", status="running")
            store.record(
                shard="s.json", stage="design", status="passed", challenge_id="c"
            )

            before_events = store.events_for_shard("s.json")
            store.reset_snapshots("s.json")
            after_events = store.events_for_shard("s.json")

            self.assertEqual(
                [event["id"] for event in before_events],
                [event["id"] for event in after_events],
            )

            dashboard_after = store.dashboard()
            shard_snapshots = [
                snapshot
                for snapshot in dashboard_after["snapshots"]
                if snapshot["shard"] == "s.json"
            ]
            self.assertEqual(shard_snapshots, [])

    def test_reset_snapshots_scoped_by_shard(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(shard="a.json", stage="queued", status="running")
            store.record(shard="b.json", stage="queued", status="running")

            store.reset_snapshots("a.json")

            shards = {
                snapshot["shard"] for snapshot in store.dashboard()["snapshots"]
            }
            self.assertNotIn("a.json", shards)
            self.assertIn("b.json", shards)


class SnapshotMonotonicityTests(unittest.TestCase):
    def test_lower_percent_does_not_reduce_snapshot(self):
        with TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            store.record(
                shard="s.json",
                stage="document",
                status="passed",
                challenge_id="c",
            )
            high = next(
                snapshot
                for snapshot in store.dashboard()["snapshots"]
                if snapshot["challenge_id"] == "c"
            )

            store.record(
                shard="s.json",
                stage="validate",
                status="running",
                challenge_id="c",
            )

            updated = next(
                snapshot
                for snapshot in store.dashboard()["snapshots"]
                if snapshot["challenge_id"] == "c"
            )
            self.assertEqual(updated["stage"], "validate")
            self.assertEqual(updated["status"], "running")
            self.assertGreaterEqual(updated["percent"], high["percent"])


if __name__ == "__main__":
    unittest.main()
