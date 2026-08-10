# Curl-тесты платформы (через master_node)

Сквозной набор проверок всего, что реализовано в переходе на OpenAI-
совместимый контракт: **epoz**, **tech_rag**, **slave_chat** (`model: "chat"`),
**document_agent** (`model: "document_chat"`) и **ocr** (не мигрирован,
проверяется отдельной capability-ручкой). Все запросы идут **через
мастер**, не напрямую в агентов — так, как будет работать реальный клиент.

Агенты обращаются друг к другу по `id` из `registry.py`.

## Подготовка

```bash
MASTER=http://127.0.0.1:8000
U=11111111-1111-1111-1111-111111111111
```

Каждый раздел ниже самодостаточен по своему агенту, но использует
переменные окружения (`CID`, `FID`, `ID`), заполняемые вручную из ответа
предыдущего запроса — тем же способом, что и в README отдельных агентов:
скопировать значение поля `id` из напечатанного JSON в следующую команду.

---

## 1. Служебное (мастер)

### Список доступных моделей (агентов)
```bash
curl $MASTER/v1/models
```
Ожидается: `epoz`, `chat`, `document_chat`, `tech_rag`, `auto` (плюс всё, что у вас
`enabled=True` в реестре).

### Debug-роутинг — какого агента выбрал бы `model: "auto"`
```bash
curl -X POST $MASTER/route \
  -H "Content-Type: application/json" \
  -d '{"message": "как подать заявку на тендер по 223-фз"}'
# -> {"agent": "epoz"}

curl -X POST $MASTER/route \
  -H "Content-Type: application/json" \
  -d '{"message": "расскажи анекдот"}'
# -> {"agent": "chat"}
```

---

## 2. epoz — RAG-агент

### 2.1 Генерация, авто-роутинг, стримом
```bash
curl -N -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "auto", "stream": true, "messages": [{"role": "user", "content": "что такое закупка"}]}'
```
В каждом чанке `"model":"epoz"` — авто-роутинг выбрал epoz, это видно прямо
в ответе, отдельно узнавать не нужно.

### 2.2 Генерация, прямой вызов, нестрим
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "epoz", "messages": [{"role": "user", "content": "что такое Меры ограничительного характера"}]}'
```
```json
{"id": "chatcmpl-...", "object": "chat.completion", "choices": [...], "usage": {...}}
```

### 2.3 Продолжение диалога — история целиком в теле
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "epoz", "stream": true, "messages": [
        {"role": "user", "content": "что такое закупка"},
        {"role": "assistant", "content": "закупка — это процесс"},
        {"role": "user", "content": "а как провести закупку"}
      ]}'
```

```bash
ID=chatcmpl-1e6b7ee7-d5bb-4f0a-8f9e-a06f19a8f3c2   # id из любого ответа выше
```

### 2.4 Получить ответ повторно по id
```bash
curl $MASTER/agents/epoz/v1/chat/completions/$ID -H "X-User-Id: $U"
```

### 2.5 Источники (только у epoz — RAG-специфика)
```bash
curl $MASTER/agents/epoz/v1/chat/completions/$ID/sources -H "X-User-Id: $U"
# -> {"id": "...", "retrieved": [...], "used_sources": [...]}
```

### 2.6 Фидбэк — `vote`/`comment`
```bash
curl -X POST $MASTER/agents/epoz/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1, "comment": "Хороший ответ"}'

curl $MASTER/agents/epoz/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"

curl -X DELETE $MASTER/agents/epoz/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
```

