"""Capability model exposed at ``GET /api/capabilities``."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

CAPABILITIES: list[dict[str, str]] = [
    {
        "id": "challenge-generator",
        "name": "题目生成器",
        "status": "enabled",
        "description": "为 web/pwn/re 三类题型批量生成题面、源码与 solve。",
        "icon": "Boxes",
        "route": "/generate/new",
    },
    {
        "id": "scenario-builder",
        "name": "情景生成",
        "status": "coming_soon",
        "description": "围绕主题装配多题情景包，串成连贯的攻防练习。",
        "icon": "GitBranch",
        "route": "/scenario",
    },
    {
        "id": "learning-materials",
        "name": "学习资料",
        "status": "coming_soon",
        "description": "把题面、知识点、参考资料编排为可发布的讲义。",
        "icon": "BookOpen",
        "route": "/learning/materials",
    },
    {
        "id": "learning-paths",
        "name": "学习路线",
        "status": "coming_soon",
        "description": "按难度与领域规划带依赖关系的学习路径。",
        "icon": "Route",
        "route": "/learning/paths",
    },
]


def create_capabilities_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/capabilities")
    def get_capabilities() -> JSONResponse:
        return JSONResponse(list(CAPABILITIES))

    return router
