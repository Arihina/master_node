from __future__ import annotations

from fastapi import HTTPException, Response
from fastapi.responses import StreamingResponse

from registry import AGENTS
from routing.router_service import MasterRouter
from adapters import AgentResponse, AgentUnavailable

master_router = MasterRouter()


def check_agent(agent_id: str) -> None:
    agent = AGENTS.get(agent_id)
    if agent is None or not agent.enabled:
        raise HTTPException(404, f"Агент {agent_id} не найден или выключен")

    if agent.transport == "contract" and not agent.url:
        raise HTTPException(503, f"У агента {agent_id} не задан url")


def relay(ar: AgentResponse) -> Response:
    return Response(content=ar.content, status_code=ar.status, media_type=ar.media_type)


async def streaming_or_error(gen) -> StreamingResponse:
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
