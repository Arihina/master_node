# Curl-тесты платформы — форма Responses API (через master_node)

Аналог `curl_tests.md`, но для формы Responses (`/v1/responses`) вместо Chat
Completions. Показывает главное отличие форм на практике: с `conversation_id`
клиент присылает **только новый ход**, историю собирает сам агент. Всё, что
не относится к генерации (фидбэк, источники, CRUD чатов, файлы) — **общее**
для обеих форм, отдельно не дублируется.

Агенты — по `id` из `registry.py`; если у вас другие ключи, замените в
переменных ниже (см. предупреждение в начале `curl_tests.md`).

## Подготовка

```bash
MASTER=http://127.0.0.1:8000
U=11111111-1111-1111-1111-111111111111
```

---

## 1. Служебное (мастер)

`GET /v1/models` не меняется от формы — модели/агенты те же:
```bash
curl $MASTER/v1/models
```

Строгая проверка формы — агент, не заявивший `"responses"` в
`contract_forms`, отдаёт `422`, а не перенаправляется на Chat Completions:
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "ocr", "input": "привет"}'
# -> {"error": {"message": "Агент ocr не поддерживает форму Responses API", "type": "invalid_request_error", ...}}
```

---

## 2. epoz — RAG-агент

### 2.1 Генерация без `conversation_id`, авто-роутинг, стримом
```bash
curl -N -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "auto", "stream": true, "input": "что такое ЕПоЗ"}'
```
В `response.created`/`response.completed` — `"model":"epoz"`, тот же принцип, что и у Chat Completions: реальный выбор роутера виден прямо в ответе.

### 2.2 Генерация без `conversation_id`, прямой вызов, нестрим
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "epoz", "input": "что такое Меры ограничительного характера"}'
```
```json
{"id": "resp_...", "object": "response", "status": "completed", "output": [...], "usage": {"input_tokens": ..., "output_tokens": ..., "total_tokens": ...}}
```

### 2.3 Диалог с `conversation_id` — история сама из БД
```bash
curl -X POST $MASTER/agents/epoz/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Тест Responses"}'
```
```bash
CID=3fa85f64-5717-4562-b3fc-2c963f66afa6   # id из ответа выше
```
```bash
# первый ход — только новый вопрос, без истории (её ещё и нет)
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"epoz\", \"conversation_id\": \"$CID\", \"input\": \"что такое РАГ\"}"

# второй ход — СНОВА только новый вопрос, БЕЗ истории — агент сам подтянет
# первый вопрос+ответ из БД (последние HISTORY_LIMIT сообщений чата)
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"epoz\", \"conversation_id\": \"$CID\", \"input\": \"а какие сроки\"}"
```

### 2.4 Ошибка — история в `input` вместе с `conversation_id`
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"epoz\", \"conversation_id\": \"$CID\", \"input\": [
        {\"role\": \"user\", \"content\": \"вопрос 1\"},
        {\"role\": \"assistant\", \"content\": \"ответ 1\"},
        {\"role\": \"user\", \"content\": \"вопрос 2\"}
      ]}"
# -> 422: "При переданном conversation_id input должен содержать только новый ход..."
```

```bash
ID=resp_1e6b7ee7-d5bb-4f0a-8f9e-a06f19a8f3c2   # id из любого ответа выше
```

### 2.5 Получить ответ повторно (форма Responses)
```bash
curl $MASTER/agents/epoz/v1/responses/$ID -H "X-User-Id: $U"
```

### 2.6 Источники — путь ОБЩИЙ для обеих форм, не под `/v1/responses/`
```bash
curl $MASTER/agents/epoz/v1/chat/completions/$ID/sources -H "X-User-Id: $U"
```

### 2.7 Фидбэк — тоже общий путь
```bash
curl -X POST $MASTER/agents/epoz/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1, "comment": "Хороший ответ"}'
curl $MASTER/agents/epoz/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
curl -X DELETE $MASTER/agents/epoz/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
```

### 2.8 Чаты — тот же `/v1/platform/conversations`, что и у Chat Completions
```bash
curl $MASTER/agents/epoz/v1/platform/conversations -H "X-User-Id: $U"
curl $MASTER/agents/epoz/v1/platform/conversations/$CID/messages -H "X-User-Id: $U"
curl -X DELETE $MASTER/agents/epoz/v1/platform/conversations/$CID -H "X-User-Id: $U"
```

---

## 3. slave_chat — общий чат-агент (`model: "chat"`)

### 3.1 Генерация без `conversation_id`, стримом
```bash
curl -N -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "chat", "stream": true, "input": "расскажи анекдот"}'
```

### 3.2 Диалог с `conversation_id`
```bash
curl -X POST $MASTER/agents/chat/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Тест"}'
```
```bash
CID=4gb96g75-6828-5673-c4gd-3d074g77bgb7   # id из ответа выше
```
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"chat\", \"conversation_id\": \"$CID\", \"input\": \"привет\"}"

# снова только новый ход
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"chat\", \"conversation_id\": \"$CID\", \"input\": \"расскажи анекдот\"}"
```

