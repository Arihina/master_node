from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class AgentResponse:
    """Унифицированный буферизованный ответ — используется только
    capability-методами (run_ocr/upload_document), не основным контрактом."""
    status: int
    content: bytes
    media_type: str = "application/json"


@dataclass
class ProxyResult:
    """Результат proxy() — статус и content-type апстрима известны СРАЗУ
    (httpx получает заголовки до тела), поэтому мастер может корректно
    выставить статус-код и media-type ответа ещё до того, как начнёт
    вычитывать body. body работает одинаково для потокового SSE-ответа
    агента и для обычного одиночного JSON — в обоих случаях это просто
    байты по мере поступления."""
    status: int
    content_type: str
    body: AsyncIterator[bytes]


class CapabilityNotSupported(Exception):
    """Агент не поддерживает запрошенную возможность (не входит в его capabilities)."""

    def __init__(self, agent_id: str, capability: str):
        self.agent_id = agent_id
        self.capability = capability
        super().__init__(
            f"{agent_id} не поддерживает возможность '{capability}'")


class AgentUnavailable(Exception):
    """Агент недоступен или соединение оборвалось ДО получения статус-кода."""

    def __init__(self, agent_id: str, detail: str):
        self.agent_id = agent_id
        self.detail = detail
        super().__init__(detail)


class AgentAdapter(ABC):
    """Единый интерфейс агента: мастер форвардит по КОНТРАКТУ, а не по
    конкретным ручкам агента. Ядро — один метод: передать method+path+тело
    агенту и вернуть его ответ как есть (статус, content-type, байты).

    Агент сам решает, что стоит за конкретным path (POST /v1/chat/completions,
    GET .../feedback, /v1/platform/conversations, ...) — мастер это не
    интерпретирует и не хранит. Правильность контракта — ответственность
    агента; мастер только проверяет, что path начинается с "v1/", 
    остальное — сквозной проброс.

    Capability-методы (run_ocr, upload_document) — отдельная, более старая
    договорённость для инструментов без диалога (OCR); НЕ переведены на
    новый /v1/chat/completions контракт и НЕ входят в proxy()."""

    agent_id: str

    @abstractmethod
    async def proxy(
        self, method: str, path: str, user_id: str,
        body: bytes | None = None, content_type: str | None = None,
    ) -> ProxyResult:
        """path — с ведущим слэшем, например "/v1/chat/completions/{id}/feedback".

        До получения статус-кода апстрима (обрыв соединения, DNS, connect
        refused) — бросает AgentUnavailable, мастер превращает это в 502.
        После получения статус-кода — статус и тело агента идут насквозь
        без интерпретации, включая ошибки агента (у него тот же формат
        {"error": {...}}, пересобирать нечего)."""
        ...

    async def aclose(self) -> None:
        """Освободить ресурсы."""

    def run_ocr(self, user_id: str, filename: str, content: bytes) -> AsyncIterator[bytes]:
        raise CapabilityNotSupported(self.agent_id, "ocr")

    async def upload_document(
        self, user_id: str, filename: str, content_type: str, data: bytes
    ) -> AgentResponse:
        raise CapabilityNotSupported(self.agent_id, "documents")
