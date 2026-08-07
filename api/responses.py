from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_user_id
from registry import AGENTS
from adapters import get_adapter
from api.deps import check_agent, proxy_response

router = APIRouter(tags=["responses"])


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "input_text"
        )
    return ""


def _has_file_part(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(p, dict) and p.get("type") == "input_file" for p in content)


def _extract_model_and_question(body: dict) -> tuple[str, str, bool]:
    model = body.get("model") or "auto"
    input_data = body.get("input")

    if input_data is None:
        raise HTTPException(422, "input обязателен")

    if isinstance(input_data, str):
        question = input_data.strip()
        if not question:
            raise HTTPException(422, "Пустой input")
        return model, question, False

    if not isinstance(input_data, list) or not input_data:
        raise HTTPException(
            422, "input должен быть строкой или непустым списком items")

    last = input_data[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise HTTPException(
            422, 'последний item в input должен иметь role="user"')

    content = last.get("content", "")
    question = _extract_text_from_content(content)
    has_file = _has_file_part(content)

    if model == "auto" and not question:
        raise HTTPException(
            422, "Не удалось извлечь текст из input для авто-роутинга")

    return model, question, has_file


def _resolve_agent(model: str, has_file: bool) -> str:
    form_capable = {a.id for a in AGENTS.values()
                    if a.enabled and "responses" in a.contract_forms}

    candidates = form_capable
    if has_file:
        candidates = {a for a in form_capable
                      if "attachments" in AGENTS[a].capabilities}

    if model != "auto":
        if model not in form_capable:
            raise HTTPException(
                422, f"Агент {model} не поддерживает форму Responses API")
        if has_file and model not in candidates:
            raise HTTPException(
                422, f"Агент {model} не поддерживает вложения")
        return model

    if not candidates:
        reason = "поддерживающего вложения" if has_file else "поддерживающего форму Responses API"
        raise HTTPException(422, f"Нет доступного агента, {reason}")

    if len(candidates) == 1:
        return next(iter(candidates))

    return sorted(candidates)[0]


@router.post("/v1/responses")
async def responses(request: Request, user_id: str = Depends(get_user_id)):
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(422, "Некорректный JSON")

    model, _question, has_file = _extract_model_and_question(body)
    agent_id = _resolve_agent(model, has_file)
    check_agent(agent_id)

    forward_body = {**body, "model": agent_id}
    payload = json.dumps(forward_body, ensure_ascii=False).encode()

    adapter = get_adapter(agent_id)
    return await proxy_response(
        adapter.proxy("POST", "/v1/responses",
                      user_id, payload, "application/json")
    )
