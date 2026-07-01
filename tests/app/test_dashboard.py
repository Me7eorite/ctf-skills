import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch
from uuid import UUID

from core.jsonio import write_json
from core.paths import ProjectPaths
from core.state import InMemoryProgressStore
from web.dashboard import DashboardService, LanePool, LaneProcess, TaskManager


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def test_state_summarizes_challenges_and_queue(self):
        challenge = self.paths.challenges / "web" / "web-0001-demo"
        write_json(
            challenge / "metadata.json",
            {
                "id": "web-0001",
                "title": "Demo",
                "category": "web",
                "difficulty": "easy",
                "runtime": "node",
                "framework": "Express",
                "build_status": "passed",
                "solve_status": "passed",
            },
        )
        write_json(
            self.paths.shards / "pending" / "web-0001-0001.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )

        state = DashboardService(self.paths).state()

        self.assertEqual(state["summary"]["challenges"], 1)
        self.assertEqual(state["summary"]["validated"], 1)
        self.assertEqual(state["summary"]["queue"]["pending"], 1)
        self.assertEqual(state["challenges"][0]["runtime"], "node")
        self.assertEqual(state["seeds"], [])
        self.assertEqual(state["progress"]["snapshots"], [])
        self.assertFalse(state["progress"]["storage"]["fallback"])

    def test_state_preserves_progress_storage_contract(self):
        progress = InMemoryProgressStore()
        progress.record(shard="x.json", stage="queued", status="running")

        state = DashboardService(self.paths, progress=progress).state()

        self.assertEqual(state["progress"]["storage"]["backend"], "memory")
        self.assertFalse(state["progress"]["storage"]["fallback"])
        self.assertEqual(state["progress"]["storage"]["warning"], "")

    def test_ui_state_is_minimal_and_fast(self):
        state = DashboardService(self.paths).ui_state()

        self.assertIn("process", state)
        self.assertIsNone(state["build_readiness"])
        self.assertIsNone(state["sequential_worker_result"])
        self.assertNotIn("summary", state)
        self.assertNotIn("challenges", state)
        self.assertNotIn("logs", state)

    def test_worker_rejects_empty_pending_queue(self):
        ok, message = TaskManager(self.paths).start("worker")

        self.assertFalse(ok)
        self.assertIn("待处理分片", message)

    def test_worker_command_omits_removed_validate_flag(self):
        write_json(
            self.paths.shards / "pending" / "web-0001.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )
        with (
            patch("web.dashboard.subprocess.Popen") as popen,
            patch("web.dashboard.time.sleep"),
        ):
            popen.return_value.poll.return_value = None

            ok, _message = TaskManager(self.paths).start("worker")

        self.assertTrue(ok)
        command = popen.call_args.args[0]
        self.assertEqual(
            command[2:],
            ["run", "--worker", "dashboard-01", "--loop"],
        )
        self.assertNotIn("--validate", command)

    def test_sequential_worker_preserves_explicit_attempt_order(self):
        first = "11111111-1111-1111-1111-111111111111"
        second = "22222222-2222-2222-2222-222222222222"
        with (
            patch("web.dashboard.subprocess.Popen") as popen,
            patch("web.dashboard.time.sleep"),
        ):
            popen.return_value.poll.return_value = None

            ok, _message = TaskManager(self.paths).start_sequential_worker(
                build_attempt_ids=[UUID(first), UUID(second)],
            )

        self.assertTrue(ok)
        command = popen.call_args.args[0]
        sequence = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--build-attempt-sequence"
        ]
        self.assertEqual(sequence, [first, second])
        self.assertIn("--allow-failed-attempts-exit-zero", command)

    def test_finished_build_workers_reports_exited_dashboard_worker(self):
        process = Mock()
        process.poll.return_value = 1
        tasks = TaskManager(self.paths)
        tasks._process = process
        tasks._kind = "worker"
        tasks._worker_ids = {"dashboard-01"}

        records = tasks.finished_build_workers()

        self.assertEqual(
            records,
            [
                {
                    "kind": "worker",
                    "worker_ids": ["dashboard-01"],
                    "returncode": 1,
                }
            ],
        )

    def test_sequential_lanes_split_attempts_round_robin(self):
        first = UUID("11111111-1111-1111-1111-111111111111")
        second = UUID("22222222-2222-2222-2222-222222222222")
        third = UUID("33333333-3333-3333-3333-333333333333")
        processes = [Mock(), Mock()]
        for process in processes:
            process.poll.return_value = None
        with (
            patch("web.dashboard.subprocess.Popen", side_effect=processes) as popen,
            patch("web.dashboard.time.sleep"),
            patch("web.dashboard.uuid4") as patched_uuid4,
        ):
            patched_uuid4.return_value.hex = "abcdef1234567890"

            ok, message, pool = TaskManager(self.paths).start_sequential_lanes(
                lanes=[[first, third], [second]],
            )

        self.assertTrue(ok)
        self.assertIn("2 条 lane", message)
        self.assertEqual(pool["lane_count"], 2)
        commands = [call.args[0] for call in popen.call_args_list]
        sequences = [
            [
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--build-attempt-sequence"
            ]
            for command in commands
        ]
        self.assertEqual(sequences, [[str(first), str(third)], [str(second)]])
        self.assertEqual(commands[0][4], "dashboard-lane-01-abcdef12")
        self.assertEqual(commands[1][4], "dashboard-lane-02-abcdef12")
        self.assertIn("--allow-failed-attempts-exit-zero", commands[0])
        self.assertIn("--allow-failed-attempts-exit-zero", commands[1])

    def test_stop_terminates_single_worker(self):
        tasks = TaskManager(self.paths)
        process = Mock()
        process.pid = 12345
        process.poll.side_effect = [None, None, 0]
        tasks._process = process
        tasks._kind = "worker"

        with patch("web.dashboard.os.killpg") as killpg:
            ok, message = tasks.stop()

        self.assertTrue(ok)
        self.assertIn("已结束 worker", message)
        killpg.assert_called_once_with(12345, signal.SIGTERM)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_stop_kills_stubborn_lane_process(self):
        first = UUID("11111111-1111-1111-1111-111111111111")
        process = Mock()
        process.pid = 23456
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired(cmd="lane", timeout=5), 0]
        tasks = TaskManager(self.paths)
        tasks._lane_pools["pool"] = LanePool(
            id="pool",
            started_at="2026-01-01 00:00:00",
            lanes=[
                LaneProcess(
                    lane=1,
                    worker="lane-01",
                    build_attempt_ids=[first],
                    log="lane.log",
                    process=process,
                )
            ],
        )

        with patch("web.dashboard.os.killpg") as killpg:
            ok, message = tasks.stop()

        self.assertTrue(ok)
        self.assertIn("强制结束", message)
        self.assertEqual(
            killpg.call_args_list,
            [
                call(23456, signal.SIGTERM),
                call(23456, signal.SIGKILL),
            ],
        )
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
