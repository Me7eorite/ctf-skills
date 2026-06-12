"""HTTP routers for the modernised web console."""

from web.api.capabilities import create_capabilities_router
from web.api.kpis import create_kpis_router
from web.api.llm import create_llm_router
from web.api.presets import create_presets_router
from web.api.runs import create_runs_router

__all__ = [
    "create_capabilities_router",
    "create_kpis_router",
    "create_llm_router",
    "create_presets_router",
    "create_runs_router",
]
