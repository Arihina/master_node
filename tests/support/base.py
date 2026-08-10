"""База тестов мастера и общий контракт агента.

`AgentContractTests` — набор проверок, обязательных для КАЖДОГО диалогового
агента платформы. В curl-файлах этот набор был скопирован по разу на агента,
и копии успевали разойтись (у одного проверялся `/sources`, у другого нет; у
одного 422, у другого 400). Здесь он один, а различия агентов выражены
атрибутами класса, а не отдельными копиями текста.
"""

from __future__ import annotations

import json
import unittest

from tests.support import env
from tests.support.client import get_client


class MasterTestCase(unittest.TestCase):
    """Общие помощники: вызовы мастера, разбор SSE, проверки формата."""

    @classmethod
    def setUpClass(cls):
        cls.client = get_client()

    # ---------------- вызовы -------------------------------------------

    def chat(self, body: dict, headers: dict | None = None, **kwargs):
        return self.client.post("/v1/chat/completions",
                                json=self._limited(body, "max_tokens"),
                                headers=env.AUTH if headers is None else headers,
                                **kwargs)

    def responses(self, body: dict, headers: dict | None = None, **kwargs):
        return self.client.post("/v1/responses",
                                json=self._limited(body, "max_output_tokens"),
                                headers=env.AUTH if headers is None else headers,
                                **kwargs)

    @staticmethod
    def _limited(body: dict, field: str) -> dict:
        """Подставляет ограничение длины ответа, если задан TEST_MAX_TOKENS.
        Проверкам формы содержание ответа не важно, а на живом стеке это
        сокращает прогон в разы. Явно указанное в тесте значение не трогаем."""
        if env.MAX_TOKENS is None or field in body:
            return body
        return {**body, field: env.MAX_TOKENS}

    def agent(self, method: str, agent_id: str, path: str,
              headers: dict | None = None, **kwargs):
        """Сквозной проброс: /agents/{agent_id}/v1/..."""
        return self.client.request(
            method, f"/agents/{agent_id}/{path.lstrip('/')}",
            headers=env.AUTH if headers is None else headers, **kwargs)

    # ---------------- разбор SSE ----------------------------------------

    @staticmethod
    def data_lines(response) -> list[str]:
        return [line[len("data: "):] for line in response.text.splitlines()
                if line.startswith("data: ")]

    def chat_chunks(self, response) -> list[dict]:
        """Чанки Chat Completions без терминатора [DONE]."""
        lines = self.data_lines(response)
        self.assertTrue(lines, "стрим пуст")
        self.assertEqual(lines[-1], "[DONE]",
                         "поток Chat Completions обязан закрываться data: [DONE]")
        return [json.loads(line) for line in lines[:-1]]

    def response_events(self, response) -> list[dict]:
        """События Responses API. Терминатора [DONE] здесь нет по спецификации:
        поток закрывается событием response.completed."""
        lines = self.data_lines(response)
        self.assertTrue(lines, "стрим пуст")
        self.assertNotIn("[DONE]", lines,
                         "в форме Responses терминатора [DONE] быть не должно")
        return [json.loads(line) for line in lines]

    @staticmethod
    def joined_content(chunks: list[dict]) -> str:
        out = []
        for chunk in chunks:
            for choice in chunk.get("choices", []):
                out.append(choice.get("delta", {}).get("content") or "")
        return "".join(out)

    # ---------------- проверки ------------------------------------------

    def assertOpenAIError(self, response, status: int, error_type: str | None = None):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertIn(
            "error", body, "ошибка должна приходить в конверте {'error': {...}}")
        self.assertEqual(set(body["error"]), {
                         "message", "type", "param", "code"})
        if error_type is not None:
            self.assertEqual(body["error"]["type"], error_type)
        return body["error"]

    def assertValidChatCompletion(self, payload: dict):
        self.assertEqual(payload.get("object"), "chat.completion")
        self.assertTrue(payload.get("choices"))
        if env.HAS_OPENAI_SDK:
            from openai.types.chat import ChatCompletion
            ChatCompletion.model_validate(payload)

    def assertValidChatChunk(self, payload: dict):
        self.assertEqual(payload.get("object"), "chat.completion.chunk")
        if env.HAS_OPENAI_SDK:
            from openai.types.chat import ChatCompletionChunk
            ChatCompletionChunk.model_validate(payload)

    def assertValidResponse(self, payload: dict):
        self.assertEqual(payload.get("object"), "response")
        self.assertIsInstance(payload.get("output"), list)
        if env.HAS_OPENAI_SDK:
            from openai.types.responses import Response
            Response.model_validate(payload)

    def assertValidResponseEvent(self, payload: dict):
        self.assertIn("type", payload)
        self.assertIn("sequence_number", payload)
        if env.HAS_OPENAI_SDK:
            from pydantic import TypeAdapter
            from openai.types.responses import ResponseStreamEvent
            TypeAdapter(ResponseStreamEvent).validate_python(payload)


