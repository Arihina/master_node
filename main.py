from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from adapters import close_all, CapabilityNotSupported
from api import meta, proxy, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_all()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Agent-Id", "X-Session-Id"],
)

app.include_router(meta.router)
app.include_router(proxy.router)
app.include_router(chat.router)


@app.exception_handler(CapabilityNotSupported)
async def _capability_handler(request: Request, exc: CapabilityNotSupported):
    return JSONResponse(
        status_code=404,
        content={
            "detail": f"Агент {exc.agent_id} не поддерживает '{exc.capability}'"},
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
