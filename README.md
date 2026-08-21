# Master Router

Мастер-агент: по запросу пользователя определяет нужного ИИ-агента и
проксирует к нему запросы. Доступ к агентам — только через мастер. Мастер
поддерживает **обе** формы OpenAI на входе — Chat Completions
(`/v1/chat/completions`) и Responses (`/v1/responses`) — см. [Контракт
агента](#контракт-агента). На текущем этапе все агенты — локальные сервисы
на разных портах; разнородность транспорта спрятана за слоем адаптеров,
поэтому подключение агента с другим API не меняет мастер.

Мастер **stateless**: не хранит чаты, сообщения и фидбэк — всё это живёт у
агентов и скоупится по `X-User-Id`. Задача мастера — роутинг (`model: "auto"`),
выбор адаптера и прозрачное проксирование.

## Архитектура

```
                 ┌─────────────────────────────────────┐
   клиент  ─────▶│            Master Router            │
                 │                                     │
                 │  routing/   embedding → llm         │
                 │  adapters/  proxy(method, path, ...)│
                 │                                     │
   админ   ─────▶│  /admin/agents ──▶ registry_service │
                 │                     │  AGENTS       │
                 │                     │  EmbeddingIndex |
                 │                     │  кэш адаптеров│
                 │                     ▼               │
                 │                 agents.yaml         │
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

Реестр агентов — не константа в коде: он лежит в `agents.yaml` и правится
через `/admin/agents` на живом сервисе. `registry_service` применяет каждое
изменение атомарно сразу к трём состояниям — словарю агентов, индексу
эмбеддингов и кэшу адаптеров, — см. [Реестр агентов](#реестр-агентов).

## Роутинг

`MasterRouter.route()` первым делом строит `candidates` — множество
`{a.id for a in AGENTS.values() if a.enabled and a.routable}`. Это
единственное место, где `routable=False`/`enabled=False` реально влияют на
авто-роутинг — раньше (до правки) `embedding_router`/`llm_router` перебирали
весь реестр без разбора, и агент вроде OCR (`routable=False`) теоретически
мог быть выбран по семантическому совпадению, просто падая дальше в
`CapabilityNotSupported`. Сейчас исключён на первом же шаге.

Дальше — каскад из двух слоёв, каждый следующий запускается только если
предыдущий не дал ответа:

1. **embedding_router** — семантическая близость запроса к описаниям агентов
   через `intfloat/multilingual-e5-small` (косинусная мера, порог отсечения),
   вычисляется только среди `candidates`.
2. **llm_router** — классификация запроса моделью `gemma2:2b` через Ollama,
   ответ форсится в JSON; результат сверяется с тем же множеством кандидатов,
   что ушло в промпт — если модель «угадает» агента не из списка, ответ
   отбрасывается, а не пропускается по факту существования id в реестре.

Если ни один слой не дал валидного агента — `FALLBACK_AGENT` из
`config.py` (по умолчанию `chat`), без собственной проверки на
`routable`/`enabled` (единственное сознательное исключение — запасной
вариант обязан сработать всегда). Но и он не имеет права вывести за
пределы разрешённого множества: `MasterRouter.route()` принимает
необязательный `allowed` (например, только агенты с нужной
`contract_forms`), и если `FALLBACK_AGENT` в него не входит, берётся
первый по алфавиту из оставшихся кандидатов — иначе запрос ушёл бы
агенту, который заявленную форму не реализует. Блокирующие вызовы (`encode`, `client.chat`) вынесены в
`asyncio.to_thread`, чтобы не блокировать event loop.

Роутинг срабатывает только при `model: "auto"` (или отсутствии `model`) — не
отдельным эндпоинтом, и одинаково в обеих формах, `/v1/chat/completions` и
`/v1/responses`, — общий `MasterRouter`, разные экстракторы вопроса из тела
запроса на входе (`messages[-1].content` vs `input`).

**Вложение — отдельная, более жёсткая проверка**, идущая ДО семантического
роутинга, а не наравне с ним: если в текущем сообщении/item'е есть ссылка на
файл, `candidates` сразу сужается до агентов с `"attachments"` в
`capabilities` — файл однозначно требует конкретного умения, и агент без
него не рассматривается, как бы близко ни совпал текст.

Внутри получившегося множества дальше работает обычный каскад. Порядок в
`api/resolve.py`:

1. отсеять агентов без `"attachments"`, затем без `routable`;
2. остался один кандидат — вернуть его (обычный случай: `document_chat`);
3. кандидатов несколько и в запросе есть текст — отдать в тот же
   `MasterRouter.route(question, allowed=candidates)`;
4. кандидатов несколько, текста нет вообще — `sorted(candidates)[0]` плюс
   `logger.warning` со списком кандидатов.

Пункт 4 — единственный оставшийся вырожденный случай: файл прислали без
единого слова, роутить не по чему. Раньше `sorted()[0]` покрывал вообще все
случаи с вложением; при статическом реестре это было безопасно (агент с
`attachments` был ровно один), но с [админским
API](#админский-api-реестра) второго такого агента заводят одним `POST`, и
выбор молча уехал бы по алфавиту.

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

Значения `transport` — закрытый список (`Literal` в `registry.py`), а не
свободная строка: опечатка в реестре должна падать на валидации, а не
доходить до `ValueError` в фабрике адаптеров. Новый транспорт (например
`cognitum`, если платформа переедет на оркестратор) добавляется двумя
правками — в `Transport` и в `_ADAPTER_CLASSES`.

`proxy()` возвращает `ProxyResult(status, content_type, body: AsyncIterator[bytes])`.
Статус и content-type апстрима известны сразу — httpx получает заголовки
раньше тела, — поэтому мастер выставляет правильный статус-код и media-type
ответа ещё до того, как начнёт вычитывать `body`. Один и тот же механизм
одинаково обслуживает и потоковый SSE-ответ (`/v1/chat/completions` со
`stream: true`), и обычный одиночный JSON (`GET .../feedback`,
`/v1/platform/conversations`, ...) — разделять их не нужно. Добавление
агента, укладывающегося в контракт, — один `POST /admin/agents`, без
рестарта мастера; число маршрутов
мастера не зависит ни от числа агентов, ни от числа ручек в их контракте (см.
[Generic proxy](#generic-proxy-агента) ниже).

## Ядро контракта и возможности

Контракт делится на **ядро** (обязательное для всех адаптеров) и
**возможности** (capabilities — необязательные умения, есть не у каждого).

Ядро — единственный `@abstractmethod` в `base.py`: `proxy()`. Реализует любой
адаптер, но по-разному: OCR честно отвечает `404` на любой путь (агент не
реализует `/v1/chat/completions` вообще — это не забытая функциональность, а
осознанное «не умею»), `ExternalAPIAdapter` — `NotImplementedError` (скелет).

Возможности — отдельные необязательные методы с дефолтом «не умею»
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
старого `GET /agents`) — это осознанное решение под OpenAI-совместимость
объекта `model`; если фронту нужны capabilities агента для UI, это отдельный
вопрос вне текущего контракта.

## Эндпоинты мастера

Все проксирующие эндпоинты требуют идентификатор пользователя и
пробрасывают его агенту в `X-User-Id` без изменений. Принимается два
способа передачи: заголовок `X-User-Id: <uuid>` (основной, приоритетный)
и `Authorization: Bearer <uuid>` — второй нужен, чтобы официальный
OpenAI SDK подключался одной сменой `base_url`, без
`default_headers`. Ни того, ни другого — `401`. Статус-коды и тела ответов агента
проходят насквозь (включая `204 No Content` и `404` на чужой ресурс) — мастер
их не переинтерпретирует.

### Служебные

| Метод  | Путь         | Назначение                                        |
|--------|--------------|----------------------------------------------------|
| `GET`  | `/v1/models` | Список включённых агентов как OpenAI-моделей + `auto` |
| `GET`  | `/v1/models/{model_id}` | Один агент как объект `model`; неизвестный/выключенный → `404` |
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
  эхом в `model` ответа клиенту (агент просто эхует, что получил). Это
  единственный канал, которым клиент узнаёт, кого выбрал роутер: отдельных
  заголовков (`X-Agent-Id`) или SSE-события `metadata`, как раньше, больше
  нет — `model` ответа и есть искомый `agent_id`, его же клиент использует
  дальше как `{agent_id}` в путях ниже.

Стрим/нестрим-форматы ответа — как в [контракте агента](#контракт-агента),
мастер их не переписывает, только проксирует байты как есть (тем же `proxy()`,
что и всё остальное).

### `POST /v1/responses`

Второй вход для генерации, форма Responses API. Работает так же, как
`/v1/chat/completions` (тот же `MasterRouter`, та же подстановка реального
`agent_id` в `model` при `"auto"`, тот же байтовый `proxy()`), но с
принципиальным отличием: **никакого неявного перевода между формами**.

```json
{
  "model": "auto",
  "input": "что такое ЕПоЗ",
  "stream": true
}
```

- `model` — конкретный `agent_id`, у которого в `contract_forms` заявлено
  `"responses"` → проброс. Если у агента этой формы нет — `400`
  (`"Агент epoz не поддерживает форму Responses API"`), а не попытка
  подставить его через `/v1/chat/completions` за него. Симметричная
  проверка есть и в `/v1/chat/completions`.
- `model: "auto"` → кандидаты — агенты с `"responses"` в `contract_forms`
  (и `routable`/`enabled`, как обычно); если ни одного — `400`, не тихий
  выбор чего попало.
- Вложение в `input` (`{"type": "input_file", ...}`) — та же логика, что и
  у Chat Completions: сужает кандидатов до `"attachments"` в `capabilities`,
  причём **на пересечении** с `"responses"` в `contract_forms` — агенту
  нужны оба флага одновременно, чтобы его выбрал `"auto"` с вложением в
  этой форме.

Это разделение форм — сознательный принцип, а не недоделка: мастер не
должен домысливать за агента, какую форму тот «имел в виду». Если агенту
нужна и Chat Completions, и Responses — это две отдельные записи флагов в
`contract_forms`, обе реализованные агентом реально, не одна за другую.

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
GET    /agents/{agent_id}/v1/responses/{completion_id}
POST   /agents/{agent_id}/v1/platform/conversations
GET    /agents/{agent_id}/v1/platform/conversations
GET    /agents/{agent_id}/v1/platform/conversations/{conversation_id}/messages
PATCH  /agents/{agent_id}/v1/platform/conversations/{conversation_id}
DELETE /agents/{agent_id}/v1/platform/conversations/{conversation_id}
```

`GET .../v1/responses/{completion_id}` — тот же ресурс, что и
`GET .../v1/chat/completions/{completion_id}`, просто сериализован в форме
`response`, а не `chat.completion` (у агентов, реализующих обе формы — id
принимается в любом виде, `chatcmpl-`/`resp_`/голый UUID). Фидбэк и источники
остаются под одним, общим для обеих форм путём — не дублируются под
`/v1/responses/...`, см. документацию агента.

Список выше — не то, что знает мастер (он его не хранит и не валидирует
детально), а то, что сейчас реализует эталонный агент epoz. Если epoz
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

Не входит в OpenAI-контракт — единственная capability-ручка мастера,
пережившая переход, специально для инструментов без диалога (сейчас — OCR).

`capabilities` агента используется в двух ролях, которые легко перепутать:
- как источник для **capability-маршрутов выше** (`ocr` → `/agents/{id}/ocr`);
- как **фильтр для авто-роутинга** при вложениях (`attachments` →
  агент принимает `file`/`input_file` части в `messages`/`input`, см.
  «Роутинг» выше) — это НЕ маршрут, просто флаг, который смотрит роутер.

У `document_chat` в реестре — `capabilities={"chat", "attachments"}`, не
`{"chat", "documents", "ocr"}`, как было раньше: те два флага были
пережитком старого контракта (single-shot ручка `/agents/{id}/documents`,
которой ни у кого нет) и вводили в заблуждение — `require_capability`
пропускал бы вызов дальше, а адаптер честно падал в `CapabilityNotSupported`.

## Контракт агента

Мастер форвардит по контракту, а не по конкретному агенту, — поэтому каждый
contract-агент обязан реализовывать одинаковую форму API для каждой из
форм, которую заявляет в `contract_forms`. Добавление нового агента,
соблюдающего контракт, требует только записи в реестре — `POST /admin/agents`
либо правки `agents.yaml`.

Обязательная часть контракта (единая для обеих форм OpenAI, различается
только в деталях самой формы — путь, тело запроса, форма стрима):

- скоупинг всех ресурсов по `X-User-Id`; обращение к чужому ресурсу → `404`;
- `POST /v1/chat/completions` и/или `POST /v1/responses` — **stateless**:
  клиент присылает всю историю диалога в `messages[]`/`input`, агент её не
  хранит и не переиспользует между вызовами (единственное исключение —
  необязательный `conversation_id`, платформенное расширение, см. ниже, и
  то только в форме Responses, где агент может подтягивать текстовую
  историю из своей БД). Формат ответа/стрима: `chat.completion` /
  `chat.completion.chunk` для Chat Completions,

  ```
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{"content":"РАГ"},"finish_reason":null}]}
  data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<ts>,"model":"epoz","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
  data: [DONE]
  ```
  типизированные SSE-события (`response.created`, `response.output_text.delta`,
  `response.completed`, ...) для Responses. `id` (`chatcmpl-<uuid>` /
  `resp_<uuid>`) — ключ, по которому дальше доступны повторное чтение,
  фидбэк и источники;

- ошибки — единый формат `{"error": {"message": "...", "type": "...", "param": null, "code": null}}`
  вместо FastAPI-дефолта `{"detail": ...}`;
- платформенные (не входящие ни в одну спеку OpenAI) расширения —
  необязательное поле `conversation_id` в обеих формах (привязка сообщения
  к чату для UI; в форме Responses дополнительно — источник истории при
  сборке контекста) и `/v1/platform/conversations` (CRUD чатов: создать,
  список, история, переименовать, удалить), общий для обеих форм.

Полная спецификация ручек агента и схема БД — в документации агента
(`epoz/README.md`, `epoz/architecture_target.md`).

## Реестр агентов

Реестр живёт в `agents.yaml` рядом с `main.py`, а не в коде. Управляется
[админским API](#админский-api-реестра) — изменения применяются на лету,
**без перезапуска мастера**.

```yaml
version: 1
agents:
- id: epoz
  name: ЕПоЗ
  url: http://127.0.0.1:8001
  transport: contract
  enabled: true
  routable: true
  capabilities:
  - chat
  contract_forms:
  - chat_completions
  - responses
  config: {}
  description: |
    Единое положение о закупках Государственной корпорации Ростех.
    Закупочная деятельность, закупочные процедуры, тендеры, конкурсы...

    Примеры запросов:
    - Какой способ закупки необходимо выбрать?
```

- `id` — `^[a-z][a-z0-9_]{0,63}$`. Неизменяем: это же значение уходит в поле
  `model` OpenAI-контракта, и «переименование» агента — это удаление одного
  и создание другого, а не правка.
- `description` используется embedding- и llm-роутерами для классификации —
  чем содержательнее (с примерами запросов), тем точнее роутинг.
- `enabled=false` исключает агента из `/v1/models` и из роутинга.
- `transport` выбирает адаптер; при `contract`/`ocr` пустой `url` не
  проходит валидацию.
- `capabilities` — умения агента; часть используется как capability-маршруты
  (`ocr`), часть — как фильтр для роутинга при вложениях (`attachments`),
  см. «Возможности (capabilities)» выше — это не взаимозаменяемые списки.
- `routable=false` убирает агента из кандидатов авто-роутинга (`model: "auto"`).
- `contract_forms` — какую(ие) форму(ы) генерации агент реализует. Мастер не
  переводит между ними сам — агент без нужной формы в `POST /v1/{форма}`
  получает `400`, а не подмену. Пустой список означает, что агент вообще не
  участвует в генерации и не показывается в `/v1/models` (случай `ocr`).
- `config` — пер-агентные переопределения дефолтов из `config.py`.

### Как устроено внутри

| Модуль | Роль |
|---|---|
| `registry.py` | модель `AgentInfo`, инварианты, чтение/запись `agents.yaml`, словарь `AGENTS` |
| `registry_service.py` | диф, пересчёт векторов, инвалидация адаптеров, CRUD, `reload` |
| `routing/embedding_router.py` | `EmbeddingIndex` с `upsert`/`remove` вместо статического `agent_vectors` |
| `adapters/factory.py` | `invalidate(agent_id)` — выбросить адаптер из кэша |

Разделение `registry.py` / `registry_service.py` не косметическое: сервису
нужны и `embedding_router`, и `factory`, а обоим нужен `registry` — в одном
модуле это дало бы цикл импортов.

Нюансы, которые ломаются молча, если их не соблюсти:

- **`AGENTS` мутируется на месте, имя не пересвязывается.** Шесть модулей
  делают `from registry import AGENTS` и держат ссылку на конкретный объект
  словаря. `registry.AGENTS = new_dict` привёл бы к тому, что часть модулей
  продолжила бы смотреть в старый словарь — например, `/v1/models` показывал
  бы нового агента, а роутинг его не видел.
- **Применение дифа полностью синхронное.** Всё долгое (кодирование описания,
  запись файла) выполняется до него, в threadpool. Один `await` внутри — и
  параллельный запрос поймает `RuntimeError: dictionary changed size during
  iteration` на обходе `AGENTS.values()`.
- **`AgentInfo` неизменяем** (`frozen=True`), при обновлении в словарь
  кладётся новый объект. Запрос, уже взявший ссылку на старый, спокойно
  доработает на нём — блокировки не нужны.
- **Вектор пересчитывается только при смене описания, адаптер пересоздаётся
  только при смене `url`/`transport`/`config`.** Иначе правка текста описания
  каждый раз убивала бы httpx-клиент с живым пулом соединений.
- **Старый адаптер закрывается с задержкой** (`AGENT_TIMEOUT`), а не сразу:
  через него в момент подмены может идти чей-то SSE-стрим. Новые запросы
  получают новый адаптер немедленно.
- **Описание нормализуется** (`" ".join(text.split())`) перед сравнением и
  кодированием — иначе переформатирование YAML руками считалось бы
  изменением и триггерило пересчёт вектора.

Файл читается на импорте `registry.py`, то есть до того, как его импортирует
любой другой модуль. Битый `agents.yaml` роняет процесс на старте — ровно как
раньше это делала синтаксическая ошибка в `registry.py`.

### Инварианты

Проверяются и при загрузке файла, и на каждом запросе к админскому API:

- `transport` ∈ `{contract, external, ocr}`, `contract_forms` ⊆
  `{chat_completions, responses}`, `capabilities` ⊆ `{chat, ocr, attachments}` —
  закрытые списки. Прошлогодняя опечатка `["chat_completions, responses"]`
  (две строки, написанные одной) на них падает с `400`, а не выключает
  роутинг молча;
- `url` обязателен при `transport` `contract` и `ocr`;
- `attachments` требует `chat` в `capabilities`;
- `routable=true` требует непустых `description` и `contract_forms` — иначе
  агент попадёт в кандидаты роутинга с мусорным вектором;
- `FALLBACK_AGENT` обязан существовать, быть `enabled` и `routable`. Его
  удаление или выключение через API → `409`; нарушение на старте → отказ
  подниматься.

### Админский API реестра

Живёт под `/admin`, отдельным тегом, в OpenAI-контракт не входит и наружу
вендорам не показывается.

| Метод | Путь | Поведение |
|---|---|---|
| `GET` | `/admin/agents` | весь реестр как есть, включая выключенных |
| `GET` | `/admin/agents/{id}` | один агент; нет → `404` |
| `POST` | `/admin/agents` | `201`; дубль `id` → `409`; невалидная схема → `400` |
| `PATCH` | `/admin/agents/{id}` | частичное обновление; `id` в теле запрещён |
| `DELETE` | `/admin/agents/{id}` | `200`; снос `FALLBACK_AGENT` → `409` |
| `POST` | `/admin/registry/reload` | перечитать `agents.yaml` с диска |
| `GET` | `/admin/registry` | статус: путь к файлу, `version`, счётчики, состав индекса и кэша адаптеров |

`PATCH` использует `exclude_unset=True`: не переданные поля сохраняют текущие
значения, а не затираются в `null`.

Мутирующие ручки возвращают не только объект, но и что именно применилось —
по этому блоку сразу видно, пересчитался ли вектор и пересоздался ли адаптер:

```json
{
  "agent": { "id": "metrology", "...": "..." },
  "applied": {
    "version": 7,
    "added": ["metrology"],
    "updated": [],
    "removed": [],
    "embeddings_recomputed": ["metrology"],
    "adapters_invalidated": []
  }
}
```

`version` — монотонный счётчик применённых изменений. Сейчас нужен для
отладки; на нём же будет строиться синхронизация между инстансами, если
реестр переедет в Postgres.

`POST /admin/registry/reload` оставлен несмотря на наличие CRUD — это путь
для случая, когда `agents.yaml` приехал мимо API, например с git-деплоем.
Битый файл при `reload` даёт `400`, а реестр в памяти остаётся целым.

Запись в файл атомарная (временный файл в той же директории плюс
`os.replace`) и сериализована через `asyncio.Lock` — два параллельных `POST`
не затрут друг друга, а оборванная запись не оставит битый реестр,
который не переживёт рестарт.

**Защита пока не включена.** Роутер висит на `Depends(require_admin)` из
`auth.py`, но функция сейчас пускает всех — это заглушка и точка расширения
под Keycloak. Включение защиты потребует правки только тела этой функции,
не API. До этого момента `/admin` не должен торчать наружу.

**`DELETE` удаляет физически.** Мастер не хранит разговоры — они в БД самих
агентов, — но клиент, у которого во фронте зашит `model: "epoz"`, после
удаления получит `404` на следующем же сообщении. Для временного вывода
агента правильный инструмент — `PATCH {"enabled": false}`.

> На текущем этапе реально запущены **ЕПоЗ**, **slave_chat** (`id: "chat"`),
> **document_chat**, **tech_rag** и **OCR**. Агентам, чьи сервисы не подняты,
> имеет смысл выставить `enabled: false`, — иначе роутер может выбрать
> агента без рабочего `url`.

## Конфигурация

`config.py` (pydantic-settings) собирает платформенные дефолты: `HOST`/`PORT`,
лимиты исходящих соединений к агентам (важны под много параллельных SSE),
`FALLBACK_AGENT`, `AGENTS_FILE`. Переопределяются через `.env`.

`AGENTS_FILE` — путь к реестру, по умолчанию `agents.yaml`. Относительный путь
резолвится от директории `registry.py`, а не от текущей рабочей: иначе
`python main.py` из корня и `python -m unittest discover -s tests -t .` видели
бы разные файлы. Абсолютный путь пригодится, если реестр должен жить вне
репозитория — например на volume, чтобы правки через API переживали
пересборку образа.

## Обработка ошибок

Единый формат вместо FastAPI-дефолта `{"detail": ...}`:
```json
{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": null}}
```
`type` — грубая классификация по HTTP-статусу: `400/409/413/415/422` →
`invalid_request_error`, `401` → `authentication_error`, `404` →
`not_found_error`, остальное → `server_error`.

Невалидное тело запроса — **`400`**, а не `422`: OpenAI отвечает на такие
запросы именно `400`, и SDK мапит `422` в `UnprocessableEntityError`, мимо
клиентского `except BadRequestError`. `param` заполняется путём до
проблемного поля (`messages.0.role`), а не оставляется `null`.

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

# --- реестр ---
AGENTS_FILE=agents.yaml

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

# --- реестр ---
AGENTS_FILE=agents.yaml

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

# ============ форма Responses ============

# авто-роутинг + генерация, стримом — тот же MasterRouter, что и у Chat Completions
curl -N -X POST http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" -H "X-User-Id: $U" \
  -d '{"model": "auto", "stream": true, "input": "что такое ЕПоЗ"}'

# у агента без формы responses -> 400, без подмены формы
curl -X POST http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" -H "X-User-Id: $U" \
  -d '{"model": "ocr", "input": "привет"}'
# -> {"error": {"message": "Агент ocr не поддерживает форму Responses API", ...}}

# вложение при auto — роутер вообще не участвует, кандидаты сразу сужены
# до agent.capabilities содержит "attachments" (сейчас — только document_chat)
curl -X POST http://127.0.0.1:8000/v1/responses \
  -H "Content-Type: application/json" -H "X-User-Id: $U" \
  -d '{"model": "auto", "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "какая сумма в накладной?"},
        {"type": "input_file", "file_id": "file-85b365de-..."}
      ]}]}'
