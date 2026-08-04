from __future__ import annotations

from typing import Awaitable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from registry import AGENTS
from routing.router_service import MasterRouter
from adapters import ProxyResult, AgentUnavailable

master_router = MasterRouter()


def check_agent(agent_id: str) -> None:
    agent = AGENTS.get(agent_id)
    if agent is None or not agent.enabled:
        raise HTTPException(404, f"Агент {agent_id} не найден или выключен")

    if agent.transport == "contract" and not agent.url:
        raise HTTPException(503, f"У агента {agent_id} не задан url")


async def proxy_response(result: Awaitable[ProxyResult]) -> StreamingResponse:
    """Основной путь для контрактных ручек (proxy()). Статус и content-type
    агента идут насквозь как есть — включая 4xx/5xx с телом вида
    {"error": {...}}, агент уже отдаёт его в нужном мастеру формате."""
    try:
        r = await result
    except AgentUnavailable as e:
        raise HTTPException(502, f"Агент {e.agent_id}: {e.detail}")
    return StreamingResponse(r.body, status_code=r.status, media_type=r.content_type)


async def streaming_or_error(gen) -> StreamingResponse:
    """Для capability-генераторов старого стиля (run_ocr) — статус агента
    заранее неизвестен, поэтому проверяем через первый чанк/AgentUnavailable."""
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        return StreamingResponse(iter(()), media_type="text/event-stream")
    except AgentUnavailable as e:
        raise HTTPException(502, f"Агент {e.agent_id}: {e.detail}")

    async def body():
        yield first
        async for chunk in gen:
            yield chunk

    return StreamingResponse(body(), media_type="text/event-stream")


def require_capability(agent_id: str, capability: str) -> None:
    agent = AGENTS[agent_id]
    if capability not in agent.capabilities:
        raise HTTPException(
            404, f"Агент {agent_id} не поддерживает '{capability}'")
