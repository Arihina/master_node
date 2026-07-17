from __future__ import annotations

import json
import uuid

import httpx

from config import settings
from registry import AgentInfo

from .base import AgentAdapter, AgentResponse, AgentUnavailable, CapabilityNotSupported


class OCRAdapter(AgentAdapter):
    """Файл-в/текст-из, без реальных сессий, сообщений и фидбэка.

    create_session синтезирует opaque id локально — ocr_agent не знает о
    сессиях вообще, но паре agent_id/session_id на стороне мастера нужен
    хоть какой-то id. list_sessions/get_messages/rename/delete/feedback-методы
    — честные заглушки (404), а не падение, если кто-то дёрнет
    /agents/ocr/sessions напрямую.
    """

    def __init__(self, agent: AgentInfo):
        self.agent_id = agent.id
        self._url = agent.url
        self._client = httpx.AsyncClient(timeout=settings.agent_timeout)

    async def create_session(self, user_id: str, title: str | None) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}), "application/json")

    async def run_ocr(self, user_id, filename, content):
        try:
            resp = await self._client.post(
                f"{self._url}/ocr",
                files={"file": (filename, content)},
                headers={"X-User-Id": user_id},
            )
        except httpx.RequestError as e:
            raise AgentUnavailable(self.agent_id, str(e)) from e
        if resp.status_code >= 400:
            raise AgentUnavailable(self.agent_id, resp.text)
        text = resp.json().get("text", "")
        yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def _not_supported(self) -> AgentResponse:
        return AgentResponse(
            404,
            json.dumps({"detail": "У ocr нет сессий, сообщений и фидбэка"}),
            "application/json",
        )

    async def stream_chat(self, user_id, session_id, message, attachment=None):
        raise CapabilityNotSupported(self.agent_id, "chat")
        yield b""

    async def list_sessions(self, user_id: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def get_messages(self, user_id: str, session_id: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def rename_session(self, user_id: str, session_id: str, title: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def delete_session(self, user_id: str, session_id: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def set_feedback(self, user_id: str, message_id: str, body: dict) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def get_feedback(self, user_id: str, message_id: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def delete_feedback(self, user_id: str, message_id: str) -> AgentResponse:
        return AgentResponse(200, json.dumps({"id": str(uuid.uuid4())}).encode(), "application/json")

    async def aclose(self) -> None:
        await self._client.aclose()
