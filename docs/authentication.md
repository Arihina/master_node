# Аутентификация и роли мастера

Master не принимает `X-User-Id` от клиента как факт. Источник идентичности —
Keycloak; master валидирует JWT и строит `Principal(user_id, roles)`.

## Режимы (`AUTH_MODE`)

| Режим | Как передаётся пользователь | Где использовать |
|---|---|---|
| `keycloak` | `Authorization: Bearer <JWT>` | прод, по умолчанию |
| `dev_header` | `X-User-Id: <uuid>` (+ опц. `X-User-Roles: admin`) | локальная разработка, STUB-тесты |

### `keycloak`

`Authorization: Bearer <JWT>`. Master:

1. проверяет подпись по JWKS Keycloak (`KEYCLOAK_JWKS_URL`);
2. валидирует `iss`, `aud`, `exp`, `iat`, `sub`;
3. берёт `user_id` из `sub` (обязан быть UUID);
4. роли собирает из `realm_access.roles` и
   `resource_access.<KEYCLOAK_CLIENT_ID>.roles`.

Правила:

- валидный токен → пользователь (`user`);
- роль `ADMIN_ROLE` (по умолчанию `platform-admin`) → администратор (`admin`);
- клиентский `X-User-Id` **игнорируется** — источник истины только `sub`;
- недопустимый алгоритм, неверная подпись, просроченный `exp`, неверные
  `aud`/`iss`, `sub` не UUID → `401`;
- JWKS недоступен/не настроен → `503` (fail-closed);
- JWKS кэшируется (`KEYCLOAK_JWKS_CACHE_TTL`), при ротации ключа (новый `kid`)
  кэш обновляется один раз.

### `dev_header`

Доверенный `X-User-Id`; роль админа — `X-User-Roles: admin`. Сознательно
небезопасен: только для локальной разработки и тестов, при старте master
пишет предупреждение в лог. В прод не выкатывать.

## Переменные окружения

Keycloak-режим:

```env
AUTH_MODE=keycloak
KEYCLOAK_ISSUER=http://127.0.0.1:8081/realms/tidi
KEYCLOAK_JWKS_URL=http://127.0.0.1:8081/realms/tidi/protocol/openid-connect/certs
KEYCLOAK_AUDIENCE=master-node
KEYCLOAK_CLIENT_ID=master-node
ADMIN_ROLE=platform-admin
```

Необязательные (есть дефолты):

```env
KEYCLOAK_ALGORITHMS=RS256          # через запятую или JSON-список
KEYCLOAK_LEEWAY=30                 # секунды, допуск по часам
KEYCLOAK_JWKS_CACHE_TTL=300        # секунды
KEYCLOAK_HTTP_TIMEOUT=5.0          # секунды
KEYCLOAK_VERIFY_TLS=true           # false только для HTTP-контура
```

`KEYCLOAK_ISSUER` обязан совпадать с `iss` в токене. Пока Keycloak работает
по HTTP на `127.0.0.1:8081`, issuer = `http://127.0.0.1:8081/realms/tidi`;
после перехода на HTTPS/nginx он сменится на `https://<домен>/realms/tidi` —
обновите `KEYCLOAK_ISSUER` и `KEYCLOAK_JWKS_URL` одновременно.

## Роли

Роли и матрица прав вынесены отдельно: [docs/roles.md](roles.md).

## Внутренняя аутентификация master → агент (этап 1)

Отдельный trust domain, не связанный с Keycloak: агент не видит JWT и не
ходит в Keycloak, он должен убедиться только в одном — запрос пришёл от
master. Master — единственный, кто проверяет JWT и роли.

### Заголовки

| Заголовок | Кто ставит | Смысл |
|---|---|---|
| `X-Internal-Token` | master | per-agent секрет; доказывает авторство master |
| `X-User-Id` | master | UUID из проверенного `sub` (или dev-заголовка) |
| `X-Request-Id` | nginx/клиент или master | корреляция логов; не аутентификация |

