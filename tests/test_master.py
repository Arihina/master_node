"""Тесты того, за что отвечает сам мастер.

Всё, что здесь проверяется, — его собственная логика: список моделей, выбор
агента, подмена `model`, проверка формы, сквозной проброс и формат ошибок.
Поведение конкретных агентов — в `test_agent_*.py`.
"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from registry import AGENTS, AgentInfo, RegistryFileError, validate_state
from tests.support import env
from tests.support.base import MasterTestCase

DIALOGUE_AGENTS = ("epoz", "chat", "tech_rag", "document_chat")


class ModelsTests(MasterTestCase):
    """GET /v1/models и /v1/models/{id}."""

    def test_models_list_shape(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["object"], "list")

        ids = {item["id"] for item in payload["data"]}
        for agent_id in DIALOGUE_AGENTS:
            self.assertIn(agent_id, ids)
        self.assertIn("auto", ids, "псевдо-модель auto обязана быть в списке")

    def test_models_list_valid_for_sdk(self):
        """owned_by обязателен: без него client.models.list() падает."""
        response = self.client.get("/v1/models")
        for item in response.json()["data"]:
            self.assertIn("owned_by", item)
            self.assertEqual(item["object"], "model")

        if env.HAS_OPENAI_SDK:
            from openai.types.model import Model
            for item in response.json()["data"]:
                Model.model_validate(item)

    def test_retrieve_model(self):
        response = self.client.get("/v1/models/epoz")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "epoz")

        if env.HAS_OPENAI_SDK:
            from openai.types.model import Model
            Model.model_validate(response.json())

    def test_retrieve_model_auto(self):
        response = self.client.get("/v1/models/auto")
        self.assertEqual(response.status_code, 200, response.text)

    def test_retrieve_unknown_model(self):
        response = self.client.get("/v1/models/no_such_agent")
        self.assertOpenAIError(response, 404, "not_found_error")


class RoutingMechanicsTests(MasterTestCase):
    """Механика выбора агента — одинаково в обеих формах."""

    def test_explicit_model_is_respected(self):
        for agent_id in DIALOGUE_AGENTS:
            with self.subTest(agent=agent_id):
                response = self.chat({
                    "model": agent_id,
                    "messages": [{"role": "user", "content": "привет"}]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["model"], agent_id)

    def test_auto_returns_concrete_agent_chat(self):
        """model=auto не протекает наружу: клиент видит реального агента."""
        response = self.chat({
            "model": "auto",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["model"], DIALOGUE_AGENTS)
        self.assertNotEqual(response.json()["model"], "auto")

    def test_auto_returns_concrete_agent_responses(self):
        """Форма Responses тоже обязана роутить, а не брать первого попавшегося.

        Это регрессия на реальный баг: в /v1/responses роутер не вызывался
        вовсе, и любой запрос уходил первому агенту по алфавиту.
        """
        response = self.responses({"model": "auto", "input": "привет"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["model"], DIALOGUE_AGENTS)
        self.assertNotEqual(response.json()["model"], "auto")

    def test_both_forms_route_alike(self):
        """Один и тот же текст обе формы отдают одному агенту."""
        text = "какой способ закупки выбрать"

        chat = self.chat({"model": "auto",
                          "messages": [{"role": "user", "content": text}]})
        responses = self.responses({"model": "auto", "input": text})

        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertEqual(responses.status_code, 200, responses.text)
        self.assertEqual(chat.json()["model"], responses.json()["model"],
                         "формы разошлись в выборе агента на одном и том же тексте")

    def test_route_debug_endpoint(self):
        response = self.client.post("/route", json={"message": "привет"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(response.json()["agent"], DIALOGUE_AGENTS)

    def test_unknown_model(self):
        response = self.chat({
            "model": "no_such_agent",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertOpenAIError(response, 404, "not_found_error")


class SemanticRoutingTests(MasterTestCase):
    """Качество семантики — только на живом стеке: в STUB-режиме роутер
    подменён детерминированной заглушкой и проверял бы сам себя."""

    CASES = (
        ("как подать заявку на тендер по 223-фз", "epoz"),
        ("что такое закупка", "epoz"),
        ("что такое аэродинамика", "tech_rag"),
        ("расскажи про метод Рунге-Кутта", "tech_rag"),
        ("расскажи анекдот", "chat"),
        ("помоги придумать идею проекта", "chat"),
    )

    @env.live_only
    def test_semantic_routing_chat(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                response = self.chat({
                    "model": "auto",
                    "messages": [{"role": "user", "content": text}]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["model"], expected)

    @env.live_only
    def test_semantic_routing_responses(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                response = self.responses({"model": "auto", "input": text})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["model"], expected)


class ContractFormTests(MasterTestCase):
    """Форма проверяется симметрично: агент без формы получает 400, а не
    подмену на другую форму."""

    def test_ocr_rejected_in_responses(self):
        response = self.responses({"model": "ocr", "input": "привет"})
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertIn("ocr", error["message"])

    def test_ocr_rejected_in_chat_completions(self):
        response = self.chat({
            "model": "ocr",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_all_dialogue_agents_support_both_forms(self):
        for agent_id in DIALOGUE_AGENTS:
            with self.subTest(agent=agent_id):
                chat = self.chat({
                    "model": agent_id,
                    "messages": [{"role": "user", "content": "привет"}]})
                self.assertEqual(chat.status_code, 200, chat.text)

                responses = self.responses(
                    {"model": agent_id, "input": "привет"})
                self.assertEqual(responses.status_code, 200, responses.text)


class AuthTests(MasterTestCase):
    """X-User-Id — основной способ; Authorization: Bearer нужен, чтобы
    официальный SDK подключался одной сменой base_url."""

    def test_missing_credentials(self):
        response = self.chat(
            {"model": "epoz", "messages": [
                {"role": "user", "content": "привет"}]},
            headers={})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_malformed_user_id(self):
        # Значение намеренно ASCII: заголовки по RFC latin-1, и нелатиница
        # упала бы в HTTP-клиенте, а не дошла до проверки мастера.
        response = self.chat(
            {"model": "epoz", "messages": [
                {"role": "user", "content": "привет"}]},
            headers={"X-User-Id": "not-a-uuid"})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_bearer_token_accepted(self):
        response = self.chat(
            {"model": "epoz", "messages": [
                {"role": "user", "content": "привет"}]},
            headers={"Authorization": f"Bearer {env.USER_ID}"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_bearer_with_bad_uuid_rejected(self):
        response = self.chat(
            {"model": "epoz", "messages": [
                {"role": "user", "content": "привет"}]},
            headers={"Authorization": "Bearer not-a-uuid"})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_passthrough_requires_credentials(self):
        response = self.agent(
            "GET", "epoz", "v1/platform/conversations", headers={})
        self.assertOpenAIError(response, 401, "authentication_error")


class RequestValidationTests(MasterTestCase):
    """Невалидное тело — 400 с заполненным param, а не 422."""

    def test_empty_messages(self):
        response = self.chat({"model": "epoz", "messages": []})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_missing_messages(self):
        response = self.chat({"model": "epoz"})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_last_message_must_be_user(self):
        response = self.chat({
            "model": "epoz",
            "messages": [{"role": "assistant", "content": "привет"}]})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_missing_input_in_responses(self):
        response = self.responses({"model": "epoz"})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_empty_input_in_responses(self):
        response = self.responses({"model": "epoz", "input": "   "})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_malformed_json(self):
        response = self.client.post(
            "/v1/chat/completions", headers=env.AUTH,
            content=b"{not json", )
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_pydantic_validation_maps_to_400_with_param(self):
        """Ошибка от валидатора FastAPI (а не наш явный raise) тоже обязана
        стать 400 с заполненным param: SDK мапит 422 в UnprocessableEntityError,
        мимо клиентского except BadRequestError."""
        response = self.client.post("/route", json={})
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertEqual(error["param"], "message",
                         "param должен указывать на проблемное поле, а не быть null")

    def test_pydantic_validation_nested_param(self):
        response = self.client.post("/route", json={"message": 123})
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertEqual(error["param"], "message")

    def test_error_envelope_has_all_fields(self):
        """Форма конверта — часть контракта: клиенты читают type/param/code."""
        response = self.chat({"model": "epoz", "messages": []})
        error = self.assertOpenAIError(response, 400)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"])


class PassthroughTests(MasterTestCase):
    """Сквозной проброс /agents/{id}/v1/... — мастер не интерпретирует path."""

    def test_path_outside_contract(self):
        response = self.agent("GET", "epoz", "docs")
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_unknown_agent_in_passthrough(self):
        response = self.agent("GET", "no_such_agent",
                              "v1/platform/conversations")
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_delete_is_proxied(self):
        """DELETE проходит насквозь — своей ручки у мастера нет."""
        created = self.responses({"model": "epoz", "input": "привет"})
        self.assertEqual(created.status_code, 200, created.text)
        response_id = created.json()["id"]

        deleted = self.agent("DELETE", "epoz", f"v1/responses/{response_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])

    def test_patch_is_proxied(self):
        created = self.agent("POST", "epoz", "v1/platform/conversations",
                             json={"title": "До"})
        self.assertIn(created.status_code, (200, 201), created.text)
        conversation_id = created.json()["id"]

        renamed = self.agent("PATCH", "epoz",
                             f"v1/platform/conversations/{conversation_id}",
                             json={"title": "После"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "После")

    def test_agent_status_passes_through(self):
        """Статус агента не переписывается мастером."""
        response = self.agent(
            "GET", "epoz", f"v1/chat/completions/{env.MISSING_COMPLETION_ID}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_stream_content_type_passes_through(self):
        response = self.chat({
            "model": "epoz", "stream": True,
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream",
                      response.headers.get("content-type", ""))

    def test_put_is_proxied(self):
        """PUT доходит до агента через catch-all. У fake-агента PUT-хендлера
        нет — ждём 405 от него; для теста важно только, что до агента дошло
        (а не 404/405 от мастера)."""
        response = self.agent("PUT", "epoz", "v1/platform/conversations/xyz",
                              json={"title": "test"})
        # 405 — от FastAPI fake-агента, значит catch-all мастера отработал.
        self.assertEqual(response.status_code, 405, response.text)

    def test_large_multipart_body_streams_through(self):
        """Тело POST /v1/files стримится, а не собирается в bytes у мастера.
        Функционально проверяем, что 5 МБ multipart корректно доходит до
        агента и его размер сохранён — если бы `body or None` рвал итератор
        или мастер буферизовал с обрезкой, размер не сошёлся бы."""
        size = 5 * 1024 * 1024
        payload = b"x" * size
        response = self.agent(
            "POST", "document_chat", "v1/files",
            files={"file": ("big.bin", payload, "application/octet-stream")})
        self.assertIn(response.status_code, (200, 201), response.text)
        self.assertEqual(response.json()["bytes"], size)


class NamespacedModelTests(MasterTestCase):
    """`model: "prefix/rest"` резолвится в агента через AgentInfo.model_prefix.

    Проверяется: маршрутизация, сохранение исходного `model` в теле форварда
    (агент видит `"prefix/rest"`, не подмену на agent_id) и то, что префиксы
    в режиме `model="auto"` не участвуют.
    """

    PREFIX = "ns"
    _ORIGINAL: AgentInfo | None = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Прицепить префикс к существующему агенту, а не заводить нового:
        # не нужен новый фиктивный апстрим и не трогаются адаптеры/векторный
        # индекс. AGENTS — единый разделяемый словарь, никогда не rebind.
        cls._ORIGINAL = AGENTS["chat"]
        AGENTS["chat"] = cls._ORIGINAL.model_copy(
            update={"model_prefix": cls.PREFIX})

    @classmethod
    def tearDownClass(cls):
        AGENTS["chat"] = cls._ORIGINAL

    def test_prefix_routes_to_owner(self):
        response = self.chat({
            "model": f"{self.PREFIX}/abc-123",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)

    def test_body_model_forwarded_as_is(self):
        """Агент получает исходное `prefix/rest`, не подмену на agent_id.
        Fake-агент echo-ит `body.model` в поле model ответа — по нему и
        сверяемся."""
        original = f"{self.PREFIX}/abc-123"
        response = self.chat({
            "model": original,
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], original)

    def test_prefix_works_in_responses_form(self):
        original = f"{self.PREFIX}/xyz-789"
        response = self.responses({"model": original, "input": "привет"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], original)

    def test_unknown_prefix_returns_404(self):
        response = self.chat({
            "model": "no_such_prefix/abc",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_prefix_as_bare_id_not_treated_as_namespace(self):
        """`"ns"` без слэша — обычный agent id, а не отсылка к владельцу
        префикса; агента с id="ns" нет, ждём 404."""
        response = self.chat({
            "model": self.PREFIX,
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_auto_does_not_route_by_prefix(self):
        """model="auto" — семантика, а не неймспейс. Ответ — обычный agent id
        без слэша."""
        response = self.chat({
            "model": "auto",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("/", response.json()["model"])

    def test_prefix_agent_still_reachable_by_id(self):
        """Владелец префикса остаётся обычным агентом с id — форвард по id
        работает как раньше, тело `model` подменяется на agent_id."""
        response = self.chat({
            "model": "chat",
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], "chat")


class ModelPrefixValidationTests(unittest.TestCase):
    """Валидация формата и уникальности model_prefix. Чистые unit-тесты,
    без HTTP-клиента."""

    def _minimal(self, **overrides) -> dict:
        base = {"id": "test", "name": "Test", "url": "http://x",
                "description": "d"}
        return {**base, **overrides}

    def test_none_allowed(self):
        agent = AgentInfo(**self._minimal())
        self.assertIsNone(agent.model_prefix)

    def test_valid_prefix_accepted(self):
        for prefix in ("rag", "r", "ns2", "with_underscore", "a" * 32):
            with self.subTest(prefix=prefix):
                agent = AgentInfo(**self._minimal(model_prefix=prefix))
                self.assertEqual(agent.model_prefix, prefix)

    def test_invalid_prefix_rejected(self):
        for prefix in ("Rag", "rag/x", "1rag", "", "-rag", "rag ",
                       "a" * 33, "rag-x"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValidationError):
                    AgentInfo(**self._minimal(model_prefix=prefix))

    def test_duplicate_prefix_rejected_by_validate_state(self):
        chat = AGENTS["chat"]  # fallback_agent, обязателен в реестре
        a = AgentInfo(id="a", name="a", url="http://x",
                      description="d", model_prefix="rag")
        b = AgentInfo(id="b", name="b", url="http://y",
                      description="d", model_prefix="rag")
        with self.assertRaises(RegistryFileError) as ctx:
            validate_state({"chat": chat, "a": a, "b": b})
        self.assertIn("rag", str(ctx.exception))

    def test_distinct_prefixes_allowed(self):
        chat = AGENTS["chat"]
        a = AgentInfo(id="a", name="a", url="http://x",
                      description="d", model_prefix="rag")
        b = AgentInfo(id="b", name="b", url="http://y",
                      description="d", model_prefix="ns")
        validate_state({"chat": chat, "a": a, "b": b})  # не должно падать

    def test_multiple_none_prefixes_allowed(self):
        """Отсутствие префикса — норма для большинства агентов, дубли None
        уникальность не нарушают."""
        chat = AGENTS["chat"]
        a = AgentInfo(id="a", name="a", url="http://x", description="d")
        b = AgentInfo(id="b", name="b", url="http://y", description="d")
        validate_state({"chat": chat, "a": a, "b": b})


class ContractHTTPAdapterHeadersTests(unittest.IsolatedAsyncioTestCase):
    """Заголовки, которые адаптер шлёт апстриму. Прямой unit на адаптере —
    сеть перехвачена локальным фейком, мастер и fake_agent не участвуют."""

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_raw(self):
            yield b"{}"

    class _FakeStreamCM:
        async def __aenter__(self):
            return ContractHTTPAdapterHeadersTests._FakeResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        """Ровно тот срез httpx.AsyncClient, который использует адаптер:
        `.stream(method, url, **kw)` и `.aclose()`. MockTransport на
        streaming-режиме httpx=0.28 не годится (материализованный Response
        валит aiter_raw StreamConsumed'ом)."""

        def __init__(self, sink: dict):
            self._sink = sink

        def stream(self, method: str, url: str, **kw):
            self._sink["method"] = method
            self._sink["url"] = url
            self._sink["headers"] = dict(kw.get("headers", {}))
            self._sink["content"] = kw.get("content")
            return ContractHTTPAdapterHeadersTests._FakeStreamCM()

        async def aclose(self):
            pass

    async def _captured(self, method: str = "POST",
                        body: bytes | None = b"{}") -> dict:
        from adapters.contract_http import ContractHTTPAdapter

        agent = AgentInfo(id="probe", name="probe",
                          url="http://agent", description="d")
        adapter = ContractHTTPAdapter(agent)

        sink: dict = {}
        adapter._client = self._FakeClient(sink)
        try:
            result = await adapter.proxy(
                method, "/v1/probe", "user-1",
                body=body,
                content_type="application/json" if body else None)
            async for _ in result.body:  # добираем тело до конца
                pass
        finally:
            await adapter.aclose()

        return sink

    async def test_accept_encoding_identity_sent_upstream(self):
        """Без identity httpx поставит "gzip, deflate, br" по умолчанию —
        а мастер отдаёт aiter_raw() и на сжатом ответе стрим ломается."""
        sink = await self._captured()
        self.assertEqual(sink["headers"].get("Accept-Encoding"), "identity",
                         "мастер обязан просить у агента несжатый ответ, "
                         "иначе aiter_raw() отдаст клиенту гзип-байты, "
                         "помеченные как text/event-stream")

    async def test_x_user_id_forwarded(self):
        """Регрессия на инвариант из адаптера: user_id обязательно уходит."""
        sink = await self._captured()
        self.assertEqual(sink["headers"].get("X-User-Id"), "user-1")

    async def test_no_body_no_content_type(self):
        """GET-подобный вызов без тела — content-type не выставляется, чтобы
        не привирать апстриму."""
        sink = await self._captured(method="GET", body=None)
        self.assertNotIn("Content-Type", sink["headers"],
                         "мастер не должен выдумывать content-type "
                         "для запросов без тела")
        self.assertIsNone(sink["content"],
                          "для методов без тела content должен быть None")


class RagIngestionAgentTests(MasterTestCase):
    """rag_ingestion — единственный агент с capability "ingest" и без
    contract_forms. Он не должен появляться в /v1/models, не должен
    участвовать в auto-роутинге, но должен быть проходим через catch-all
    /agents/rag_ingestion/v1/platform/rags/..."""

    AGENT_ID = "rag_ingestion"

    def test_registered_with_ingest_capability(self):
        agent = AGENTS[self.AGENT_ID]
        self.assertIn("ingest", agent.capabilities)
        self.assertFalse(agent.contract_forms,
                         "у ingestion нет форм генерации — только платформенные ручки")
        self.assertFalse(agent.routable,
                         "ingestion не участвует в семантической маршрутизации")

    def test_not_listed_in_models(self):
        """Модели — это то, что можно указать в поле `model`. У ingestion
        нет contract_forms, значит и в списке моделей ему не место —
        _is_model это уже фильтрует, тест защищает инвариант."""
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200, response.text)
        ids = {item["id"] for item in response.json()["data"]}
        self.assertNotIn(self.AGENT_ID, ids)

    def test_not_addressable_as_model_in_chat(self):
        """Прямая попытка позвать ingestion как модель — 400: он не
        реализует ни chat_completions, ни responses."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": "привет"}]})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_not_addressable_as_model_in_responses(self):
        response = self.responses({"model": self.AGENT_ID, "input": "привет"})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_passthrough_reaches_ingest(self):
        """Мастер должен донести POST /v1/platform/rags до ingestion —
        catch-all для этого и существует. Тело JSON, X-User-Id обязателен."""
        response = self.agent("POST", self.AGENT_ID, "v1/platform/rags",
                              json={"name": "Регламенты"})
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["name"], "Регламенты")
        self.assertEqual(payload["owner_id"], env.USER_ID)

    def test_passthrough_requires_user(self):
        response = self.agent("POST", self.AGENT_ID, "v1/platform/rags",
                              json={"name": "x"}, headers={})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_passthrough_scopes_by_user(self):
        """Второй пользователь ingestion не видит чужие наборы — стандартный
        инвариант скоупинга по X-User-Id, ingestion его соблюдает."""
        mine = self.agent("POST", self.AGENT_ID, "v1/platform/rags",
                          json={"name": "мой"})
        self.assertEqual(mine.status_code, 201, mine.text)
        rag_id = mine.json()["id"]

        other = self.agent("GET", self.AGENT_ID, f"v1/platform/rags/{rag_id}",
                           headers=env.OTHER_AUTH)
        self.assertOpenAIError(other, 404, "not_found_error")


class IngestCapabilityValidationTests(unittest.TestCase):
    """Capability "ingest" — валидный литерал, не эквивалент "chat"."""

    def test_ingest_accepted(self):
        agent = AgentInfo(id="x", name="x", url="http://y",
                          description="d", routable=False,
                          capabilities={"ingest"}, contract_forms=set())
        self.assertEqual(agent.capabilities, {"ingest"})

    def test_ingest_and_chat_coexist(self):
        """Гипотетический сервис, умеющий и то и другое, должен собираться."""
        agent = AgentInfo(id="x", name="x", url="http://y",
                          description="d",
                          capabilities={"chat", "ingest"},
                          contract_forms={"chat_completions"})
        self.assertEqual(agent.capabilities, {"chat", "ingest"})

    def test_empty_capabilities_still_rejected(self):
        """Введение "ingest" не смягчает инвариант непустого набора."""
        with self.assertRaises(ValidationError):
            AgentInfo(id="x", name="x", url="http://y",
                      description="d", routable=False,
                      capabilities=set(), contract_forms=set())


if __name__ == "__main__":
    unittest.main()
