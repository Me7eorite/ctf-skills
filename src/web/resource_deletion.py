"""Composition helper for resource-deletion HTTP endpoints."""

from __future__ import annotations

from fastapi import FastAPI

import services
from core.paths import ProjectPaths


def deletion_service(app: FastAPI):
    """Build the service with the dashboard's configured progress adapter."""
    paths = getattr(app.state, "project_paths", None) or ProjectPaths.discover()
    progress = getattr(app.state, "progress_store", None)
    return services.ResourceDeletionService(paths=paths, progress=progress)
