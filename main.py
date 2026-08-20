from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings
from adapters import close_all, CapabilityNotSupported
from registry_service import agent_registry
from api import meta, chat, responses, agent_proxy, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await agent_registry.aclose()
    await close_all()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(meta.router)
app.include_router(chat.router)
app.include_router(responses.router)
app.include_router(agent_proxy.router)
app.include_router(admin.router)


_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    404: "not_found_error",
    409: "invalid_request_error",
    413: "invalid_request_error",
    415: "invalid_request_error",
    422: "invalid_request_error",
}


_VALIDATION_STATUS = 400

_LOC_PREFIXES = {"body", "query", "path", "header", "cookie"}


def _param_from_loc(loc) -> str | None:
    """('body', 'messages', 0, 'role') -> 'messages.0.role'"""
    parts = [str(p) for p in loc if p not in _LOC_PREFIXES]
    return ".".join(parts) or None


def _error_body(status_code: int, message: str, param: str | None = None) -> dict:
    return {"error": {
        "message": message,
        "type": _ERROR_TYPES.get(status_code, "server_error"),
        "param": param,
        "code": None,
    }}


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.status_code, str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    message = first.get("msg", "Некорректный запрос")
    param = _param_from_loc(first.get("loc", ()))
    return JSONResponse(
        status_code=_VALIDATION_STATUS,
        content=_error_body(_VALIDATION_STATUS, message, param),
    )


@app.exception_handler(CapabilityNotSupported)
async def _capability_handler(request: Request, exc: CapabilityNotSupported):
    return JSONResponse(
        status_code=404,
        content=_error_body(
            404, f"Агент {exc.agent_id} не поддерживает '{exc.capability}'"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        timeout_keep_alive=settings.timeout_keep_alive,
    )
