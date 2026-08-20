"""Выбор агента — общий для /v1/chat/completions и /v1/responses.
Порядок проверок:
  1. Форма. Агент обязан заявить её в contract_forms.
  2. Вложение — более жёсткое требование, чем смысл текста: если файл есть,
     кандидаты сразу сужаются до агентов с capability "attachments".
  3. Явный model — используется как есть (после проверок 1-2).
     model="auto" — семантический роутинг СРЕДИ оставшихся кандидатов.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from registry import AGENTS
from routing.router_service import NoRoutableAgent
from api.deps import master_router

logger = logging.getLogger(__name__)

_FORM_TITLES = {
    "chat_completions": "Chat Completions",
    "responses": "Responses API",
}


def _form_capable(form: str) -> set[str]:
    return {a.id for a in AGENTS.values()
            if a.enabled and form in a.contract_forms}


async def resolve_agent(form: str, model: str, question: str, has_file: bool) -> str:
    form_title = _FORM_TITLES.get(form, form)
    form_capable = _form_capable(form)

    candidates = form_capable
    if has_file:
        candidates = {a for a in form_capable
                      if "attachments" in AGENTS[a].capabilities}

    if model != "auto":
        agent = AGENTS.get(model)

        if agent is None or not agent.enabled:
            raise HTTPException(404, f"Агент {model} не найден или выключен")

        if model not in form_capable:
            raise HTTPException(
                400, f"Агент {model} не поддерживает форму {form_title}")

        if has_file and model not in candidates:
            raise HTTPException(
                400, f"Агент {model} не поддерживает вложения")

        return model

    if not candidates:
        reason = ("поддерживающего вложения" if has_file
                  else f"поддерживающего форму {form_title}")
        raise HTTPException(400, f"Нет доступного агента, {reason}")

    if has_file:
        candidates = {a for a in candidates if AGENTS[a].routable}
        if not candidates:
            raise HTTPException(
                400, "Нет доступного агента, поддерживающего вложения "
                     "и участвующего в роутинге")

        if len(candidates) == 1:
            return next(iter(candidates))

        if not question.strip():
            chosen = sorted(candidates)[0]
            logger.warning(
                'model="auto" с вложением и без текста: кандидатов %s, '
                "выбран %s по алфавиту", sorted(candidates), chosen)
            return chosen

    if not question.strip():
        raise HTTPException(
            400, 'Не удалось извлечь текст запроса для роутинга при model="auto"')

    try:
        return await master_router.route(question, allowed=candidates)
    except NoRoutableAgent:
        raise HTTPException(
            400,
            f"Нет доступного агента, поддерживающего форму {form_title} "
            "и участвующего в роутинге",
        )
