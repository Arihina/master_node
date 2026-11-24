"""Внутренняя аутентификация master -> агент.

Проверяется то, за что отвечает master: загрузка per-agent токенов,
fail-closed старт, проброс X-Internal-Token/X-Request-Id, корреляция логов
и отсутствие секретов в API/логах/repr.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

import config
from adapters.contract_http import ContractHTTPAdapter
from adapters.ocr import OCRAdapter
from config import Settings, settings
from internal_auth import (
    INTERNAL_TOKEN_HEADER, MIN_PROD_TOKEN_LENGTH, REQUEST_ID_HEADER,
    RequestContextMiddleware, _safe_log_value, agent_internal_token,
    current_request_id, normalize_request_id, reset_request_id,
    set_request_id, validate_startup,
)
from registry import AgentInfo
from tests.support import env
from tests.support.base import MasterTestCase

TOKEN = "t" * MIN_PROD_TOKEN_LENGTH


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "ollama_model": "test-model",
        "embedd_model": "test-embedding",
    }
    values.update(overrides)
    return Settings(**values)


def make_agent(agent_id: str, **overrides) -> AgentInfo:
    values = {
        "id": agent_id,
        "name": agent_id,
        "url": f"http://{agent_id}",
        "description": "тестовый агент",
    }
    values.update(overrides)
    return AgentInfo(**values)


class SettingsTokenTests(unittest.TestCase):
    """Загрузка и валидация AGENT_INTERNAL_TOKENS / ..._FILE."""

    def test_defaults(self):
        s = make_settings()
        self.assertEqual(s.env, "dev")
        self.assertEqual(s.agent_internal_tokens, {})

    def test_env_aliases(self):
        self.assertEqual(make_settings(env="PRODUCTION").env, "prod")
        self.assertEqual(make_settings(env="local").env, "dev")
        with self.assertRaises(ValidationError):
            make_settings(env="staging")

    def test_tokens_from_json_string(self):
        s = make_settings(agent_internal_tokens=json.dumps({"epoz": TOKEN}))
        self.assertEqual(s.agent_internal_tokens, {"epoz": TOKEN})

    def test_bad_agent_id_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(agent_internal_tokens={"Epöz": TOKEN})

    def test_empty_token_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(agent_internal_tokens={"epoz": "   "})

    def test_token_with_space_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(agent_internal_tokens={"epoz": "two words"})

    def test_token_map_not_object_rejected(self):
        with self.assertRaises(ValidationError):
            make_settings(agent_internal_tokens=["epoz", TOKEN])

    def test_file_loaded_and_overrides_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            path.write_text(json.dumps({
                "epoz": "f" * 32,
                "chat": "c" * 32,
            }), encoding="utf-8")

            s = make_settings(
                agent_internal_tokens={"epoz": "e" * 32},
                agent_internal_tokens_file=str(path),
            )

        self.assertEqual(s.agent_internal_tokens["epoz"], "f" * 32,
                         "значение из secret-файла важнее env")
        self.assertEqual(s.agent_internal_tokens["chat"], "c" * 32)

    def test_relative_file_resolved_from_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            path.write_text(json.dumps({"epoz": TOKEN}), encoding="utf-8")
            with mock.patch.object(config, "_CONFIG_DIR", Path(tmp)):
                s = make_settings(agent_internal_tokens_file="tokens.json")
        self.assertEqual(s.agent_internal_tokens, {"epoz": TOKEN})

    def test_missing_file_fails_startup(self):
        with self.assertRaises(ValidationError) as cm:
            make_settings(agent_internal_tokens_file="/no/such/tokens.json")
        self.assertIn("AGENT_INTERNAL_TOKENS_FILE", str(cm.exception))

    def test_invalid_json_file_fails_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokens.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValidationError) as cm:
                make_settings(agent_internal_tokens_file=str(path))
        self.assertIn("некорректный JSON", str(cm.exception))

    def test_repr_does_not_leak_tokens(self):
        s = make_settings(agent_internal_tokens={"epoz": TOKEN})
        self.assertNotIn(TOKEN, repr(s))
        self.assertNotIn(TOKEN, str(s))


class StartupValidationTests(unittest.TestCase):
    """validate_startup: fail-closed в prod, предупреждения в dev."""

    def setUp(self):
        self.agents = {
            "epoz": make_agent("epoz"),
            "chat": make_agent("chat"),
            "off": make_agent("off", enabled=False),
        }

    def test_prod_requires_token_for_every_enabled_agent(self):
        with mock.patch.object(settings, "env", "prod"), \
                mock.patch.object(settings, "agent_internal_tokens",
                                  {"epoz": TOKEN}):
            with self.assertRaises(RuntimeError) as cm:
                validate_startup(self.agents)
        self.assertIn("chat", str(cm.exception))
        self.assertNotIn("off", str(cm.exception),
                         "выключенный агент не требует токена")

    def test_prod_rejects_short_token(self):
        with mock.patch.object(settings, "env", "prod"), \
                mock.patch.object(settings, "agent_internal_tokens",
                                  {"epoz": TOKEN, "chat": "short"}):
            with self.assertRaises(RuntimeError) as cm:
                validate_startup(self.agents)
        self.assertIn(str(MIN_PROD_TOKEN_LENGTH), str(cm.exception))

    def test_prod_accepts_complete_config(self):
        with mock.patch.object(settings, "env", "prod"), \
                mock.patch.object(settings, "agent_internal_tokens",
                                  {"epoz": TOKEN, "chat": "c" * 32}):
            validate_startup(self.agents)  # не должно падать

    def test_unknown_agent_token_warns_but_does_not_block(self):
        with mock.patch.object(settings, "env", "prod"), \
                mock.patch.object(settings, "agent_internal_tokens",
                                  {"epoz": TOKEN, "chat": "c" * 32,
                                   "ghost": "g" * 32}):
            with self.assertLogs("master.internal_auth", level="WARNING") as cm:
                validate_startup(self.agents)
        self.assertIn("ghost", "\n".join(cm.output))

    def test_dev_missing_tokens_only_warns(self):
        with mock.patch.object(settings, "env", "dev"), \
                mock.patch.object(settings, "agent_internal_tokens", {}):
            with self.assertLogs("master.internal_auth", level="WARNING") as cm:
                validate_startup(self.agents)
        self.assertIn("epoz", "\n".join(cm.output))
        self.assertIn("chat", "\n".join(cm.output))

    def test_lifespan_refuses_prod_without_tokens(self):
        import main

        async def run_lifespan():
            async with main.lifespan(main.app):
                pass

        with mock.patch.object(settings, "env", "prod"), \
                mock.patch.object(settings, "agent_internal_tokens", {}):
            with self.assertRaises(RuntimeError):
                asyncio.run(run_lifespan())


class RequestIdUnitTests(unittest.IsolatedAsyncioTestCase):
    """Middleware отдельно от приложения: state, заголовок ответа, лог."""

    async def _call(self, incoming: str | None):
        seen: dict = {}
        sent: list[dict] = []

        async def app(scope, receive, send):
            seen["request_id"] = scope["state"]["request_id"]
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body",
                        "body": seen["request_id"].encode()})

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            sent.append(message)

        headers = []
        if incoming is not None:
            headers.append((REQUEST_ID_HEADER.lower().encode(),
                            incoming.encode()))

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "headers": headers,
            "state": {},
        }
        middleware = RequestContextMiddleware(app)
        with self.assertLogs("master.internal_auth", level="INFO") as logs:
            await middleware(scope, receive, send)

        response_headers = dict(sent[0]["headers"])
        return seen["request_id"], response_headers, sent, logs.output

    async def test_incoming_request_id_reused(self):
        request_id, headers, sent, logs = await self._call("client-req-1")
        self.assertEqual(request_id, "client-req-1")
        self.assertEqual(headers[REQUEST_ID_HEADER.lower().encode()],
                         b"client-req-1")
        self.assertEqual(sent[1]["body"], b"client-req-1")
        self.assertIn("request_id=client-req-1", "\n".join(logs))

    async def test_invalid_request_id_replaced(self):
        request_id, headers, _, _ = await self._call("bad id")
        self.assertNotEqual(request_id, "bad id")
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")

    async def test_missing_request_id_generated(self):
        request_id, headers, _, _ = await self._call(None)
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")
        self.assertEqual(headers[REQUEST_ID_HEADER.lower().encode()],
                         request_id.encode())

    def test_normalize_limits(self):
        self.assertEqual(normalize_request_id("a" * 128), "a" * 128)
        self.assertNotEqual(normalize_request_id("a" * 129), "a" * 129)

    def test_log_value_has_no_control_chars(self):
        """`%0a` в URL декодируется в path — в лог он не должен попасть."""
        self.assertEqual(_safe_log_value("/x\nINJECT"), "/x?INJECT")


class RequestIdHTTPTests(MasterTestCase):
    """Сквозь приложение: заголовок ответа и access-лог."""

    def test_request_id_echoed_to_client(self):
        response = self.client.get(
            "/v1/models", headers={REQUEST_ID_HEADER: "client-42"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get(REQUEST_ID_HEADER.lower()),
                         "client-42")

    def test_generated_request_id_is_valid(self):
        response = self.client.get("/v1/models")
        request_id = response.headers.get(REQUEST_ID_HEADER.lower())
        self.assertRegex(request_id or "", r"^[0-9a-f]{32}$")

    @env.stub_only
    def test_access_log_has_request_and_user_but_no_token(self):
        with mock.patch.object(settings, "agent_internal_tokens",
                               {"epoz": TOKEN}):
            with self.assertLogs("master.internal_auth", level="INFO") as cm:
                response = self.chat(
                    {"model": "epoz",
                     "messages": [{"role": "user", "content": "привет"}]},
                    headers={**env.AUTH, REQUEST_ID_HEADER: "log-req-1"})

        self.assertEqual(response.status_code, 200, response.text)
        logs = "\n".join(cm.output)
        self.assertIn("request_id=log-req-1", logs)
        self.assertIn(f"user_id={env.USER_ID}", logs)
        self.assertNotIn(TOKEN, logs,
                         "internal-токены не должны попадать в логи")

    @env.stub_only
    def test_admin_agents_does_not_expose_tokens(self):
        with mock.patch.object(settings, "agent_internal_tokens",
                               {"epoz": TOKEN}):
            response = self.client.get("/admin/agents", headers=env.ADMIN_AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(INTERNAL_TOKEN_HEADER.lower(), response.text.lower())


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}

    async def aiter_raw(self):
        yield b"{}"


class _FakeStreamCM:
    async def __aenter__(self):
        return _FakeResponse()

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """Минимальный срез httpx.AsyncClient, который нужен адаптеру."""

    def __init__(self, sink: dict):
        self._sink = sink

    def stream(self, method: str, url: str, **kw):
        self._sink["method"] = method
        self._sink["url"] = url
        self._sink["headers"] = dict(kw.get("headers", {}))
        return _FakeStreamCM()

    async def aclose(self):
        pass


class AdapterHeadersTests(unittest.IsolatedAsyncioTestCase):
    """Заголовки, которые master шлёт агенту."""

    async def _captured(self, agent_id: str = "probe", **proxy_kwargs):
        adapter = ContractHTTPAdapter(
            make_agent(agent_id))
        sink: dict = {}
        adapter._client = _FakeClient(sink)
        try:
            result = await adapter.proxy(
                "POST", "/v1/probe", "user-1", body=b"{}",
                content_type="application/json", **proxy_kwargs)
            async for _ in result.body:
                pass
        finally:
            await adapter.aclose()
        return sink["headers"]

    async def test_internal_token_sent_for_configured_agent(self):
        with mock.patch.object(settings, "agent_internal_tokens",
                               {"probe": TOKEN}):
            headers = await self._captured()
        self.assertEqual(headers.get(INTERNAL_TOKEN_HEADER), TOKEN)

    async def test_internal_token_absent_when_not_configured(self):
        with mock.patch.object(settings, "agent_internal_tokens", {}):
            headers = await self._captured()
        self.assertNotIn(INTERNAL_TOKEN_HEADER, headers)

    async def test_tokens_are_per_agent(self):
        with mock.patch.object(settings, "agent_internal_tokens",
                               {"probe": "p" * 32, "other": "o" * 32}):
            probe_headers = await self._captured("probe")
            other_headers = await self._captured("other")
        self.assertEqual(probe_headers[INTERNAL_TOKEN_HEADER], "p" * 32)
        self.assertEqual(other_headers[INTERNAL_TOKEN_HEADER], "o" * 32)

    async def test_explicit_request_id_forwarded(self):
        headers = await self._captured(request_id="explicit-req")
        self.assertEqual(headers[REQUEST_ID_HEADER], "explicit-req")

    async def test_request_id_from_context(self):
        token = set_request_id("ctx-req")
        try:
            headers = await self._captured()
        finally:
            reset_request_id(token)
        self.assertEqual(headers[REQUEST_ID_HEADER], "ctx-req")

    async def test_request_id_generated_when_missing(self):
        headers = await self._captured()
        self.assertRegex(headers[REQUEST_ID_HEADER], r"^[0-9a-f]{32}$")


class _FakeOCRResponse:
    status_code = 200

    def json(self):
        return {"text": "ok"}


class _FakeOCRClient:
    def __init__(self, sink: dict):
        self._sink = sink

    async def post(self, url, files=None, headers=None):
        self._sink["url"] = url
        self._sink["headers"] = dict(headers or {})
        return _FakeOCRResponse()

    async def aclose(self):
        pass


class OCRAdapterHeadersTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_ocr_sends_internal_token_and_request_id(self):
        adapter = OCRAdapter(make_agent("ocr", transport="ocr"))
        sink: dict = {}
        adapter._client = _FakeOCRClient(sink)
        adapter._url = "http://ocr"
        try:
            with mock.patch.object(settings, "agent_internal_tokens",
                                   {"ocr": TOKEN}):
                chunks = [chunk async for chunk in adapter.run_ocr(
                    "user-1", "f.txt", b"data", request_id="ocr-req")]
        finally:
            await adapter.aclose()

        self.assertTrue(chunks)
        self.assertEqual(sink["headers"].get(INTERNAL_TOKEN_HEADER), TOKEN)
        self.assertEqual(sink["headers"].get(REQUEST_ID_HEADER), "ocr-req")


class HelperTests(unittest.TestCase):
    def test_agent_internal_token_is_looked_up_per_agent(self):
        with mock.patch.object(settings, "agent_internal_tokens",
                               {"epoz": TOKEN}):
            self.assertEqual(agent_internal_token("epoz"), TOKEN)
            self.assertIsNone(agent_internal_token("chat"))

    def test_context_is_empty_by_default(self):
        self.assertIsNone(current_request_id())