class AgentContractTests:
    """Контракт, обязательный для любого диалогового агента платформы.

    Подмешивается к `MasterTestCase` в модулях `test_agent_*.py`. Настройка —
    атрибутами класса, без копирования тел тестов.
    """

    AGENT_ID: str = ""
    #: заявлен ли `/v1/chat/completions/{id}/sources` (RAG-агенты)
    HAS_SOURCES: bool = False
    #: заявлен ли `/v1/files` (агент с вложениями)
    HAS_FILES: bool = False
    #: вопрос, на котором агент заведомо что-то ответит
    QUESTION: str = "привет"

    # ---------------- вспомогательное -----------------------------------

    def _completion_id(self, **extra) -> str:
        body = {"model": self.AGENT_ID,
                "messages": [{"role": "user", "content": self.QUESTION}]}
        body.update(extra)
        response = self.chat(body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def _response_id(self, **extra) -> str:
        body = {"model": self.AGENT_ID, "input": self.QUESTION}
        body.update(extra)
        response = self.responses(body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def _new_conversation(self, title: str = "Тестовый чат") -> str:
        response = self.agent("POST", self.AGENT_ID, "v1/platform/conversations",
                              json={"title": title})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()["id"]

    # ---------------- Chat Completions ----------------------------------

    def test_chat_completion_shape(self):
        """Нестрим-ответ — валидный объект chat.completion."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertValidChatCompletion(payload)
        self.assertTrue(payload["id"].startswith("chatcmpl-"))
        self.assertEqual(payload["model"], self.AGENT_ID,
                         "мастер обязан вернуть id агента, которому ушёл запрос")
        self.assertEqual(payload["choices"][0]["message"]["role"], "assistant")

    def test_chat_completion_stream(self):
        """Стрим: валидные чанки, единый id, терминатор [DONE]."""
        response = self.chat({
            "model": self.AGENT_ID, "stream": True,
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        chunks = self.chat_chunks(response)
        self.assertTrue(chunks)

        for chunk in chunks:
            self.assertValidChatChunk(chunk)

        ids = {chunk["id"] for chunk in chunks}
        self.assertEqual(
            len(ids), 1, "id обязан быть одинаковым во всех чанках")

        finish = [choice["finish_reason"] for chunk in chunks
                  for choice in chunk.get("choices", [])]
        self.assertEqual(finish[-1], "stop")
        self.assertTrue(self.joined_content(
            chunks).strip(), "пустой ответ в стриме")

    def test_chat_stream_usage_only_when_requested(self):
        """usage в стриме появляется только по stream_options.include_usage."""
        without = self.chat({
            "model": self.AGENT_ID, "stream": True,
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        self.assertFalse(
            any(chunk.get("usage") for chunk in self.chat_chunks(without)),
            "без include_usage финального чанка с usage быть не должно")

        with_usage = self.chat({
            "model": self.AGENT_ID, "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        chunks = self.chat_chunks(with_usage)
        last = chunks[-1]
        self.assertIsNotNone(last.get("usage"),
                             "финальный чанк с usage не пришёл")
        self.assertEqual(last["choices"], [],
                         "у чанка с usage choices должен быть пустым")

    def test_chat_accepts_history(self):
        """История приходит целиком в messages — форма stateless."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [
                {"role": "user", "content": self.QUESTION},
                {"role": "assistant", "content": "предыдущий ответ"},
                {"role": "user", "content": "а подробнее"},
            ],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertValidChatCompletion(response.json())

    def test_chat_accepts_system_role(self):
        """system/developer принимаются, а не ломают запрос."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [
                {"role": "system", "content": "Отвечай кратко"},
                {"role": "user", "content": self.QUESTION},
            ],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def test_chat_accepts_content_parts(self):
        """content — не только строка, но и массив частей."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": self.QUESTION},
            ]}],
        })
        self.assertEqual(response.status_code, 200, response.text)

    def test_chat_rejects_multiple_choices(self):
        """n>1 отклоняется, а не отдаёт молча один вариант."""
        response = self.chat({
            "model": self.AGENT_ID, "n": 3,
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_chat_completion_roundtrip(self):
        """Ответ читается повторно по id и совпадает с исходным."""
        completion_id = self._completion_id()
        response = self.agent("GET", self.AGENT_ID,
                              f"v1/chat/completions/{completion_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertValidChatCompletion(response.json())
        self.assertEqual(response.json()["id"], completion_id)

    def test_chat_completion_delete(self):
        """DELETE удаляет ответ; повторное чтение — 404."""
        completion_id = self._completion_id()
        deleted = self.agent("DELETE", self.AGENT_ID,
                             f"v1/chat/completions/{completion_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json(), {
            "id": completion_id, "object": "chat.completion.deleted",
            "deleted": True})

        gone = self.agent("GET", self.AGENT_ID,
                          f"v1/chat/completions/{completion_id}")
        self.assertOpenAIError(gone, 404, "not_found_error")

    # ---------------- Responses -----------------------------------------

    def test_response_shape(self):
        response = self.responses(
            {"model": self.AGENT_ID, "input": self.QUESTION})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertValidResponse(payload)
        self.assertTrue(payload["id"].startswith("resp_"))
        self.assertEqual(payload["model"], self.AGENT_ID)
        self.assertEqual(payload["status"], "completed")

    def test_response_stream_event_order(self):
        """События идут в порядке спецификации, sequence_number монотонен."""
        response = self.responses({
            "model": self.AGENT_ID, "input": self.QUESTION, "stream": True})
        self.assertEqual(response.status_code, 200, response.text)
        events = self.response_events(response)
        self.assertTrue(events)

        for event in events:
            self.assertValidResponseEvent(event)

        self.assertEqual([e["sequence_number"] for e in events],
                         list(range(1, len(events) + 1)),
                         "sequence_number обязан быть монотонным без пропусков")
        self.assertEqual(events[0]["type"], "response.created")
        self.assertEqual(events[-1]["type"], "response.completed")

        types = [e["type"] for e in events]
        self.assertIn("response.output_text.delta", types)
        self.assertIn("response.output_item.done", types)

        completed = events[-1]["response"]
        self.assertValidResponse(completed)
        self.assertIn("input_tokens_details", completed.get("usage", {}))

    def test_response_input_items(self):
        """input может быть списком items, а не только строкой."""
        response = self.responses({
            "model": self.AGENT_ID,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": self.QUESTION}]}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertValidResponse(response.json())

    def test_response_roundtrip_and_delete(self):
        response_id = self._response_id()

        fetched = self.agent("GET", self.AGENT_ID,
                             f"v1/responses/{response_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertValidResponse(fetched.json())

        deleted = self.agent("DELETE", self.AGENT_ID,
                             f"v1/responses/{response_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json(), {
            "id": response_id, "object": "response.deleted", "deleted": True})

        gone = self.agent("GET", self.AGENT_ID, f"v1/responses/{response_id}")
        self.assertOpenAIError(gone, 404, "not_found_error")

    def test_response_store_false_not_persisted(self):
        """store=false — ответ не сохраняется, читать нечего."""
        response_id = self._response_id(store=False)
        gone = self.agent("GET", self.AGENT_ID, f"v1/responses/{response_id}")
        self.assertOpenAIError(gone, 404, "not_found_error")

    def test_response_conversation_alias(self):
        """`conversation` — имя из спецификации; conversation_id — алиас."""
        conversation_id = self._new_conversation()

        by_spec = self.responses({"model": self.AGENT_ID, "input": self.QUESTION,
                                  "conversation": conversation_id})
        self.assertEqual(by_spec.status_code, 200, by_spec.text)
        self.assertEqual(by_spec.json()["conversation_id"], conversation_id)

        by_alias = self.responses({"model": self.AGENT_ID, "input": self.QUESTION,
                                   "conversation_id": conversation_id})
        self.assertEqual(by_alias.status_code, 200, by_alias.text)
        self.assertEqual(by_alias.json()["conversation_id"], conversation_id)

    def test_response_previous_response_id(self):
        """Стандартный способ продолжить цепочку — по id предыдущего ответа."""
        conversation_id = self._new_conversation()
        first = self._response_id(conversation=conversation_id)

        second = self.responses({"model": self.AGENT_ID, "input": "а подробнее",
                                 "previous_response_id": first})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["conversation_id"], conversation_id)

    def test_response_previous_response_id_unknown(self):
        response = self.responses({"model": self.AGENT_ID, "input": self.QUESTION,
                                   "previous_response_id": env.MISSING_RESPONSE_ID})
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_response_conversation_rejects_history(self):
        """С conversation в input должен быть только новый ход."""
        conversation_id = self._new_conversation()
        response = self.responses({
            "model": self.AGENT_ID, "conversation": conversation_id,
            "input": [
                {"role": "user", "content": [
                    {"type": "input_text", "text": "раз"}]},
                {"role": "assistant", "content": [
                    {"type": "output_text", "text": "два"}]},
                {"role": "user", "content": [
                    {"type": "input_text", "text": "три"}]},
            ],
        })
        self.assertOpenAIError(response, 400, "invalid_request_error")

    # ---------------- фидбэк --------------------------------------------

    def test_feedback_lifecycle(self):
        completion_id = self._completion_id()
        base = f"v1/chat/completions/{completion_id}/feedback"

        created = self.agent("POST", self.AGENT_ID, base,
                             json={"vote": 1, "comment": "по делу"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["vote"], 1,
                         "vote должен приходить верхним уровнем — форма фидбэка "
                         "одинакова у всех агентов, как бы он ни хранился")

        fetched = self.agent("GET", self.AGENT_ID, base)
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["vote"], 1)
        self.assertEqual(fetched.json()["comment"], "по делу")

        removed = self.agent("DELETE", self.AGENT_ID, base)
        self.assertIn(removed.status_code, (200, 204), removed.text)

    def test_feedback_absent_is_not_404(self):
        """«Не оценивали» и «сообщение не найдено» — разные ситуации, и
        различать их по одному коду фронт не должен."""
        completion_id = self._completion_id()
        response = self.agent("GET", self.AGENT_ID,
                              f"v1/chat/completions/{completion_id}/feedback")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["vote"])

    def test_feedback_rejects_bad_vote(self):
        completion_id = self._completion_id()
        response = self.agent(
            "POST", self.AGENT_ID,
            f"v1/chat/completions/{completion_id}/feedback", json={"vote": 5})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_feedback_rejects_bad_comment(self):
        completion_id = self._completion_id()
        response = self.agent(
            "POST", self.AGENT_ID,
            f"v1/chat/completions/{completion_id}/feedback", json={"comment": 42})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_feedback_unknown_completion(self):
        response = self.agent(
            "GET", self.AGENT_ID,
            f"v1/chat/completions/{env.MISSING_COMPLETION_ID}/feedback")
        self.assertOpenAIError(response, 404, "not_found_error")

    # ---------------- источники ------------------------------------------

    def test_sources(self):
        if not self.HAS_SOURCES:
            self.skipTest(f"{self.AGENT_ID} не RAG-агент, /sources не заявлен")

        completion_id = self._completion_id()
        response = self.agent("GET", self.AGENT_ID,
                              f"v1/chat/completions/{completion_id}/sources")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("retrieved", payload)
        self.assertIn("used_sources", payload)
        self.assertIsInstance(payload["retrieved"], list)
        self.assertIsInstance(payload["used_sources"], list)

    def test_sources_absent_for_non_rag(self):
        if self.HAS_SOURCES:
            self.skipTest(f"{self.AGENT_ID} — RAG-агент, /sources у него есть")

        completion_id = self._completion_id()
        response = self.agent("GET", self.AGENT_ID,
                              f"v1/chat/completions/{completion_id}/sources")
        self.assertEqual(response.status_code, 404, response.text)

    # ---------------- чаты ------------------------------------------------

    def test_conversation_crud(self):
        conversation_id = self._new_conversation("Черновик")

        listed = self.agent("GET", self.AGENT_ID, "v1/platform/conversations")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        items = payload if isinstance(
            payload, list) else payload.get("data", [])
        self.assertIn(conversation_id, [item["id"] for item in items])

        renamed = self.agent("PATCH", self.AGENT_ID,
                             f"v1/platform/conversations/{conversation_id}",
                             json={"title": "Новое название"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "Новое название")

        removed = self.agent("DELETE", self.AGENT_ID,
                             f"v1/platform/conversations/{conversation_id}")
        self.assertIn(removed.status_code, (200, 204), removed.text)

        gone = self.agent("GET", self.AGENT_ID,
                          f"v1/platform/conversations/{conversation_id}/messages")
        self.assertEqual(gone.status_code, 404, gone.text)

    def test_conversation_binds_message(self):
        """conversation_id в теле привязывает сообщение к чату."""
        conversation_id = self._new_conversation()
        completion_id = self._completion_id(conversation_id=conversation_id)

        fetched = self.agent("GET", self.AGENT_ID,
                             f"v1/chat/completions/{completion_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["conversation_id"], conversation_id)

        history = self.agent("GET", self.AGENT_ID,
                             f"v1/platform/conversations/{conversation_id}/messages")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertTrue(history.json(), "история чата пуста")

    def test_conversation_unknown(self):
        response = self.chat({
            "model": self.AGENT_ID, "conversation_id": env.MISSING_UUID,
            "messages": [{"role": "user", "content": self.QUESTION}],
        })
        self.assertOpenAIError(response, 404, "not_found_error")

    # ---------------- изоляция пользователей -----------------------------

    def test_other_user_cannot_read(self):
        """Ресурсы скоупятся по X-User-Id; чужое — 404, а не 403."""
        completion_id = self._completion_id()
        response = self.agent("GET", self.AGENT_ID,
                              f"v1/chat/completions/{completion_id}",
                              headers=env.OTHER_AUTH)
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_requires_user_header(self):
        response = self.chat(
            {"model": self.AGENT_ID,
             "messages": [{"role": "user", "content": self.QUESTION}]},
            headers={})
        self.assertOpenAIError(response, 401, "authentication_error")
