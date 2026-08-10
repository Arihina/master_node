"""slave_chat — общий чат-агент, в реестре под id "chat".

Без RAG: /sources у него нет, и это проверяется явно — расхождение между
агентами должно быть заявленным, а не случайным.
"""

from __future__ import annotations

import unittest

from tests.support import env
from tests.support.base import AgentContractTests, MasterTestCase


class SlaveChatContractTests(AgentContractTests, MasterTestCase):
    AGENT_ID = "chat"
    HAS_SOURCES = False
    QUESTION = "привет"


class SlaveChatSpecificTests(MasterTestCase):
    AGENT_ID = "chat"

    @env.live_only
    def test_auto_routes_general_question_here(self):
        for text in ("расскажи анекдот", "помоги написать письмо"):
            with self.subTest(text=text):
                response = self.chat({
                    "model": "auto",
                    "messages": [{"role": "user", "content": text}]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["model"], "chat")

    def test_is_the_fallback_agent(self):
        """chat — запасной вариант роутера: он обязан быть включён и
        поддерживать обе формы, иначе fallback ведёт в никуда."""
        from registry import AGENTS

        agent = AGENTS["chat"]
        self.assertTrue(agent.enabled)
        self.assertTrue(agent.routable)
        self.assertIn("chat_completions", agent.contract_forms)
        self.assertIn("responses", agent.contract_forms)


if __name__ == "__main__":
    unittest.main()
