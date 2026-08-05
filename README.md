# Master Router

Мастер-агент: по запросу пользователя определяет нужного ИИ-агента и
проксирует к нему запросы. Доступ к агентам — только через мастер. API
мастера и канонический контракт агентов совместимы с OpenAI Chat Completions
(`/v1/chat/completions`) — см. [Контракт агента](#контракт-агента). На
текущем этапе все агенты — локальные сервисы на разных портах; разнородность
транспорта спрятана за слоем адаптеров, поэтому подключение агента с другим
API не меняет мастер.

Мастер **stateless**: не хранит чаты, сообщения и фидбэк — всё это живёт у
агентов и скоупится по `X-User-Id`. Задача мастера — роутинг (`model: "auto"`),
выбор адаптера и прозрачное проксирование.

## Архитектура

```
                 ┌─────────────────────────────────────┐
   клиент  ─────▶│            Master Router            │
                 │                                     │
                 │  routing/   embedding → llm         │
                 │  adapters/  proxy(method, path, ...) │
                 └───────┬───────────────┬─────────────┘
                         │               │
              ┌──────────▼───┐   ┌───────▼──────┐   ...
              │  агент ЕПоЗ  │   │  агент OCR   │
              │  :8001       │   │  :xxxx       │
              └──────────────┘   └──────────────┘
```

Мастер — единственная точка входа. Агенты сидят на localhost и доверяют
заголовку `X-User-Id`, который проставляет мастер (на текущем этапе — берётся
из заголовка клиента).

## Роутинг

`MasterRouter.route()` определяет агента каскадом из двух слоёв. Каждый
следующий слой запускается, только если предыдущий не дал ответа:

1. **embedding_router** — семантическая близость запроса к описаниям агентов
   через `intfloat/multilingual-e5-small` (косинусная мера, порог отсечения).
2. **llm_router** — классификация запроса моделью `gemma2:2b` через Ollama,
   ответ форсится в JSON.

Если ни один слой не дал валидного агента, запрос уходит на `FALLBACK_AGENT`
(`chat`). Блокирующие вызовы (`encode`, `client.chat`) вынесены в
`asyncio.to_thread`, чтобы не блокировать event loop.

Роутинг срабатывает только при `model: "auto"` в `POST /v1/chat/completions`
(см. ниже) — не отдельным эндпоинтом. В нём участвуют только агенты с
`routable=True`. Инструменты без диалога (OCR) помечены `routable=False` — их
нельзя выбрать по тексту, к ним обращаются напрямую по id через их
capability-маршрут.

## Слой адаптеров

Мастер общается с агентом через один универсальный метод —
`AgentAdapter.proxy(method, path, user_id, body, content_type)` — а не через
намерения (`create_session`, `stream_chat`, …), как было раньше. Мастер не
знает и не интерпретирует, что стоит за конкретным `path`; это ответственность
агента. Транспорт выбирается по полю `transport` в реестре:

| transport  | адаптер              | для кого                                        |
|------------|----------------------|-------------------------------------------------|
| `contract` | `ContractHTTPAdapter`| диалоговые агенты на каноническом контракте (ЕПоЗ)|
| `external` | `ExternalAPIAdapter` | чужой API вендора (скелет; беседы хранит сам)   |
| `ocr`      | `OCRAdapter`         | инструмент файл-в/текст-из, без диалога         |

`proxy()` возвращает `ProxyResult(status, content_type, body: AsyncIterator[bytes])`.
Статус и content-type апстрима известны сразу — httpx получает заголовки
раньше тела, — поэтому мастер выставляет правильный статус-код и media-type
ответа ещё до того, как начнёт вычитывать `body`. Один и тот же механизм
одинаково обслуживает и потоковый SSE-ответ (`/v1/chat/completions` со
`stream: true`), и обычный одиночный JSON (`GET .../feedback`,
`/v1/platform/conversations`, ...) — разделять их не нужно. Добавление
агента, укладывающегося в контракт, — запись в `registry.py`; число маршрутов
мастера не зависит ни от числа агентов, ни от числа ручек в их контракте (см.
[Generic proxy](#generic-proxy-агента) ниже).

## Ядро контракта и возможности

Контракт делится на **ядро** (обязательное для всех адаптеров) и
**возможности** (capabilities — необязательные умения, есть не у каждого).

Ядро — единственный `@abstractmethod` в `base.py`: `proxy()`. Реализует любой
адаптер, но по-разному: OCR отвечает `404` на любой путь (агент не
реализует `/v1/chat/completions`), `ExternalAPIAdapter` — `NotImplementedError` (скелет).

Возможности — отдельные необязательные методы
(`raise CapabilityNotSupported`), не входящие в `proxy()`/новый контракт.
Каждая возможность:

- объявляется адаптером как метод (`run_ocr`, `upload_document`, …);
- перечисляется в `capabilities` агента в реестре;
- имеет **один общий** маршрут мастера (рост маршрутов O(возможностей), а не
  O(агентов)).

Перед вызовом маршрут проверяет `require_capability(agent_id, "...")`; если
возможность не объявлена — `404`. На случай рассинхрона (объявлена в реестре,
но не реализована в адаптере) в `main.py` висит обработчик
`CapabilityNotSupported` → `404` в едином формате ошибок (см. ниже).
`GET /v1/models` не отдаёт `capabilities` в объекте модели (в отличие от
старого `GET /agents`) — это решение под OpenAI-совместимость
объекта `model`.

## Эндпоинты мастера

Все проксирующие эндпоинты требуют заголовок `X-User-Id: <uuid>` и
пробрасывают его агенту без изменений. Статус-коды и тела ответов агента
проходят насквозь — мастер
их не переинтерпретирует.

### Служебные

| Метод  | Путь         | Назначение                                        |
|--------|--------------|----------------------------------------------------|
| `GET`  | `/v1/models` | Список включённых агентов как OpenAI-моделей + `auto` |
| `POST` | `/route`     | Debug: какого агента выбрал бы `model: "auto"`, без реального вызова |

`GET /v1/models`:
```json
{
  "object": "list",
  "data": [
    {"id": "epoz", "object": "model", "created": 1735900000, "owned_by": "arihina"},
    {"id": "auto", "object": "model", "created": 1735900000, "owned_by": "arihina"}
  ]
}
```
`POST /route` — тело `{"message": "..."}`, ответ `{"agent": "epoz"}`. Не часть
OpenAI-контракта, оставлен как внутренняя debug-ручка.

### `POST /v1/chat/completions`

Единственный вход для генерации. Тело/формат ответа — OpenAI Chat Completions,
см. [Контракт агента](#контракт-агента).

```json
{
  "model": "epoz",
  "messages": [{"role": "user", "content": "что такое ЕПоЗ"}],
  "stream": true
}
```

- `model` — конкретный `agent_id` → прямой проброс в `ContractHTTPAdapter`
  этого агента без роутинга.
- `model: "auto"` (или поле отсутствует) → мастер берёт последнее сообщение с
  `role: "user"`, прогоняет через `MasterRouter.route()`, подставляет реальный
  `agent_id` в поле `model` исходящего запроса — тем самым он же приходит
  эхом в `model` ответа клиенту. 
  Это единственный канал, которым клиент узнаёт, кого выбрал роутер: отдельных
  заголовков (`X-Agent-Id`) или SSE-события `metadata`, как раньше, больше
  нет — `model` ответа и есть искомый `agent_id`, его же клиент использует
  дальше как `{agent_id}` в путях ниже.

Стрим/нестрим-форматы ответа — как в [контракте агента](#контракт-агента),
мастер их не переписывает, только проксирует байты как есть (тем же `proxy()`,
что и всё остальное).

### Generic proxy агента

| Метод  | Путь                             |
|--------|-----------------------------------|
| `GET`/`POST`/`PATCH`/`DELETE` | `/agents/{agent_id}/{path:path}` (только если `path` начинается с `v1/`) |

Один catch-all маршрут форвардит **любой** путь контракта агента, начинающийся
с `v1/`, — мастер не регистрирует под каждую ручку агента отдельный маршрут.
На практике сюда попадает всё, что не генерация (та идёт через
`POST /v1/chat/completions` выше):

```
GET    /agents/{agent_id}/v1/chat/completions/{completion_id}
POST   /agents/{agent_id}/v1/chat/completions/{completion_id}/feedback
GET    /agents/{agent_id}/v1/chat/completions/{completion_id}/feedback
DELETE /agents/{agent_id}/v1/chat/completions/{completion_id}/feedback
GET    /agents/{agent_id}/v1/chat/completions/{completion_id}/sources
POST   /agents/{agent_id}/v1/platform/conversations
GET    /agents/{agent_id}/v1/platform/conversations
GET    /agents/{agent_id}/v1/platform/conversations/{conversation_id}/messages
PATCH  /agents/{agent_id}/v1/platform/conversations/{conversation_id}
DELETE /agents/{agent_id}/v1/platform/conversations/{conversation_id}
```

Список выше — не то, что знает мастер (он его не хранит и не валидирует
детально), а то, что сейчас реализует агент, например, заглушка epoz и tech_rag. Если tech_rag
добавит новую ручку под `/v1/...` — она автоматически станет доступна через
мастер, без изменений на его стороне.

> Специфичные маршруты (например `/agents/{agent_id}/ocr` ниже)
> регистрируются в `main.py` раньше catch-all'а — FastAPI матчит маршруты по
> порядку регистрации, а не по специфичности, поэтому порядок важен.

### Возможности (capabilities)

Общий маршрут на каждое умение; доступен только агентам, у которых умение
объявлено в `capabilities` (иначе `404`). Тело — `multipart/form-data`, поле
файла — `file`.

| Метод  | Путь                          | Возможность | Ответ                       |
|--------|-------------------------------|-------------|-----------------------------|
| `POST` | `/agents/{agent_id}/ocr`      | `ocr`       | SSE (`token` + `[DONE]`)    |
| `POST` | `/agents/{agent_id}/documents`| `documents` | статус загрузки             |

Не входит в OpenAI-контракт и не тронуто переходом на него — вложения
(`multipart` → мультимодальные `content parts`) будут переработаны отдельным
этапом.

## Контракт агента

Мастер форвардит по контракту, а не по конкретному агенту, — поэтому каждый
contract-агент обязан реализовывать одинаковую форму API, совместимую с
OpenAI Chat Completions. Добавление нового агента, соблюдающего контракт,
требует только записи в `registry.py`.

Обязательная часть контракта:

- скоупинг всех ресурсов по `X-User-Id`; обращение к чужому ресурсу → `404`;
- `POST /v1/chat/completions` — **stateless**: клиент присылает всю историю
  диалога в `messages[]`, агент её не хранит и не переиспользует между
  вызовами. Формат ответа/стрима — `chat.completion` / `chat.completion.chunk`:

  ```
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{"content":"РАГ"},"finish_reason":null}]}
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
  data: [DONE]
  ```
  `id` (`chatcmpl-<uuid>`) — ключ, по которому дальше доступны повторное
  чтение, фидбэк и источники;

- ошибки — единый формат `{"error": {"message": "...", "type": "...", "param": null, "code": null}}`
  вместо FastAPI-дефолта `{"detail": ...}`;
- платформенные (не входящие в OpenAI-стандарт) расширения — необязательное
  поле `conversation_id` в `/v1/chat/completions` (привязка сообщения к чату
  для UI, не участвует в сборке контекста) и `/v1/platform/conversations`
  (CRUD чатов: создать, список, история, переименовать, удалить).

Полная спецификация ручек агента и схема БД — в README.md агента.

## Реестр агентов

`registry.py` — единственное место, где задаются агенты:

```python
@dataclass
class AgentInfo:
    id: str
    name: str
    url: str
    description: str
    enabled: bool = True
    transport: str = "contract"          # "contract" | "external" | "ocr"
    config: dict = field(default_factory=dict)


AGENTS: dict[str, AgentInfo] = {
    "epoz": AgentInfo(
        id="epoz",
        name="ЕПоЗ",
        url="http://127.0.0.1:8001",
        description="Закупки, ЕПоЗ",
    ),
    # ...
}
```

- `description` используется embedding- и llm-роутерами для классификации —
  чем содержательнее (с примерами запросов), тем точнее роутинг.
- `enabled=False` исключает агента из `/v1/models` и из роутинга.
- `transport` выбирает адаптер; у `contract` пустой `url` → `503`.
- `capabilities` — умения агента; проверяются на capability-маршрутах.
- `routable=False` убирает агента из кандидатов авто-роутинга (`model: "auto"`).
- `config` — пер-агентные переопределения дефолтов из `config.py`.

## Конфигурация

`config.py` (pydantic-settings) собирает платформенные дефолты: `HOST`/`PORT`,
лимиты исходящих соединений к агентам (важны под много параллельных SSE),
`FALLBACK_AGENT`. Переопределяются через `.env`.

## Обработка ошибок

Единый формат вместо FastAPI-дефолта `{"detail": ...}`:
```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": null}}
```
`type` — грубая классификация по HTTP-статусу: `400/413/415/422` →
`invalid_request_error`, `401` → `authentication_error`, `404` →
`not_found_error`, остальное → `server_error`.

Проксирование устроено так, чтобы различать сбои до и во время потока:

- агент **недоступен** (обрыв на уровне соединения, до получения заголовков) →
  `AgentUnavailable` → клиент получает чистый `502` в едином формате ошибок,
  поток не начат;
- агент **ответил** `4xx/5xx` — статус и тело агента идут насквозь без
  изменений (у агента тот же формат `{"error": {...}}`, пересобирать нечего);
- соединение оборвалось **в середине** уже начатого стрима → в поток
  дописывается `data: {"error": {"message": "...", "type": "server_error", ...}}`;
- обращение к возможности, которой у агента нет → `CapabilityNotSupported`,
  обработчик в `main.py` превращает в `404` в едином формате.

## Запуск

```bash
pip install -r requirements.txt

ollama pull gemma2:2b

touch .env
```
Пример заполнения `.env`
```
# --- сеть мастера ---
HOST=127.0.0.1
PORT=8000
TIMEOUT_KEEP_ALIVE=300

# --- исходящие к contract-агентам ---
AGENT_TIMEOUT=300
AGENT_VERIFY_TLS=false
AGENT_MAX_CONNECTIONS=200
AGENT_MAX_KEEPALIVE=40

# --- роутинг ---
FALLBACK_AGENT=chat

# --- models ---
OLLAMA_MODEL=gemma2:2b
EMBEDD_MODEL=intfloat/multilingual-e5-small
```
или
```
# --- сеть мастера ---
HOST=127.0.0.1
PORT=8000
TIMEOUT_KEEP_ALIVE=300

# --- исходящие к contract-агентам ---
AGENT_TIMEOUT=300
AGENT_VERIFY_TLS=false
AGENT_MAX_CONNECTIONS=200
AGENT_MAX_KEEPALIVE=40

# --- роутинг ---
FALLBACK_AGENT=chat

# --- models ---
OLLAMA_MODEL=qwen3.6:35b
EMBEDD_MODEL=BAAI/bge-m3
```
```bash
# запуск
python main.py
# или
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Мастер поднимается на `http://127.0.0.1:8000`. `timeout_keep_alive=300`
и `timeout=300` у httpx-клиента — под долгие SSE-стримы.

## Примеры запросов

```bash
U=11111111-1111-1111-1111-111111111111

# список доступных моделей (агентов)
curl http://127.0.0.1:8000/v1/models

# определить агента без реального вызова (debug)
curl -X POST http://127.0.0.1:8000/route \
  -H "Content-Type: application/json" \
  -d '{"message": "как подать заявку на тендер по 223-фз"}'
# {"agent": "epoz"}

# авто-роутинг + генерация, стримом
curl -N -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-User-Id: $U" \
  -d '{"model": "auto", "stream": true, "messages": [{"role": "user", "content": "что такое ЕПоЗ"}]}'
# в каждом chunk "model":"epoz" — это и есть выбор роутера, отдельно узнавать не нужно

# прямой вызов конкретного агента, без роутинга
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-User-Id: $U" \
  -d '{"model": "epoz", "messages": [{"role": "user", "content": "что такое ЕПоЗ"}]}'

ID=chatcmpl-1e6b7ee7-d5bb-4f0a-8f9e-a06f19a8f3c2

# фидбэк и источники — через generic proxy, {agent_id} = значению "model" из ответа
curl -X POST http://127.0.0.1:8000/agents/epoz/v1/chat/completions/$ID/feedback \
  -H "Content-Type: application/json" -H "X-User-Id: $U" -d '{"vote": 1}'
curl http://127.0.0.1:8000/agents/epoz/v1/chat/completions/$ID/sources -H "X-User-Id: $U"

# CRUD чатов — тоже generic proxy
curl -X POST http://127.0.0.1:8000/agents/epoz/v1/platform/conversations \
  -H "Content-Type: application/json" -H "X-User-Id: $U" -d '{"title": "Тестовый чат"}'
curl http://127.0.0.1:8000/agents/epoz/v1/platform/conversations -H "X-User-Id: $U"

# OCR: файл-в/текст-из, напрямую по id (без диалога), поле файла — file
curl -N -X POST http://127.0.0.1:8000/agents/ocr/ocr \
  -H "X-User-Id: $U" \
  -F "file=@scan.png"
# data: {"token": "распознанный текст"}
# data: [DONE]
```