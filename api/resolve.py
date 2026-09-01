"""Выбор агента — общий для /v1/chat/completions и /v1/responses.
Порядок проверок:
  1. Форма. Агент обязан заявить её в contract_forms.
  2. Вложение — более жёсткое требование, чем смысл текста: если файл есть,
     кандидаты сразу сужаются до агентов с capability "attachments".
  3. Явный model:
     - "prefix/..." — резолвится в агента через AgentInfo.model_prefix,
       body.model форвардится агенту как есть (внутренний идентификатор
       ресурса — например, uuid набора — прячется в хвосте после '/');
     - без '/' — трактуется как id агента, body.model заменяется на него же.
     model="auto" — семантический роутинг СРЕДИ оставшихся кандидатов;
     префиксы в этом режиме не рассматриваются.

Возвращаемое значение — пара (agent_id, forward_model):
  - agent_id — кому мастер направит запрос;
  - forward_model — что положить в body["model"] на форварде. Для неймспейса
    это исходная строка «как есть», для остальных случаев — agent_id.
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


def _resolve_prefix(prefix: str) -> str | None:
    """Первый (и по инварианту реестра — единственный) enabled-агент с таким
    model_prefix. Уникальность гарантируется validate_state."""
    for agent_id, agent in AGENTS.items():
        if agent.enabled and agent.model_prefix == prefix:
            return agent_id
    return None


async def resolve_agent(
    form: str, model: str, question: str, has_file: bool,
) -> tuple[str, str]:
    form_title = _FORM_TITLES.get(form, form)
    form_capable = _form_capable(form)

    candidates = form_capable
    if has_file:
        candidates = {a for a in form_capable
                      if "attachments" in AGENTS[a].capabilities}

    if model != "auto":
        if "/" in model:
            prefix, _, _ = model.partition("/")
            agent_id = _resolve_prefix(prefix)
            if agent_id is None:
                raise HTTPException(
                    404, f"Неизвестный префикс модели {prefix!r}")
            forward_model = model
        else:
            agent_id = model
            forward_model = model

        agent = AGENTS.get(agent_id)
        if agent is None or not agent.enabled:
            raise HTTPException(
                404, f"Агент {agent_id} не найден или выключен")

        if agent_id not in form_capable:
            raise HTTPException(
                400, f"Агент {agent_id} не поддерживает форму {form_title}")

        if has_file and agent_id not in candidates:
            raise HTTPException(
                400, f"Агент {agent_id} не поддерживает вложения")

        return agent_id, forward_model

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
            chosen = next(iter(candidates))
            return chosen, chosen

        if not question.strip():
            chosen = sorted(candidates)[0]
            logger.warning(
                'model="auto" с вложением и без текста: кандидатов %s, '
                "выбран %s по алфавиту", sorted(candidates), chosen)
            return chosen, chosen

    if not question.strip():
        raise HTTPException(
            400, 'Не удалось извлечь текст запроса для роутинга при model="auto"')

    try:
        chosen = await master_router.route(question, allowed=candidates)
    except NoRoutableAgent:
        raise HTTPException(
            400,
            f"Нет доступного агента, поддерживающего форму {form_title} "
            "и участвующего в роутинге",
        )
    return chosen, chosen
