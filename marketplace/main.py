import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from plugins_market.core.config import settings
from plugins_market.core.database import engine, DATABASE_URL
from plugins_market.core.errors import PublishError
from plugins_market.core.logging import setup_logging
from plugins_market.core.middleware.request_id import RequestIDMiddleware
from plugins_market.models.base import Base
from plugins_market.routers.register import router_register
from plugins_market.core.s3_storage_client import close_storage_client_if_initialized
from plugins_market.validation.constants import MAX_FILE_SIZE

from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    setup_logging(debug=settings.debug)
    Base.metadata.create_all(bind=engine)
    yield
    try:
        close_storage_client_if_initialized()
    except Exception as e:
        logger.warning("shutdown cleanup: failed to close storage client: %s", e)
    try:
        engine.dispose()
    except Exception as e:
        logger.warning("shutdown cleanup: failed to dispose db engine: %s", e)


def create_app() -> FastAPI:
    fastapi_app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    fastapi_app.add_middleware(RequestIDMiddleware)

    @fastapi_app.exception_handler(PublishError)
    async def publish_error_handler(request: Request, exc: PublishError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @fastapi_app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @fastapi_app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @fastapi_app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc) or "服务器内部错误，请稍后重试"},
        )

    @fastapi_app.middleware("http")
    async def reject_oversized_upload(request: Request, call_next):
        if request.method == "POST" and request.url.path.rstrip("/").endswith("/plugins"):
            cl_str = request.headers.get("content-length")
            if cl_str is not None:
                try:
                    if int(cl_str) > MAX_FILE_SIZE:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "detail": {
                                    "code": 413,
                                    "data": None,
                                    "error": "file_too_large",
                                    "message": f"文件大小超过限制（最大 {MAX_FILE_SIZE // (1024 * 1024)} MB）",
                                }
                            },
                        )
                except ValueError:
                    pass
        return await call_next(request)

    @fastapi_app.get("/api/health")
    async def health():
        return {"status": "ok"}

    router_register(fastapi_app)

    return fastapi_app


app = create_app()


def main() -> None:
    host = os.getenv("STORE_HOST", settings.host)
    port = int(os.getenv("STORE_PORT", settings.port))
    workers = int(os.getenv("STORE_WORKERS", "1").strip() or "1")

    reload = bool(settings.debug)
    if reload:
        workers = 1

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["plugins_market", "common"] if reload else None,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
