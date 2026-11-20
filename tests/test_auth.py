"""Аутентификация и роли мастера.

dev_header-режим — то, в чём работает STUB-набор тестов (доверенный
X-User-Id + X-User-Roles). keycloak-режим проверяется без живого Keycloak:
_jwt_principal/маршруты через подменённый _verify_token, а сама проверка
подписи — юнит-тестом на настоящем RSA-ключе.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from contextlib import ExitStack
from unittest import mock

import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

import auth
from config import settings
from tests.support import env
from tests.support.base import MasterTestCase

USER_CLAIMS = {
    "sub": env.USER_ID,
    "realm_access": {"roles": ["platform-user"]},
}
ADMIN_CLAIMS = {
    "sub": env.USER_ID,
    "realm_access": {"roles": ["platform-admin"]},
}


def _make_request(headers: dict) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
    }
    return Request(scope)


class DevHeaderAuthTests(MasterTestCase):
    """Роли в dev_header-режиме приходят заголовком X-User-Roles."""

    def test_admin_role_grants_admin_api(self):
        response = self.client.get("/admin/agents", headers=env.ADMIN_AUTH)
        self.assertEqual(response.status_code, 200, response.text)

    def test_user_without_admin_role_denied(self):
        response = self.client.get("/admin/agents", headers=env.AUTH)
        self.assertOpenAIError(response, 403, "permission_error")

    def test_route_requires_auth(self):
        response = self.client.post("/route", json={"message": "x"})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_route_available_to_user(self):
        """Resolve-ручка доступна обычному пользователю (нужна фронту для
        создания чата до первого сообщения), админ не требуется."""
        response = self.client.post("/route", json={"message": "x"},
                                    headers=env.AUTH)
        self.assertEqual(response.status_code, 200, response.text)


class KeycloakAuthTests(MasterTestCase):
    """keycloak-режим: валидация JWT (подпись подменяется в этих тестах)."""

    def test_valid_token_routes(self):
        with mock.patch.object(settings, "auth_mode", "keycloak"), \
                mock.patch("auth._verify_token",
                           new=mock.AsyncMock(return_value=USER_CLAIMS)):
            response = self.chat(
                {"model": "epoz",
                 "messages": [{"role": "user", "content": "привет"}]},
                headers={"Authorization": "Bearer valid"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_admin_role_grants_admin_api(self):
        with mock.patch.object(settings, "auth_mode", "keycloak"), \
                mock.patch("auth._verify_token",
                           new=mock.AsyncMock(return_value=ADMIN_CLAIMS)):
            response = self.client.get(
                "/admin/agents", headers={"Authorization": "Bearer valid"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_user_role_denied_on_admin_api(self):
        with mock.patch.object(settings, "auth_mode", "keycloak"), \
                mock.patch("auth._verify_token",
                           new=mock.AsyncMock(return_value=USER_CLAIMS)):
            response = self.client.get(
                "/admin/agents", headers={"Authorization": "Bearer valid"})
        self.assertOpenAIError(response, 403, "permission_error")

    def test_missing_bearer_rejected(self):
        with mock.patch.object(settings, "auth_mode", "keycloak"):
            response = self.chat(
                {"model": "epoz",
                 "messages": [{"role": "user", "content": "привет"}]},
                headers=env.AUTH)
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_non_uuid_sub_rejected(self):
        claims = {"sub": "not-a-uuid", "realm_access": {"roles": []}}
        with mock.patch.object(settings, "auth_mode", "keycloak"), \
                mock.patch("auth._verify_token",
                           new=mock.AsyncMock(return_value=claims)):
            response = self.chat(
                {"model": "epoz",
                 "messages": [{"role": "user", "content": "привет"}]},
                headers={"Authorization": "Bearer valid"})
        self.assertOpenAIError(response, 401, "authentication_error")

    def test_client_x_user_id_ignored(self):
        """В keycloak-режиме user_id берётся из sub, а не из X-User-Id."""
        request = _make_request({
            "Authorization": "Bearer valid",
            "X-User-Id": env.OTHER_USER_ID,
        })
        with mock.patch.object(settings, "auth_mode", "keycloak"), \
                mock.patch("auth._verify_token",
                           new=mock.AsyncMock(return_value=USER_CLAIMS)):
            principal = asyncio.run(auth.get_principal(request))
        self.assertEqual(principal.user_id, env.USER_ID)
        self.assertNotEqual(principal.user_id, env.OTHER_USER_ID)


class JwtVerificationTests(unittest.TestCase):
    """Проверка подписи на настоящем RSA-ключе, без сети."""

    def setUp(self):
        self.private = rsa.generate_private_key(public_exponent=65537,
                                                key_size=2048)
        self.public = self.private.public_key()

    def _token(self, **overrides) -> str:
        claims = {
            "iss": "http://iss/realms/tidi",
            "aud": "master-node",
            "sub": env.USER_ID,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides)
        return pyjwt.encode(claims, self.private, algorithm="RS256",
                            headers={"kid": "k1"})

    def _patched(self):
        return (
            mock.patch.object(settings, "keycloak_jwks_url", "http://x/certs"),
            mock.patch.object(settings, "keycloak_issuer",
                              "http://iss/realms/tidi"),
            mock.patch.object(settings, "keycloak_audience", "master-node"),
            mock.patch.object(settings, "keycloak_algorithms", ["RS256"]),
            mock.patch.object(settings, "keycloak_leeway", 0),
            mock.patch.object(auth._jwks, "get_signing_key",
                              new=mock.AsyncMock(return_value=self.public)),
        )

    def _verify(self, token: str) -> dict:
        with ExitStack() as stack:
            for cm in self._patched():
                stack.enter_context(cm)
            return asyncio.run(auth._verify_token(token))

    def test_valid_token(self):
        claims = self._verify(self._token())
        self.assertEqual(claims["sub"], env.USER_ID)

    def test_expired_token_rejected(self):
        token = self._token(iat=int(time.time()) - 600,
                            exp=int(time.time()) - 10)
        with self.assertRaises(HTTPException) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_audience_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self._verify(self._token(aud="other-client"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_issuer_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self._verify(self._token(iss="http://evil/realms/tidi"))
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
