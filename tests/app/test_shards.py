import json
import tempfile
import unittest
from pathlib import Path

from paths import ProjectPaths
from shards import ShardQueue, split_matrix


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

        self.assertEqual(len(created), 3)
        self.assertTrue(all(path.exists() for path in created))

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
