import time

from fastapi import APIRouter

from registry import AGENTS
from schemas.chat import RouteRequest
from api.deps import master_router

router = APIRouter(tags=["meta"])


@router.get("/v1/models")
async def list_models():
    created = int(time.time())
    data = [
        {"id": a.id, "object": "model", "created": created}
        for a in AGENTS.values() if a.enabled
    ]
    data.append({"id": "auto", "object": "model",
                "created": created})
    return {"object": "list", "data": data}


@router.post("/route")
async def route_message(payload: RouteRequest):
    """Внутренняя debug-ручка — не часть OpenAI-контракта. Показывает, какого
    агента выбрал бы model="auto" для этого текста, без реального вызова."""
    return {"agent": await master_router.route(payload.message)}
