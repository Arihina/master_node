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