```bash
ID=resp_2f7c8ee8-e6cc-5f1b-9g0f-b17g20b9g4d3   # id из любого ответа выше
```

### 3.3 Получить ответ повторно
```bash
curl $MASTER/agents/chat/v1/responses/$ID -H "X-User-Id: $U"
```

### 3.4 Фидбэк — общий путь, merge-семантика (как и у Chat Completions)
```bash
curl -X POST $MASTER/agents/chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1}'
curl -X POST $MASTER/agents/chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"comment": "смешно"}'
# -> payload: {"vote": 1, "comment": "смешно"} — второй вызов не затёр первый ключ
curl $MASTER/agents/chat/v1/chat/completions/$ID/feedback -H "X-User-Id: $U"
```

---

## 4. document_chat — документ + LLM (`model: "document_chat"`)

Загрузка файла не форма-специфична — `/v1/files` общий для обеих форм.
Меняется формат ссылки на файл в теле генерации, и — только в форме
Responses — появляется возможность подключить файл автоматически, если он
был загружен именно в этот чат.

### 4.1 Загрузить документ без привязки к чату
```bash
curl -X POST $MASTER/agents/document_chat/v1/files \
  -H "X-User-Id: $U" -F "file=@test.pdf;type=application/pdf"
```
```bash
FID=file-85b365de-1234-4c7d-8e9f-0a1b2c3d4e5f   # id из ответа выше
```

### 4.2 Вопрос по документу явной ссылкой, без `conversation_id`

Обратите внимание: `file_id` здесь — **плоское поле прямо в части**
(`input_file`), не вложено под ключ `"file"`, как в форме Chat Completions:
```bash
curl -N -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"stream\": true, \"input\": [
        {\"role\": \"user\", \"content\": [
          {\"type\": \"input_text\", \"text\": \"Про что документ?\"},
          {\"type\": \"input_file\", \"file_id\": \"$FID\"}
        ]}
      ]}"
```

### 4.3 Загрузить документ С привязкой к чату — файл дальше подключается сам

```bash
curl -X POST $MASTER/agents/document_chat/v1/platform/conversations \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"title": "Накладная"}'
```
```bash
CID=6id18i97-8040-7895-e6if-5f296i99did9   # id из ответа выше
```
```bash
# poле формы conversation_id — привязывает файл к чату при загрузке
curl -X POST $MASTER/agents/document_chat/v1/files \
  -H "X-User-Id: $U" -F "file=@test.pdf;type=application/pdf" -F "conversation_id=$CID"
# -> {"id": "file-...", "conversation_id": "6id18i97-...", "status": "done", ...}
```
```bash
FID2=file-99999999-1234-4c7d-8e9f-0a1b2c3d4e5f   # id из ответа выше
```
```bash
# первый вопрос — file_id указывать не нужно, подхватится сам
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"conversation_id\": \"$CID\", \"input\": \"про что документ?\"}"

# следующий вопрос — снова только новый ход, документ по-прежнему подхватится сам
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"conversation_id\": \"$CID\", \"input\": \"А дата какая?\"}"
```

