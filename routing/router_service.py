import asyncio

from routing import embedding_router, llm_router
from registry import AGENTS

FALLBACK_AGENT = "chat"


class MasterRouter:
    async def route(self, message: str) -> str:
        candidates = {a.id for a in AGENTS.values()
                      if a.enabled and a.routable}

        result = await asyncio.to_thread(embedding_router.route, message, candidates)

        if result["decision"] == "direct":
            return result["agent"]

        try:
            if result["decision"] == "ambiguous":
                agent = await asyncio.to_thread(llm_router.route, message, result["candidates"])
            else:
                agent = await asyncio.to_thread(llm_router.route, message, candidates)
        except Exception:
            agent = None

        return agent if agent in candidates else FALLBACK_AGENT
