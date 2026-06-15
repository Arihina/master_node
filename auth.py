from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException


async def get_user_id(x_user_id: str = Header(...)) -> str:
    try:
        UUID(x_user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            401, "X-User-Id отсутствует или не является валидным UUID")
    return x_user_id
