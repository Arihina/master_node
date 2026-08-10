"""Эталонный агент контракта — in-memory, для STUB-режима.

Это не «мок, возвращающий заглушку»: это минимальная, но честная реализация
того контракта, который платформа требует от агента. Мастер в тестах ходит в
него через свой НАСТОЯЩИЙ адаптер (`ContractHTTPAdapter`), поэтому проброс
статуса, content-type и потокового тела проверяется по-настоящему.

Заодно эта реализация служит исполняемой спецификацией: если агент платформы
расходится с ней в форме ответа — расходится он, а не тесты.
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ANSWER_TOKENS = ("Ответ", " от", " агента")
ANSWER = "".join(ANSWER_TOKENS)
PROMPT_TOKENS = 11
COMPLETION_TOKENS = 7

_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    404: "not_found_error",
    413: "invalid_request_error",
    415: "invalid_request_error",
}


def _error_body(status: int, message: str, param: str | None = None) -> dict:
    return {"error": {
        "message": message,
        "type": _ERROR_TYPES.get(status, "server_error"),
        "param": param,
        "code": None,
    }}


def _chat_usage() -> dict:
    return {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS,
            "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS}


def _responses_usage() -> dict:
    return {
        "input_tokens": PROMPT_TOKENS,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": COMPLETION_TOKENS,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
    }


def _text_part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": [], "logprobs": []}


def _message_item(item_id: str, text: str, status: str) -> dict:
    return {"id": item_id, "type": "message", "status": status,
            "role": "assistant", "content": [_text_part(text)]}


def _extract_text(content, text_types) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(p.get("text", "")) for p in content
                     if isinstance(p, dict) and p.get("type") in text_types)


def _find_file_id(content) -> str | None:
    if not isinstance(content, list):
        return None
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "file":
            ref = part.get("file") or {}
            if ref.get("file_id"):
                return ref["file_id"]
        if part.get("type") == "input_file" and part.get("file_id"):
            return part["file_id"]
    return None


def _has_inline_attachment(content) -> bool:
    if not isinstance(content, list):
        return False
    inline = {"image_url", "input_image", "input_audio"}
    return any(isinstance(p, dict) and p.get("type") in inline for p in content)


def make_fake_agent(agent_id: str, *, has_sources: bool = False,
                    has_files: bool = False) -> FastAPI:
    app = FastAPI()

    # user_id -> данные. Мастер обязан пробрасывать X-User-Id без изменений,
    # и скоупинг по нему — часть контракта: чужой ресурс даёт 404, а не 403.
    messages: dict[str, dict] = {}
    conversations: dict[str, dict] = {}
    feedback: dict[str, dict] = {}
    files: dict[str, dict] = {}

    def require_user(x_user_id: str | None) -> str:
        if not x_user_id:
            raise HTTPException(401, "Не передан идентификатор пользователя")
        return x_user_id

    def own_message(raw_id: str, user_id: str) -> dict:
        key = raw_id
        for prefix in ("chatcmpl-", "resp_", "msg_"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break
        msg = messages.get(key)
        if msg is None or msg["user_id"] != user_id:
            raise HTTPException(404, "Сообщение не найдено")
        return msg

    def save(user_id: str, question: str, answer: str, model: str,
             conversation_id: str | None, store: bool) -> str:
        msg_id = str(uuid4())
        if store:
            messages[msg_id] = {
                "id": msg_id, "user_id": user_id, "question": question,
                "answer": answer, "model": model, "created": int(time.time()),
                "conversation_id": conversation_id,
            }
        return msg_id

    def check_conversation(conversation_id, user_id) -> str | None:
        if conversation_id is None:
            return None
        conv = conversations.get(str(conversation_id))
        if conv is None or conv["user_id"] != user_id:
            raise HTTPException(404, "Чат не найден")
        return str(conversation_id)

    # ---------------- ошибки в общем формате -----------------------------

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content=_error_body(exc.status_code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        return JSONResponse(
            status_code=400,
            content=_error_body(400, first.get("msg", "Некорректный запрос")))

    # ---------------- Chat Completions -----------------------------------

    @app.post("/v1/chat/completions")
    async def chat_completions(body: dict = Body(...),
                               x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        model = body.get("model") or agent_id
        msgs = body.get("messages")

        if not isinstance(msgs, list) or not msgs:
            raise HTTPException(
                400, "messages обязателен и не должен быть пустым")
        if body.get("n") not in (None, 1):
            raise HTTPException(400, "Поддерживается только n=1")

        last = msgs[-1]
        if not isinstance(last, dict) or last.get("role") != "user":
            raise HTTPException(
                400, 'последнее сообщение должно иметь role="user"')

        content = last.get("content")
        if _has_inline_attachment(content) and has_files:
            raise HTTPException(
                400, "Загрузите документ через POST /v1/files и передайте file_id")

        question = _extract_text(content, {"text"}).strip()
        if not question:
            raise HTTPException(400, "Пустой вопрос")

        file_id = _find_file_id(content)
        if file_id is not None:
            f = files.get(file_id)
            if f is None or f["user_id"] != user_id:
                raise HTTPException(404, "Файл (file_id) не найден")
            if f["processing_status"] != "done":
                raise HTTPException(400, "Файл ещё не готов к использованию")

        conversation_id = check_conversation(
            body.get("conversation_id"), user_id)
        store = bool(body.get("store", True))
        msg_id = save(user_id, question, ANSWER, model, conversation_id, store)

        created = int(time.time())
        completion_id = f"chatcmpl-{msg_id}"
        conv_out = conversation_id

        if not body.get("stream"):
            return _completion(completion_id, created, model, conv_out, ANSWER)

        include_usage = bool(
            (body.get("stream_options") or {}).get("include_usage"))

        def _gen():
            yield _chunk(completion_id, created, model, conv_out,
                         {"role": "assistant", "content": ""})
            for token in ANSWER_TOKENS:
                yield _chunk(completion_id, created, model, conv_out,
                             {"content": token})
            yield _chunk(completion_id, created, model, conv_out, {},
                         finish_reason="stop")
            if include_usage:
                yield _chunk(completion_id, created, model, conv_out, None,
                             usage=_chat_usage())
            yield "data: [DONE]\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    def _completion(completion_id, created, model, conv, text) -> dict:
        return {
            "id": completion_id, "object": "chat.completion", "created": created,
            "model": model, "system_fingerprint": None, "conversation_id": conv,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text,
                            "refusal": None, "annotations": []},
                "logprobs": None, "finish_reason": "stop",
            }],
            "usage": _chat_usage(),
        }

    def _chunk(completion_id, created, model, conv, delta,
               finish_reason=None, usage=None) -> str:
        payload = {
            "id": completion_id, "object": "chat.completion.chunk",
            "created": created, "model": model, "system_fingerprint": None,
            "conversation_id": conv,
            "choices": [] if delta is None else [{
                "index": 0, "delta": delta, "logprobs": None,
                "finish_reason": finish_reason,
            }],
        }
        if usage is not None:
            payload["usage"] = usage
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @app.get("/v1/chat/completions/{completion_id}")
    async def get_completion(completion_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(completion_id, require_user(x_user_id))
        return _completion(f"chatcmpl-{msg['id']}", msg["created"], msg["model"],
                           msg["conversation_id"], msg["answer"])

    @app.delete("/v1/chat/completions/{completion_id}")
    async def delete_completion(completion_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(completion_id, require_user(x_user_id))
        messages.pop(msg["id"], None)
        feedback.pop(msg["id"], None)
        return {"id": f"chatcmpl-{msg['id']}",
                "object": "chat.completion.deleted", "deleted": True}

    # ---------------- Responses ------------------------------------------

    def _response_object(response_id, created, model, conv, status, output,
                         usage=None, req=None) -> dict:
        req = req or {}
        obj = {
            "id": response_id, "object": "response", "created_at": created,
            "status": status, "model": model, "output": output,
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "error": None, "incomplete_details": None,
            "instructions": req.get("instructions"),
            "metadata": req.get("metadata") or {},
            "temperature": req.get("temperature"), "top_p": req.get("top_p"),
            "max_output_tokens": req.get("max_output_tokens"),
            "previous_response_id": req.get("previous_response_id"),
            "store": req.get("store", True), "truncation": "disabled",
            "text": {"format": {"type": "text"}},
            "conversation_id": conv,
        }
        if usage is not None:
            obj["usage"] = usage
        return obj

    def _sse(seq: int, event_type: str, **fields) -> str:
        payload = {"type": event_type, "sequence_number": seq, **fields}
        return (f"event: {event_type}\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")

    @app.post("/v1/responses")
    async def create_response(body: dict = Body(...),
                              x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        model = body.get("model") or agent_id
        input_data = body.get("input")

        if input_data is None:
            raise HTTPException(400, "input обязателен")

        history: list = []
        if isinstance(input_data, str):
            question = input_data.strip()
        elif isinstance(input_data, list) and input_data:
            last = input_data[-1]
            if not isinstance(last, dict) or last.get("role") != "user":
                raise HTTPException(
                    400, 'последний item в input должен иметь role="user"')
            content = last.get("content")
            if _has_inline_attachment(content) and has_files:
                raise HTTPException(
                    400, "Загрузите документ через POST /v1/files и передайте file_id")
            question = _extract_text(
                content, {"input_text", "output_text"}).strip()
            history = [m for m in input_data[:-1]
                       if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
            file_id = _find_file_id(content)
            if file_id is not None:
                f = files.get(file_id)
                if f is None or f["user_id"] != user_id:
                    raise HTTPException(404, "Файл (file_id) не найден")
        else:
            raise HTTPException(
                400, "input должен быть строкой или непустым списком items")

        if not question:
            raise HTTPException(400, "Пустой вопрос")

        raw_conv = body.get("conversation")
        if isinstance(raw_conv, dict):
            raw_conv = raw_conv.get("id")
        if raw_conv is None:
            raw_conv = body.get("conversation_id")

        if raw_conv is None and body.get("previous_response_id"):
            prev = own_message(str(body["previous_response_id"]), user_id)
            conversation_id = prev["conversation_id"]
        else:
            conversation_id = check_conversation(raw_conv, user_id)

        if conversation_id is not None and history:
            raise HTTPException(
                400, "При переданном conversation input должен содержать "
                     "только новый ход (без истории)")

        req = {
            "instructions": body.get("instructions"),
            "metadata": body.get("metadata"),
            "temperature": body.get("temperature"), "top_p": body.get("top_p"),
            "max_output_tokens": body.get("max_output_tokens"),
            "previous_response_id": body.get("previous_response_id"),
            "store": bool(body.get("store", True)),
        }

        msg_id = save(user_id, question, ANSWER, model,
                      conversation_id, req["store"])
        response_id, item_id = f"resp_{msg_id}", f"msg_{msg_id}"
        created = int(time.time())

        if not body.get("stream"):
            return _response_object(
                response_id, created, model, conversation_id, "completed",
                [_message_item(item_id, ANSWER, "completed")],
                usage=_responses_usage(), req=req)

        def _gen():
            seq = 0

            def nxt():
                nonlocal seq
                seq += 1
                return seq

            in_progress = _response_object(response_id, created, model,
                                           conversation_id, "in_progress", [], req=req)
            yield _sse(nxt(), "response.created", response=in_progress)
            yield _sse(nxt(), "response.in_progress", response=in_progress)
            yield _sse(nxt(), "response.output_item.added", output_index=0,
                       item=_message_item(item_id, "", "in_progress"))
            yield _sse(nxt(), "response.content_part.added", item_id=item_id,
                       output_index=0, content_index=0, part=_text_part(""))
            for token in ANSWER_TOKENS:
                yield _sse(nxt(), "response.output_text.delta", item_id=item_id,
                           output_index=0, content_index=0, delta=token, logprobs=[])
            yield _sse(nxt(), "response.output_text.done", item_id=item_id,
                       output_index=0, content_index=0, text=ANSWER, logprobs=[])
            yield _sse(nxt(), "response.content_part.done", item_id=item_id,
                       output_index=0, content_index=0, part=_text_part(ANSWER))
            final_item = _message_item(item_id, ANSWER, "completed")
            yield _sse(nxt(), "response.output_item.done", output_index=0,
                       item=final_item)
            yield _sse(nxt(), "response.completed", response=_response_object(
                response_id, created, model, conversation_id, "completed",
                [final_item], usage=_responses_usage(), req=req))

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.get("/v1/responses/{response_id}")
    async def get_response(response_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(response_id, require_user(x_user_id))
        return _response_object(
            f"resp_{msg['id']}", msg["created"], msg["model"],
            msg["conversation_id"], "completed",
            [_message_item(f"msg_{msg['id']}", msg["answer"], "completed")],
            usage=_responses_usage())

    @app.delete("/v1/responses/{response_id}")
    async def delete_response(response_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(response_id, require_user(x_user_id))
        messages.pop(msg["id"], None)
        feedback.pop(msg["id"], None)
        return {"id": f"resp_{msg['id']}", "object": "response.deleted",
                "deleted": True}

    # ---------------- фидбэк ---------------------------------------------

    @app.post("/v1/chat/completions/{completion_id}/feedback")
    async def set_feedback(completion_id: str, body: dict = Body(default={}),
                           x_user_id: str = Header(default=None)):
        msg = own_message(completion_id, require_user(x_user_id))
        if body.get("vote", 1) not in (1, -1, None):
            raise HTTPException(400, "vote должен быть 1, -1 или null")
        if not isinstance(body.get("comment", ""), (str, type(None))):
            raise HTTPException(400, "comment должен быть строкой или null")
        current = feedback.setdefault(
            msg["id"], {"message_id": msg["id"], "vote": None, "comment": None})
        current.update({k: v for k, v in body.items()
                       if k in ("vote", "comment")})
        return current

    @app.get("/v1/chat/completions/{completion_id}/feedback")
    async def get_feedback(completion_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(completion_id, require_user(x_user_id))
        return feedback.get(
            msg["id"], {"message_id": msg["id"], "vote": None, "comment": None})

    @app.delete("/v1/chat/completions/{completion_id}/feedback", status_code=204)
    async def delete_feedback(completion_id: str, x_user_id: str = Header(default=None)):
        msg = own_message(completion_id, require_user(x_user_id))
        if msg["id"] not in feedback:
            raise HTTPException(404, "Оценка не найдена")
        feedback.pop(msg["id"])

    # ---------------- источники (только RAG-агенты) -----------------------

    if has_sources:
        @app.get("/v1/chat/completions/{completion_id}/sources")
        async def get_sources(completion_id: str, x_user_id: str = Header(default=None)):
            msg = own_message(completion_id, require_user(x_user_id))
            return {
                "id": f"chatcmpl-{msg['id']}",
                "retrieved": [{"text": "фрагмент", "source": "doc1.pdf", "score": 0.87}],
                "used_sources": ["doc1.pdf"],
            }

    # ---------------- чаты ------------------------------------------------

    @app.post("/v1/platform/conversations", status_code=201)
    async def create_conversation(body: dict = Body(default={}),
                                  x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        conv = {"id": str(uuid4()), "user_id": user_id,
                "title": body.get("title"), "created_at": int(time.time())}
        conversations[conv["id"]] = conv
        return {k: v for k, v in conv.items() if k != "user_id"}

    @app.get("/v1/platform/conversations")
    async def list_conversations(x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        return [{k: v for k, v in c.items() if k != "user_id"}
                for c in conversations.values() if c["user_id"] == user_id]

    @app.get("/v1/platform/conversations/{conversation_id}/messages")
    async def conversation_messages(conversation_id: str,
                                    x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        check_conversation(conversation_id, user_id)
        out = []
        for m in messages.values():
            if m["conversation_id"] == conversation_id and m["user_id"] == user_id:
                out.append({"id": m["id"], "role": "user",
                           "content": m["question"]})
                out.append(
                    {"id": m["id"], "role": "assistant", "content": m["answer"]})
        return out

    @app.patch("/v1/platform/conversations/{conversation_id}")
    async def rename_conversation(conversation_id: str, body: dict = Body(...),
                                  x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        check_conversation(conversation_id, user_id)
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "title не может быть пустым")
        conversations[conversation_id]["title"] = title
        return {k: v for k, v in conversations[conversation_id].items()
                if k != "user_id"}

    @app.delete("/v1/platform/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str,
                                  x_user_id: str = Header(default=None)):
        user_id = require_user(x_user_id)
        check_conversation(conversation_id, user_id)
        conversations.pop(conversation_id)

    # ---------------- файлы (только document_chat) ------------------------

    if has_files:
        _STATUS_MAP = {"pending": "uploaded", "processing": "uploaded",
                       "done": "processed", "failed": "error"}

        def _file_out(f: dict) -> dict:
            return {
                "id": f["id"], "object": "file", "bytes": f["bytes"],
                "created_at": f["created_at"], "filename": f["filename"],
                "purpose": "assistants",
                "status": _STATUS_MAP.get(f["processing_status"], "error"),
                "status_details": None,
                "processing_status": f["processing_status"],
                "conversation_id": f["conversation_id"],
            }

        @app.post("/v1/files", status_code=201)
        async def upload_file(file: UploadFile = File(...),
                              conversation_id: str = Form(None),
                              x_user_id: str = Header(default=None)):
            user_id = require_user(x_user_id)
            content = await file.read()
            f = {
                "id": f"file-{uuid4()}", "user_id": user_id,
                "filename": file.filename or "документ", "bytes": len(content),
                "created_at": int(time.time()), "processing_status": "done",
                "conversation_id": conversation_id,
            }
            files[f["id"]] = f
            return _file_out(f)

        @app.get("/v1/files")
        async def list_files(x_user_id: str = Header(default=None)):
            user_id = require_user(x_user_id)
            return {"object": "list",
                    "data": [_file_out(f) for f in files.values()
                             if f["user_id"] == user_id]}

        @app.get("/v1/files/{file_id}")
        async def get_file(file_id: str, x_user_id: str = Header(default=None)):
            user_id = require_user(x_user_id)
            f = files.get(file_id)
            if f is None or f["user_id"] != user_id:
                raise HTTPException(404, "Файл не найден")
            return _file_out(f)

        @app.delete("/v1/files/{file_id}")
        async def delete_file(file_id: str, x_user_id: str = Header(default=None)):
            user_id = require_user(x_user_id)
            f = files.get(file_id)
            if f is None or f["user_id"] != user_id:
                raise HTTPException(404, "Файл не найден")
            files.pop(file_id)
            return {"id": file_id, "object": "file", "deleted": True}

    return app


def make_fake_ocr_agent() -> FastAPI:
    """OCR живёт по старой договорённости: файл-в / текст-из, без /v1/."""
    app = FastAPI()

    @app.post("/ocr")
    async def ocr(file: UploadFile = File(...), x_user_id: str = Header(default=None)):
        content = await file.read()
        return {"text": f"распознанный текст ({len(content)} байт)"}

    return app
