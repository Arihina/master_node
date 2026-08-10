from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from auth import get_user_id
from adapters import get_adapter
from api.content import (
    CHAT_ATTACHMENT_TYPES, CHAT_TEXT_TYPES, extract_text, has_attachment,
)
from api.deps import check_agent, require_capability, streaming_or_error, proxy_response
from api.resolve import resolve_agent

router = APIRouter(tags=["chat"])

FORM = "chat_completions"


def _extract_model_and_question(body: dict) -> tuple[str, str, bool]:
    model = body.get("model") or "auto"
    messages = body.get("messages")

    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages обязателен и не должен быть пустым")

    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise HTTPException(
            400, 'последнее сообщение должно иметь role="user"')

    content = last.get("content", "")
    return (
        model,
        extract_text(content, CHAT_TEXT_TYPES),
        has_attachment(content, CHAT_ATTACHMENT_TYPES),
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, user_id: str = Depends(get_user_id)):
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(400, "Некорректный JSON")

    model, question, has_file = _extract_model_and_question(body)

    agent_id = await resolve_agent(FORM, model, question, has_file)
    check_agent(agent_id)

    forward_body = {**body, "model": agent_id}
    payload = json.dumps(forward_body, ensure_ascii=False).encode()

    adapter = get_adapter(agent_id)
    return await proxy_response(
        adapter.proxy("POST", "/v1/chat/completions",
                      user_id, payload, "application/json")
    )


@router.post("/agents/{agent_id}/ocr")
async def ocr(agent_id: str, file: UploadFile = File(...),
              user_id: str = Depends(get_user_id)):
    check_agent(agent_id)
    require_capability(agent_id, "ocr")

    content = await file.read()
    gen = get_adapter(agent_id).run_ocr(user_id, file.filename, content)

    return await streaming_or_error(gen)
