"""Тесты того, за что отвечает сам мастер.

Всё, что здесь проверяется, — его собственная логика: список моделей, выбор
агента, подмена `model`, проверка формы, сквозной проброс и формат ошибок.
Поведение конкретных агентов — в `test_agent_*.py`.
"""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
