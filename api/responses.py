from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_user_id
from adapters import get_adapter
from api.content import (
    RESPONSES_ATTACHMENT_TYPES, RESPONSES_TEXT_TYPES, extract_text, has_attachment,
)
from api.deps import check_agent, proxy_response
from api.resolve import resolve_agent

router = APIRouter(tags=["responses"])

FORM = "responses"


def _extract_model_and_question(body: dict) -> tuple[str, str, bool]:
    model = body.get("model") or "auto"
    input_data = body.get("input")

    if input_data is None:
        raise HTTPException(400, "input обязателен")

    if isinstance(input_data, str):
        question = input_data.strip()
        if not question:
            raise HTTPException(400, "Пустой input")
        return model, question, False

    if not isinstance(input_data, list) or not input_data:
        raise HTTPException(
            400, "input должен быть строкой или непустым списком items")

    last = input_data[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise HTTPException(
            400, 'последний item в input должен иметь role="user"')

    content = last.get("content", "")
    return (
        model,
        extract_text(content, RESPONSES_TEXT_TYPES),
        has_attachment(content, RESPONSES_ATTACHMENT_TYPES),
    )


@router.post("/v1/responses")
async def responses(request: Request, user_id: str = Depends(get_user_id)):
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(400, "Некорректный JSON")

    model, question, has_file = _extract_model_and_question(body)

    agent_id, forward_model = await resolve_agent(
        FORM, model, question, has_file)
    check_agent(agent_id)

    forward_body = {**body, "model": forward_model}
    payload = json.dumps(forward_body, ensure_ascii=False).encode()

    adapter = get_adapter(agent_id)
    return await proxy_response(
        adapter.proxy("POST", "/v1/responses",
                      user_id, payload, "application/json")
    )
