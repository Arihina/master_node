import httpx
import json

from registry import AGENTS


class AgentUnavailable(Exception):
    def __init__(self, agent_id, detail):
        self.agent_id = agent_id
        self.detail = detail
        super().__init__(detail)


def _sse_error(msg: str) -> bytes:
    return f"data: {json.dumps({'error': msg}, ensure_ascii=False)}\n\n".encode()


class AgentDispatcher:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=300, verify=False)

    async def aclose(self):
        await self.client.aclose()

    def _base(self, agent_id: str) -> str:
        agent = AGENTS.get(agent_id)
        if agent is None or not agent.enabled:
            raise KeyError(agent_id)
        if not agent.url:
            raise ValueError(f"У агента {agent_id} не задан url")
        return agent.url

    async def forward(
        self,
        agent_id: str,
        method: str,
        path: str,
        user_id: str,
        json_body=None,
    ) -> httpx.Response:
        base = self._base(agent_id)
        kwargs = {"headers": {"X-User-Id": user_id}}
        if json_body is not None:
            kwargs["json"] = json_body
        return await self.client.request(method, f"{base}{path}", **kwargs)

    async def stream_chat(self, agent_id, session_id, user_id, message):
        base = self._base(agent_id)
        try:
            stream_cm = self.client.stream(
                "POST",
                f"{base}/sessions/{session_id}/chat",
                headers={"X-User-Id": user_id},
                json={"message": message},
            )
            response = await stream_cm.__aenter__()
        except httpx.ConnectError as e:
            raise AgentUnavailable(agent_id, f"агент недоступен: {e}")

        try:
            if response.status_code >= 400:
                body = await response.aread()
                raise AgentUnavailable(
                    agent_id,
                    f"{response.status_code}: {body.decode(errors='replace')}",
                )
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            except httpx.HTTPError as e:
                yield _sse_error(f"соединение с агентом прервано: {e}")
        finally:
            await stream_cm.__aexit__(None, None, None)
