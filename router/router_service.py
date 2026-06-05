import asyncio

from router import rule_router, embedding_router, llm_router
from registry import AGENTS

FALLBACK_AGENT = "chat"


class MasterRouter:
    async def route(self, message: str) -> str:
        if agent := rule_router.route(message):
            return agent

        agent, _ = await asyncio.to_thread(embedding_router.route, message)
        if agent:
            return agent

        try:
            agent = await asyncio.to_thread(llm_router.route, message)
        except (ValueError, Exception):
            agent = None

        return agent if agent in AGENTS and AGENTS[agent].enabled else FALLBACK_AGENT
