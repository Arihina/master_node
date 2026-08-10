"""epoz — RAG по Единому положению о закупках Ростеха."""

from __future__ import annotations

import unittest

from tests.support import env
from tests.support.base import AgentContractTests, MasterTestCase


class EpozContractTests(AgentContractTests, MasterTestCase):
    AGENT_ID = "epoz"
    HAS_SOURCES = True
    QUESTION = "что такое закупка"


class EpozSpecificTests(MasterTestCase):
    """То, что отличает epoz от остальных агентов."""

    AGENT_ID = "epoz"

    @env.live_only
    def test_auto_routes_procurement_question_here(self):
        response = self.chat({
            "model": "auto",
            "messages": [{"role": "user", "content":
                          "какой способ закупки необходимо выбрать"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], "epoz")

    @env.live_only
    def test_answer_is_not_empty(self):
        """На живом стеке ответ должен быть содержательным, а не пустым."""
        response = self.chat({
            "model": "epoz",
            "messages": [{"role": "user", "content":
                          "что такое меры ограничительного характера"}]})
        self.assertEqual(response.status_code, 200, response.text)
        content = response.json()["choices"][0]["message"]["content"]
        self.assertTrue(content.strip(), "агент вернул пустой ответ")

    @env.live_only
    def test_used_sources_reported(self):
        """RAG-специфика: по профильному вопросу должны найтись источники."""
        completion = self.chat({
            "model": "epoz",
            "messages": [{"role": "user", "content":
                          "каков порядок проведения конкурса"}]})
        self.assertEqual(completion.status_code, 200, completion.text)

        sources = self.agent(
            "GET", "epoz",
            f"v1/chat/completions/{completion.json()['id']}/sources")
        self.assertEqual(sources.status_code, 200, sources.text)
        self.assertTrue(sources.json()["retrieved"],
                        "по профильному вопросу пул retrieved не должен быть пуст")


if __name__ == "__main__":
    unittest.main()
