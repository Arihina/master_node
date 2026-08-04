from __future__ import annotations

import json

import httpx

from registry import AgentInfo
from config import settings
from .base import AgentAdapter, ProxyResult, AgentUnavailable


class ContractHTTPAdapter(AgentAdapter):
    """Для диалоговых агентов на каноническом контракте:
    /v1/chat/completions, /v1/chat/completions/{id}(/feedback|/sources),
    /v1/platform/conversations(...). Мастер не знает про эти пути ничего,
    кроме того, что они начинаются с "v1/"
    """

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

    async def proxy(self, method, path, user_id, body=None, content_type=None) -> ProxyResult:
        url = f"{self._base}{path}"
        headers = {"X-User-Id": user_id}
        if content_type:
            headers["Content-Type"] = content_type

        try:
            cm = self._client.stream(
                method, url, headers=headers,
                content=body if body else None,
            )
            resp = await cm.__aenter__()
        except httpx.ConnectError as e:
            raise AgentUnavailable(self.agent_id, f"агент недоступен: {e}")

        status = resp.status_code
        upstream_content_type = resp.headers.get(
            "content-type", "application/json")

        async def _body():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except httpx.HTTPError as e:
                err = {"error": {
                    "message": f"соединение с агентом прервано: {e}",
                    "type": "server_error", "param": None, "code": None,
                }}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
            finally:
                await cm.__aexit__(None, None, None)

        return ProxyResult(status=status, content_type=upstream_content_type, body=_body())

    async def aclose(self):
        await self._client.aclose()
