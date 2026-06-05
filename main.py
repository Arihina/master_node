from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse

import httpx

from registry import AGENTS
from router.router_service import MasterRouter
from dispatcher import AgentDispatcher
from schemas.chat import (
    RouteRequest,
    CreateSessionRequest,
    RenameSessionRequest,
    ChatRequest,
    FeedbackRequest,
    SmartChatRequest,
)

router = MasterRouter()
dispatcher = AgentDispatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispatcher.aclose()


app = FastAPI(lifespan=lifespan)


def _check_agent(agent_id: str):
    agent = AGENTS.get(agent_id)
    if agent is None or not agent.enabled:
        raise HTTPException(404, f"Агент {agent_id} не найден или выключен")
    if not agent.url:
        raise HTTPException(503, f"У агента {agent_id} не задан url")


def _relay(resp: httpx.Response) -> Response:
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


@app.get("/agents")
async def agents():
    return [
        {"id": a.id, "name": a.name}
        for a in AGENTS.values()
        if a.enabled
    ]


@app.post("/route")
async def route_message(payload: RouteRequest):
    return {"agent": await router.route(payload.message)}


@app.post("/agents/{agent_id}/sessions")
async def create_session(
    agent_id: str,
    payload: CreateSessionRequest,
    x_user_id: str = Header(...),
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "POST", "/sessions", x_user_id, payload.model_dump()
    )
    return _relay(resp)


@app.get("/agents/{agent_id}/sessions")
async def list_sessions(agent_id: str, x_user_id: str = Header(...)):
    _check_agent(agent_id)
    resp = await dispatcher.forward(agent_id, "GET", "/sessions", x_user_id)
    return _relay(resp)


@app.get("/agents/{agent_id}/sessions/{session_id}/messages")
async def session_messages(
    agent_id: str, session_id: int, x_user_id: str = Header(...)
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "GET", f"/sessions/{session_id}/messages", x_user_id
    )
    return _relay(resp)


@app.patch("/agents/{agent_id}/sessions/{session_id}")
async def rename_session(
    agent_id: str,
    session_id: int,
    payload: RenameSessionRequest,
    x_user_id: str = Header(...),
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "PATCH", f"/sessions/{session_id}", x_user_id, payload.model_dump()
    )
    return _relay(resp)


@app.delete("/agents/{agent_id}/sessions/{session_id}")
async def delete_session(
    agent_id: str, session_id: int, x_user_id: str = Header(...)
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "DELETE", f"/sessions/{session_id}", x_user_id
    )
    return _relay(resp)


@app.post("/agents/{agent_id}/sessions/{session_id}/chat")
async def chat(
    agent_id: str,
    session_id: int,
    payload: ChatRequest,
    x_user_id: str = Header(...),
):
    _check_agent(agent_id)
    return StreamingResponse(
        dispatcher.stream_chat(agent_id, session_id,
                               x_user_id, payload.message),
        media_type="text/event-stream",
    )


@app.post("/agents/{agent_id}/messages/{message_id}/feedback")
async def set_feedback(
    agent_id: str,
    message_id: int,
    payload: FeedbackRequest,
    x_user_id: str = Header(...),
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "POST", f"/messages/{message_id}/feedback",
        x_user_id, payload.model_dump(),
    )
    return _relay(resp)


@app.get("/agents/{agent_id}/messages/{message_id}/feedback")
async def get_feedback(
    agent_id: str, message_id: int, x_user_id: str = Header(...)
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "GET", f"/messages/{message_id}/feedback", x_user_id
    )
    return _relay(resp)


@app.delete("/agents/{agent_id}/messages/{message_id}/feedback")
async def delete_feedback(
    agent_id: str, message_id: int, x_user_id: str = Header(...)
):
    _check_agent(agent_id)
    resp = await dispatcher.forward(
        agent_id, "DELETE", f"/messages/{message_id}/feedback", x_user_id
    )
    return _relay(resp)


@app.post("/chat")
async def smart_chat(payload: SmartChatRequest, x_user_id: str = Header(...)):
    agent_id = payload.agent_id or await router.route(payload.message)
    _check_agent(agent_id)

    session_id = payload.session_id
    if session_id is None:
        resp = await dispatcher.forward(agent_id, "POST", "/sessions", x_user_id, {})
        if resp.status_code >= 400:
            raise HTTPException(
                resp.status_code, "Не удалось создать сессию у агента")
        session_id = resp.json()["id"]

    return StreamingResponse(
        dispatcher.stream_chat(agent_id, session_id,
                               x_user_id, payload.message),
        media_type="text/event-stream",
        headers={"X-Agent-Id": agent_id, "X-Session-Id": str(session_id)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        timeout_keep_alive=300,
    )