### 4.4 Явная ссылка внутри того же чата — переопределяет автоматику

Если в этом чате нужно спросить про **другой** документ (не тот, что
автоматически подхватывается) — достаточно один раз явно указать `file_id`,
он временно возьмёт верх над автоматикой для этого хода:
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"document_chat\", \"conversation_id\": \"$CID\", \"input\": [
        {\"role\": \"user\", \"content\": [
          {\"type\": \"input_text\", \"text\": \"А в этом документе что?\"},
          {\"type\": \"input_file\", \"file_id\": \"$FID\"}
        ]}
      ]}"
```

### 4.5 Вопрос без документа (проверка, что file_id/автоматика — не обязательны)
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "document_chat", "input": "Что такое MinerU?"}'
```

```bash
ID=resp_5hc07h86-7939-6784-d5he-4e185h88chc8   # id из любого ответа 4.2-4.5
```

### 4.6 Получить ответ повторно
```bash
curl $MASTER/agents/document_chat/v1/responses/$ID -H "X-User-Id: $U"
```

### 4.7 Фидбэк — общий путь, `vote`/`comment` (как у epoz)
```bash
curl -X POST $MASTER/agents/document_chat/v1/chat/completions/$ID/feedback \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"vote": 1, "comment": "точно"}'

# файлы больше не нужны
curl -X DELETE $MASTER/agents/document_chat/v1/files/$FID -H "X-User-Id: $U"
curl -X DELETE $MASTER/agents/document_chat/v1/files/$FID2 -H "X-User-Id: $U"
```

---

## 5. Роутинг при `model: "auto"` — специфика формы Responses

### 5.1 Обычный текстовый запрос — общий `MasterRouter`, что и у Chat Completions
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "auto", "input": "расскажи анекдот"}'
# -> "model":"chat" в ответе
```

### 5.2 Вложение при `"auto"` — сразу сужение до `attachments`, без семантики

Роутер вообще не вызывается — кандидаты сразу ограничены агентами,
у которых ОДНОВРЕМЕННО `"attachments"` в `capabilities` И `"responses"` в
`contract_forms` (сейчас это только `document_chat`):
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"auto\", \"input\": [{\"role\": \"user\", \"content\": [
        {\"type\": \"input_text\", \"text\": \"какая сумма в накладной?\"},
        {\"type\": \"input_file\", \"file_id\": \"$FID\"}
      ]}]}"
# -> "model":"document_chat"
```

### 5.3 Явный агент без `attachments`, но с файлом в `input` -> `422`
```bash
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d "{\"model\": \"epoz\", \"input\": [{\"role\": \"user\", \"content\": [
        {\"type\": \"input_text\", \"text\": \"вопрос\"},
        {\"type\": \"input_file\", \"file_id\": \"$FID\"}
      ]}]}"
# -> {"error": {"message": "Агент epoz не поддерживает вложения", ...}}
```

---

## 6. Ошибки — единый формат, специфичные для формы Responses случаи

```bash
# пустой input -> 422
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" -d '{"model": "epoz"}'
# -> {"error": {"message": "input обязателен", "type": "invalid_request_error", ...}}

# input списком, но последний item не role:"user" -> 422
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "epoz", "input": [{"role": "assistant", "content": "..."}]}'
# -> {"error": {"message": "последний item в input должен иметь role=\"user\"", ...}}

# нет доступного агента формы Responses вообще (все выключены/не заявляют форму) -> 422
curl -X POST $MASTER/v1/responses \
  -H "X-User-Id: $U" -H "Content-Type: application/json" \
  -d '{"model": "auto", "input": "привет"}'
# -> {"error": {"message": "Нет доступного агента, поддерживающего форму Responses API", ...}}
# (актуально, только если ни один агент не заявил "responses" в contract_forms)

# чужой/несуществующий response_id -> 404 (общий путь feedback, как и в форме Chat Completions)
curl $MASTER/agents/epoz/v1/chat/completions/resp_00000000-0000-0000-0000-000000000000/feedback \
  -H "X-User-Id: $U"
# -> {"error": {"message": "Сообщение не найдено", "type": "not_found_error", ...}}
```