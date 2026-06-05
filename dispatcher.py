import httpx
from registry import AGENTS


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

    async def stream_chat(
        self,
        agent_id: str,
        session_id: int,
        user_id: str,
        message: str,
    ):
        base = self._base(agent_id)
        async with self.client.stream(
            "POST",
            f"{base}/sessions/{session_id}/chat",
            headers={"X-User-Id": user_id},
            json={"message": message},
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"Агент {agent_id} вернул {response.status_code}: "
                    f"{body.decode(errors='replace')}",
                    request=response.request,
                    response=response,
                )
            async for chunk in response.aiter_raw():
                yield chunk
