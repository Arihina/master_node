# Keycloak: краткий глоссарий

Термины, которые встречаются в админке Keycloak, токенах и нашей
документации. Привязка — к контуру `tidi` / клиент `master-node`.

## Сущности

| Термин | Что это | У нас |
|---|---|---|
| **Realm** | Изолированный «тенант»: свои пользователи, клиенты, роли, настройки. | `tidi` (плюс служебный `master`) |
| **Client** | Приложение/сервис, который запрашивает токены. | `master-node` |
| **Public client** | Без секрета (SPA, мобилка, CLI), полагается на PKCE. | `master-node` |
| **Confidential client** | С секретом (backend, server-to-server). | не используем |
| **User** | Учётная запись. | `tester` |
| **Group** | Иерархическая группа пользователей; может нести роли. | не используем |
| **Role** | Право/ярлык, попадающий в токен. | `platform-user`, `platform-admin` |
| **Realm role** | Роль уровня realm → `realm_access.roles`. | наши две роли |
| **Client role** | Роль уровня клиента → `resource_access.<client>.roles`. | не используем |
| **Composite role** | Роль, включающая другие роли. | `default-roles-tidi` |
| **Client scope** | Набор мапперов/ролей, подключаемый к клиенту. | `openid`, `profile`, `email`, `roles` |
| **Protocol mapper** | Правило, добавляющее claim в токен. | audience mapper → `aud: master-node` |

## Потоки и гранты

| Термин | Что это | У нас |
|---|---|---|
| **Authorization Code flow** (standard flow) | Браузер → логин Keycloak → `code` → обмен на токены. | основной путь фронта |
| **PKCE** | Защита code flow для public-клиентов: `code_verifier` + `code_challenge` (S256). | включён |
| **Implicit flow** | Устаревший flow, токен сразу в URL. | выключен |
| **Direct Access Grants** (ROPC) | Логин+пароль прямо в `/token`, без браузера. | только для отладки |
| **Client Credentials / service account** | Клиент ходит от своего имени, без пользователя. | выключено |
| **Refresh token** | Обмен на новый access token без повторного логина. | есть в ответе `/token` |
| **Offline access** | Scope `offline_access` — долгоживущий refresh вне SSO-сессии. | не включали |

## Токены и claims

| Термин | Что это |
|---|---|
| **Access token** | JWT для `Authorization: Bearer`; его проверяет master. |
| **ID token** | JWT для клиента: «кто пользователь» (имя, email). Для API не нужен. |
| **Refresh token** | Непрозрачная строка для обновления access token. |
| **Claims** | Поля внутри JWT. |
| **`iss`** | Issuer — URL realm. Master сверяет с `KEYCLOAK_ISSUER`. |
| **`aud`** | Audience — для кого токен. Master сверяет с `master-node`. |
| **`sub`** | Subject — id пользователя (UUID). Master берёт как `user_id`. |
| **`exp` / `iat`** | Время истечения / выпуска. |
| **`azp`** | Клиент, запросивший токен. |
| **`scope`** | Выданные scope (`openid profile email`). |
| **`realm_access.roles`** | Realm-роли пользователя. |
| **`resource_access.<client>.roles`** | Клиентские роли. |
| **`jti` / `sid` / `acr`** | Id токена / id сессии / уровень аутентификации. |
| **JWKS** | Публичные ключи Keycloak (`/protocol/openid-connect/certs`). |
| **`kid`** | Id ключа в заголовке JWT; по нему выбирается ключ из JWKS. |

## Сессии и настройки

| Термин | Что это | У нас |
|---|---|---|
| **SSO session** | Сессия браузера в Keycloak (cookie), позволяет не логиниться повторно. | — |
| **`ssoSessionIdleTimeout`** | Сколько сессия живёт без активности. | 1800 с (30 мин) |
| **`ssoSessionMaxLifespan`** | Максимальный срок сессии. | 36000 с (10 ч) |
| **`accessTokenLifespan`** | Время жизни access token. | 43200 с (12 ч) |
| **Brute force detection** | Блокировка после серии неудачных логинов. | включена |
| **Email verification** | Требовать подтверждение email. | выключена |
| **Registration** | Самостоятельная регистрация. | выключена |
| **Identity provider** | Внешний IdP (Google, Microsoft и т.п.). | не используем |
| **User federation** | Подключение LDAP/AD. | не используем |
| **Authentication flow** | Сценарий логина (browser flow, direct grant flow). | дефолтные |
| **Authorization services** | Собственный RBAC/policy-движок Keycloak (UMA). | выключен |
| **Admin console** | UI управления Keycloak (`/admin/`). | только внутренний доступ |
| **Account console** | Личный кабинет пользователя (`/realms/tidi/account/`). | можно дать пользователям |

## Связанные документы

- [docs/authentication.md](authentication.md) — JWT, режимы, env.
- [docs/roles.md](roles.md) — роли и права master.
- [docs/frontend-auth.md](frontend-auth.md) — инструкция фронтенду и nginx.
- [deploy/keycloak/README.md](../deploy/keycloak/README.md) — развёртывание.
