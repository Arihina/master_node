"""Подмены для STUB-режима.

Две вещи, которые нужно подменить, чтобы мастер поднялся и работал без
внешнего окружения:

1. **Тяжёлые зависимости роутинга** — `sentence_transformers` и `ollama`
   загружают модели прямо на импорте. Вместо них ставится детерминированный
   роутер по ключевым словам: он позволяет проверить МЕХАНИКУ (что мастер
   зовёт роутер с правильным множеством кандидатов и форвардит выбранному
   агенту), но не качество семантики — семантические тесты помечены
   `@live_only`.

2. **Транспорт до агентов** — вместо HTTP-соединения адаптер получает
   ASGI-клиент, замкнутый на `fake_agent`. Класс адаптера при этом остаётся
   НАСТОЯЩИМ: проброс статуса, content-type и потокового тела проверяется
   на реальном коде мастера, а не на моке.
"""

from __future__ import annotations

import os
import sys
import types

import httpx

from tests.support import fake_agent


def install_dependency_stubs() -> None:
    """Ставится ДО импорта main. Идемпотентно."""
    # Settings требует эти поля; в STUB-режиме модели не грузятся, но
    # config.Settings() всё равно должен собраться.
    os.environ.setdefault("ollama_model", "stub-router-model")
    os.environ.setdefault("embedd_model", "stub-embedding-model")

    if "sentence_transformers" not in sys.modules:
        st = types.ModuleType("sentence_transformers")

        class _SentenceTransformer:
            def __init__(self, *a, **k):
                pass

            def encode(self, *a, **k):
                return [0.0]

        st.SentenceTransformer = _SentenceTransformer
        sys.modules["sentence_transformers"] = st

    if "ollama" not in sys.modules:
        ol = types.ModuleType("ollama")

        class _Client:
            def __init__(self, *a, **k):
                pass

            def chat(self, *a, **k):
                return {"message": {"content": '{"agent": null}'}}

        ol.Client = _Client
        sys.modules["ollama"] = ol


# Детерминированная замена семантике: по одному якорному слову на агента.
# Ровно те темы, что заявлены в описаниях агентов в registry.py.
_KEYWORD_ROUTES = (
    ("закуп", "epoz"),
    ("тендер", "epoz"),
    ("договор", "epoz"),
    ("аэродинамик", "tech_rag"),
    ("численн", "tech_rag"),
    ("уравнени", "tech_rag"),
    ("документ", "document_chat"),
)


def install_router_stub() -> str:
    """Возвращает id агента, который получает всё нераспознанное."""
    from api import deps

    async def _route(message: str, allowed: set[str] | None = None) -> str:
        pool = allowed if allowed is not None else set()
        lower = (message or "").lower()

        for keyword, agent_id in _KEYWORD_ROUTES:
            if keyword in lower and (not pool or agent_id in pool):
                return agent_id

        if not pool:
            return "chat"
        return "chat" if "chat" in pool else sorted(pool)[0]

    deps.master_router.route = _route
    return "chat"


def install_agent_transport() -> dict[str, object]:
    """Замыкает адаптеры мастера на in-memory агентов.

    Возвращает словарь `agent_id -> fake app` — тестам он не нужен, но
    полезен при отладке.
    """
    from adapters import factory
    from adapters.contract_http import ContractHTTPAdapter
    from adapters.ocr import OCRAdapter
    from registry import AGENTS

    apps: dict[str, object] = {}
    for agent_id in ("epoz", "chat", "tech_rag", "document_chat"):
        apps[agent_id] = fake_agent.make_fake_agent(
            agent_id,
            has_sources=agent_id in ("epoz", "tech_rag"),
            has_files=agent_id == "document_chat",
        )
    apps["ocr"] = fake_agent.make_fake_ocr_agent()
    if "rag_ingestion" in AGENTS:
        apps["rag_ingestion"] = fake_agent.make_fake_ingest_agent()

    instances: dict[str, object] = {}

    def _build(agent_id: str):
        agent = AGENTS[agent_id]
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[agent_id]),
            base_url="http://agent",
        )
        if agent.transport == "ocr":
            adapter = OCRAdapter(agent)
            adapter._client = client
            adapter._url = ""
        else:
            adapter = ContractHTTPAdapter(agent)
            adapter._client = client
            adapter._base = ""
        return adapter

    def get_adapter(agent_id: str):
        if agent_id not in instances:
            instances[agent_id] = _build(agent_id)
        return instances[agent_id]

    factory.get_adapter = get_adapter
    for module_name in ("api.chat", "api.responses", "api.agent_proxy"):
        module = sys.modules.get(module_name)
        if module is not None:
            module.get_adapter = get_adapter

    return apps
