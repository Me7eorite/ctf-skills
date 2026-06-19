"""HTTP contract tests for resource deletion endpoints."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import services
from services import ResourceDeletionConflictError, ResourceDeletionNotFoundError
from web.build_attempts_endpoints import register_build_attempts_endpoints
from web.design_task_endpoints import register_design_task_read_endpoints
from web.research_endpoints import register_research_endpoints


class _Result:
    def __init__(self, resource_type: str, resource_id):
        self.resource_type = resource_type
        self.resource_id = resource_id

    def to_dict(self):
        return {
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "deleted": [],
            "retained": [],
            "skipped": [],
            "quarantined": [],
            "warnings": [],
        }


class _FakeDeletionService:
    mode = "ok"
    seen = []

    def __init__(self, **_kwargs):
        pass

    def delete_generation_request(self, request_id, *, delete_artifacts=False):
        return self._delete("generation_request", request_id, delete_artifacts)

    def delete_design_task(self, task_id, *, delete_artifacts=False):
        return self._delete("design_task", task_id, delete_artifacts)

    def delete_build_attempt(self, attempt_id, *, delete_artifacts=False):
        return self._delete("build_attempt", attempt_id, delete_artifacts)

    def _delete(self, resource_type, resource_id, delete_artifacts):
        self.seen.append((resource_type, resource_id, delete_artifacts))
        if self.mode == "missing":
            raise ResourceDeletionNotFoundError("missing")
        if self.mode == "conflict":
            raise ResourceDeletionConflictError("active")
        return _Result(resource_type, resource_id)


def _client(monkeypatch):
    _FakeDeletionService.mode = "ok"
    _FakeDeletionService.seen = []
    monkeypatch.setattr(services, "ResourceDeletionService", _FakeDeletionService)
    app = FastAPI()
    register_research_endpoints(app)
    register_design_task_read_endpoints(app)
    register_build_attempts_endpoints(app)
    return TestClient(app)


def test_delete_endpoints_accept_explicit_artifact_flag(monkeypatch):
    client = _client(monkeypatch)
    request_id = uuid4()
    task_id = uuid4()
    attempt_id = uuid4()

    assert client.delete(
        f"/api/research/requests/{request_id}?delete_artifacts=true"
    ).json()["resource_type"] == "generation_request"
    assert client.delete(
        f"/api/design-tasks/{task_id}?delete_artifacts=false"
    ).json()["resource_type"] == "design_task"
    assert client.delete(
        f"/api/build-attempts/{attempt_id}?delete_artifacts=true"
    ).json()["resource_type"] == "build_attempt"

    assert _FakeDeletionService.seen == [
        ("generation_request", request_id, True),
        ("design_task", task_id, False),
        ("build_attempt", attempt_id, True),
    ]


def test_delete_endpoints_default_to_retaining_artifacts(monkeypatch):
    client = _client(monkeypatch)
    ids = [uuid4(), uuid4(), uuid4()]

    assert client.delete(f"/api/research/requests/{ids[0]}").status_code == 200
    assert client.delete(f"/api/design-tasks/{ids[1]}").status_code == 200
    assert client.delete(f"/api/build-attempts/{ids[2]}").status_code == 200

    assert [seen[2] for seen in _FakeDeletionService.seen] == [False, False, False]


def test_delete_endpoints_map_not_found_and_conflict(monkeypatch):
    client = _client(monkeypatch)
    _FakeDeletionService.mode = "missing"
    missing_urls = [
        f"/api/research/requests/{uuid4()}",
        f"/api/design-tasks/{uuid4()}",
        f"/api/build-attempts/{uuid4()}",
    ]
    assert [client.delete(url).status_code for url in missing_urls] == [404, 404, 404]

    _FakeDeletionService.mode = "conflict"
    conflict_urls = [
        f"/api/research/requests/{uuid4()}",
        f"/api/design-tasks/{uuid4()}",
        f"/api/build-attempts/{uuid4()}",
    ]
    responses = [client.delete(url) for url in conflict_urls]
    assert [response.status_code for response in responses] == [409, 409, 409]
    assert all(response.json()["detail"] == "active" for response in responses)


def test_malformed_delete_ids_are_not_found(monkeypatch):
    client = _client(monkeypatch)
    assert client.delete("/api/research/requests/not-a-uuid").status_code == 404
    assert client.delete("/api/design-tasks/not-a-uuid").status_code == 404
    assert client.delete("/api/build-attempts/not-a-uuid").status_code == 404
