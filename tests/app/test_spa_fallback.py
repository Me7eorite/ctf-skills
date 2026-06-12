"""SPA fallback contract: APIs return JSON; everything else returns the SPA shell."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from web.dashboard import DashboardService
from web.server import create_app


class SpaFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ProjectPaths(
            root=Path(self.temp.name) / "factory",
            repository=Path(self.temp.name),
        )
        self.paths.initialize()
        # The catch-all reads ``paths.static / "dist" / "index.html"`` — that is
        # the real built file shipped with the package, so the test relies on the
        # frontend build having been run at least once. Confirm and bail early
        # with a clear message if not.
        self.dist_index = self.paths.static / "dist" / "index.html"
        if not self.dist_index.is_file():
            self.skipTest("frontend dist/index.html missing; run `cd frontend && npm run build`")

    def _client(self) -> TestClient:
        return TestClient(create_app(DashboardService(self.paths)))

    def test_api_state_returns_json(self) -> None:
        with self._client() as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        payload = response.json()
        self.assertIn("summary", payload)

    def test_spa_path_returns_html_shell(self) -> None:
        with self._client() as client:
            response = client.get("/generate/runs/web-0001-0001.json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn('<div id="app">', response.text)

    def test_unmatched_root_returns_html_shell(self) -> None:
        with self._client() as client:
            response = client.get("/anything/else")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<div id="app">', response.text)

    def test_dist_assets_are_immutable_cached(self) -> None:
        # Pick a real hashed asset from the build output.
        assets_dir = self.paths.static / "dist" / "assets"
        first_asset = next(assets_dir.glob("index-*.js"), None)
        if first_asset is None:
            self.skipTest("no hashed index js asset present")
        with self._client() as client:
            response = client.get(f"/static/dist/assets/{first_asset.name}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )

    def test_dist_asset_path_traversal_rejected(self) -> None:
        with self._client() as client:
            response = client.get("/static/dist/%2E%2E/%2E%2E/etc/passwd")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
