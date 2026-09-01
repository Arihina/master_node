from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from registry import AgentInfo, Capability, ContractForm, Transport


class AgentCreate(BaseModel):
    """Валидируется дважды: здесь — форма запроса, дальше в `AgentInfo` —
    инварианты самого агента (url при contract, attachments требует chat,
    routable требует description и contract_forms)."""
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    url: str = ""
    description: str = ""
    transport: Transport = "contract"
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    capabilities: list[Capability] = Field(default_factory=lambda: ["chat"])
    routable: bool = True
    contract_forms: list[ContractForm] = Field(
        default_factory=lambda: ["chat_completions"])
    model_prefix: str | None = None


class AgentUpdate(BaseModel):
    """Частичное обновление. `id` неизменяем и в схему не входит вовсе:
    он уходит в поле `model` OpenAI-контракта, и смена id — это удаление
    одного агента и создание другого, а не правка."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    url: str | None = None
    description: str | None = None
    transport: Transport | None = None
    config: dict | None = None
    enabled: bool | None = None
    capabilities: list[Capability] | None = None
    routable: bool | None = None
    contract_forms: list[ContractForm] | None = None
    model_prefix: str | None = None


class AgentRead(BaseModel):
    id: str
    name: str
    url: str
    description: str
    transport: Transport
    config: dict
    enabled: bool
    capabilities: list[Capability]
    routable: bool
    contract_forms: list[ContractForm]
    model_prefix: str | None

    @classmethod
    def from_info(cls, agent: AgentInfo) -> "AgentRead":
        return cls(
            id=agent.id,
            name=agent.name,
            url=agent.url,
            description=agent.description,
            transport=agent.transport,
            config=agent.config,
            enabled=agent.enabled,
            capabilities=sorted(agent.capabilities),
            routable=agent.routable,
            contract_forms=sorted(agent.contract_forms),
            model_prefix=agent.model_prefix,
        )


class AppliedRead(BaseModel):
    """Что именно применилось. Нужен для отладки: по нему сразу видно,
    пересчитался ли вектор и пересоздался ли адаптер."""
    version: int
    added: list[str]
    updated: list[str]
    removed: list[str]
    embeddings_recomputed: list[str]
    adapters_invalidated: list[str]


class AgentMutationRead(BaseModel):
    agent: AgentRead
    applied: AppliedRead


class RegistryApplyRead(BaseModel):
    applied: AppliedRead


class RegistryStatusRead(BaseModel):
    file: str
    version: int
    loaded_at: str
    agents: int
    enabled: int
    routable: int
    embedding_index: list[str]
    adapter_cache: list[str]
    fallback_agent: str
