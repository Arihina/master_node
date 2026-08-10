import time

from fastapi import APIRouter, HTTPException

from registry import AGENTS
from schemas.chat import RouteRequest
from api.deps import master_router

router = APIRouter(tags=["meta"])


MODEL_OWNER = "pass"


def _model_object(model_id: str, created: int) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "created": created,
        "owned_by": MODEL_OWNER,
    }


@router.get("/v1/models")
async def list_models():
    created = int(time.time())
    data = [_model_object(a.id, created)
            for a in AGENTS.values() if a.enabled]
    data.append(_model_object("auto", created))

    return {"object": "list", "data": data}


@router.get("/v1/models/{model_id}")
async def retrieve_model(model_id: str):
    created = int(time.time())

    if model_id == "auto":
        return _model_object("auto", created)

    agent = AGENTS.get(model_id)
    if agent is None or not agent.enabled:
        raise HTTPException(404, f"Модель {model_id} не найдена")

    return _model_object(agent.id, created)


@router.post("/route")
async def route_message(payload: RouteRequest):
    """Внутренняя debug-ручка — не часть OpenAI-контракта. Показывает, какого
    агента выбрал бы model="auto" для этого текста, без реального вызова."""
    return {"agent": await master_router.route(payload.message)}
