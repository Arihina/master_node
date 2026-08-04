from __future__ import annotations

from registry import AgentInfo
from .base import AgentAdapter, ProxyResult


class ExternalAPIAdapter(AgentAdapter):
    """СКЕЛЕТ. Агент поверх чужого API.

    Ключевое отличие от contract-агента: у вендора НЕТ ни completion-объектов,
    ни conversations, ни фидбэка в нашем формате. Чтобы соблюсти контракт
    платформы, адаптер должен САМ:
      * распознавать path (`/v1/chat/completions`, `.../feedback`,
        `/v1/platform/conversations...`) и обслуживать его своими силами —
        мастер просто форвардит method+path+body, дальше адаптер сам решает,
        что с этим делать;
      * для генерации — звать API вендора и транслировать дельты его стрима в
        канонические `chat.completion.chunk` (см. epoz/architecture_target.md);
      * хранить completions/conversations/фидбэк в общем сторе (Postgres) —
        у вендора для этого нет своего хранилища с нужной формой;
      * держать креды вендора у себя (наружу/клиенту не отдаются).

    Транспорт в реестре: transport="external", config={...вендорские поля...}.
    """

    def __init__(self, agent: AgentInfo):
        self.agent_id = agent.id
        self._cfg = agent.config
        # self._store = ConversationStore(...)
        # self._client = httpx.AsyncClient(base_url=...)

    async def proxy(self, method, path, user_id, body=None, content_type=None) -> ProxyResult:
        raise NotImplementedError(
            "разобрать path, обслужить его поверх API вендора и общего "
            "стора, вернуть ProxyResult(status, content_type, body)"
        )
