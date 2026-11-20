# Ролевая система мастера (RBAC)

> **Статус:** реализовано в master. Роли проверяет только master; агенты роли
> не проверяют и ключ Keycloak не видят — им приходит проверенный `X-User-Id`.

## Роли

| Роль | Источник | Назначение |
|---|---|---|
| `user` | валидный JWT (по умолчанию) | работа с диалогами и данными, только своими |
| `admin` | роль `platform-admin` в JWT | реестр агентов, служебные ручки |
| `service` | — | машинная identity (агент→агент). **Пока не реализована**, только в плане |

`admin` наследует `user`: админ может всё то же, что пользователь, плюс админку.
Отдельной роли «суперпользователь, который видит все данные» нет.

## Откуда берутся роли

### Keycloak-режим (`AUTH_MODE=keycloak`)

Master валидирует JWT по JWKS и собирает роли из двух мест claims:

- `realm_access.roles` — realm-роли (`platform-user`, `platform-admin`);
- `resource_access["master-node"].roles` — клиентские роли клиента `master-node`.

`user_id` берётся из `sub` (обязан быть UUID). Клиентский `X-User-Id`
игнорируется.

### dev-режим (`AUTH_MODE=dev_header`)

Только для локальной разработки и STUB-тестов:

- `X-User-Id: <uuid>` — пользователь;
- `X-User-Roles: admin` — даёт роль админа (через запятую можно несколько).

В прод не выкатывать: при старте master пишет предупреждение.

## Проверка в коде

```python
Principal(user_id, roles)        # user_id из sub, roles — frozenset
principal.is_admin               # True, если есть platform-admin или admin

get_user_id(...)                 # аутентификация: любой валидный пользователь
require_admin(...)               # авторизация: только admin, иначе 403
```

Проверка идёт по permission, а не по строке роли — добавление новых ролей
(`auditor`, `support`) не потребует переписывать эндпоинты, только маппинг.

## Матрица прав

| Эндпоинт | `user` | `admin` | Без токена |
|---|---:|---:|---|
| `/v1/chat/completions` | ✅ | ✅ | `401` |
| `/v1/responses` | ✅ | ✅ | `401` |
| `/agents/{id}/v1/**` (proxy к агенту) | ✅ | ✅ | `401` |
| `/route` (resolve агента) | ✅ | ✅ | `401` |
| `/admin/agents*`, `/admin/registry*` | ❌ | ✅ | `401` |
| `/v1/models`, `/v1/models/{id}` | ✅ | ✅ | ✅ (публично) |

Коды ошибок: нет/битый токен → `401 authentication_error`; аутентифицирован,
но нет прав → `403 permission_error`.

## Что админ НЕ может

- **Видеть/менять данные других пользователей.** Скоупинг по `sub`/`user_id`
  остаётся в агентах; у админа нет права «имперсонации». Если понадобится
  (например, поддержка), это будет отдельное право `support.impersonate` с
  явным `X-Act-As-User` и аудитом.
- **Подменять пользователя.** `user_id` всегда из проверенного `sub`, а не из
  произвольного заголовка.

## Как выдать роль в Keycloak

1. Открыть админ-консоль → realm `tidi`.
2. Users → выбрать пользователя → Role mapping → Assign role:
   - обычный пользователь — `platform-user`;
   - администратор — `platform-admin`.
3. Сохранить. Роль попадёт в следующий access token (текущий токен не
   обновится до перевыпуска — у SPA это происходит при refresh/re-login).

## Связанные документы

- [docs/authentication.md](authentication.md) — валидация JWT, режимы,
  переменные окружения.
- [deploy/keycloak/README.md](../deploy/keycloak/README.md) — развёртывание
  Keycloak, создание пользователей.
- [docs/agent-side-changes.md](agent-side-changes.md) — планы по изоляции
  агентов и service-identity.
