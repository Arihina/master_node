from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException


def _from_authorization(header: str | None) -> str | None:
    """OpenAI-совместимый клиент шлёт креды в Authorization: Bearer <...>.
    Принимаем UUID и оттуда, чтобы SDK подключался сменой base_url без
    прокидывания кастомных заголовков. X-User-Id имеет приоритет."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


async def get_user_id(
    x_user_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    raw = x_user_id or _from_authorization(authorization)

    if not raw:
        raise HTTPException(
            401, "Не передан идентификатор пользователя "
                 "(X-User-Id или Authorization: Bearer <uuid>)")

    try:
        UUID(raw)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            401, "Идентификатор пользователя не является валидным UUID")

    return raw


async def require_admin(
    authorization: str | None = Header(default=None),
) -> None:
    """ЗАГЛУШКА — пускает всех. Точка расширения под Keycloak: здесь будет
    разбор JWT, валидация подписи по JWKS и проверка админской роли. Ручки
    /admin уже висят на этой зависимости, поэтому включение защиты не потребует
    правок API — только тела этой функции."""
    return None
