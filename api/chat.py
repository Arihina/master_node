import json

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from auth import get_user_id
from adapters import get_adapter, AgentUnavailable
from api.deps import check_agent, require_capability, streaming_or_error, master_router

router = APIRouter(tags=["chat"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_CHAT_BODY_SCHEMA = {
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            }
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["message"],
            }
        },
    },
    "required": True,
}

_SMART_CHAT_BODY_SCHEMA = {
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "agent_id": {"type": "string", "nullable": True},
                    "session_id": {"type": "string", "nullable": True},
                },
                "required": ["message"],
            }
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "agent_id": {"type": "string", "nullable": True},
                    "session_id": {"type": "string", "nullable": True},
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["message"],
            }
        },
    },
    "required": True,
}


async def _read_capped(upload: StarletteUploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Файл больше {limit // (1024 * 1024)} МБ")
    return data


_MISSING = object()


async def _parse_body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data") or content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        form = await request.form()
        message = form.get("message", _MISSING)
        attachment = None
        upload = form.get("file")
        if isinstance(upload, StarletteUploadFile):
            content = await _read_capped(upload)
            attachment = (upload.filename, content)
        result = {
            "message": message,
            "agent_id": form.get("agent_id") or None,
            "session_id": form.get("session_id") or None,
            "attachment": attachment,
        }
    elif content_type.startswith("application/json"):
        body = await request.json()
        result = {
            "message": body.get("message", _MISSING),
            "agent_id": body.get("agent_id"),
            "session_id": body.get("session_id"),
            "attachment": None,
        }
    else:
        raise HTTPException(
            415, f"Неподдерживаемый Content-Type: {content_type or '<пусто>'}")

    if result["message"] is _MISSING or not isinstance(result["message"], str):
        raise HTTPException(422, "message обязателен и должен быть строкой")

    return result


@router.post(
    "/agents/{agent_id}/sessions/{session_id}/chat",
    openapi_extra={"requestBody": _CHAT_BODY_SCHEMA},
)
async def chat(
    agent_id: str, session_id: str, request: Request,
    user_id: str = Depends(get_user_id),
):
    check_agent(agent_id)
    parsed = await _parse_body(request)
    gen = get_adapter(agent_id).stream_chat(
        user_id, session_id, parsed["message"], attachment=parsed["attachment"])

    return await streaming_or_error(gen)


@router.post("/chat", openapi_extra={"requestBody": _SMART_CHAT_BODY_SCHEMA})
async def smart_chat(
    request: Request,
    user_id: str = Depends(get_user_id)
):
    parsed = await _parse_body(request)
    message = parsed["message"]
    agent_id = parsed["agent_id"]
    session_id = parsed["session_id"]
    attachment = parsed["attachment"]

    if session_id is not None and agent_id is None:
        raise HTTPException(
            422, "session_id требует agent_id: сессия принадлежит конкретному агенту"
        )

    agent_id = agent_id or await master_router.route(message)
    check_agent(agent_id)
    adapter = get_adapter(agent_id)

    if session_id is None:
        try:
            ar = await adapter.create_session(user_id, None)
        except AgentUnavailable as e:
            raise HTTPException(502, f"Агент {e.agent_id}: {e.detail}")
        if ar.status >= 400:
            raise HTTPException(
                ar.status, "Не удалось создать сессию у агента")
        session_id = json.loads(ar.content)["id"]

    gen = adapter.stream_chat(
        user_id, session_id, message, attachment=attachment)

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
