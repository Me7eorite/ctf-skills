"""Runs / shard API endpoint tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.server import create_app


class RunsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def _client(self) -> TestClient:
        return TestClient(create_app(DashboardService(self.paths)))

    def _write_shard(self, state: str, name: str, *, challenges: list[dict]) -> Path:
        shard_path = self.paths.shards / state / name
        shard_path.write_text(json.dumps({"challenges": challenges}), encoding="utf-8")
        return shard_path

    def _write_challenge(self, category: str, challenge_id: str) -> Path:
        directory = self.paths.challenges / category / challenge_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "metadata.json").write_text(
            json.dumps({"id": challenge_id, "category": category, "title": "demo"}),
            encoding="utf-8",
        )
        (directory / "brief.md").write_text("Brief content", encoding="utf-8")
        return directory

    def test_list_runs_aggregates_states(self) -> None:
        self._write_shard(
            "pending",
            "web-0001-0001.json",
            challenges=[{"id": "web-0001", "category": "web"}],
        )
        self._write_shard(
            "done",
            "web-0001-0002.json",
            challenges=[{"id": "web-0002", "category": "web"}],
        )
        with self._client() as client:
            response = client.get("/api/runs")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 2)
        names = {item["name"] for item in payload["items"]}
        self.assertEqual(names, {"web-0001-0001.json", "web-0001-0002.json"})

    def test_run_detail_returns_404_for_missing(self) -> None:
        with self._client() as client:
            response = client.get("/api/runs/missing.json")
        self.assertEqual(response.status_code, 404)

    def test_run_detail_returns_summary(self) -> None:
        self._write_shard(
            "running",
            "web-0001-0003.json",
            challenges=[{"id": "web-0003", "category": "web"}],
        )
        with self._client() as client:
            response = client.get("/api/runs/web-0001-0003.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "web-0001-0003.json")
        self.assertEqual(payload["state"], "running")
        self.assertEqual(payload["challenge_ids"], ["web-0003"])

    def test_challenge_detail_returns_metadata(self) -> None:
        self._write_shard(
            "done",
            "web-0001-0004.json",
            challenges=[{"id": "web-0004", "category": "web"}],
        )
        self._write_challenge("web", "web-0004")
        with self._client() as client:
            response = client.get(
                "/api/runs/web-0001-0004.json/challenges/web-0004"
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], "web-0004")
        self.assertEqual(payload["metadata"]["title"], "demo")
        self.assertIn("brief.md", payload["files"])

    def test_artifact_path_traversal_rejected(self) -> None:
        self._write_shard(
            "done",
            "web-0001-0005.json",
            challenges=[{"id": "web-0005", "category": "web"}],
        )
        self._write_challenge("web", "web-0005")
        # Percent-encode the traversal so httpx does not normalize it client side
        # before the request reaches the server's path validator.
        encoded = "%2E%2E/%2E%2E/%2E%2E/etc/passwd"
        with self._client() as client:
            response = client.get(
                f"/api/runs/web-0001-0005.json/artifacts/{encoded}"
            )
        self.assertEqual(response.status_code, 400)

    def test_artifact_absolute_path_rejected(self) -> None:
        self._write_shard(
            "done",
            "web-0001-0007.json",
            challenges=[{"id": "web-0007", "category": "web"}],
        )
        self._write_challenge("web", "web-0007")
        with self._client() as client:
            response = client.get(
                "/api/runs/web-0001-0007.json/artifacts/%2Fetc%2Fpasswd"
            )
        self.assertEqual(response.status_code, 400)

    def test_artifact_returns_file_bytes(self) -> None:
        self._write_shard(
            "done",
            "web-0001-0006.json",
            challenges=[{"id": "web-0006", "category": "web"}],
        )
        self._write_challenge("web", "web-0006")
        with self._client() as client:
            response = client.get(
                "/api/runs/web-0001-0006.json/artifacts/web/web-0006/brief.md"
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Brief content", response.content)


if __name__ == "__main__":
    unittest.main()
