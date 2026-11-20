import time

from fastapi import APIRouter, Depends, HTTPException

from auth import get_user_id
from registry import AGENTS
from schemas.chat import RouteRequest
from api.deps import master_router

router = APIRouter(tags=["meta"])

MODEL_OWNER = " "


def _model_object(model_id: str, name: str, created: int) -> dict:
    return {
        "id": model_id,
        "name": name,
        "object": "model",
        "created": created,
        "owned_by": MODEL_OWNER,
    }


def _is_model(agent) -> bool:
    return agent.enabled and bool(agent.contract_forms)


@router.get("/v1/models")
async def list_models():
    created = int(time.time())
    data = [_model_object(a.id, a.name, created)
            for a in AGENTS.values() if _is_model(a)]
    data.append(_model_object("auto", "auto", created))
    return {"object": "list", "data": data}


@router.get("/v1/models/{model_id}")
async def retrieve_model(model_id: str):
    created = int(time.time())

    if model_id == "auto":
        return _model_object("auto", "auto", created)

    agent = AGENTS.get(model_id)
    if agent is None or not _is_model(agent):
        raise HTTPException(404, f"Модель {model_id} не найдена")

    return _model_object(agent.id, agent.name, created)


@router.post("/route")
async def route_message(payload: RouteRequest,
                        _user: str = Depends(get_user_id)):
    """Resolve-ручка для фронтенда (и отладки): какого агента выбрал бы
    model="auto" для этого текста, без реального вызова генерации.

    Нужна, чтобы фронт мог создать чат ДО первого сообщения: сначала узнать
    agent_id, затем POST /agents/{agent_id}/v1/platform/conversations, и уже
    потом слать сообщения с model=<agent_id> и conversation_id. Не часть
    OpenAI-контракта; доступна любому аутентифицированному пользователю."""
    return {"agent": await master_router.route(payload.message)}
