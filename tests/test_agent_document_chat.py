"""document_chat — единственный агент с вложениями.

Своя часть контракта: /v1/files (OpenAI Files API) и подключение документа к
вопросу через file_id. Документ сначала загружается и проходит распознавание,
и только потом на него ссылаются — инлайновых вложений агент не принимает.
"""

from __future__ import annotations

import unittest

from tests.support import env
from tests.support.base import AgentContractTests, MasterTestCase

# Минимальный валидный PDF — чтобы в LIVE-режиме распознаватель получил файл,
# который в принципе можно разобрать, а не случайные байты.
SAMPLE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


class DocumentChatContractTests(AgentContractTests, MasterTestCase):
    AGENT_ID = "document_chat"
    HAS_SOURCES = False
    HAS_FILES = True
    QUESTION = "о чём документ"


class DocumentChatFilesTests(MasterTestCase):
    """Файлы: загрузка, чтение, список, удаление, использование в вопросе."""

    AGENT_ID = "document_chat"

    def upload(self, filename: str = "накладная.pdf") -> dict:
        response = self.agent(
            "POST", self.AGENT_ID, "v1/files",
            files={"file": (filename, SAMPLE_PDF, "application/pdf")})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def test_upload_shape(self):
        payload = self.upload()
        self.assertTrue(payload["id"].startswith("file-"))
        self.assertEqual(payload["object"], "file")
        self.assertEqual(payload["purpose"], "assistants")
        self.assertIn(payload["status"], ("uploaded", "processed", "error"),
                      "status обязан быть значением из спецификации OpenAI, "
                      "внутренний статус конвейера — в processing_status")

        if env.HAS_OPENAI_SDK:
            from openai.types import FileObject
            FileObject.model_validate(payload)

    def test_internal_status_kept_separately(self):
        payload = self.upload()
        self.assertIn("processing_status", payload,
                      "подробный статус обработки — платформенное расширение")

    def test_file_lifecycle(self):
        file_id = self.upload()["id"]

        fetched = self.agent("GET", self.AGENT_ID, f"v1/files/{file_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["id"], file_id)

        listed = self.agent("GET", self.AGENT_ID, "v1/files")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(file_id, [f["id"] for f in listed.json()["data"]])

        removed = self.agent("DELETE", self.AGENT_ID, f"v1/files/{file_id}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["deleted"])

        gone = self.agent("GET", self.AGENT_ID, f"v1/files/{file_id}")
        self.assertEqual(gone.status_code, 404, gone.text)

    def test_other_user_cannot_read_file(self):
        file_id = self.upload()["id"]
        response = self.agent("GET", self.AGENT_ID, f"v1/files/{file_id}",
                              headers=env.OTHER_AUTH)
        self.assertEqual(response.status_code, 404, response.text)

    def test_question_with_file_chat_form(self):
        file_id = self.upload()["id"]
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "про что документ"},
                {"type": "file", "file": {"file_id": file_id}},
            ]}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertValidChatCompletion(response.json())

    def test_question_with_file_responses_form(self):
        file_id = self.upload()["id"]
        response = self.responses({
            "model": self.AGENT_ID,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "про что документ"},
                {"type": "input_file", "file_id": file_id},
            ]}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertValidResponse(response.json())

    def test_question_without_file(self):
        """file_id не обязателен — агент отвечает и без документа."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": "от какого числа документ"}]})
        self.assertEqual(response.status_code, 200, response.text)

    def test_unknown_file_id(self):
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "про что документ"},
                {"type": "file", "file": {"file_id": f"file-{env.MISSING_UUID}"}},
            ]}],
        })
        self.assertOpenAIError(response, 404, "not_found_error")

    def test_inline_attachment_rejected_with_hint(self):
        """Картинку по url агент принять не может: документ должен пройти
        распознавание. Молчаливый ответ «документ не приложен» здесь хуже
        ошибки — мастер направил запрос сюда именно из-за вложения."""
        response = self.chat({
            "model": self.AGENT_ID,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "что на картинке"},
                {"type": "image_url", "image_url": {
                    "url": "https://example.com/x.png"}},
            ]}],
        })
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertIn("/v1/files", error["message"],
                      "в ошибке должна быть подсказка, как приложить документ")

    def test_inline_attachment_rejected_responses_form(self):
        response = self.responses({
            "model": self.AGENT_ID,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "что на картинке"},
                {"type": "input_image", "image_url": "https://example.com/x.png"},
            ]}],
        })
        self.assertOpenAIError(response, 400, "invalid_request_error")


class DocumentChatRoutingTests(MasterTestCase):
    """Вложение — более жёсткий признак, чем смысл текста: запрос с файлом
    уходит агенту с capability attachments независимо от семантики."""

    def test_auto_with_file_part_routes_to_attachment_agent(self):
        response = self.chat({
            "model": "auto",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "что такое закупка"},
                {"type": "file", "file": {"file_id": f"file-{env.MISSING_UUID}"}},
            ]}],
        })
        # Файл несуществующий, поэтому ответ — 404 от document_chat, а не от
        # роутера: важно, что запрос вообще доехал до агента с вложениями.
        self.assertIn(response.status_code, (200, 404), response.text)
        if response.status_code == 404:
            self.assertOpenAIError(response, 404, "not_found_error")

    def test_auto_with_image_url_routes_to_attachment_agent(self):
        """image_url — тоже вложение. Раньше мастер его не распознавал и
        отправлял такой запрос в семантический роутинг как обычный текст."""
        response = self.chat({
            "model": "auto",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "что такое закупка"},
                {"type": "image_url", "image_url": {
                    "url": "https://example.com/x.png"}},
            ]}],
        })
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertIn("/v1/files", error["message"],
                      "запрос с картинкой должен был доехать до document_chat")

    def test_only_attachment_agent_declares_capability(self):
        from registry import AGENTS

        with_attachments = {a.id for a in AGENTS.values()
                            if "attachments" in a.capabilities}
        self.assertEqual(with_attachments, {"document_chat"})


if __name__ == "__main__":
    unittest.main()
