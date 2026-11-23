# Frontend: регистрация, авторизация и работа с master

Коротко: **пароли и логин — только на стороне Keycloak**, фронт получает
JWT по Authorization Code + PKCE и дальше ходит с ним в master через nginx.

---

## 1. Схема

```
браузер/приложение ──(1) login/registration──▶ Keycloak
        │                                      │
        │◀────────(2) code + redirect──────────┘
        │
        ├──(3) code + code_verifier──▶ Keycloak /token
        │◀────────(4) access_token ────────────┘
        │
        └──(5) Authorization: Bearer <access_token>──▶ nginx /api/ ──▶ master
```

- Фронт **не хранит пароли** и не вызывает `grant_type=password`.
- Фронт **не знает client secret** — клиент `master-node` публичный.
- Keycloak выдаёт JWT; master проверяет подпись, `iss`, `aud`, `exp`, `sub` и роли.
- Агенты (epoz, chat, tech_rag, …) напрямую не вызываются — только через master.

---

## 2. URL-ы

### 2.1. Контракт текущего контура (HTTP, белый IP, без домена)

Это фиксированные значения, которые фронт использует **как есть**. `<ПУБЛИЧНЫЙ_IP>`
— белый IP сервера (без `http://`).

| Что | Значение |
|---|---|
| OIDC authority | `http://<ПУБЛИЧНЫЙ_IP>:8005/auth/realms/tidi` |
| client_id | `master-node` |
| scope | `openid profile email` |
| API base URL | `http://<ПУБЛИЧНЫЙ_IP>:8005/api` |
| Web redirect URI | `http://<ПУБЛИЧНЫЙ_IP>:8005/auth/callback` |
| Web origin | `http://<ПУБЛИЧНЫЙ_IP>:8005` |
| Mobile redirect URI (приложение 1) | `pro.tidi.tidi_assistant_flutter:/oauth2redirect` |
| Mobile redirect URI (приложение 2) | `com.example.tidi_assistant_flutter:/oauth2redirect` |
| Mobile redirect URI (приложение 3) | `ru.tidi.app:/oauth2redirect` |

Эти же redirect URI / web origin зарегистрированы в клиенте `master-node`
(в `tidi-realm.json` через `${PUBLIC_IP}`). Frontend-разработчики ничего не
«выдумывают» — они обязаны использовать ровно эти строки.

### 2.2. Локальная разработка

| Что | Значение |
|---|---|
| Сайт | `http://localhost:3000` |
| API master | `http://127.0.0.1:8111` (напрямую) или `http://127.0.0.1:8005/api` (через nginx) |
| OIDC authority | `http://127.0.0.1:8081/auth/realms/tidi` |
| Redirect URI (web) | `http://localhost:3000/auth/callback` |

### 2.3. OIDC-эндпоинты

Все пути — относительно `{authority}`:

| Назначение | Путь |
|---|---|
| Discovery (не обязателен, но полезен) | `/.well-known/openid-configuration` |
| Логин (Authorization Code + PKCE) | `/protocol/openid-connect/auth` |
| Обмен кода / refresh | `/protocol/openid-connect/token` |
| Профиль пользователя | `/protocol/openid-connect/userinfo` |
| Logout | `/protocol/openid-connect/logout` |
| Регистрация | `/protocol/openid-connect/registrations` |
| Личный кабинет | `/account/` |
| JWKS (только master, фронту не нужен) | `/protocol/openid-connect/certs` |

---

## 3. Регистрация

Сейчас в realm `registrationAllowed: false` — пользователей заводит админ.
Если нужна самостоятельная регистрация:

1. Realm `tidi` → **Realm settings** → **Login** → **User registration** → ON → Save.
2. На странице логина Keycloak появится ссылка «Register».
3. Настоятельно рекомендуется включить **Verify email** и настроить SMTP;
   иначе кто угодно создаст аккаунт с произвольной почтой.

Как открыть регистрацию из приложения напрямую (не через ссылку на логине):
используйте `/protocol/openid-connect/registrations` с **теми же**
`client_id`, `response_type=code`, `scope`, `redirect_uri`, `state`,
`code_challenge`, `code_challenge_method=S256`, что и при логине. Именно этот
эндпоинт показывает hosted-страницу регистрации Keycloak.

После успешной регистрации:

- если `Verify email` выключен — Keycloak может сразу залогинить и вернуть
  `code` на `redirect_uri`;
