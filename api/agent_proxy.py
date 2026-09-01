from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_user_id
from adapters import get_adapter
from api.deps import check_agent, proxy_response

router = APIRouter(tags=["proxy"])

_METHODS_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})


async def _agent_passthrough(
    agent_id: str, path: str, request: Request, user_id: str,
):
    check_agent(agent_id)
    if not path.startswith("v1/"):
        raise HTTPException(404, "Маршрут не входит в контракт агента")

    content_type = request.headers.get("content-type")
    body = request.stream() if request.method in _METHODS_WITH_BODY else None

    adapter = get_adapter(agent_id)
    return await proxy_response(
        adapter.proxy(request.method, f"/{path}",
                      user_id, body, content_type)
    )


_DOC = """Форвардит /agents/{agent_id}/v1/... агенту как есть — мастер не
знает и не должен знать про конкретные ручки его контракта (completions,
feedback, sources, platform/conversations, ...). Единственное, что мастер
проверяет сам, — что путь входит в контракт (начинается с "v1/"); всё
остальное, включая интерпретацию path и тела, отдано агенту.

Тело запроса передаётся адаптеру потоком (`request.stream()`), не собирается
в bytes: у мастера иначе весь multipart-upload лежал бы в RSS до окончания
загрузки. httpx `content=` принимает async-итератор наравне с bytes, ничего
менять в адаптере не потребовалось.

ВАЖНО: регистрируется в main.py ПОСЛЕ роутеров с более специфичными путями
(например /agents/{agent_id}/ocr в api/chat.py) — иначе catch-all перехватит
их первым, так как FastAPI матчит маршруты по порядку регистрации, а не по
специфичности."""


@router.get("/agents/{agent_id}/{path:path}", description=_DOC)
async def agent_passthrough_get(agent_id: str, path: str, request: Request,
                                user_id: str = Depends(get_user_id)):
    return await _agent_passthrough(agent_id, path, request, user_id)


@router.post("/agents/{agent_id}/{path:path}", description=_DOC)
async def agent_passthrough_post(agent_id: str, path: str, request: Request,
                                 user_id: str = Depends(get_user_id)):
    return await _agent_passthrough(agent_id, path, request, user_id)


@router.put("/agents/{agent_id}/{path:path}", description=_DOC)
async def agent_passthrough_put(agent_id: str, path: str, request: Request,
                                user_id: str = Depends(get_user_id)):
    return await _agent_passthrough(agent_id, path, request, user_id)


@router.patch("/agents/{agent_id}/{path:path}", description=_DOC)
async def agent_passthrough_patch(agent_id: str, path: str, request: Request,
                                  user_id: str = Depends(get_user_id)):
    return await _agent_passthrough(agent_id, path, request, user_id)


@router.delete("/agents/{agent_id}/{path:path}", description=_DOC)
async def agent_passthrough_delete(agent_id: str, path: str, request: Request,
                                   user_id: str = Depends(get_user_id)):
    return await _agent_passthrough(agent_id, path, request, user_id)
