from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core.paths import ProjectPaths
from core.state import STAGES, StateStore
from hermes.fake import DEMO_ROWS, FakeHermesRunner


class FakeHermesRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def test_replay_completes_and_records_every_stage(self):
        started = time.monotonic()
        FakeHermesRunner(self.paths, delay=0).run()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5)
        state = StateStore(self.paths).dashboard(event_limit=200)
        stages = {event["stage"] for event in state["events"]}
        self.assertTrue(set(STAGES).issubset(stages))
        self.assertEqual(len(list(self.paths.challenges.glob("*/*/metadata.json"))), len(DEMO_ROWS))

    def test_second_run_replays_from_scratch(self):
        runner = FakeHermesRunner(self.paths, delay=0)
        runner.run()
        first = StateStore(self.paths).dashboard(event_limit=200)["events"]
        runner.run()
        second = StateStore(self.paths).dashboard(event_limit=200)["events"]

        self.assertGreater(len(second), 0)
        self.assertLessEqual(len(second), len(first) + 5)


if __name__ == "__main__":
    unittest.main()
