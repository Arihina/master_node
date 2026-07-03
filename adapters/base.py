from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class AgentResponse:
    """Унифицированный ответ адаптера на НЕ-стримовые операции.

    Несёт статус-код, чтобы мастер сохранял семантику контракта
    (например, 404 на чужой ресурс) независимо от транспорта агента.
    """
    status: int
    content: bytes
    media_type: str = "application/json"


class CapabilityNotSupported(Exception):
    """Агент не поддерживает запрошенную возможность (не входит в его capabilities)."""

    def __init__(self, agent_id: str, capability: str):
        self.agent_id = agent_id
        self.capability = capability
        super().__init__(
            f"{agent_id} не поддерживает возможность '{capability}'")


class AgentUnavailable(Exception):
    """Агент недоступен или ответил ошибкой ДО начала стрима."""

    def __init__(self, agent_id: str, detail: str):
        self.agent_id = agent_id
        self.detail = detail
        super().__init__(detail)


class AgentAdapter(ABC):
    """Единый интерфейс агента в терминах НАМЕРЕНИЙ, а не HTTP-путей.

    Реализация знает, как говорить с конкретным транспортом (contract-агент,
    внешний API вендора, Cognitum), и обязана выдавать наружу канонический
    формат платформы. Для стрима это SSE-события:

        data: {"chunks": [...]}      # опционально, первым
        data: {"token": "..."}
        data: {"message_id": <id>}
        data: [DONE]

    session_id и message_id для мастера ОПАКОВЫ — он их не интерпретирует,
    только прокидывает обратно клиенту.
    """

    agent_id: str

    @abstractmethod
    async def create_session(
        self, user_id: str, title: str | None) -> AgentResponse: ...

    @abstractmethod
    async def list_sessions(self, user_id: str) -> AgentResponse: ...

    @abstractmethod
    async def get_messages(
        self, user_id: str, session_id: str) -> AgentResponse: ...

    @abstractmethod
    async def rename_session(
        self, user_id: str, session_id: str, title: str) -> AgentResponse: ...

    @abstractmethod
    async def delete_session(
        self, user_id: str, session_id: str) -> AgentResponse: ...

    @abstractmethod
    async def set_feedback(
        self, user_id: str, message_id: str, body: dict) -> AgentResponse: ...

    @abstractmethod
    async def get_feedback(
        self, user_id: str, message_id: str) -> AgentResponse: ...

    @abstractmethod
    async def delete_feedback(
        self, user_id: str, message_id: str) -> AgentResponse: ...

    @abstractmethod
    def stream_chat(self, user_id: str, session_id: str, message: str,
                    attachment: tuple[str, bytes] | None = None,) -> AsyncIterator[bytes]:
        """Async-генератор SSE-байтов.

        До первого чанка может бросить AgentUnavailable (мастер превратит в 502).
        После первого чанка ошибки досылаются SSE-событием {"error": ...}.
        """
        ...

    async def aclose(self) -> None:
        """Освободить ресурсы."""

    def run_ocr(self, user_id: str, filename: str, content: bytes) -> AsyncIterator[bytes]:
        raise CapabilityNotSupported(self.agent_id, "ocr")

    async def upload_document(
        self, user_id: str, filename: str, content_type: str, data: bytes
    ) -> AgentResponse:
        raise CapabilityNotSupported(self.agent_id, "documents")
