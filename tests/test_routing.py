"""Юнит-тесты выбора агента — без HTTP и без мастера.

Здесь проверяется сам `MasterRouter`: как он сужает кандидатов, когда зовёт
LLM-слой и как ведёт себя запасной вариант. Слои эмбеддингов и LLM
подменяются — их качество проверяется на живом стеке (`SemanticRoutingTests`),
а здесь важна логика вокруг них.

Отдельный модуль нужен потому, что в остальных тестах роутер подменён
заглушкой целиком: без этих проверок его собственный код остался бы
непокрытым.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from tests.support import env  # noqa: F401  — добавляет корень репозитория в sys.path
from tests.support import stubs

stubs.install_dependency_stubs()

from registry import AGENTS  # noqa: E402
from routing.router_service import MasterRouter, NoRoutableAgent  # noqa: E402

ALL_ROUTABLE = {a.id for a in AGENTS.values() if a.enabled and a.routable}


def run(coro):
    return asyncio.run(coro)


class RouterCandidateTests(unittest.TestCase):
    """`allowed` обязан сужать выбор ДО голосования, а не отбраковывать
    результат после: иначе запрос уходит агенту, который нужную форму не
    реализует."""

    def setUp(self):
        self.router = MasterRouter()

    def test_single_candidate_short_circuits(self):
        """Один кандидат — ни эмбеддингов, ни LLM звать не нужно."""
        with mock.patch("routing.embedding_router.route") as embedding:
            agent = run(self.router.route("любой текст", allowed={"epoz"}))

        self.assertEqual(agent, "epoz")
        embedding.assert_not_called()

    def test_embedding_direct_hit_is_used(self):
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "direct", "agent": "tech_rag"}):
            agent = run(self.router.route("что такое аэродинамика",
                                          allowed={"tech_rag", "chat"}))
        self.assertEqual(agent, "tech_rag")

    def test_embedding_sees_only_allowed_candidates(self):
        """Множество кандидатов должно доехать до слоя эмбеддингов."""
        allowed = {"epoz", "chat"}
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "direct", "agent": "epoz"}) as embedding:
            run(self.router.route("что такое закупка", allowed=allowed))

        _, passed = embedding.call_args[0]
        self.assertEqual(passed, allowed)

    def test_ambiguous_goes_to_llm_with_shortlist(self):
        shortlist = {"epoz", "tech_rag"}
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "ambiguous", "candidates": shortlist}), \
                mock.patch("routing.llm_router.route", return_value="tech_rag") as llm:
            agent = run(self.router.route("пограничный вопрос",
                                          allowed={"epoz", "tech_rag", "chat"}))

        self.assertEqual(agent, "tech_rag")
        self.assertEqual(llm.call_args[0][1], shortlist)

    def test_llm_answer_outside_candidates_is_discarded(self):
        """LLM может назвать агента вне разрешённого множества — доверять
        такому ответу нельзя."""
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "unclear"}), \
                mock.patch("routing.llm_router.route", return_value="document_chat"):
            agent = run(self.router.route(
                "текст", allowed={"epoz", "tech_rag"}))

        self.assertIn(agent, {"epoz", "tech_rag"})

    def test_llm_failure_falls_back(self):
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "unclear"}), \
                mock.patch("routing.llm_router.route", side_effect=RuntimeError("ollama лёг")):
            agent = run(self.router.route("текст", allowed={"epoz", "chat"}))

        self.assertEqual(
            agent, "chat", "при отказе LLM должен сработать fallback")

    def test_empty_candidate_set_raises(self):
        with self.assertRaises(NoRoutableAgent):
            run(self.router.route("текст", allowed=set()))

    def test_non_routable_agents_never_selected(self):
        """Агент с routable=False не участвует в выборе, даже если его
        явно разрешили сверху."""
        with mock.patch("routing.embedding_router.route",
                        return_value={"decision": "direct", "agent": "epoz"}):
            agent = run(self.router.route("текст", allowed={"epoz", "ocr"}))
        self.assertNotEqual(agent, "ocr")

        with self.assertRaises(NoRoutableAgent):
            run(self.router.route("текст", allowed={"ocr"}))


class RouterFallbackTests(unittest.TestCase):
    """Запасной вариант обязан сработать всегда — но не имеет права вывести
    за пределы разрешённого множества."""

    def setUp(self):
        self.router = MasterRouter()

    def test_configured_fallback_is_preferred(self):
        """Проверка должна отличать значение из настроек от «первого по
        алфавиту»: у нынешнего набора агентов они совпадают ("chat"), поэтому
        на время теста настройка подменяется — иначе тест прошёл бы и при
        полностью проигнорированном `settings.fallback_agent`."""
        from config import settings

        with mock.patch.object(settings, "fallback_agent", "tech_rag"):
            self.assertEqual(self.router._fallback(ALL_ROUTABLE), "tech_rag")

        self.assertEqual(self.router._fallback(ALL_ROUTABLE),
                         settings.fallback_agent)

    def test_fallback_stays_inside_allowed(self):
        from config import settings

        narrowed = {"epoz", "tech_rag"}
        self.assertNotIn(settings.fallback_agent, narrowed)

        chosen = self.router._fallback(narrowed)
        self.assertIn(chosen, narrowed,
                      "fallback не должен выводить за пределы кандидатов")

    def test_fallback_is_deterministic(self):
        narrowed = {"tech_rag", "epoz", "document_chat"}
        self.assertEqual(self.router._fallback(narrowed),
                         self.router._fallback(narrowed))


class RegistryConsistencyTests(unittest.TestCase):
    """Реестр — источник правды для роутинга; несогласованность в нём
    проявится не ошибкой, а тихо неправильной маршрутизацией."""

    def test_fallback_agent_exists_and_is_routable(self):
        from config import settings

        self.assertIn(settings.fallback_agent, AGENTS)
        agent = AGENTS[settings.fallback_agent]
        self.assertTrue(agent.enabled)
        self.assertTrue(agent.routable)
        self.assertTrue(agent.contract_forms)

    def test_routable_agents_declare_a_form(self):
        for agent in AGENTS.values():
            if agent.enabled and agent.routable:
                with self.subTest(agent=agent.id):
                    self.assertTrue(
                        agent.contract_forms,
                        f"{agent.id} участвует в роутинге, но не реализует "
                        "ни одной формы — auto может выбрать его и упереться в 400")

    def test_every_agent_has_url_and_description(self):
        for agent in AGENTS.values():
            with self.subTest(agent=agent.id):
                self.assertTrue(agent.url)
                self.assertTrue(agent.description.strip(),
                                "описание используется для семантического роутинга")

    def test_contract_forms_are_known(self):
        known = {"chat_completions", "responses"}
        for agent in AGENTS.values():
            with self.subTest(agent=agent.id):
                self.assertTrue(
                    agent.contract_forms <= known,
                    f"неизвестная форма у {agent.id}: {agent.contract_forms - known}")


if __name__ == "__main__":
    unittest.main()
