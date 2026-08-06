from __future__ import annotations

import json
from typing import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from auth import get_user_id
from registry import AGENTS
from adapters import get_adapter
from api.deps import check_agent, require_capability, streaming_or_error, proxy_response, master_router

router = APIRouter(tags=["chat"])


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _has_file_part(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(p, dict) and p.get("type") == "file" for p in content)


def _extract_model_and_question(body: dict) -> tuple[str, str, bool]:
    model = body.get("model") or "auto"
    messages = body.get("messages")

    if not isinstance(messages, list) or not messages:
        raise HTTPException(422, "messages обязателен и не должен быть пустым")

    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise HTTPException(
            422, 'последнее сообщение должно иметь role="user"')

    content = last.get("content", "")
    return model, _extract_text(content), _has_file_part(content)


def _resolve_agent(model: str, question: str, has_file: bool) -> Awaitable[str] | str:
    if has_file:
        capable = [a.id for a in AGENTS.values()
                   if a.enabled and "attachments" in a.capabilities]
        if model != "auto":
            if model not in capable:
                raise HTTPException(
                    422, f"Агент {model} не поддерживает вложения")
            return model
        if not capable:
            raise HTTPException(
                422, "Нет доступного агента, поддерживающего вложения")

        return capable[0]

    return model if model != "auto" else master_router.route(question)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, user_id: str = Depends(get_user_id)):
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(422, "Некорректный JSON")

    model, question, has_file = _extract_model_and_question(body)

    resolved = _resolve_agent(model, question, has_file)
    agent_id = resolved if isinstance(resolved, str) else await resolved
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