# в ответе "model":"document_chat" — единственный кандидат с attachments+responses

# OCR: файл-в/текст-из, напрямую по id (без диалога), поле файла — file
curl -N -X POST http://127.0.0.1:8000/agents/ocr/ocr \
  -H "X-User-Id: $U" \
  -F "file=@scan.png"
# data: {"token": "распознанный текст"}
# data: [DONE]
```
### Управление реестром

```bash
# состояние реестра: файл, версия, состав индекса и кэша адаптеров
curl -s http://127.0.0.1:8000/admin/registry | python -m json.tool

# весь реестр, включая выключенных агентов
curl -s http://127.0.0.1:8000/admin/agents | python -m json.tool

# добавить агента — применяется сразу, рестарт не нужен
curl -X POST http://127.0.0.1:8000/admin/agents \
  -H "Content-Type: application/json" \
  -d '{
    "id": "metrology",
    "name": "Метрология",
    "url": "http://127.0.0.1:8009",
    "description": "Поверка приборов, средства измерений, метрология.\n\nПримеры запросов:\n- Как поверить манометр?",
    "contract_forms": ["chat_completions", "responses"]
  }'
# {"agent": {...}, "applied": {"version": 2, "added": ["metrology"],
#  "embeddings_recomputed": ["metrology"], "adapters_invalidated": []}}

