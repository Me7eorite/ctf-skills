from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.server import create_app


class DemoModeWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()

    def _client(self, *, demo: bool) -> TestClient:
        service = DashboardService(self.paths)
        return TestClient(create_app(service, demo=demo))

    def test_mode_endpoint_reports_demo_state(self):
        with self._client(demo=True) as client:
            self.assertEqual(client.get("/api/mode").json(), {"demo": True})
        with self._client(demo=False) as client:
            self.assertEqual(client.get("/api/mode").json(), {"demo": False})

    def test_mutating_endpoints_are_read_only_in_demo(self):
        endpoints = [
            ("post", "/api/actions/worker", None),
            ("post", "/api/actions/validate", None),
            ("post", "/api/runs", {"seeds": []}),
            ("post", "/api/seeds", {}),
            ("delete", "/api/seeds/web-0001", None),
            ("post", "/api/seeds/enqueue", {"size": 1}),
            ("post", "/api/shards/failed/web-0001-0001.json/requeue", None),
        ]
        with self._client(demo=True) as client:
            for method, path, payload in endpoints:
                request = getattr(client, method)
                response = request(path, json=payload) if payload is not None else request(path)
                self.assertEqual(response.status_code, 409, path)
                self.assertEqual(
                    response.json(),
                    {"ok": False, "message": "Demo mode is read-only"},
                )

    def test_read_endpoints_work_in_demo(self):
        (self.paths.logs / "demo.log").write_text("hello\n", encoding="utf-8")
        with self._client(demo=True) as client:
            self.assertEqual(client.get("/api/state").status_code, 200)
            self.assertEqual(client.get("/api/mode").status_code, 200)
            self.assertEqual(client.get("/api/logs/demo.log").status_code, 200)


if __name__ == "__main__":
    unittest.main()
