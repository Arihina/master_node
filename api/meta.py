from fastapi import APIRouter

from registry import AGENTS
from schemas.chat import RouteRequest
from api.deps import master_router

router = APIRouter(tags=["meta"])


@router.get("/agents")
async def agents():
    return [{"id": a.id, "name": a.name} for a in AGENTS.values() if a.enabled]


@router.post("/route")
async def route_message(payload: RouteRequest):
    return {"agent": await master_router.route(payload.message)}
