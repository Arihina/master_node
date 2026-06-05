import asyncio
from registry import AGENTS
from router import rule_router
from router import embedding_router
from router import llm_router


class MasterRouter:
    async def route(self, message: str) -> str:
        if agent := rule_router.route(message):
            return agent

        agent, _ = await asyncio.to_thread(embedding_router.route, message)
        if agent:
            return agent

        try:
            agent = await asyncio.to_thread(llm_router.route, message)
        except ValueError:
            agent = None
        return agent if agent in AGENTS else "chat"