- если включён — пользователь сначала подтверждает почту, потом логинится.

**Кастомную форму регистрации** (свой UI, а не страница Keycloak) делать не
рекомендуем: для этого нужен backend-сервис с service account и ролью
`manage-users`. Для пилота достаточно hosted-страницы Keycloak.

---

## 4. Авторизация: Authorization Code + PKCE

### 4.1. Шаги

1. Сгенерировать `code_verifier` (43–128 символов) и
   `code_challenge = BASE64URL(SHA256(code_verifier))`.
2. Сгенерировать `state` (случайная строка) и сохранить его.
3. Редирект на `{authority}/protocol/openid-connect/auth` с параметрами:
   `client_id=master-node`, `response_type=code`, `scope=openid profile email`,
   `redirect_uri`, `state`, `code_challenge`, `code_challenge_method=S256`.
4. Пользователь логинится на странице Keycloak.
5. Keycloak возвращает на `redirect_uri?code=...&state=...`.
6. Проверить `state`, отправить `code` + `code_verifier` на `/token`.
7. Получить `access_token`, `refresh_token`, `id_token`, `expires_in`.
8. В API ходить с `Authorization: Bearer <access_token>`.

### 4.2. Пример для web (TypeScript, без библиотеки)

```ts
const AUTHORITY = 'https://auth.example.com/realms/tidi';
const CLIENT_ID = 'master-node';
const REDIRECT_URI = window.location.origin + '/auth/callback';
const SCOPE = 'openid profile email';

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

const randomBytes = (n = 32) =>
  b64url(crypto.getRandomValues(new Uint8Array(n)));

async function sha256(text: string) {
  const digest = await crypto.subtle.digest(
    'SHA-256', new TextEncoder().encode(text));
  return new Uint8Array(digest);
}

export async function login() {
  const verifier = randomBytes(32);
  const state = randomBytes(16);
  sessionStorage.setItem('pkce_verifier', verifier);
  sessionStorage.setItem('oidc_state', state);

  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: 'code',
    scope: SCOPE,
    redirect_uri: REDIRECT_URI,
    state,
    code_challenge: b64url(await sha256(verifier)),
    code_challenge_method: 'S256',
  });
  window.location.assign(
    `${AUTHORITY}/protocol/openid-connect/auth?${params}`);
}

export async function handleCallback() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const state = params.get('state');

  if (!code) throw new Error(params.get('error_description') || 'no code');
  if (state !== sessionStorage.getItem('oidc_state')) {
    throw new Error('state mismatch');
  }

  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: CLIENT_ID,
    code,
    redirect_uri: REDIRECT_URI,
    code_verifier: sessionStorage.getItem('pkce_verifier') || '',
  });

  const res = await fetch(`${AUTHORITY}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) throw new Error(await res.text());

  const tokens = await res.json();
  // tokens.access_token, tokens.refresh_token, tokens.expires_in
  return tokens;
}
```

### 4.3. Refresh

```ts
export async function refresh(refreshToken: string) {
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    client_id: CLIENT_ID,
    refresh_token: refreshToken,
  });
  const res = await fetch(`${AUTHORITY}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) throw new Error('refresh failed');
  return res.json();
}
```

Access token живёт 12 часов (значение `accessTokenLifespan` в realm; в уже
развёрнутом realm проверьте **Realm settings → Tokens**), но полагаться на это
не стоит: при `401` пробуйте `refresh`, если не вышло — на логин. Refresh
работает, пока жива SSO-сессия Keycloak (idle 30 минут, максимум 10 часов).

### 4.4. Logout

```ts
const params = new URLSearchParams({
  id_token_hint: idToken,            // рекомендуется
  post_logout_redirect_uri: window.location.origin + '/',
});
window.location.assign(
  `${AUTHORITY}/protocol/openid-connect/logout?${params}`);
```

`post_logout_redirect_uri` должен быть в `post.logout.redirect.uris` клиента.
Перед редиректом очистите локальные токены.

### 4.5. Claims и роли

Полезные поля access token:

| Claim | Смысл |
|---|---|
| `sub` | UUID пользователя (master использует его как `user_id`) |
| `realm_access.roles` | роли, в т.ч. `platform-user` / `platform-admin` |
| `email`, `preferred_username`, `name` | профиль для UI |
| `exp` | когда истекает |

Роли можно декодировать на клиенте для UI (показать админ-раздел), но
**решения о доступе принимает сервер**. Скрытие кнопки — не защита.

---

## 5. Хранение токенов (web)

Рекомендации по убыванию безопасности:

1. **BFF-паттерн** (backend for frontend): токены живут на сервере, браузер
   получает только httpOnly-сессионную cookie. Самый безопасный, но нужен
   backend.
2. **Access token в памяти** + refresh token в `sessionStorage`/`localStorage` —
   компромисс для SPA. При XSS токен украдут; снижайте риск CSP, санитайзингом
   и коротким временем жизни.
3. **Всё в `localStorage`** — просто, но наименее безопасно. Для пилота
   допустимо, для прода — только с пониманием рисков.

Никогда не кладите в токены/URL пароли и не храните пароль в приложении.

---

## 6. Мобильное приложение

### 6.1. Общие правила

- Только **Authorization Code + PKCE**, без client secret.
- Redirect URI — кастомная схема. У нас **три** мобильные схемы (все
  зарегистрированы в клиенте `master-node`):
  `pro.tidi.tidi_assistant_flutter:/oauth2redirect`,
  `com.example.tidi_assistant_flutter:/oauth2redirect` и
  `ru.tidi.app:/oauth2redirect`.
- Токены — в **Keychain (iOS)** / **EncryptedSharedPreferences или Keystore
  (Android)**, не в обычных настройках.
- Все запросы — по HTTPS.
- При `401` — refresh; если refresh не удался — logout и повторный логин.

### 6.2. Библиотеки

- **Android:** [AppAuth-Android](https://github.com/openid/AppAuth-Android)
  (Custom Tabs) или [openid/AppAuth](https://github.com/openid/AppAuth-Android).
- **iOS:** [AppAuth-iOS](https://github.com/openid/AppAuth-iOS)
  (`ASWebAuthenticationSession`).
- **Flutter:** `flutter_appauth`.
- **React Native:** `react-native-app-auth`.

### 6.3. Redirect URI

В Keycloak (Clients → `master-node` → Settings → Valid redirect URIs)
зарегистрированы **три** схемы:

```
pro.tidi.tidi_assistant_flutter:/oauth2redirect
com.example.tidi_assistant_flutter:/oauth2redirect
ru.tidi.app:/oauth2redirect
```

Каждое приложение обязано использовать ровно свою схему. Не используйте `*`
для мобильных схем. Если библиотека формирует схему с `//`
(`pro.tidi.tidi_assistant_flutter://oauth2redirect`) — регистрируйте и
используйте именно тот вариант с `//`.

### 6.4. Пример (Android, AppAuth)

```kotlin
val serviceConfig = AuthorizationServiceConfiguration(
  Uri.parse("$AUTHORITY/protocol/openid-connect/auth"),
  Uri.parse("$AUTHORITY/protocol/openid-connect/token")
)

val authRequest = AuthorizationRequest.Builder(
  serviceConfig,
  "master-node",
  ResponseTypeValues.CODE,
  Uri.parse("pro.tidi.tidi_assistant_flutter:/oauth2redirect")
).setScope("openid profile email").build()
```

Дальше — стандартный `AuthorizationService` + `AuthorizationServiceConfiguration`.
AppAuth сам сделает PKCE и refresh.

### 6.5. iOS (AppAuth)

```swift
let request = OIDAuthorizationRequest(
  configuration: config,
  clientId: "master-node",
  scopes: ["openid", "profile", "email"],
  redirectURL: URL(string: "pro.tidi.tidi_assistant_flutter:/oauth2redirect")!,
  responseType: OIDResponseTypeCode,
  additionalParameters: nil
)
```

---

## 7. Работа с master API

Base URL — `https://app.example.com/api` (через nginx). Заголовки:

```
Authorization: Bearer <access_token>
Content-Type: application/json
```

### 7.1. Эндпоинты

| Метод | Путь (через nginx) | Назначение |
|---|---|---|
| `GET` | `/api/v1/models` | список агентов как моделей + `auto` |
| `POST` | `/api/route` | resolve: какого агента выберет `auto` (без генерации) |
| `POST` | `/api/v1/chat/completions` | чат (OpenAI Chat Completions) |
| `POST` | `/api/v1/responses` | чат (OpenAI Responses) |
| `POST` | `/api/agents/{agent_id}/v1/platform/conversations` | создать чат |
| `GET` | `/api/agents/{agent_id}/v1/platform/conversations` | список чатов |
| `GET` | `/api/agents/{agent_id}/v1/platform/conversations/{id}/messages` | история |
| `POST` | `/api/agents/{agent_id}/v1/chat/completions/{id}/feedback` | фидбэк |
| `GET` | `/api/agents/{agent_id}/v1/chat/completions/{id}/sources` | источники |

`agent_id` — это `id` агента из `/api/v1/models` (например, `epoz`, `chat`,
`tech_rag`, `document_chat`, `agentic_rag`).

### 7.2. Новый чат с автороутингом

```ts
// 1. узнать агента
const route = await fetch(`${API}/route`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ message: 'что такое ЕПоЗ' }),
}).then(r => r.json());          // { "agent": "epoz" }

// 2. создать чат у этого агента
const conv = await fetch(`${API}/agents/${route.agent}/v1/platform/conversations`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ title: 'Новый чат' }),
}).then(r => r.json());          // { "id": "...", ... }

// 3. отправить сообщение в этот чат
const answer = await fetch(`${API}/v1/chat/completions`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({
    model: route.agent,
    conversation_id: conv.id,
    messages: [{ role: 'user', content: 'что такое ЕПоЗ' }],
  }),
}).then(r => r.json());
```

### 7.3. Stateless-вариант

Если история не нужна:

```ts
const answer = await fetch(`${API}/v1/chat/completions`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({
    model: 'auto',
    messages: [{ role: 'user', content: 'привет' }],
  }),
}).then(r => r.json());
// answer.model — кто именно ответил (epoz/chat/...)
```

### 7.4. Стрим (SSE)

В теле запроса `"stream": true`. `EventSource` **не умеет** передавать
`Authorization`, поэтому используйте `fetch` + `ReadableStream`:

```ts
const res = await fetch(`${API}/v1/chat/completions`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({
    model: 'auto',
    stream: true,
    messages: [{ role: 'user', content: 'привет' }],
  }),
});

const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  let idx;
  while ((idx = buffer.indexOf('\n\n')) >= 0) {
    const event = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    for (const line of event.split('\n')) {
      if (!line.startsWith('data:')) continue;
      const data = line.slice(5).trim();
      if (data === '[DONE]') return;
      const chunk = JSON.parse(data);
      const delta = chunk.choices?.[0]?.delta?.content;
      if (delta) appendToUI(delta);
    }
  }
}
```

Nginx должен быть настроен с `proxy_buffering off`, иначе стрим «залипнет».

### 7.5. Ошибки

Формат:

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": null}}
```

| Код | Что делать |
|---|---|
| `401` | токен истёк/невалиден → refresh, иначе login |
| `403` | токен валиден, но нет прав (например, не админ) → показать «нет доступа» |
| `400` | неверный запрос → показать сообщение |
| `404` | агент/чат/ресурс не найден |
| `502` | агент недоступен → «сервис временно недоступен» |

---

## 8. Роли в UI

- `platform-admin` — показывать админ-раздел (управление реестром агентов).
- `platform-user` — обычный пользователь.
- Роли читаются из `realm_access.roles` access token.
- Админ **не получает** доступ к чужим чатам: скоупинг по `sub` остаётся в
  агентах. Если нужен «просмотр от имени пользователя» — это отдельная фича,
  не в этом контуре.

---

## 9. Nginx

### 9.1. Текущая схема

Сейчас nginx на порту `8005` проксирует `/api/` на master `127.0.0.1:8111`.
Этого достаточно для API, но **не для логина**: фронту нужен Keycloak.

Минимальный рабочий блок для текущего HTTP-контура:

```nginx
server {
    listen 8005;
    server_name _;

    client_max_body_size 200m;

    location /api/ {
        # слэш на конце обязателен: /api/v1/chat/completions -> /v1/chat/completions
        proxy_pass http://127.0.0.1:8111/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE: без этого стрим буферизуется и «залипает»
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # админку master наружу не отдаём
    location /api/admin/ {
        return 404;
    }
}
```

Дальше итоговая схема для текущего контура (HTTP, белый IP, без домена).

### 9.2. Итоговая схема (один порт, отдельный порт для Keycloak НЕ нужен)

Всё на одном nginx-порту `8005`:

- `/api/` → master `127.0.0.1:8111`;
- `/auth/` → Keycloak `127.0.0.1:8081`;
- `/` → SPA.

```nginx
server {
    listen 8005;
    server_name _;

    client_max_body_size 200m;

    # админку master наружу не отдаём
    location /api/admin/ { return 404; }

    location /api/ {
        # слэш обязателен: /api/v1/chat/completions -> /v1/chat/completions
        proxy_pass http://127.0.0.1:8111/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # админ-консоль Keycloak наружу не отдаём
    location /auth/admin/ { return 404; }

    location /auth/ {
        # БЕЗ слэша: сохраняем /auth/... (Keycloak работает с относительным путём)
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Port $server_port;
    }

    # SPA
    location / {
        root /var/www/app;
        try_files $uri $uri/ /index.html;
    }
}
```

Keycloak (compose) должен быть с относительным путём:

```env
KC_HTTP_RELATIVE_PATH=/auth
KC_PROXY_HEADERS=xforwarded
```

Issuer = `http://<ПУБЛИЧНЫЙ_IP>:8005/auth/realms/tidi`. В `.env` master:

```env
KEYCLOAK_ISSUER=http://<ПУБЛИЧНЫЙ_IP>:8005/auth/realms/tidi
KEYCLOAK_JWKS_URL=http://127.0.0.1:8081/auth/realms/tidi/protocol/openid-connect/certs
```

> JWKS можно тянуть по внутреннему `127.0.0.1:8081`, но там тоже есть префикс
> `/auth` из-за `KC_HTTP_RELATIVE_PATH`. `issuer` обязан быть публичным.

### 9.3. Чек-лист nginx

- [ ] `/api/` → `http://127.0.0.1:8111/` **со слэшем** в `proxy_pass`.
- [ ] `/auth/` → `http://127.0.0.1:8081` **без слэша** (путь сохраняется).
- [ ] `/auth/admin/` и `/api/admin/` наружу не отдаются.
- [ ] `proxy_buffering off` на `/api/` (SSE).
- [ ] `client_max_body_size` ≥ 100–200 МБ (документы/аудио).
- [ ] `proxy_read_timeout`/`proxy_send_timeout` большие (долгие ответы).
- [ ] Порты агентов (`8001/8002/8004/8007/8020/8011/8012`) не проксируются.
- [ ] `X-Forwarded-For/Proto/Host/Port` проброшены и в master, и в Keycloak.
- [ ] TLS (когда появится домен) — на nginx, не на Keycloak.

---

## 10. Частые ошибки

| Симптом | Причина |
|---|---|
| `Invalid parameter: redirect_uri` | URI в приложении не совпадает с зарегистрированным (порт, схема, слэш) |
| `401`, в токене `aud` без `master-node` | не сработал audience mapper в клиенте |
| `401`, `iss` другой | `KEYCLOAK_ISSUER` в master не совпадает с issuer токена (сменился домен/схема) |
| `403` на `/admin` | у пользователя нет `platform-admin` |
| CORS на `/token` | origin фронта не добавлен в **Web origins** клиента |
| Стрим не идёт | nginx буферизует (`proxy_buffering on`) |
| `404` на `/api/v1/...` | в `proxy_pass` нет слэша, master получил `/api/v1/...` |
| Токен «просрочен» через 5 минут | старый access token; перевыпустите (сейчас 12 ч) |
| `EventSource` не работает | он не умеет `Authorization` — используйте `fetch` |
| Логин редиректит на `localhost` | в клиенте не добавлен production redirect URI |

---

## 11. Полезное для отладки

- Discovery: `{authority}/.well-known/openid-configuration` — покажет все
  эндпоинты и `issuer`.
- Токен можно распаковать на [jwt.io](https://jwt.io) или локально:
  `JSON.parse(atob(token.split('.')[1]))`.
- Для проверки master без фронта есть dev-страница
  [deploy/keycloak/dev-login.html](../deploy/keycloak/dev-login.html) — она
  делает PKCE-логин и показывает access token. Только для локальной отладки.

## См. также

- [docs/authentication.md](authentication.md) — как master валидирует JWT.
- [docs/roles.md](roles.md) — роли `user` / `admin` и права.
- [docs/keycloak-glossary.md](keycloak-glossary.md) — термины Keycloak.
- [deploy/keycloak/README.md](../deploy/keycloak/README.md) — развёртывание.
