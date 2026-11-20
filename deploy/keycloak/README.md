# Keycloak (Postgres + Keycloak, HTTP)

Локальный контур IdP для master-node. TLS на этом этапе не используется:
снаружи ничего не публикуется, оба порта забинжены на `127.0.0.1`. Когда
появится сертификат — nginx будет терминировать HTTPS и проксировать на
Keycloak, а `KC_HOSTNAME_STRICT` можно вернуть в `true`.

## Запуск

```bash
cd deploy/keycloak
cp .env.example .env
# задайте KC_ADMIN_PASSWORD и KC_DB_PASSWORD
docker compose up -d
```

Readiness (realm уже импортирован и отдаёт discovery):

```bash
curl -sf http://127.0.0.1:8081/realms/tidi/.well-known/openid-configuration >/dev/null && echo UP
```

(Health-эндпоинты `/health*` в Keycloak 26 висят на management-порту 9000
внутри контейнера, наружу он не публикуется; для внешней проверки readiness
удобнее discovery-эндпоинт выше.)

Точки входа:

- админ-консоль: http://127.0.0.1:8081/admin/
- realm: http://127.0.0.1:8081/realms/tidi
- discovery (issuer): http://127.0.0.1:8081/realms/tidi/.well-known/openid-configuration
- JWKS для master: http://127.0.0.1:8081/realms/tidi/protocol/openid-connect/certs

## Что импортируется

`tidi-realm.json` — realm `tidi` с:

- клиент `master-node` (public, Authorization Code + PKCE, `S256`);
- audience-mapper, добавляющий `master-node` в `aud` access-токена;
- realm-роли `platform-user` и `platform-admin`.

Импорт выполняется при **первом** старте (`start --import-realm`). Если realm
уже существует, Keycloak **пропускает** импорт, поэтому созданные через
админ-консоль пользователи и роли не затрутся при рестарте. Чтобы
пересоздать realm заново — `docker compose down -v` (удалит и БД).

## Создание пользователей и ролей

Админ-консоль → `tidi` → Users → Add user:

1. Username/Email/First name; флажок "Email verified" при необходимости.
2. Credentials → Set password (снять "Temporary", чтобы не было сброса при
   первом входе).
3. Role mapping → Assign role → `platform-user` (обычным) и/или
   `platform-admin` (администраторам).

Сам master различает:

- валидный токен → пользователь (`user`);
- токен с ролью `platform-admin` → администратор (`admin`).

Роль `platform-admin` даёт доступ только к `/admin/**` и `/route`. Доступа к
чужим данным она не даёт: скоупинг по `sub` остаётся в агентах.

Регистрация самостоятельная (`registrationAllowed`) по умолчанию **выключена**:
пользователей заводит администратор. Для открытой регистрации включите её в
Realm settings → Login и обязательно настройте SMTP + Email verified + защиту
от ботов; для пилота рекомендуется приглашения/ограничение по домену.

## Как получить JWT для проверки master (без фронтенда)

Проще всего через админ-консоль:

1. Clients → `master-node` → Client scopes → Evaluate.
2. Выбрать пользователя (ему уже выдана роль), нажать Generated access token.
3. Скопировать access token — это и есть `Authorization: Bearer <token>`.

Либо через Authorization Code + PKCE любым OIDC-клиентом. Direct access
grants (`grant_type=password`) на клиенте `master-node` **выключены** — для
SPA они не нужны и небезопасны.

## Настройки master-node

В `.env` master (корень репозитория):

```env
AUTH_MODE=keycloak
KEYCLOAK_ISSUER=http://127.0.0.1:8081/realms/tidi
KEYCLOAK_JWKS_URL=http://127.0.0.1:8081/realms/tidi/protocol/openid-connect/certs
KEYCLOAK_AUDIENCE=master-node
KEYCLOAK_CLIENT_ID=master-node
ADMIN_ROLE=platform-admin
```

`KEYCLOAK_ISSUER` обязан совпадать с `iss` в токене. Пока контур на HTTP и
`KC_HOSTNAME_STRICT=false`, issuer строится из того URL, по которому клиент
обратился к Keycloak (`http://127.0.0.1:8081/realms/tidi`). После перехода на
HTTPS/nginx issuer станет `https://<домен>/realms/tidi` — обновите
`KEYCLOAK_ISSUER` и `KEYCLOAK_JWKS_URL` одновременно с `KC_HOSTNAME`.

## Остановка и данные

```bash
docker compose down          # контейнеры, данные сохраняются в volume pgdata
docker compose down -v       # полное удаление БД (и импортированного realm)
```

Бэкап: штатный `pg_dump` из контейнера postgres или `docker compose exec
postgres pg_dump -U keycloak keycloak > keycloak.sql`. Realm можно
дополнительно экспортировать через `kc.sh export` (см. docs Keycloak).
