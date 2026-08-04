from __future__ import annotations

import json

import httpx

from config import settings
from registry import AgentInfo

from .base import AgentAdapter, AgentUnavailable, ProxyResult


class OCRAdapter(AgentAdapter):
    """Файл-в/текст-из, без чата и без сессий — реализует только capability
    run_ocr. Не участвует в контракте /v1/chat/completions вообще: proxy()
    честно отвечает 404 на любой путь, а не падает и не выдумывает ответ."""

    def __init__(self, agent: AgentInfo):
        self.agent_id = agent.id
        self._url = agent.url
        self._client = httpx.AsyncClient(timeout=settings.agent_timeout)

    async def proxy(self, method, path, user_id, body=None, content_type=None) -> ProxyResult:
        async def _not_found():
            err = {"error": {
                "message": f"У ocr нет '{path}' — это не contract-агент",
                "type": "not_found_error", "param": None, "code": None,
            }}
            yield json.dumps(err, ensure_ascii=False).encode()
        return ProxyResult(status=404, content_type="application/json", body=_not_found())

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

    async def aclose(self) -> None:
        await self._client.aclose()
