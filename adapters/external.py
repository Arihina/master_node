from __future__ import annotations

from typing import AsyncIterator

from registry import AgentInfo
from .base import AgentAdapter, AgentResponse


class ExternalAPIAdapter(AgentAdapter):
    """СКЕЛЕТ. Агент поверх чужого API.

    Ключевое отличие от contract-агента: у вендора НЕТ ни сессий, ни
    message_id, ни фидбэка. Чтобы соблюсти контракт платформы, адаптер:
      * сам хранит сессии/сообщения/фидбэк в общем сторе (Postgres);
      * транслирует дельты стрима вендора в канонические {"token": ...};
      * синтезирует message_id и финальный [DONE];
      * держит креды вендора у себя (наружу/клиенту не отдаются).

    Транспорт в реестре: transport="external", config={...вендорские поля...}.
    """

    def __init__(self, agent: AgentInfo):
        self.agent_id = agent.id
        self._cfg = agent.config
        # self._store = ConversationStore(...)
        # self._client = httpx.AsyncClient(base_url=...)

    async def create_session(self, user_id, title) -> AgentResponse:
        raise NotImplementedError(
            "создать запись сессии в общем сторе, вернуть {'id': ...}")

    async def list_sessions(self, user_id) -> AgentResponse:
        raise NotImplementedError

    async def get_messages(self, user_id, session_id) -> AgentResponse:
        raise NotImplementedError

    async def rename_session(self, user_id, session_id, title) -> AgentResponse:
        raise NotImplementedError

    async def delete_session(self, user_id, session_id) -> AgentResponse:
        raise NotImplementedError

    async def set_feedback(self, user_id, message_id, body) -> AgentResponse:
        raise NotImplementedError

    async def get_feedback(self, user_id, message_id) -> AgentResponse:
        raise NotImplementedError

    async def delete_feedback(self, user_id, message_id) -> AgentResponse:
        raise NotImplementedError

    async def stream_chat(self, user_id, session_id, message) -> AsyncIterator[bytes]:
        raise NotImplementedError(
            "позвать API вендора, транслировать дельты в {'token': ...}, "
            "сохранить ответ в стор, отдать {'message_id': ...} и [DONE]"
        )
        yield b""
