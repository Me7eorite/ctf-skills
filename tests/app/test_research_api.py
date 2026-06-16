"""HTTP tests for the Section 10 read endpoints.

The endpoints open a fresh `persistence.session.transaction()` per
request. Tests patch `persistence.session.transaction` to yield a stub
session whose only consumer is the patched `ResearchRepository`. This
keeps the test in-process without a real Postgres.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from core.paths import ProjectPaths
from domain.research import (
    ChallengeCategory,
    GenerationRequest,
    HermesProfileBinding,
    ResearchFinding,
    ResearchRun,
    ResearchSource,
)
from web.dashboard import DashboardService
from web.server import create_app

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _client(stub_repo, *, scalar=None, scalars=None) -> TestClient:
    """Build a TestClient whose research endpoints see `stub_repo`."""
    temp = tempfile.TemporaryDirectory()
    paths = ProjectPaths(root=Path(temp.name), repository=Path(temp.name))
    paths.initialize()
    service = DashboardService(paths)
    app = create_app(service)

    # 中文注释：fake session 暴露 scalar / scalars，供 bindings 端点用 SQLAlchemy 查询。
    fake_session = SimpleNamespace(
        scalar=scalar if scalar is not None else (lambda _stmt: None),
        scalars=scalars if scalars is not None else (lambda _stmt: []),
    )

    @contextlib.contextmanager
    def _ctx():
        yield fake_session

    client = TestClient(app)
    client._patches = [  # type: ignore[attr-defined]
        patch("persistence.session.transaction", _ctx),
        patch(
            "persistence.repositories.ResearchRepository", return_value=stub_repo
        ),
    ]
    for p in client._patches:  # type: ignore[attr-defined]
        p.start()
    client._temp = temp  # type: ignore[attr-defined]
    return client


def _close(client: TestClient) -> None:
    for p in client._patches:  # type: ignore[attr-defined]
        p.stop()
    client._temp.cleanup()  # type: ignore[attr-defined]


def _make_request(
    *,
    request_id: UUID | None = None,
    category: str = "web",
    topic: str = "SQLi",
    status: str = "draft",
) -> GenerationRequest:
    request_id = request_id or uuid4()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return GenerationRequest(
        id=request_id,
        category=category,
        topic=topic,
        target_count=5,
        difficulty_distribution=MappingProxyType({"easy": 5}),
        runtime_constraints=MappingProxyType({}),
        seed_urls=(),
        max_attempts=3,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_run(
    *,
    run_id: UUID | None = None,
    request_id: UUID | None = None,
    attempt: int = 1,
    status: str = "queued",
) -> ResearchRun:
    return ResearchRun(
        id=run_id or uuid4(),
        generation_request_id=request_id or uuid4(),
        parent_run_id=None,
        attempt=attempt,
        status=status,
        claimed_by=None,
        claim_token=None,
        claimed_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
        last_error=None,
        hermes_log_path=None,
        profile_name_used=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_binding(
    *, role: str = "research", profile_name: str = "default"
) -> HermesProfileBinding:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return HermesProfileBinding(
        role=role,
        profile_name=profile_name,
        description=None,
        status="enabled",
        last_used_at=None,
        last_used_run_id=None,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# 10.4 GET /api/research/categories
# ---------------------------------------------------------------------------


class CategoriesEndpointTests(unittest.TestCase):
    def test_returns_each_category_row(self):
        # 中文注释：列出所有 challenge_categories 行，按 code 排序。
        repo = SimpleNamespace(
            list_categories=lambda: [
                ChallengeCategory("pwn", "Pwn", "二进制"),
                ChallengeCategory("re", "Reverse", "逆向"),
                ChallengeCategory("web", "Web 安全", "HTTP"),
            ]
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/categories")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                resp.json(),
                [
                    {"code": "pwn", "display_name": "Pwn", "description": "二进制"},
                    {"code": "re", "display_name": "Reverse", "description": "逆向"},
                    {"code": "web", "display_name": "Web 安全", "description": "HTTP"},
                ],
            )
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.1 GET /api/research/requests
# ---------------------------------------------------------------------------


class RequestsListEndpointTests(unittest.TestCase):
    def test_returns_filtered_requests(self):
        req = _make_request(category="web", topic="A")
        repo = SimpleNamespace(
            list_categories=lambda: [
                ChallengeCategory("web", "Web", None),
                ChallengeCategory("pwn", "Pwn", None),
            ],
            list_generation_requests=lambda **_kw: [req],
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/requests?category=web")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["id"], str(req.id))
            self.assertEqual(payload[0]["category"], "web")
            self.assertEqual(payload[0]["topic"], "A")
        finally:
            _close(client)

    def test_unknown_category_returns_400_with_allowed_set(self):
        repo = SimpleNamespace(
            list_categories=lambda: [
                ChallengeCategory("web", "Web", None),
                ChallengeCategory("pwn", "Pwn", None),
            ],
            list_generation_requests=lambda **_kw: [],
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/requests?category=crypto")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("crypto", resp.json()["detail"])
            self.assertIn("web", resp.json()["detail"])
        finally:
            _close(client)

    def test_invalid_status_returns_400(self):
        # 中文注释：spec 10.1 应在 enum 命中前拒绝非法 status，避免 500。
        repo = SimpleNamespace(
            list_categories=lambda: [],
            list_generation_requests=lambda **_kw: [],
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/requests?status=bogus")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("bogus", resp.json()["detail"])
            self.assertIn("draft", resp.json()["detail"])
        finally:
            _close(client)

    def test_no_filters_returns_all(self):
        req_a = _make_request(category="web", topic="A")
        req_b = _make_request(category="pwn", topic="B")
        repo = SimpleNamespace(
            list_categories=lambda: [],
            list_generation_requests=lambda **_kw: [req_a, req_b],
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/requests")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()), 2)
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.2 GET /api/research/requests/{id}
# ---------------------------------------------------------------------------


class RequestDetailEndpointTests(unittest.TestCase):
    def test_returns_full_detail(self):
        req = _make_request()
        run_old = _make_run(request_id=req.id, attempt=1, status="failed")
        run_new = _make_run(request_id=req.id, attempt=2, status="completed")
        # Make sure list_runs returns the newer one with a later created_at.
        run_new = ResearchRun(
            **{
                **{f: getattr(run_new, f) for f in run_new.__dataclass_fields__},
                "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
            }
        )

        src = ResearchSource(
            id=uuid4(),
            research_run_id=run_new.id,
            url="https://x",
            title="T",
            summary="S",
            content_hash="h",
            fetched_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            raw_text_path=None,
        )
        finding_a = ResearchFinding(
            id=uuid4(), research_run_id=run_new.id, kind="technique",
            label="LA", summary="SA",
        )
        finding_b = ResearchFinding(
            id=uuid4(), research_run_id=run_new.id, kind="variant",
            label="LB", summary="SB",
        )
        finding_c = ResearchFinding(
            id=uuid4(), research_run_id=run_new.id, kind="technique",
            label="LC", summary="SC",
        )

        repo = SimpleNamespace(
            get_generation_request=lambda _: req,
            list_runs=lambda **_kw: [run_old, run_new],
            get_latest_run_for_request=lambda _: run_new,
            list_sources=lambda _: [src],
            list_findings=lambda _: [finding_a, finding_b, finding_c],
        )
        client = _client(repo)
        try:
            resp = client.get(f"/api/research/requests/{req.id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["request"]["id"], str(req.id))
            self.assertEqual(payload["latest_run"]["id"], str(run_new.id))
            self.assertEqual(len(payload["runs"]), 2)
            self.assertEqual(len(payload["sources"]), 1)
            # Spec 10.2: findings grouped by kind.
            self.assertEqual(len(payload["findings_by_kind"]["technique"]), 2)
            self.assertEqual(len(payload["findings_by_kind"]["variant"]), 1)
        finally:
            _close(client)

    def test_latest_run_uses_unpaginated_repository_lookup(self):
        req = _make_request()
        run_visible = _make_run(request_id=req.id, attempt=100, status="failed")
        run_latest = _make_run(request_id=req.id, attempt=101, status="completed")

        repo = SimpleNamespace(
            get_generation_request=lambda _: req,
            list_runs=lambda **_kw: [run_visible],
            get_latest_run_for_request=lambda _: run_latest,
            list_sources=lambda run_id: [] if run_id == run_latest.id else [object()],
            list_findings=lambda run_id: [] if run_id == run_latest.id else [object()],
        )
        client = _client(repo)
        try:
            resp = client.get(f"/api/research/requests/{req.id}")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["latest_run"]["id"], str(run_latest.id))
            self.assertEqual([r["id"] for r in payload["runs"]], [str(run_visible.id)])
            self.assertEqual(payload["sources"], [])
            self.assertEqual(payload["findings_by_kind"], {})
        finally:
            _close(client)

    def test_unknown_id_returns_404(self):
        repo = SimpleNamespace(get_generation_request=lambda _: None)
        client = _client(repo)
        try:
            resp = client.get(f"/api/research/requests/{uuid4()}")
            self.assertEqual(resp.status_code, 404)
        finally:
            _close(client)

    def test_non_uuid_returns_404(self):
        repo = SimpleNamespace(get_generation_request=lambda _: None)
        client = _client(repo)
        try:
            resp = client.get("/api/research/requests/not-a-uuid")
            self.assertEqual(resp.status_code, 404)
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.7 GET /api/research/runs
# ---------------------------------------------------------------------------


class RunsListEndpointTests(unittest.TestCase):
    def test_returns_runs_joined_with_category(self):
        run = _make_run(status="running")
        # Spec 10.7: real SQL JOIN — endpoint uses list_runs_with_category
        # which returns [(ResearchRun, category)] tuples, no extra get calls.
        repo = SimpleNamespace(
            list_runs_with_category=lambda **_kw: [(run, "web")],
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/runs?status=running")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["id"], str(run.id))
            self.assertEqual(payload[0]["status"], "running")
            self.assertEqual(payload[0]["category"], "web")
        finally:
            _close(client)

    def test_invalid_generation_request_id_returns_400(self):
        repo = SimpleNamespace(list_runs_with_category=lambda **_kw: [])
        client = _client(repo)
        try:
            resp = client.get(
                "/api/research/runs?generation_request_id=not-a-uuid"
            )
            self.assertEqual(resp.status_code, 400)
        finally:
            _close(client)

    def test_invalid_status_returns_400(self):
        # 中文注释：spec 10.7 应在 enum 命中前拒绝非法 status，避免 500。
        repo = SimpleNamespace(list_runs_with_category=lambda **_kw: [])
        client = _client(repo)
        try:
            resp = client.get("/api/research/runs?status=bogus")
            self.assertEqual(resp.status_code, 400)
            self.assertIn("bogus", resp.json()["detail"])
            self.assertIn("queued", resp.json()["detail"])
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.8 GET /api/research/queue/stats
# ---------------------------------------------------------------------------


class QueueStatsEndpointTests(unittest.TestCase):
    def test_returns_aggregate_with_stringified_run_ids(self):
        near_id = uuid4()
        repo = SimpleNamespace(
            queue_stats=lambda: {
                "queued": 3,
                "running": 2,
                "completed": 5,
                "failed": 1,
                "oldest_queued_age_seconds": 42.0,
                "runs_near_lease_expiry": [near_id],
            }
        )
        client = _client(repo)
        try:
            resp = client.get("/api/research/queue/stats")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["queued"], 3)
            self.assertEqual(payload["running"], 2)
            self.assertEqual(payload["completed"], 5)
            self.assertEqual(payload["failed"], 1)
            self.assertEqual(payload["oldest_queued_age_seconds"], 42.0)
            # UUIDs serialized to strings for JSON transport.
            self.assertEqual(payload["runs_near_lease_expiry"], [str(near_id)])
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.5 GET /api/profile/bindings
# ---------------------------------------------------------------------------


class BindingsListEndpointTests(unittest.TestCase):
    def test_returns_bindings_joined_with_display_name(self):
        binding = _make_binding(role="research", profile_name="default")
        repo = SimpleNamespace(list_bindings=lambda: [binding])
        # 中文注释：SQLAlchemy Result 暴露 .all()；用 SimpleNamespace 模拟。
        roles_result = SimpleNamespace(
            all=lambda: [SimpleNamespace(code="research", display_name="研究 Agent")]
        )
        client = _client(repo, scalars=lambda _stmt: roles_result)
        try:
            resp = client.get("/api/profile/bindings")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["role"], "research")
            self.assertEqual(payload[0]["display_name"], "研究 Agent")
            self.assertEqual(payload[0]["profile_name"], "default")
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# 10.6 GET /api/profile/bindings/{role}
# ---------------------------------------------------------------------------


class BindingDetailEndpointTests(unittest.TestCase):
    def test_known_role_returns_binding(self):
        binding = _make_binding(role="research")
        repo = SimpleNamespace(get_binding=lambda _r: binding)
        client = _client(repo, scalar=lambda _stmt: "研究 Agent")
        try:
            resp = client.get("/api/profile/bindings/research")
            self.assertEqual(resp.status_code, 200)
            payload = resp.json()
            self.assertEqual(payload["role"], "research")
            self.assertEqual(payload["display_name"], "研究 Agent")
        finally:
            _close(client)

    def test_unknown_role_returns_404(self):
        repo = SimpleNamespace(get_binding=lambda _r: None)
        client = _client(repo)
        try:
            resp = client.get("/api/profile/bindings/planning")
            self.assertEqual(resp.status_code, 404)
            self.assertIn("planning", resp.json()["detail"])
        finally:
            _close(client)


# ---------------------------------------------------------------------------
# POST /api/research/requests
# ---------------------------------------------------------------------------


class SubmitRequestEndpointTests(unittest.TestCase):
    def _body(self, **overrides) -> dict:
        body = {
            "category": "web",
            "topic": "SQL injection sample",
            "target_count": 2,
            "difficulty_distribution": {"easy": 1, "medium": 1},
            "seed_urls": ["https://example.com/sqli"],
            "max_attempts": 3,
        }
        body.update(overrides)
        return body

    def test_happy_path_returns_201_with_ids(self):
        request = _make_request(category="web")
        run = _make_run(request_id=request.id, status="queued")

        captured: dict = {}

        class FakeJobService:
            def __init__(self, *_a, **_kw):
                pass

            def submit_request(self, **kwargs):
                captured.update(kwargs)
                return (request, run)

        client = _client(SimpleNamespace())
        try:
            with patch("services.ResearchJobService", FakeJobService):
                resp = client.post("/api/research/requests", json=self._body())
            self.assertEqual(resp.status_code, 201)
            payload = resp.json()
            self.assertEqual(payload["request_id"], str(request.id))
            self.assertEqual(payload["run_id"], str(run.id))
            self.assertEqual(payload["category"], "web")
            self.assertEqual(payload["status"], "queued")
            # 中文注释：seed_urls 与 distribution 必须原样进入 service。
            self.assertEqual(captured["seed_urls"], ["https://example.com/sqli"])
            self.assertEqual(
                captured["difficulty_distribution"], {"easy": 1, "medium": 1}
            )
        finally:
            _close(client)

    def test_missing_category_returns_400(self):
        client = _client(SimpleNamespace())
        try:
            resp = client.post(
                "/api/research/requests",
                json={k: v for k, v in self._body().items() if k != "category"},
            )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("category", resp.json()["detail"])
        finally:
            _close(client)

    def test_non_positive_target_count_returns_400(self):
        client = _client(SimpleNamespace())
        try:
            resp = client.post(
                "/api/research/requests", json=self._body(target_count=0)
            )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("target_count", resp.json()["detail"])
        finally:
            _close(client)

    def test_invalid_distribution_returns_400(self):
        client = _client(SimpleNamespace())
        try:
            resp = client.post(
                "/api/research/requests", json=self._body(difficulty_distribution=[])
            )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("difficulty_distribution", resp.json()["detail"])
        finally:
            _close(client)

    def test_seed_urls_must_be_list_of_strings(self):
        client = _client(SimpleNamespace())
        try:
            resp = client.post(
                "/api/research/requests",
                json=self._body(seed_urls=[123, "ok"]),
            )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("seed_urls", resp.json()["detail"])
        finally:
            _close(client)

    def test_service_validation_error_translates_to_400(self):
        from domain.research_validators import ResearchValidationError as RVE

        class FakeJobService:
            def __init__(self, *_a, **_kw):
                pass

            def submit_request(self, **_kw):
                raise RVE("distribution sum 2 != target_count 3")

        client = _client(SimpleNamespace())
        try:
            with patch("services.ResearchJobService", FakeJobService):
                resp = client.post(
                    "/api/research/requests", json=self._body(target_count=3)
                )
            self.assertEqual(resp.status_code, 400)
            self.assertIn("distribution sum", resp.json()["detail"])
        finally:
            _close(client)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
