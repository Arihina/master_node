import asyncio

from routing import embedding_router, llm_router
from registry import AGENTS
from config import settings


class NoRoutableAgent(Exception):
    """Не осталось ни одного агента, которому можно отдать запрос."""


class MasterRouter:
    async def route(self, message: str, allowed: set[str] | None = None) -> str:

        candidates = {a.id for a in AGENTS.values()
                      if a.enabled and a.routable}
        
        if allowed is not None:
            candidates &= allowed

        if not candidates:
            raise NoRoutableAgent()

        if len(candidates) == 1:
            return next(iter(candidates))

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

        if agent in candidates:
            return agent

        return self._fallback(candidates)

    @staticmethod
    def _fallback(candidates: set[str]) -> str:
        if settings.fallback_agent in candidates:
            return settings.fallback_agent
        return sorted(candidates)[0]
