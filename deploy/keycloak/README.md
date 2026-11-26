# Keycloak (часть единого deploy)

IdP для master-node. Keycloak запускается **общим** `docker-compose.yml` из
[deploy/](../README.md) вместе с Postgres и Qdrant — отдельного compose для
Keycloak больше нет. Сам Keycloak слушает `127.0.0.1:8081` с относительным
путём `/auth`; наружу его отдаёт nginx на порту `8005` по пути `/auth/`. TLS
пока не используется.

## Запуск

```bash
cd deploy
cp .env.example .env
# задайте POSTGRES_PASSWORD, KC_ADMIN_PASSWORD и PUBLIC_IP (белый IP сервера)
docker compose up -d
```

Readiness (realm уже импортирован и отдаёт discovery):

```bash
curl -sf http://127.0.0.1:8081/auth/realms/tidi/.well-known/openid-configuration >/dev/null && echo UP
```

(Health-эндпоинты `/health*` в Keycloak 26 висят на management-порту 9000
внутри контейнера, наружу он не публикуется; для внешней проверки readiness
удобнее discovery-эндпоинт выше.)

Точки входа (напрямую, внутри контейнера):

- админ-консоль: http://127.0.0.1:8081/auth/admin/
- realm: http://127.0.0.1:8081/auth/realms/tidi
- discovery (issuer): http://127.0.0.1:8081/auth/realms/tidi/.well-known/openid-configuration
- JWKS для master: http://127.0.0.1:8081/auth/realms/tidi/protocol/openid-connect/certs

Наружу (через nginx на белом IP):

- OIDC authority: `http://<PUBLIC_IP>:8005/auth/realms/tidi`
- админ-консоль: `http://<PUBLIC_IP>:8005/auth/admin/` (наружу НЕ открывать)

## Что импортируется

`tidi-realm.json` — realm `tidi` с:

- клиент `master-node` (public, Authorization Code + PKCE, `S256`);
- audience-mapper, добавляющий `master-node` в `aud` access-токена;
- realm-роли `platform-user` и `platform-admin`;
- redirect URIs для web (`http://${PUBLIC_IP}:8005/auth/callback`) и mobile
  (`ru.tidi.assistant:/oauth2redirect`), web origin `http://${PUBLIC_IP}:8005`.
  `${PUBLIC_IP}` подставляется из env контейнера при импорте (значение из
  `.env`).

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

Регистрация самостоятельная (`registrationAllowed`) сейчас **включена** без
верификации email и без SMTP — временно, для пилота. Позже её можно выключить
в Realm settings → Login. Перед боевым использованием открытую регистрацию
нужно усилить: SMTP + Email verified + защита от ботов (или invite-only).

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
KEYCLOAK_ISSUER=http://<PUBLIC_IP>:8005/auth/realms/tidi
KEYCLOAK_JWKS_URL=http://127.0.0.1:8081/auth/realms/tidi/protocol/openid-connect/certs
KEYCLOAK_AUDIENCE=master-node
KEYCLOAK_CLIENT_ID=master-node
ADMIN_ROLE=platform-admin
```

`KEYCLOAK_ISSUER` обязан совпадать с `iss` в токене, т.е. с публичным URL
Keycloak (`http://<PUBLIC_IP>:8005/auth/realms/tidi`). JWKS master может тянуть
по внутреннему адресу `127.0.0.1:8081` — важно, что там тоже есть префикс
`/auth` из-за `KC_HTTP_RELATIVE_PATH`.

## Остановка и данные

```bash
cd deploy
docker compose down          # контейнеры останавливаются, данные сохраняются
docker compose down -v       # полное удаление БД (и импортированного realm)
```

Бэкап: штатный `pg_dump` из общего postgres или
`docker compose exec postgres pg_dump -U postgres keycloak > keycloak.sql`.
Realm можно дополнительно экспортировать через `kc.sh export`.

## Связанные документы

- [docs/authentication.md](../../docs/authentication.md) — JWT, режимы, env.
- [docs/roles.md](../../docs/roles.md) — роли и права master.
- [docs/frontend-auth.md](../../docs/frontend-auth.md) — инструкция фронтенду
  (web + mobile) и настройка nginx.