# он уже в списке моделей и участвует в роутинге
curl -s http://127.0.0.1:8000/v1/models | python -m json.tool
curl -X POST http://127.0.0.1:8000/route \
  -H "Content-Type: application/json" -d '{"message": "как поверить манометр"}'

# правка описания -> пересчёт вектора, адаптер не трогается
curl -X PATCH http://127.0.0.1:8000/admin/agents/metrology \
  -H "Content-Type: application/json" \
  -d '{"description": "Метрология, поверка и калибровка средств измерений"}'
# "embeddings_recomputed": ["metrology"], "adapters_invalidated": []

# смена url -> пересоздание адаптера, вектор не трогается
curl -X PATCH http://127.0.0.1:8000/admin/agents/metrology \
  -H "Content-Type: application/json" -d '{"url": "http://127.0.0.1:8010"}'
# "embeddings_recomputed": [], "adapters_invalidated": ["metrology"]

# временно вывести агента из строя, не удаляя
curl -X PATCH http://127.0.0.1:8000/admin/agents/metrology \
  -H "Content-Type: application/json" -d '{"enabled": false}'

# удалить
curl -X DELETE http://127.0.0.1:8000/admin/agents/metrology

# перечитать agents.yaml с диска (файл приехал с деплоем мимо API)
curl -X POST http://127.0.0.1:8000/admin/registry/reload

# fallback-агента снести нельзя
curl -X DELETE http://127.0.0.1:8000/admin/agents/chat
# 409 {"error": {"message": "агент chat назначен fallback_agent — ...", ...}}
```