### 2.7 Чаты — `/v1/platform/conversations` (полный CRUD)
```bash
# создать
curl -X POST $MASTER/agents/epoz/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Тестовый чат epoz"}'
```
```bash
CID=3fa85f64-5717-4562-b3fc-2c963f66afa6   # id из ответа выше
```
```bash
# сообщение внутри чата — conversation_id привязывает запись к нему
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"epoz\", \"conversation_id\": \"$CID\", \"messages\": [{\"role\": \"user\", \"content\": \"что такое РАГ\"}]}"

# список чатов
curl $MASTER/agents/epoz/v1/platform/conversations -H "X-User-Id: $U"

# история сообщений чата
curl $MASTER/agents/epoz/v1/platform/conversations/$CID/messages -H "X-User-Id: $U"

# переименовать
curl -X PATCH $MASTER/agents/epoz/v1/platform/conversations/$CID \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Новое название"}'

# удалить
curl -X DELETE $MASTER/agents/epoz/v1/platform/conversations/$CID -H "X-User-Id: $U"
```

---

## 3. tech_rag — RAG-агент

### 3.1 Генерация, авто-роутинг, стримом
```bash
curl -N -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "auto", "stream": true, "messages": [{"role": "user", "content": "что такое Аэродинамика"}]}'
```
В каждом чанке `"model":"tech_rag"` — авто-роутинг выбрал tech_rag, это видно прямо
в ответе, отдельно узнавать не нужно.

### 3.2 Генерация, прямой вызов, нестрим
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "tech_rag", "messages": [{"role": "user", "content": "что такое метод конечных разностей"}]}'
```
```json
{"id": "chatcmpl-...", "object": "chat.completion", "choices": [...], "usage": {...}}
```

### 3.3 Продолжение диалога — история целиком в теле
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "tech_rag", "messages": [
        {"role": "user", "content": "что такое аэродинамика"},
        {"role": "assistant", "content": "аэродинамика — это раздел механики сплошных сред"},
        {"role": "user", "content": "что такое аэродинамика"}
      ]}'
```

```bash
ID=chatcmpl-1e6b7ee7-d5bb-4f0a-8f9e-a06f19a8f3c2   # id из любого ответа выше
```

### 3.4 Получить ответ повторно по id
```bash
curl $MASTER/agents/tech_rag/v1/chat/completions/$ID -H "X-User-Id: $U"
```

### 3.5 Источники (только у tech_rag — RAG-специфика)
```bash
curl $MASTER/agents/tech_rag/v1/chat/completions/$ID/sources -H "X-User-Id: $U"
# -> {"id": "...", "retrieved": [...], "used_sources": [...]}
```

### 3.6 Фидбэк — `vote`/`comment`
```bash
curl -X POST $MASTER/agents/tech_rag/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1, "comment": "Хороший ответ"}'

curl $MASTER/agents/tech_rag/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"

curl -X DELETE $MASTER/agents/tech_rag/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
```

### 3.7 Чаты — `/v1/platform/conversations` (полный CRUD)
```bash
# создать
curl -X POST $MASTER/agents/tech_rag/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Тестовый тех чат"}'
```
```bash
CID=3fa85f64-5717-4562-b3fc-2c963f66afa6   # id из ответа выше
```
```bash
# сообщение внутри чата — conversation_id привязывает запись к нему
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"tech_rag\", \"conversation_id\": \"$CID\", \"messages\": [{\"role\": \"user\", \"content\": \"что такое РАГ\"}]}"

# список чатов
curl $MASTER/agents/tech_rag/v1/platform/conversations -H "X-User-Id: $U"

# история сообщений чата
curl $MASTER/agents/tech_rag/v1/platform/conversations/$CID/messages -H "X-User-Id: $U"

# переименовать
curl -X PATCH $MASTER/agents/tech_rag/v1/platform/conversations/$CID \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Новое название"}'

# удалить
curl -X DELETE $MASTER/agents/tech_rag/v1/platform/conversations/$CID -H "X-User-Id: $U"
```

---

## 4. slave_chat — общий чат-агент (`model: "chat"`)

Без RAG — нет `/sources`;

### 4.1 Генерация, стримом
```bash
curl -N -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "chat", "stream": true, "messages": [{"role": "user", "content": "расскажи анекдот"}]}'
```

```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "chat", "stream": true, "messages": [
        {"role": "user", "content": "привет, я пользователь"},
        {"role": "assistant", "content": "привет, чем могу помочь"},
        {"role": "user", "content": "кто я"}
      ]}'
```

