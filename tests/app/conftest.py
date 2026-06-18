"""Shared app-test fixtures."""

from __future__ import annotations

import pytest

from core.state import InMemoryProgressStore, ProgressStore


@pytest.fixture
def progress_store() -> ProgressStore:
    return InMemoryProgressStore()
