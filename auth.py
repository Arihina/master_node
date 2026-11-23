from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request

from config import settings
from internal_auth import set_current_user

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Проверенный пользователь мастера: user_id из sub + роли из JWT.

    Роли — сырые значения из realm_access.roles и
    resource_access.<client>.roles. Проверка «админ/не админ» — через
    settings.admin_role, плюс литерал "admin" для dev_header-режима.
    """

    user_id: str
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & {settings.admin_role, "admin"})


def _from_authorization(header: str | None) -> str | None:
    """Authorization: Bearer <token> -> token. Не-Bearer игнорируется."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _parse_roles(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(r.strip() for r in raw.split(",") if r.strip())


def _require_uuid(raw: str | None, message: str) -> str:
    if not raw:
        raise HTTPException(401, message)
    try:
        UUID(raw)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(401, message)
    return raw


def _dev_principal(request: Request) -> Principal:
    """Режим dev_header: доверенный заголовок для локальной разработки/тестов.

    Сознательно небезопасен и используется только в STUB-тестах и на
    loopback-хосте. Роли приходят в X-User-Roles через запятую."""
    raw = request.headers.get("X-User-Id") or _from_authorization(
        request.headers.get("Authorization"))
    user_id = _require_uuid(
        raw, "Не передан идентификатор пользователя "
             "(X-User-Id или Authorization: Bearer <uuid>)")
    roles = _parse_roles(request.headers.get("X-User-Roles"))
    return Principal(user_id=user_id, roles=roles)


class _JwksCache:
    """Кэш ключей JWKS Keycloak. По kid, с перезапросом при ротации."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=settings.keycloak_http_timeout,
            verify=settings.keycloak_verify_tls,
        )
        self._jwks: jwt.PyJWKSet | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _stale(self) -> bool:
        return time.time() - self._fetched_at > settings.keycloak_jwks_cache_ttl

    async def get_signing_key(self, kid: str | None):
        async with self._lock:
            if self._jwks is None or self._stale():
                await self._refresh()
            try:
                return self._pick(kid).key
            except KeyError:
                # Ротация: новый kid ещё не в кэше — обновляем один раз.
                await self._refresh()
                try:
                    return self._pick(kid).key
                except KeyError:
                    raise HTTPException(401, "Неизвестный ключ подписи токена")

    def _pick(self, kid: str | None) -> jwt.PyJWK:
        """PyJWKSet ищет ключ по kid через __getitem__ (KeyError, если нет)."""
        if kid:
            return self._jwks[kid]
        # Некоторые JWKS не отдают kid; если ключ ровно один — берём его.
        if len(self._jwks.keys) == 1:
            return self._jwks.keys[0]
        raise KeyError(kid)

    async def _refresh(self) -> None:
        if not settings.keycloak_jwks_url:
            raise HTTPException(503, "Не настроен Keycloak JWKS URL")
        try:
            resp = await self._client.get(settings.keycloak_jwks_url)
        except httpx.TransportError as e:
            raise HTTPException(503, f"Keycloak JWKS недоступен: {e}")
        if resp.status_code != 200:
            raise HTTPException(503, f"Keycloak JWKS вернул {resp.status_code}")
        try:
            self._jwks = jwt.PyJWKSet.from_dict(resp.json())
        except (jwt.PyJWTError, ValueError) as e:
            raise HTTPException(503, f"Некорректный JWKS: {e}")
        self._fetched_at = time.time()

    async def aclose(self) -> None:
        await self._client.aclose()


_jwks = _JwksCache()


def _extract_roles(claims: dict) -> frozenset[str]:
    roles: set[str] = set()
    realm = claims.get("realm_access") or {}
    roles.update(realm.get("roles") or [])
    resource_access = claims.get("resource_access") or {}
    client = resource_access.get(settings.keycloak_client_id) or {}
    roles.update(client.get("roles") or [])
    return frozenset(roles)


async def _verify_token(token: str) -> dict:
    """Проверка подписи и обязательных claims. Возвращает claims или 401/503."""
    if not settings.keycloak_jwks_url:
        raise HTTPException(503, "Не настроен Keycloak JWKS URL")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Невалидный токен")

    alg = header.get("alg")
    if alg not in settings.keycloak_algorithms:
        raise HTTPException(401, f"Недопустимый алгоритм подписи: {alg}")

    key = await _jwks.get_signing_key(header.get("kid"))
    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=settings.keycloak_algorithms,
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=settings.keycloak_leeway,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Токен просрочен")
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Невалидный токен: {e}")


async def _jwt_principal(request: Request) -> Principal:
    token = _from_authorization(request.headers.get("Authorization"))
    if not token:
        raise HTTPException(401, "Не передан Authorization: Bearer <JWT>")

    claims = await _verify_token(token)
    user_id = _require_uuid(claims.get("sub"), "sub токена не является UUID")
    return Principal(user_id=user_id, roles=_extract_roles(claims))


async def get_principal(request: Request) -> Principal:
    if settings.auth_mode == "keycloak":
        principal = await _jwt_principal(request)
    elif settings.auth_mode == "dev_header":
        principal = _dev_principal(request)
    else:
        raise HTTPException(500, f"Неизвестный auth_mode: {settings.auth_mode!r}")

    set_current_user(principal.user_id)
    return principal


async def get_user_id(principal: Principal = Depends(get_principal)) -> str:
    """Зависимость для пользовательских ручек: возвращает проверенный user_id."""
    return principal.user_id


async def require_admin(principal: Principal = Depends(get_principal)) -> None:
    """Зависимость для /admin и служебных ручек. 403 без роли админа."""
    if not principal.is_admin:
        raise HTTPException(403, "Требуются права администратора")


async def close_auth() -> None:
    await _jwks.aclose()
