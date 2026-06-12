"""Presets CRUD round-trip tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.server import create_app


class PresetsApiTests(unittest.TestCase):
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

    def test_crud_round_trip(self) -> None:
        with self._client() as client:
            initial = client.get("/api/presets")
            self.assertEqual(initial.json(), {"presets": []})

            created = client.post(
                "/api/presets",
                json={"name": "demo", "payload": {"category": "web", "size": 5}},
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["preset"]["name"], "demo")

            listed = client.get("/api/presets").json()
            self.assertEqual(len(listed["presets"]), 1)
            self.assertEqual(
                listed["presets"][0]["payload"], {"category": "web", "size": 5}
            )

            replaced = client.post(
                "/api/presets",
                json={"name": "demo", "payload": {"category": "pwn", "size": 3}},
            )
            self.assertEqual(replaced.status_code, 201)
            listed = client.get("/api/presets").json()
            self.assertEqual(len(listed["presets"]), 1)
            self.assertEqual(listed["presets"][0]["payload"]["category"], "pwn")

            deleted = client.delete("/api/presets/demo")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(client.get("/api/presets").json(), {"presets": []})

    def test_post_requires_name(self) -> None:
        with self._client() as client:
            response = client.post("/api/presets", json={"payload": {}})
        self.assertEqual(response.status_code, 400)

    def test_delete_missing_returns_404(self) -> None:
        with self._client() as client:
            response = client.delete("/api/presets/missing")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