Клиентский `Authorization` агенту не передаётся: master не пробрасывает его
в адаптерах. `X-Request-Id` master принимает от nginx/клиента только в виде
`[A-Za-z0-9._:-]{1,128}`, иначе генерирует новый (32 hex-символа) — это
защита логов от injection, а не проверка доступа. Итоговый id возвращается
клиенту в заголовке ответа и открыт браузеру через CORS.

### Настройки master

```env
ENV=prod
LOG_LEVEL=INFO

# Вариант 1: JSON прямо в env.
AGENT_INTERNAL_TOKENS={"epoz":"<secret>","chat":"<secret>"}

# Вариант 2: secret-файл (значения файла важнее env).
# AGENT_INTERNAL_TOKENS_FILE=/run/secrets/agent_internal_tokens.json
```

Формат файла:

```json
{
  "epoz": "<secret>",
  "chat": "<secret>",
  "tech_rag": "<secret>",
  "document_chat": "<secret>",
  "agentic_rag": "<secret>",
  "rag_ingestion": "<secret>"
}
```

Правила:

- **per-agent, а не один общий токен.** Утечка секрета одного агента не даёт
  звать остальных; ротация и аудит — по агенту;
- **не в `agents.yaml`.** Реестр правится через API и отдаётся в
  `GET /admin/agents`; секрет там стал бы публичным;
- **`ENV=prod` fail-closed.** Нет токена у включённого агента с HTTP-транспортом
  или токен короче `32` символов → master не стартует (`RuntimeError` в
  lifespan, до приёма запросов);
- **`ENV=dev`** допускает отсутствие токенов: заголовок не отправляется, в лог
  пишется предупреждение. Это режим локальной разработки, не прода;
- токены для агентов, которых нет в реестре, дают предупреждение (обычно
  забытый секрет после удаления агента), но старт не блокируют.

`repr(settings)` значения токенов скрывает, но `settings.model_dump()` — нет.
Не логируйте дамп настроек целиком и не отдавайте его в диагностические
ручки.

Генерация токена:

```bash
openssl rand -hex 32
# или
python -c 'import secrets; print(secrets.token_hex(32))'
```

Ротация: заменить значение в secret-файле (или env) и перезапустить master и
агента. Пока агент не проверяет токен (этап 1 в агентах не внедрён), смена
секрета на стороне master безопасна.

### Логи

При `LOG_LEVEL=INFO` master пишет access-строку вида:

```
request_id=... method=POST path=/v1/chat/completions status=200 user_id=... duration_ms=...
```

`user_id` появляется только после успешной проверки пользователя. Значения
`X-Internal-Token`, `Authorization` и тела запросов в лог не пишутся. Для
отладки не включайте DEBUG для `httpx`/`httpcore` в prod: они логируют
заголовки запросов.

### Статус на стороне агентов

В этом репозитории реализована только master-часть: master уже умеет
отправлять `X-Internal-Token` и `X-Request-Id`, но агенты пока не проверяют
заголовок. Требования к агентам и порядок раскатки —
[docs/agent-side-changes.md](agent-side-changes.md).

## Как получить JWT для проверки

См. [deploy/keycloak/README.md](../deploy/keycloak/README.md) — «Как получить
JWT для проверки master». Кратко: админ-консоль → Clients → `master-node` →
Client scopes → Evaluate → выбрать пользователя → Generated access token.

Direct access grants (`grant_type=password`) на клиенте `master-node`
выключены — для SPA используется Authorization Code + PKCE.

## Развёртывание Keycloak

Docker-compose (Postgres + Keycloak, HTTP без TLS) и импорт realm — в
[deploy/keycloak/README.md](../deploy/keycloak/README.md). Там же — создание
пользователей, назначение ролей и точки входа.

## Фронтенд (сайт и мобильное приложение)

Инструкция для фронтендеров — [docs/frontend-auth.md](frontend-auth.md):
регистрация, Authorization Code + PKCE, refresh/logout, работа с master API,
мобильные redirect URI и настройка nginx.

## Термины

Глоссарий Keycloak (realm, client, claims, PKCE, direct grants и т.д.) —
[docs/keycloak-glossary.md](keycloak-glossary.md).