### 4.2 Генерация, нестрим
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "chat", "messages": [{"role": "user", "content": "привет"}]}'
```

```bash
ID=chatcmpl-2f7c8ee8-e6cc-5f1b-9g0f-b17g20b9g4d3   # id из ответа выше
```

### 4.3 Получить ответ повторно по id
```bash
curl $MASTER/agents/chat/v1/chat/completions/$ID -H "X-User-Id: $U"
```

### 4.4 Фидбэк — произвольный payload, повторный `POST` мёржит ключи
```bash
curl -X POST $MASTER/agents/chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1}'

curl -X POST $MASTER/agents/chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"comment": "смешно"}'
# -> payload: {"vote": 1, "comment": "смешно"}

curl $MASTER/agents/chat/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
curl -X DELETE $MASTER/agents/chat/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
```

### 4.5 Чаты — `/v1/platform/conversations` (полный CRUD)
```bash
curl -X POST $MASTER/agents/chat/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Тестовый чат"}'
```
```bash
CID=4gb96g75-6828-5673-c4gd-3d074g77bgb7   # id из ответа выше
```
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"chat\", \"conversation_id\": \"$CID\", \"messages\": [{\"role\": \"user\", \"content\": \"привет\"}]}"

curl $MASTER/agents/chat/v1/platform/conversations -H "X-User-Id: $U"
curl $MASTER/agents/chat/v1/platform/conversations/$CID/messages -H "X-User-Id: $U"

curl -X PATCH $MASTER/agents/chat/v1/platform/conversations/$CID \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Новое название"}'

curl -X DELETE $MASTER/agents/chat/v1/platform/conversations/$CID -H "X-User-Id: $U"
```

---

## 5. document_chat — документ + LLM (`model: "document_chat"`)

Единственный агент с вложениями — свой `/v1/files` (OpenAI Files API),
подключается к вопросу явной ссылкой `file_id`, без памяти между ходами.

### 5.1 Загрузить документ
```bash
curl -X POST $MASTER/agents/document_chat/v1/files \
  -H "X-User-Id: $U" -F "file=@test.pdf;type=application/pdf"
```
```json
{"id": "file-85b365de-...", "object": "file", "bytes": 245678, "filename": "накладная.pdf", "status": "done", ...}
```
```bash
FID=file-85b365de-1234-4c7d-8e9f-0a1b2c3d4e5f   # id из ответа выше
```

### 5.2 Файлы — список / получить / удалить (CRUD)
```bash
curl $MASTER/agents/document_chat/v1/files -H "X-User-Id: $U"
curl $MASTER/agents/document_chat/v1/files/$FID -H "X-User-Id: $U"
# удаление — в конце раздела 5.7, после того как файл больше не нужен
```

### 5.3 Вопрос по документу
```bash
curl -N -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"stream\": true, \"messages\": [
        {\"role\": \"user\", \"content\": [
          {\"type\": \"text\", \"text\": \"Про что документ?\"},
          {\"type\": \"file\", \"file\": {\"file_id\": \"$FID\"}}
        ]}
      ]}"
```

### 5.4 Продолжение по тому же документу — `file_id` передаётся снова
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"messages\": [
        {\"role\": \"user\", \"content\": [
          {\"type\": \"text\", \"text\": \"Какая сумма в накладной?\"},
          {\"type\": \"file\", \"file\": {\"file_id\": \"$FID\"}}
        ]},
        {\"role\": \"assistant\", \"content\": \"В накладной сумма 1000 руб.\"},
        {\"role\": \"user\", \"content\": [
          {\"type\": \"text\", \"text\": \"А дата какая?\"},
          {\"type\": \"file\", \"file\": {\"file_id\": \"$FID\"}}
        ]}
      ]}"
```

### 5.5 Вопрос без документа (проверка, что file_id — не обязателен)
```bash
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "document_chat", "messages": [{"role": "user", "content": "От какого числа документ?"}]}'
```

```bash
ID=chatcmpl-5hc07h86-7939-6784-d5he-4e185h88chc8   # id из любого ответа 4.3-4.5
```

### 5.6 Получить ответ повторно по id
```bash
curl $MASTER/agents/document_chat/v1/chat/completions/$ID -H "X-User-Id: $U"
```

### 5.7 Фидбэк — `vote`/`comment`, как у epoz
```bash
curl -X POST $MASTER/agents/document_chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1, "comment": "точно"}'
curl $MASTER/agents/document_chat/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
curl -X DELETE $MASTER/agents/document_chat/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"

