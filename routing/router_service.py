import asyncio
from collections import OrderedDict

from routing import embedding_router, llm_router
from registry import AGENTS
from registry_service import agent_registry
from config import settings


class NoRoutableAgent(Exception):
    """Не осталось ни одного агента, которому можно отдать запрос."""


def _normalize_query(message: str) -> str:
    """Ключ кэша по тексту: регистр и повторные пробелы не должны дублироваться"""
    return " ".join((message or "").lower().split())


class _LruCache:
    """LRU-кэш финального решения роутинга."""

    def __init__(self, maxsize: int):
        self._maxsize = maxsize
        self._items: "OrderedDict[tuple, str]" = OrderedDict()

    def get(self, key):
        try:
            self._items.move_to_end(key)
            return self._items[key]
        except KeyError:
            return None

    def put(self, key, value: str) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


_cache = _LruCache(settings.routing_cache_size)


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

        key = (_normalize_query(message), frozenset(candidates),
               agent_registry.version)
        cached = _cache.get(key)
        if cached is not None:
            return cached

        chosen = await self._route_uncached(message, candidates)
        _cache.put(key, chosen)
        return chosen

    async def _route_uncached(self, message: str,
                              candidates: set[str]) -> str:
        result = await asyncio.to_thread(embedding_router.route, message,
                                         candidates)

        if result["decision"] == "direct":
            return result["agent"]

        if result["decision"] == "no_match":
            return self._fallback(candidates)

        try:
            if result["decision"] == "ambiguous":
                agent = await asyncio.wait_for(
                    asyncio.to_thread(llm_router.route, message,
                                      result["candidates"]),
                    timeout=settings.router_llm_timeout)
            else:
                shortlist = result.get("candidates") or candidates
                agent = await asyncio.wait_for(
                    asyncio.to_thread(llm_router.route, message, shortlist),
                    timeout=settings.router_llm_timeout)
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
