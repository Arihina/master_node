from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

import registry
from registry import AGENTS, AgentInfo, RegistryFileError
from adapters import factory
from routing.embedding_router import index
from config import settings
from schemas.agents import AgentCreate, AgentUpdate

logger = logging.getLogger(__name__)


class AgentNotFound(Exception):
    """Агента с таким id нет в реестре."""


class RegistryConflict(Exception):
    """Изменение нарушает инвариант реестра: дубль id, снос fallback-агента."""


class RegistryValidationError(Exception):
    """Агент не проходит валидацию, либо файл реестра битый."""


@dataclass
class Diff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    embeddings: list[str] = field(default_factory=list)
    embeddings_cleared: list[str] = field(default_factory=list)
    adapters: list[str] = field(default_factory=list)


@dataclass
class Applied:
    version: int
    added: list[str]
    updated: list[str]
    removed: list[str]
    embeddings_recomputed: list[str]
    adapters_invalidated: list[str]


def _diff(old: dict[str, AgentInfo], new: dict[str, AgentInfo]) -> Diff:
    result = Diff(
        added=[k for k in new if k not in old],
        removed=[k for k in old if k not in new],
        updated=[k for k in new if k in old and new[k] != old[k]],
    )

    for agent_id in result.added + result.updated:
        agent = new[agent_id]
        was = old.get(agent_id)
        if not agent.description_key:
            result.embeddings_cleared.append(agent_id)
        elif was is None or was.description_key != agent.description_key:
            result.embeddings.append(agent_id)

    result.adapters = result.removed + [
        k for k in result.updated if old[k].adapter_key != new[k].adapter_key]

    return result


class AgentRegistry:
    def __init__(self):
        self.version = 1
        self.loaded_at = datetime.now(timezone.utc)
        self._lock = asyncio.Lock()
        self._closing: set[asyncio.Task] = set()

    # --- чтение ---------------------------------------------------------

    def get(self, agent_id: str) -> AgentInfo:
        agent = AGENTS.get(agent_id)
        if agent is None:
            raise AgentNotFound(agent_id)
        return agent

    def all(self) -> list[AgentInfo]:
        return list(AGENTS.values())

    def status(self) -> dict:
        return {
            "file": str(registry.AGENTS_FILE),
            "version": self.version,
            "loaded_at": self.loaded_at.isoformat(),
            "agents": len(AGENTS),
            "enabled": sum(1 for a in AGENTS.values() if a.enabled),
            "routable": sum(1 for a in AGENTS.values()
                            if a.enabled and a.routable),
            "embedding_index": sorted(index.ids()),
            "adapter_cache": sorted(factory.cached_ids()),
            "fallback_agent": settings.fallback_agent,
        }

    # --- запись ---------------------------------------------------------

    async def create(self, spec: AgentCreate) -> tuple[AgentInfo, Applied]:
        async with self._lock:
            if spec.id in AGENTS:
                raise RegistryConflict(f"агент {spec.id} уже существует")
            agent = self._build(spec.model_dump())
            applied = await self._commit({**AGENTS, agent.id: agent})
            return agent, applied

    async def update(self, agent_id: str,
                     patch: AgentUpdate) -> tuple[AgentInfo, Applied]:
        async with self._lock:
            current = AGENTS.get(agent_id)
            if current is None:
                raise AgentNotFound(agent_id)

            data = current.model_dump() | patch.model_dump(exclude_unset=True)
            agent = self._build(data)
            applied = await self._commit({**AGENTS, agent_id: agent})
            return agent, applied

    async def delete(self, agent_id: str) -> Applied:
        async with self._lock:
            if agent_id not in AGENTS:
                raise AgentNotFound(agent_id)
            if agent_id == settings.fallback_agent:
                raise RegistryConflict(
                    f"агент {agent_id} назначен fallback_agent — "
                    "сначала смените FALLBACK_AGENT в .env")

            new_state = {k: v for k, v in AGENTS.items() if k != agent_id}
            return await self._commit(new_state)

    async def reload(self) -> Applied:
        """Перечитать файл с диска. Нужен, когда agents.yaml приехал мимо API —
        например, с git-деплоем."""
        async with self._lock:
            try:
                new_state = await run_in_threadpool(registry.read_file)
            except RegistryFileError as e:
                raise RegistryValidationError(str(e))
            return await self._commit(new_state, write=False)

    # --- внутреннее -----------------------------------------------------

    @staticmethod
    def _build(data: dict) -> AgentInfo:
        try:
            return registry.build_agent(data)
        except RegistryFileError as e:
            raise RegistryValidationError(str(e))

    async def _commit(self, new_state: dict[str, AgentInfo],
                      write: bool = True) -> Applied:
        try:
            registry.validate_state(new_state)
        except RegistryFileError as e:
            raise RegistryConflict(str(e))

        diff = _diff(AGENTS, new_state)

        vectors = {}
        for agent_id in diff.embeddings:
            text = new_state[agent_id].description_key
            vectors[agent_id] = await run_in_threadpool(index.encode_passage,
                                                        text)

        if write:
            await run_in_threadpool(registry.write_file, new_state)

        return self._apply(new_state, diff, vectors)

    def _apply(self, new_state: dict[str, AgentInfo], diff: Diff,
               vectors: dict) -> Applied:
        for agent_id in diff.removed + diff.embeddings_cleared:
            index.remove(agent_id)
        for agent_id, vector in vectors.items():
            index.upsert(agent_id, vector)

        for agent_id in diff.adapters:
            adapter = factory.invalidate(agent_id)
            if adapter is not None:
                self._close_later(adapter)

        AGENTS.clear()
        AGENTS.update(new_state)

        self.version += 1
        self.loaded_at = datetime.now(timezone.utc)

        applied = Applied(
            version=self.version,
            added=sorted(diff.added),
            updated=sorted(diff.updated),
            removed=sorted(diff.removed),
            embeddings_recomputed=sorted(vectors),
            adapters_invalidated=sorted(diff.adapters),
        )
        logger.info("Реестр обновлён: %s", applied)
        return applied

    def _close_later(self, adapter) -> None:
        """Старый адаптер мог обслуживать стрим в момент подмены. Закрываем
        не сразу, а через agent_timeout — к этому моменту любой запрос через
        него либо завершился, либо уже отвалился по таймауту."""
        async def _close():
            try:
                await asyncio.sleep(settings.agent_timeout)
                await adapter.aclose()
            except Exception:
                logger.exception("Не удалось закрыть адаптер %s",
                                 adapter.agent_id)

        task = asyncio.create_task(_close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def aclose(self) -> None:
        for task in list(self._closing):
            task.cancel()
        self._closing.clear()


agent_registry = AgentRegistry()
