"""tech_rag — RAG по технической документации (численные методы, аэродинамика)."""

from __future__ import annotations

import unittest

from tests.support import env
from tests.support.base import AgentContractTests, MasterTestCase


class TechRagContractTests(AgentContractTests, MasterTestCase):
    AGENT_ID = "tech_rag"
    HAS_SOURCES = True
    QUESTION = "что такое аэродинамика"


class TechRagSpecificTests(MasterTestCase):
    AGENT_ID = "tech_rag"

    @env.live_only
    def test_auto_routes_technical_question_here(self):
        for text in ("что такое метод конечных разностей",
                     "для чего нужно уравнение Навье-Стокса"):
            with self.subTest(text=text):
                response = self.chat({
                    "model": "auto",
                    "messages": [{"role": "user", "content": text}]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["model"], "tech_rag")

    @env.live_only
    def test_used_sources_reported(self):
        completion = self.chat({
            "model": "tech_rag",
            "messages": [{"role": "user", "content":
                          "расскажи про метод Рунге-Кутта"}]})
        self.assertEqual(completion.status_code, 200, completion.text)

        sources = self.agent(
            "GET", "tech_rag",
            f"v1/chat/completions/{completion.json()['id']}/sources")
        self.assertEqual(sources.status_code, 200, sources.text)
        self.assertTrue(sources.json()["retrieved"])


if __name__ == "__main__":
    unittest.main()
