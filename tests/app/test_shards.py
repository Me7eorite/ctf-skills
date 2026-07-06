import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from core.paths import ProjectPaths
from core.queue import ShardQueue, split_challenges, split_matrix


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "factory"
        repository = Path(self.temp.name)
        self.paths = ProjectPaths(root=root, repository=repository)
        self.paths.initialize()

    def test_split_matrix_groups_categories(self):
        matrix = self.paths.root / "matrix.jsonl"
        rows = [
            {"id": "web-0001", "category": "web"},
            {"id": "web-0002", "category": "web"},
            {"id": "pwn-0001", "category": "pwn"},
            {"id": "crypto-0001", "category": "crypto"},
        ]
        matrix.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

        created = split_matrix(matrix, self.paths.shards / "pending", size=1)

        self.assertEqual(len(created), 4)
        self.assertTrue(all(path.exists() for path in created))
        self.assertTrue(any(path.name.startswith("crypto-") for path in created))

    def test_split_challenges_preserves_unknown_design_field(self):
        design = {
            "deployment": "docker",
            "flag_location": "environment",
            "hints": ["one", "two"],
        }
        created = split_challenges(
            [
                {
                    "id": "web-0001",
                    "category": "web",
                    "title": "Designed",
                    "design": design,
                }
            ],
            self.paths.shards / "pending",
            size=1,
        )

        payload = json.loads(created[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["challenges"][0]["design"], design)

    def test_claim_and_complete_preserve_original_name(self):
        shard = self.paths.shards / "pending" / "web-0001-0002.json"
        shard.write_text('{"challenges": []}\n', encoding="utf-8")
        queue = ShardQueue(self.paths)

        claimed = queue.claim("worker-1")
        completed = queue.complete(claimed, "done")

        self.assertEqual(completed.name, shard.name)
        self.assertTrue(completed.exists())

    def test_retry_moves_failed_shard_to_pending(self):
        shard = self.paths.shards / "failed" / "re-0001-0001.json"
        shard.write_text('{"challenges": []}\n', encoding="utf-8")

        retried = ShardQueue(self.paths).retry(shard.name)

        self.assertEqual(retried.parent.name, "pending")
        self.assertFalse(shard.exists())

    def test_requeue_running_restores_original_name(self):
        shard = self.paths.shards / "running" / "re-0001.worker-1.json"
        shard.write_text('{"challenges": []}\n', encoding="utf-8")
        shard.with_suffix(".json.claim.json").write_text(
            '{"source_name": "re-0001.json"}\n',
            encoding="utf-8",
        )

        requeued = ShardQueue(self.paths).requeue(shard.name, "running")

        self.assertEqual(requeued.name, "re-0001.json")
        self.assertTrue(requeued.exists())

    def _write_payload(self, name: str, payload: object) -> Path:
        path = self.paths.shards / "pending" / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_category_claim_skips_earlier_other_category(self):
        pwn = self._write_payload(
            "pwn-0001.json",
            {"challenges": [{"id": "pwn-0001", "category": "pwn"}]},
        )
        web = self._write_payload(
            "web-0001.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )

        claimed = ShardQueue(self.paths).claim("worker-1", category="web")

        self.assertIsNotNone(claimed)
        self.assertEqual(ShardQueue(self.paths).original_name(claimed), web.name)
        self.assertTrue(pwn.exists())

    def test_category_claim_reads_uuid_named_payload(self):
        attempt_id = uuid4()
        design_task_id = uuid4()
        shard = self._write_payload(
            f"{attempt_id}.json",
            {
                "build_attempt_id": str(attempt_id),
                "design_task_id": str(design_task_id),
                "challenges": [{"id": "web-0001", "category": "web"}],
            },
        )

        claimed = ShardQueue(self.paths).claim("worker-1", category="web")

        self.assertEqual(ShardQueue(self.paths).original_name(claimed), shard.name)

    def test_category_claim_accepts_non_default_category(self):
        shard = self._write_payload(
            "crypto-0001.json",
            {"challenges": [{"id": "crypto-0001", "category": "crypto"}]},
        )

        claimed = ShardQueue(self.paths).claim("worker-1", category="crypto")

        self.assertIsNotNone(claimed)
        self.assertEqual(ShardQueue(self.paths).original_name(claimed), shard.name)

    def test_require_build_attempt_skips_matching_legacy_shard(self):
        legacy = self._write_payload(
            "a-web.json",
            {"challenges": [{"id": "web-0001", "category": "web"}]},
        )
        attempt_id = uuid4()
        attributed = self._write_payload(
            "b-web.json",
            {
                "build_attempt_id": str(attempt_id),
                "design_task_id": str(uuid4()),
                "challenges": [{"id": "web-0002", "category": "web"}],
            },
        )

        claimed = ShardQueue(self.paths).claim(
            "worker-1",
            category="web",
            require_build_attempt=True,
        )

        self.assertEqual(ShardQueue(self.paths).original_name(claimed), attributed.name)
        self.assertTrue(legacy.exists())

    def test_exact_attempt_requires_matching_id_and_valid_design_task(self):
        wanted = uuid4()
        other = uuid4()
        wrong = self._write_payload(
            f"{other}.json",
            {
                "build_attempt_id": str(other),
                "design_task_id": str(uuid4()),
                "challenges": [{"id": "web-0001", "category": "web"}],
            },
        )
        matching = self._write_payload(
            f"{wanted}.json",
            {
                "build_attempt_id": str(wanted),
                "design_task_id": str(uuid4()),
                "challenges": [{"id": "web-0002", "category": "web"}],
            },
        )

        claimed = ShardQueue(self.paths).claim(
            "worker-1",
            category="web",
            build_attempt_id=wanted,
        )

        self.assertEqual(ShardQueue(self.paths).original_name(claimed), matching.name)
        self.assertTrue(wrong.exists())

    def test_exact_attempt_skips_duplicate_noncanonical_basename(self):
        wanted = uuid4()
        payload = {
            "build_attempt_id": str(wanted),
            "design_task_id": str(uuid4()),
            "challenges": [{"id": "web-0001", "category": "web"}],
        }
        duplicate = self._write_payload("a-duplicate.json", payload)
        canonical = self._write_payload(f"{wanted}.json", payload)

        claimed = ShardQueue(self.paths).claim(
            "worker-1",
            category="web",
            build_attempt_id=wanted,
        )

        self.assertEqual(ShardQueue(self.paths).original_name(claimed), canonical.name)
        self.assertTrue(duplicate.exists())

    def test_exact_attempt_accepts_iteration_basename(self):
        wanted = uuid4()
        payload = {
            "build_attempt_id": str(wanted),
            "design_task_id": str(uuid4()),
            "challenges": [{"id": "web-0001", "category": "web"}],
        }
        duplicate = self._write_payload("a-duplicate.json", payload)
        iteration = self._write_payload(f"{wanted}.iter-002.json", payload)

        claimed = ShardQueue(self.paths).claim(
            "worker-1",
            category="web",
            build_attempt_id=wanted,
        )

        self.assertEqual(ShardQueue(self.paths).original_name(claimed), iteration.name)
        self.assertTrue(duplicate.exists())

    def test_constrained_claim_skips_malformed_and_symlink(self):
        malformed = self.paths.shards / "pending" / "a.json"
        malformed.write_text("{", encoding="utf-8")
        target = self.paths.root / "outside.json"
        target.write_text(
            json.dumps({"challenges": [{"id": "web-1", "category": "web"}]}),
            encoding="utf-8",
        )
        symlink = self.paths.shards / "pending" / "b.json"
        symlink.symlink_to(target)

        claimed = ShardQueue(self.paths).claim("worker-1", category="web")

        self.assertIsNone(claimed)
        self.assertTrue(malformed.exists())
        self.assertTrue(symlink.is_symlink())

    def test_invalid_filter_does_not_mutate_queue(self):
        shard = self._write_payload(
            "web.json",
            {"challenges": [{"id": "web-1", "category": "web"}]},
        )

        with self.assertRaises(ValueError):
            ShardQueue(self.paths).claim("worker-1", build_attempt_id="invalid")

        self.assertTrue(shard.exists())

    def test_unconstrained_claim_keeps_malformed_compatibility(self):
        shard = self.paths.shards / "pending" / "bad.json"
        shard.write_text("{", encoding="utf-8")

        claimed = ShardQueue(self.paths).claim("worker-1")

        self.assertIsNotNone(claimed)
        self.assertFalse(shard.exists())