# файл больше не нужен — удаляем (проверка DELETE из 5.2)
curl -X DELETE $MASTER/agents/document_chat/v1/files/$FID -H "X-User-Id: $U"
```

### 5.8 Чаты — `/v1/platform/conversations` (полный CRUD)
```bash
curl -X POST $MASTER/agents/document_chat/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Накладная"}'
```
```bash
CID=6id18i97-8040-7895-e6if-5f296i99did9   # id из ответа выше
```
```bash
curl $MASTER/agents/document_chat/v1/platform/conversations -H "X-User-Id: $U"
curl $MASTER/agents/document_chat/v1/platform/conversations/$CID/messages -H "X-User-Id: $U"

curl -X PATCH $MASTER/agents/document_chat/v1/platform/conversations/$CID \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Накладная №1"}'

curl -X DELETE $MASTER/agents/document_chat/v1/platform/conversations/$CID -H "X-User-Id: $U"
```

---

## 6. ocr — файл-в/текст-из (НЕ мигрирован на новый контракт)

Отдельная capability-ручка, не `/v1/chat/completions` — намеренно, у OCR
нет диалога (см. обсуждение в epoz/architecture_target.md о том, почему
этот агент не приводится к OpenAI-контракту). Без `X-User-Id` — сервис пока
не привязан к пользователю платформы.

```bash
curl -N -X POST $MASTER/agents/ocr/ocr \
  -H "X-User-Id: $U" \
  -F "file=@scan.png"
# data: {"token": "распознанный текст"}
# data: [DONE]
```

---

## 7. Ошибки — единый формат `{"error": {...}}`

```bash
# без X-User-Id -> 401 (эндпоинты мастера/агентов, требующие пользователя)
curl -X POST $MASTER/v1/chat/completions \
  -H "Content-Type: application/json" -d '{"model": "epoz", "messages": [{"role": "user", "content": "привет"}]}'
# -> {"error": {"message": "...", "type": "authentication_error", "param": null, "code": null}}

# неизвестный агент -> 404
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "no_such_agent", "messages": [{"role": "user", "content": "привет"}]}'
# -> {"error": {"message": "Агент no_such_agent не найден или выключен", "type": "not_found_error", "param": null, "code": null}}

# путь вне контракта агента (не начинается с v1/) -> 404
curl $MASTER/agents/epoz/docs -H "X-User-Id: $U"
# -> {"error": {"message": "Маршрут не входит в контракт агента", "type": "not_found_error", "param": null, "code": null}}

# чужой/несуществующий completion_id -> 404
curl $MASTER/agents/epoz/v1/chat/completions/chatcmpl-00000000-0000-0000-0000-000000000000/feedback \
  -H "X-User-Id: $U"
# -> {"error": {"message": "Сообщение не найдено", "type": "not_found_error", "param": null, "code": null}}

# пустой messages -> 422
curl -X POST $MASTER/v1/chat/completions \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"model": "epoz", "messages": []}'
# -> {"error": {"message": "messages обязателен и не должен быть пустым", "type": "invalid_request_error", "param": null, "code": null}}

# capability, которой у агента нет -> 404
curl -X POST $MASTER/agents/epoz/ocr -H "X-User-Id: $U" -F "file=@scan.png"
# -> {"error": {"message": "Агент epoz не поддерживает 'ocr'", "type": "not_found_error", "param": null, "code": null}}
```