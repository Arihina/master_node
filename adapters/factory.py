from __future__ import annotations

from registry import AGENTS
from .base import AgentAdapter
from .contract_http import ContractHTTPAdapter
from .external import ExternalAPIAdapter
from .ocr import OCRAdapter

_ADAPTER_CLASSES: dict[str, type[AgentAdapter]] = {
    "contract": ContractHTTPAdapter,
    "external": ExternalAPIAdapter,
    "ocr": OCRAdapter
}

_INSTANCES: dict[str, AgentAdapter] = {}


def get_adapter(agent_id: str) -> AgentAdapter:
    if agent_id not in _INSTANCES:
        agent = AGENTS[agent_id]
        cls = _ADAPTER_CLASSES.get(agent.transport)
        if cls is None:
            raise ValueError(
                f"Неизвестный транспорт '{agent.transport}' у агента {agent_id}"
            )
        _INSTANCES[agent_id] = cls(agent)
    return _INSTANCES[agent_id]


async def close_all() -> None:
    for adapter in _INSTANCES.values():
        await adapter.aclose()
    _INSTANCES.clear()
