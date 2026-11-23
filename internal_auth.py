"""Внутренняя аутентификация master -> агент и контекст запроса.

Это НЕ пользовательская аутентификация: здесь master
доказывает агенту, что запрос пришёл именно от него, и передаёт
`X-Request-Id` для сквозной корреляции логов.

Почему отдельный модуль, а не настройки в `auth.py`:

* `auth.py` отвечает за JWT Keycloak и роли пользователя;
* здесь — service-identity master и request-scoped контекст;
* токены агентов не должны даже импортироваться в код, который проверяет
  пользователя, — меньше шансов залогировать или отдать их наружу.

Секреты берутся только из `settings.agent_internal_tokens` (env или
secret-файл). В реестре агентов (`agents.yaml`) их нет и быть не должно.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid
from typing import Any, Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import settings

logger = logging.getLogger("master.internal_auth")

# Заголовок, которым master доказывает агенту своё авторство.
INTERNAL_TOKEN_HEADER = "X-Internal-Token"
# Заголовок корреляции. Не является аутентификацией.
REQUEST_ID_HEADER = "X-Request-Id"

# Минимальная длина токена в prod. 
MIN_PROD_TOKEN_LENGTH = 32

# Значение X-Request-Id принимается от nginx/клиента только в этом виде.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "master_request_id", default=None)
_user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "master_user_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def normalize_request_id(value: str | None) -> str:
    """Принять валидный X-Request-Id или сгенерировать новый.

    Некорректный id не повод отдавать 400: это диагностический заголовок,
    а не часть аутентификации. Но и пропускать его в логи как есть нельзя.
    """
    if value and _REQUEST_ID_RE.match(value):
        return value
    return new_request_id()


def current_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id_var.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)


def set_current_user(user_id: str | None) -> None:
    """Вызывается после проверки пользователя (auth.get_principal).

    Нужен только access-логу: middleware не должна угадывать user_id по
    заголовкам, которые ещё не проверены.
    """
    _user_id_var.set(user_id)


def current_user_id() -> str | None:
    return _user_id_var.get()


def agent_internal_token(agent_id: str) -> str | None:
    """Per-agent токен из настроек. None — master не шлёт заголовок.

    В prod отсутствие токена для включённого агента несовместимо со стартом
    (см. validate_startup). В dev None допустим: агенты могут ещё не
    проверять заголовок.
    """
    return settings.agent_internal_tokens.get(agent_id)


def _safe_log_value(value: object, limit: int = 512) -> str:
    """Убрать из значения для лога управляющие символы.

    `scope["path"]` в ASGI уже URL-декодирован, поэтому `%0a` в URL мог бы
    превратиться в перевод строки и подделать следующую запись лога.
    """
    text = str(value)[:limit]
    return "".join(ch if ch.isprintable() else "?" for ch in text)


class RequestContextMiddleware:
    """Чистый ASGI-middleware: request_id + access-лог.

    Реализован без BaseHTTPMiddleware сознательно: тот оборачивает ответ в
    ещё одну задачу и исторически мешает потоковым SSE-ответам. Здесь мы
    только читаем заголовок, подменяем `http.response.start` и логируем —
    тело не буферизуется и не трогается.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = normalize_request_id(incoming)

        # Request.state читает именно scope["state"]; middleware выполняется
        # до Router, поэтому словарь создаём сами.
        scope.setdefault("state", {})["request_id"] = request_id

        ctx_token = set_request_id(request_id)
        # Пользователь появится только после auth-зависимости; до неё — None.
        set_current_user(None)

        status = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "request_id=%s method=%s path=%s status=%d user_id=%s "
                "duration_ms=%.1f",
                request_id,
                _safe_log_value(scope.get("method", "")),
                _safe_log_value(scope.get("path", "")),
                status,
                current_user_id() or "-",
                duration_ms,
            )
            reset_request_id(ctx_token)


# Транспорты, которые реально ходят в агента по HTTP. Для них в prod
# обязателен per-agent токен.
_HTTP_TRANSPORTS = frozenset({"contract", "ocr", "external"})


def validate_startup(agents: Mapping[str, Any] | None = None) -> None:
    """Fail-closed проверка конфигурации внутренней аутентификации.

    Вызывается из lifespan ДО старта приёма запросов:

    * `ENV=prod`: у каждого включённого агента с HTTP-транспортом должен быть
      токен длиной не меньше MIN_PROD_TOKEN_LENGTH, иначе RuntimeError;
    * `ENV=dev`: отсутствие токенов — предупреждение, чтобы можно было
      запускать мастер локально до того, как агенты начали проверку;
    * токены для неизвестных/удалённых агентов — предупреждение, не ошибка:
      они ничего не открывают, но сигнализируют о рассинхроне конфига.
    """
    if agents is None:
        from registry import AGENTS as agents  # локальный импорт: нет цикла

    tokens = settings.agent_internal_tokens
    prod = settings.env == "prod"

    missing: list[str] = []
    too_short: list[str] = []
    for agent in agents.values():
        if not agent.enabled or agent.transport not in _HTTP_TRANSPORTS:
            continue
        token = tokens.get(agent.id)
        if not token:
            missing.append(agent.id)
        elif prod and len(token) < MIN_PROD_TOKEN_LENGTH:
            too_short.append(agent.id)

    unknown = sorted(set(tokens) - set(agents))
    if unknown:
        logger.warning(
            "Токены заданы для агентов, которых нет в реестре: %s",
            ", ".join(unknown))

    if prod:
        problems: list[str] = []
        if missing:
            problems.append(
                "нет токена для включённых агентов: " + ", ".join(missing))
        if too_short:
            problems.append(
                f"токен короче {MIN_PROD_TOKEN_LENGTH} символов: "
                + ", ".join(too_short))
        if problems:
            raise RuntimeError(
                "Внутренняя аутентификация master -> агент не настроена "
                f"(ENV=prod): {'; '.join(problems)}. Задайте "
                "AGENT_INTERNAL_TOKENS или AGENT_INTERNAL_TOKENS_FILE.")
        logger.info(
            "внутренняя аутентификация: токены заданы для %d агентов",
            len(tokens))
        return

    if missing:
        logger.warning(
            "ENV=dev: X-Internal-Token не будет отправлен агентам без "
            "токена: %s. В prod мастер с такой конфигурацией не стартует.",
            ", ".join(missing))
