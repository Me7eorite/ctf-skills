"""Capability endpoint contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.server import create_app


class CapabilitiesApiTests(unittest.TestCase):
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

    def test_endpoint_returns_exactly_four_entries(self) -> None:
        with self._client() as client:
            response = client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 4)

    def test_status_distribution(self) -> None:
        with self._client() as client:
            payload = client.get("/api/capabilities").json()
        enabled = [item for item in payload if item["status"] == "enabled"]
        coming = [item for item in payload if item["status"] == "coming_soon"]
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["id"], "challenge-generator")
        self.assertEqual(
            {item["id"] for item in coming},
            {"scenario-builder", "learning-materials", "learning-paths"},
        )

    def test_required_field_shape(self) -> None:
        required_fields = {"id", "name", "status", "description", "icon", "route"}
        with self._client() as client:
            payload = client.get("/api/capabilities").json()
        for entry in payload:
            with self.subTest(entry=entry["id"]):
                self.assertTrue(required_fields.issubset(entry.keys()))
                self.assertTrue(entry["route"].startswith("/"))
                self.assertIn(
                    entry["status"], {"enabled", "coming_soon", "disabled"}
                )


if __name__ == "__main__":
    unittest.main()
