import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from auth import get_user_id
from adapters import get_adapter, AgentUnavailable
from api.deps import check_agent, require_capability, streaming_or_error, master_router
from schemas.chat import ChatRequest, SmartChatRequest

router = APIRouter(tags=["chat"])


@router.post("/agents/{agent_id}/sessions/{session_id}/chat")
async def chat(
    agent_id: str, session_id: str, payload: ChatRequest,
    user_id: str = Depends(get_user_id),
):
    check_agent(agent_id)
    gen = get_adapter(agent_id).stream_chat(
        user_id, session_id, payload.message)

    return await streaming_or_error(gen)


@router.post("/chat")
async def smart_chat(
    payload: SmartChatRequest,
    user_id: str = Depends(get_user_id)
):
    if payload.session_id is not None and payload.agent_id is None:
        raise HTTPException(
            422, "session_id требует agent_id: сессия принадлежит конкретному агенту"
        )

    agent_id = payload.agent_id or await master_router.route(payload.message)
    check_agent(agent_id)
    adapter = get_adapter(agent_id)

    session_id = payload.session_id
    if session_id is None:
        try:
            ar = await adapter.create_session(user_id, None)
        except AgentUnavailable as e:
            raise HTTPException(502, f"Агент {e.agent_id}: {e.detail}")
        if ar.status >= 400:
            raise HTTPException(
                ar.status, "Не удалось создать сессию у агента")
        session_id = json.loads(ar.content)["id"]

    gen = adapter.stream_chat(user_id, session_id, payload.message)
    
    try:
        first_chunk = await gen.__anext__()
    except StopAsyncIteration:
        first_chunk = None
    except AgentUnavailable as e:
        raise HTTPException(502, f"Агент {e.agent_id}: {e.detail}")

    async def body():
        meta = {
            "type": "metadata",
            "agent_id": agent_id,
            "session_id": str(session_id),
        }

        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n".encode()

        if first_chunk is not None:
            yield first_chunk
            async for chunk in gen:
                yield chunk

    resp = StreamingResponse(body(), media_type="text/event-stream")
    resp.headers["X-Agent-Id"] = agent_id
    resp.headers["X-Session-Id"] = str(session_id)

    return resp


@router.post("/agents/{agent_id}/ocr")
async def ocr(agent_id: str, file: UploadFile = File(...),
              user_id: str = Depends(get_user_id)):
    check_agent(agent_id)
    require_capability(agent_id, "ocr")
    content = await file.read()
    gen = get_adapter(agent_id).run_ocr(user_id, file.filename, content)
    return await streaming_or_error(gen)
