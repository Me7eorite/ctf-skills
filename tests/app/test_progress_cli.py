"""Tests for the progress CLI wrapper."""

from __future__ import annotations

import json

import pytest

import cli
from persistence import PersistenceConnectionError


class _FakeProgressStore:
    def record(self, **kwargs):
        return {
            "event_id": 7,
            "shard": kwargs["shard"],
            "challenge_id": kwargs["challenge_id"],
            "worker": kwargs["worker"],
            "stage": kwargs["stage"],
            "status": kwargs["status"],
            "percent": 53,
            "message": kwargs["message"],
            "updated_at": "2026-06-18T00:00:00Z",
        }


def _argv(*extra: str) -> list[str]:
    return [
        "challenge-factory",
        "progress",
        "--shard",
        "x.json",
        "--stage",
        "build",
        "--status",
        "running",
        *extra,
    ]


def test_progress_cli_prints_recorded_json(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", _argv("--message", "building"))
    monkeypatch.setattr(cli, "make_postgres_progress_store", _FakeProgressStore)

    cli.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["event_id"] == 7
    assert payload["message"] == "building"
    assert captured.err == ""


def test_progress_cli_fails_loud_without_best_effort(monkeypatch, capsys):
    def raise_connection():
        raise PersistenceConnectionError("db down")

    monkeypatch.setattr(cli.sys, "argv", _argv())
    monkeypatch.setattr(cli, "make_postgres_progress_store", raise_connection)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert captured.out == ""
    assert "PersistenceConnectionError" in captured.err


def test_progress_cli_best_effort_warns_without_stdout(monkeypatch, capsys):
    def raise_connection():
        raise PersistenceConnectionError("db down")

    monkeypatch.setattr(cli.sys, "argv", _argv("--best-effort"))
    monkeypatch.setattr(cli, "make_postgres_progress_store", raise_connection)

    cli.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "progress write skipped" in captured.err
