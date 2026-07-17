from __future__ import annotations

import json

import httpx

from registry import AgentInfo
from config import settings
from .base import AgentAdapter, AgentResponse, AgentUnavailable


class ContractHTTPAdapter(AgentAdapter):
    def __init__(self, agent: AgentInfo):
        self.agent_id = agent.id
        self._base = agent.url
        cfg = agent.config
        self._client = httpx.AsyncClient(
            timeout=cfg.get("timeout", settings.agent_timeout),
            verify=cfg.get("verify", settings.agent_verify_tls),
            limits=httpx.Limits(
                max_connections=cfg.get(
                    "max_connections", settings.agent_max_connections),
                max_keepalive_connections=cfg.get(
                    "max_keepalive", settings.agent_max_keepalive),
            ),
        )

    async def _req(self, method: str, path: str, user_id: str, body=None) -> AgentResponse:
        kwargs = {"headers": {"X-User-Id": user_id}}
        if body is not None:
            kwargs["json"] = body
        try:
            r = await self._client.request(method, f"{self._base}{path}", **kwargs)
        except httpx.ConnectError as e:
            raise AgentUnavailable(self.agent_id, f"агент недоступен: {e}")
        return AgentResponse(
            status=r.status_code,
            content=r.content,
            media_type=r.headers.get("content-type", "application/json"),
        )

    async def create_session(self, user_id, title):
        return await self._req("POST", "/sessions", user_id, {"title": title} if title else {})

    async def list_sessions(self, user_id):
        return await self._req("GET", "/sessions", user_id)

    async def get_messages(self, user_id, session_id):
        return await self._req("GET", f"/sessions/{session_id}/messages", user_id)

    async def rename_session(self, user_id, session_id, title):
        return await self._req("PATCH", f"/sessions/{session_id}", user_id, {"title": title})

    async def delete_session(self, user_id, session_id):
        return await self._req("DELETE", f"/sessions/{session_id}", user_id)

    async def set_feedback(self, user_id, message_id, body):
        return await self._req("POST", f"/messages/{message_id}/feedback", user_id, body)

    async def get_feedback(self, user_id, message_id):
        return await self._req("GET", f"/messages/{message_id}/feedback", user_id)

    async def delete_feedback(self, user_id, message_id):
        return await self._req("DELETE", f"/messages/{message_id}/feedback", user_id)

    async def stream_chat(self, user_id, session_id, message, attachment=None):
        url = f"{self._base}/sessions/{session_id}/chat"
        headers = {"X-User-Id": user_id}

        if attachment is None:
            request_kwargs = {"json": {"message": message}}
        else:
            filename, content = attachment
            request_kwargs = {
                "data": {"message": message},
                "files": {"file": (filename, content)},
            }

        try:
            cm = self._client.stream(
                "POST", url, headers=headers, **request_kwargs)
            resp = await cm.__aenter__()
        except httpx.ConnectError as e:
            raise AgentUnavailable(self.agent_id, f"агент недоступен: {e}")

        try:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise AgentUnavailable(
                    self.agent_id,
                    f"{resp.status_code}: {body.decode(errors='replace')}",
                )
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except httpx.HTTPError as e:
                err = {"error": f"соединение с агентом прервано: {e}"}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
        finally:
            await cm.__aexit__(None, None, None)

    async def aclose(self):
        await self._client.aclose()
