"""ocr — файл-в / текст-из, намеренно вне OpenAI-контракта.

У OCR нет диалога, поэтому он не приведён к формам Chat Completions и
Responses: у него отдельная capability-ручка. Тесты фиксируют это как
осознанное решение, а не недоделку — и проверяют, что мастер отсекает
попытки обратиться к нему как к диалоговому агенту.
"""

from __future__ import annotations

import unittest

from tests.support import env
from tests.support.base import MasterTestCase

SAMPLE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class OcrCapabilityTests(MasterTestCase):
    AGENT_ID = "ocr"

    def test_ocr_streams_text(self):
        response = self.client.post(
            "/agents/ocr/ocr", headers=env.AUTH,
            files={"file": ("scan.png", SAMPLE_PNG, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream",
                      response.headers.get("content-type", ""))

        lines = self.data_lines(response)
        self.assertTrue(lines, "OCR не отдал ни одного события")
        self.assertEqual(lines[-1], "[DONE]")

        import json
        first = json.loads(lines[0])
        self.assertIn("token", first)

    def test_ocr_requires_credentials(self):
        response = self.client.post(
            "/agents/ocr/ocr",
            files={"file": ("scan.png", SAMPLE_PNG, "image/png")})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_capability_absent_on_other_agents(self):
        """/ocr есть только у ocr — у остальных 404, а не тихий проброс."""
        for agent_id in ("epoz", "chat", "tech_rag", "document_chat"):
            with self.subTest(agent=agent_id):
                response = self.client.post(
                    f"/agents/{agent_id}/ocr", headers=env.AUTH,
                    files={"file": ("scan.png", SAMPLE_PNG, "image/png")})
                self.assertOpenAIError(response, 404, "not_found_error")


class OcrIsOutsideOpenAIContractTests(MasterTestCase):
    """Обе формы обязаны отсечь ocr на валидации мастера — до похода в агента."""

    def test_rejected_in_chat_completions(self):
        response = self.chat({
            "model": "ocr",
            "messages": [{"role": "user", "content": "распознай текст"}]})
        self.assertOpenAIError(response, 400, "invalid_request_error")

    def test_rejected_in_responses(self):
        response = self.responses({"model": "ocr", "input": "распознай текст"})
        error = self.assertOpenAIError(response, 400, "invalid_request_error")
        self.assertIn("Responses", error["message"])

    def test_not_offered_as_a_model(self):
        """ocr не должен предлагаться как модель: он не отвечает ни в одной
        форме, и клиент, выбравший его из /v1/models, упрётся в 400."""
        models = self.client.get("/v1/models").json()["data"]
        self.assertNotIn(
            "ocr", {item["id"] for item in models},
            "ocr не реализует ни одной формы OpenAI и не должен быть в списке моделей")

    def test_excluded_from_auto_routing(self):
        from registry import AGENTS

        agent = AGENTS["ocr"]
        self.assertFalse(agent.routable,
                         "ocr не участвует в семантическом роутинге")
        self.assertEqual(agent.contract_forms, set(),
                         "ocr не заявляет ни одной формы OpenAI")

    def test_proxy_path_returns_not_found(self):
        """Даже сквозным путём ocr отвечает 404 на contract-пути — честно, а
        не выдумывая ответ."""
        response = self.agent("GET", "ocr", "v1/platform/conversations")
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
