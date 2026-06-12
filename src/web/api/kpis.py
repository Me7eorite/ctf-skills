"""Overview KPIs at ``GET /api/kpis``.

``avg_quality_score`` is intentionally ``None`` until the Phase 1 quality
pipeline ships; it must be ``null`` rather than ``0`` so the SPA can render
the "no data yet" state without ambiguity.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from web.dashboard import DashboardService


def create_kpis_router(service: DashboardService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/kpis")
    def get_kpis() -> JSONResponse:
        summary = service.state().get("summary", {})
        total = int(summary.get("challenges", 0))
        validated = int(summary.get("validated", 0))
        pass_rate = (validated / total) if total else 0.0
        return JSONResponse(
            {
                "total_challenges": total,
                "pass_rate": round(pass_rate, 4),
                "avg_generation_minutes": None,
                "avg_quality_score": None,
            }
        )

    return router
