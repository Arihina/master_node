import asyncio

from router import embedding_router, llm_router
from registry import AGENTS

FALLBACK_AGENT = "chat"


class MasterRouter:
    async def route(self, message: str) -> str:
        result = await asyncio.to_thread(embedding_router.route, message)

        if result["decision"] == "direct":
            return result["agent"]

        try:
            if result["decision"] == "ambiguous":
                agent = await asyncio.to_thread(llm_router.route, message, result["candidates"])
            else:
                agent = await asyncio.to_thread(llm_router.route, message)
        except Exception:
            agent = None

        return agent if agent in AGENTS else FALLBACK_AGENT
