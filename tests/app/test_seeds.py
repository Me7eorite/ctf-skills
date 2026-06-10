import tempfile
import unittest
from pathlib import Path

from core.jsonio import read_json
from core.paths import ProjectPaths
from domain.seeds import SeedStore


class SeedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        self.store = SeedStore(self.paths)

    @staticmethod
    def _seed(challenge_id: str = "web-0001") -> dict:
        category = challenge_id.split("-", 1)[0]
        seed = {
            "id": challenge_id,
            "title": "Demo",
            "category": category,
            "difficulty": "easy",
            "points": 100,
            "primary_technique": "auth bypass",
            "learning_objective": "Understand trust boundaries",
        }
        if category in {"web", "pwn"}:
            seed["port"] = 8080 if category == "web" else 9001
        return seed

    def test_save_updates_existing_seed_without_duplicate(self):
        self.store.save(self._seed())
        updated = self._seed()
        updated["title"] = "Updated"

        self.store.save(updated)

        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.list()[0]["title"], "Updated")

    def test_save_preserves_category_specific_fields(self):
        seed = self._seed()
        seed.update({"runtime": "node", "framework": "Express"})

        saved = self.store.save(seed)

        self.assertEqual(saved["runtime"], "node")
        self.assertEqual(read_json(self.paths.challenge_seeds)["seeds"][0]["framework"], "Express")

    def test_validation_rejects_mismatched_id_prefix(self):
        seed = self._seed()
        seed["category"] = "pwn"

        with self.assertRaisesRegex(ValueError, "id 前缀"):
            self.store.save(seed)

    def test_web_seed_requires_port(self):
        seed = self._seed()
        seed.pop("port")

        with self.assertRaisesRegex(ValueError, "有效端口"):
            self.store.save(seed)

    def test_delete_removes_seed(self):
        self.store.save(self._seed())

        self.store.delete("web-0001")

        self.assertEqual(self.store.list(), [])

    def test_enqueue_groups_seeds_and_preserves_full_rows(self):
        web = self._seed("web-0001")
        web["runtime"] = "node"
        self.store.save(web)
        self.store.save(self._seed("pwn-0001"))

        created = self.store.enqueue(size=5)

        self.assertEqual({path.name for path in created}, {"web-0001-0001.json", "pwn-0001-0001.json"})
        payload = read_json(self.paths.shards / "pending" / "web-0001-0001.json")
        self.assertEqual(payload["challenges"][0]["runtime"], "node")

    def test_enqueue_refuses_to_overwrite_pending_shard(self):
        self.store.save(self._seed())
        existing = self.paths.shards / "pending" / "web-0001-0001.json"
        existing.write_text('{"challenges":[]}\n', encoding="utf-8")

        with self.assertRaisesRegex(FileExistsError, "分片已存在"):
            self.store.enqueue()


if __name__ == "__main__":
    unittest.main()
