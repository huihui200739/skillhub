# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import sys
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path, override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Make the retrieval/ package importable.  Its __init__.py handles the
# internal sys.path setup required by bare imports (e.g. "from indexing.bm25").
#
# In local dev, main.py lives in the project root alongside retrieval/.
# In Docker, main.py is in /app/site-packages/ but retrieval/ source is at
# /app/retrieval/ (copied separately, wheel-installed copy removed).
_RETRIEVAL_PARENTS = [
    "/app",
    str(Path(__file__).resolve().parent),
]
for _parent in _RETRIEVAL_PARENTS:
    if not os.path.isdir(os.path.join(_parent, "retrieval", "indexing")):
        continue
    # Prefer retrieval parent first on sys.path, without insert(0, ...) (G.PSL.03).
    # Dedupe by normpath so the same directory under different spellings does not
    # appear twice and shadow unrelated modules.
    normalized = os.path.normpath(_parent)
    deduped = [p for p in sys.path if os.path.normpath(p) != normalized]
    sys.path[:] = [normalized] + deduped
    break

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
import plugins_market.models.site_notifications  # noqa: F401  # register table for create_all
from plugins_market.routers.register import router_register
from plugins_market.core.s3_storage_client import close_storage_client_if_initialized
from plugins_market.validation.constants import MAX_FILE_SIZE

from plugins_market.core.logging import get_logger

logger = get_logger(__name__)


def _resolve_build_method(value: str):
    """Parse build_method text into BuildMethod flags."""
    from indexing.workflows.artifacts import BuildMethod  # type: ignore[import]

    norm_build_method = str(value or "").strip().lower().replace("-", "_")
    if norm_build_method in ("", "all"):
        return BuildMethod.ALL
    if norm_build_method == "embedding_bm25":
        return BuildMethod.EMBEDDING | BuildMethod.BM25

    method = BuildMethod(0)
    for part in norm_build_method.replace("|", "+").replace(",", "+").split("+"):
        token = part.strip()
        if not token:
            continue
        if token == "bm25":
            method |= BuildMethod.BM25
        elif token == "embedding":
            method |= BuildMethod.EMBEDDING
        elif token in ("tree", "llm"):
            method |= BuildMethod.TREE
        elif token == "all":
            return BuildMethod.ALL
    return method or BuildMethod.ALL


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    setup_logging(debug=settings.debug)
    Base.metadata.create_all(bind=engine)

    # ── retrieval startup ──────────────────────────────────────────────────
    from plugins_market.core.database import SessionLocal
    from plugins_market.core.s3_storage_client import get_storage_client
    from plugins_market.retrieval.daily_rebuild import (
        SkillTagRefreshOptions,
        _storage_uri_scheme,
        list_index_dirs,
        rebuild_all,
        refresh_skill_tags,
    )
    from plugins_market.retrieval.index_manager import get_index_manager
    from plugins_market.retrieval.reload_consumer import run_reload_consumer

    index_manager = get_index_manager()
    skill_prefix = settings.retrieval_skill_index_obs_prefix
    plugin_prefix = settings.retrieval_plugin_index_obs_prefix

    from common.security.security_utils import SecurityUtils
    from openai import OpenAI

    storage = get_storage_client()

    _llm_client = None
    _llm_model = settings.retrieval_finder_llm_model or settings.retrieval_default_llm_model
    if settings.retrieval_finder_llm_base_url or settings.retrieval_model_api_base_url:
        try:
            _llm_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or "dummy"
            _llm_base_url = settings.retrieval_finder_llm_base_url or settings.retrieval_model_api_base_url or None
            _llm_client = OpenAI(base_url=_llm_base_url, api_key=_llm_api_key)
        except Exception as _exc:
            logger.warning("retrieval: failed to create LLM client: %s", _exc)

    _embedding_client = None
    _embedding_build_client = None
    _emb_api_key = ""
    if settings.retrieval_embedding_api_base_url:
        try:
            _emb_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_EMBEDDING_API_KEY", default="") or ""
            _embedding_client = OpenAI(
                base_url=settings.retrieval_embedding_api_base_url,
                api_key=_emb_api_key or "dummy",
            )
            from indexing.embedding import create_openai_embedding_client  # type: ignore[import]

            _embedding_build_client = create_openai_embedding_client(
                base_url=settings.retrieval_embedding_api_base_url,
                api_key=_emb_api_key,
                model=settings.retrieval_embedding_model,
            )
        except Exception as _exc:
            logger.warning("retrieval: failed to create embedding client: %s", _exc)

    index_manager.configure(
        llm_openai_client=_llm_client,
        llm_model=_llm_model,
        embedding_openai_client=_embedding_client,
        embedding_model=settings.retrieval_embedding_model,
    )

    try:
        from indexing.workflows.artifacts import BuildConfig  # type: ignore[import]

        _index_build_config = BuildConfig(
            method=_resolve_build_method(settings.retrieval_build_method),
            llm_openai_client=_llm_client,
            llm_model=_llm_model,
            embedding_openai_client=_embedding_build_client,
            embedding_model=settings.retrieval_embedding_model,
            embedding_batch_size=settings.retrieval_embedding_batch_size,
        )

        # Separate config for skill tag classification (build_skill_tags)
        # Ensure LLM client is available for proper skill categorization
        _skill_tag_llm_client = _llm_client
        _skill_tag_llm_model = _llm_model

        # Check if there's independent skill tag LLM configuration
        if settings.retrieval_skill_tag_llm_model:
            _skill_tag_llm_model = settings.retrieval_skill_tag_llm_model
            _skill_tag_llm_base_url = (
                settings.retrieval_skill_tag_llm_api_base_url or settings.retrieval_model_api_base_url
            )
            try:
                _skill_tag_api_key = SecurityUtils.get_decrypt_secret(
                    "MARKET_RETRIEVAL_SKILL_TAG_LLM_API_KEY",
                    default="",
                ) or (SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or "")
                if _skill_tag_api_key and _skill_tag_llm_base_url:
                    _skill_tag_llm_client = OpenAI(base_url=_skill_tag_llm_base_url, api_key=_skill_tag_api_key)
                    logger.info("retrieval: using independent skill tag LLM config (model=%s)", _skill_tag_llm_model)
            except Exception as _exc:
                logger.warning("retrieval: failed to create independent skill tag LLM client: %s", _exc)
        elif _skill_tag_llm_client is None and settings.retrieval_model_api_base_url:
            # Fallback: create LLM client using main config if it wasn't created earlier
            try:
                _llm_api_key = SecurityUtils.get_decrypt_secret("MARKET_RETRIEVAL_MODEL_API_KEY", default="") or ""
                if _llm_api_key:
                    _skill_tag_llm_client = OpenAI(base_url=settings.retrieval_model_api_base_url, api_key=_llm_api_key)
                    logger.info("retrieval: created LLM client for skill tag classification (fallback to main config)")
            except Exception as _exc:
                logger.warning("retrieval: failed to create LLM client for skill tagging: %s", _exc)

        _skill_tag_build_config = BuildConfig(
            method=_resolve_build_method(settings.retrieval_build_method),
            llm_openai_client=_skill_tag_llm_client,
            llm_model=_skill_tag_llm_model,
            embedding_openai_client=_embedding_build_client,
            embedding_model=settings.retrieval_embedding_model,
            embedding_batch_size=settings.retrieval_embedding_batch_size,
        )
    except ImportError:
        _index_build_config = None
        _skill_tag_build_config = None
        logger.warning("retrieval: BuildConfig not importable, builds will run without model config")

    redis_client = None
    if settings.redis_host:
        try:
            import redis as redis_lib
            redis_password = (
                SecurityUtils.get_decrypt_secret("MARKET_REDIS_PASSWORD", default="")
                or None
            )
            redis_client = redis_lib.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=redis_password,
                decode_responses=False,
                socket_connect_timeout=3,
            )
            redis_client.ping()
            logger.info("retrieval: Redis connected %s:%s", settings.redis_host, settings.redis_port)
        except Exception as exc:
            logger.warning("retrieval: Redis unavailable (%s), reload consumer disabled", exc)
            redis_client = None

    uri_scheme = _storage_uri_scheme(storage)
    for group, prefix in (("skill", skill_prefix), ("plugin", plugin_prefix)):
        try:
            direct_path = getattr(settings, f"retrieval_{group}_index_path", "").strip()
            index_load_timeout = 600  # 10 minutes max per group
            if direct_path:
                logger.info("retrieval warm-start: loading group=%s from direct path %s", group, direct_path)
                try:
                    index_manager.load(group, direct_path)
                    logger.info("retrieval warm-start: group=%s loaded successfully", group)
                except Exception as e:
                    logger.warning("retrieval warm-start: group=%s load failed: %s", group, e)
            else:
                dirs = list_index_dirs(storage, prefix)
                if dirs:
                    bucket = storage.config.bucket_name
                    obs_uri = f"{uri_scheme}://{bucket}/{dirs[0]}"
                    logger.info(
                        "retrieval warm-start: loading group=%s from %s (timeout=%ds)",
                        group,
                        obs_uri,
                        index_load_timeout,
                    )
                    try:
                        index_manager.load(group, obs_uri)
                        logger.info("retrieval warm-start: group=%s loaded successfully", group)
                    except Exception as e:
                        logger.warning("retrieval warm-start: group=%s load failed: %s", group, e)
                else:
                    logger.info("retrieval warm-start: no index found for group=%s prefix=%s, skipping", group, prefix)
        except Exception as exc:
            logger.warning("retrieval warm-start unexpected error group=%s: %s", group, exc, exc_info=True)

    if redis_client is not None:
        reload_task = asyncio.create_task(run_reload_consumer(index_manager, redis_client))
        app.state.reload_task = reload_task

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    def _run_index_rebuild(skip_lock: bool = False) -> None:
        started = time.monotonic()
        logger.info("retrieval index rebuild run begin [skip_lock=%s]", skip_lock)
        rebuild_all(
            SessionLocal,
            skill_prefix,
            plugin_prefix,
            storage,
            index_manager,
            redis_client,
            _index_build_config,
            _skill_tag_build_config,
            run_skill_tag=False,
            max_index_versions=settings.retrieval_index_max_versions,
            skip_lock=skip_lock,
        )
        elapsed = time.monotonic() - started
        logger.info("retrieval index rebuild run end [skip_lock=%s elapsed=%.1fs]", skip_lock, elapsed)

    def _run_skill_tag_refresh(skip_lock: bool = False) -> None:
        started = time.monotonic()
        logger.info("retrieval skill-tag refresh run begin [skip_lock=%s]", skip_lock)
        refresh_skill_tags(
            SkillTagRefreshOptions(
                db_factory=SessionLocal,
                skill_prefix=skill_prefix,
                storage=storage,
                redis_client=redis_client,
                build_config=_index_build_config,
                skill_tag_build_config=_skill_tag_build_config,
                skip_lock=skip_lock,
            )
        )
        elapsed = time.monotonic() - started
        logger.info("retrieval skill-tag refresh run end [skip_lock=%s elapsed=%.1fs]", skip_lock, elapsed)

    async def _index_rebuild_job() -> None:
        loop = asyncio.get_running_loop()
        # Set timeout to 2350s (39.2 min), slightly less than lock TTL (2400s = 40min) to prevent race condition
        try:
            await asyncio.wait_for(loop.run_in_executor(None, _run_index_rebuild), timeout=2350)
        except asyncio.TimeoutError:
            logger.error("retrieval index rebuild timed out after 39.2 minutes")

    async def _skill_tag_job() -> None:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, _run_skill_tag_refresh), timeout=2350)
        except asyncio.TimeoutError:
            logger.error("retrieval skill-tag refresh timed out after 39.2 minutes")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _index_rebuild_job,
        CronTrigger.from_crontab(settings.retrieval_rebuild_cron),
        id="index_rebuild",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        _skill_tag_job,
        CronTrigger.from_crontab(settings.retrieval_skill_tag_cron),
        id="skill_tag_refresh",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    app.state.retrieval_scheduler = scheduler
    app.state.retrieval_redis = redis_client
    logger.info(
        "retrieval startup complete — index_cron=%s skill_tag_cron=%s",
        settings.retrieval_rebuild_cron,
        settings.retrieval_skill_tag_cron,
    )

    if settings.retrieval_rebuild_on_startup:
        logger.info("retrieval: REBUILD_ON_STARTUP=true, scheduling immediate index rebuild")

        async def _startup_rebuild() -> None:
            logger.info("retrieval startup index rebuild begin")
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _run_index_rebuild(skip_lock=True)),
                    timeout=2350,
                )
            except asyncio.TimeoutError:
                logger.error("retrieval startup index rebuild timed out after 39.2 minutes")
            except Exception as exc:
                logger.exception("retrieval startup index rebuild failed: %s", exc)
            finally:
                elapsed = time.monotonic() - started
                logger.info("retrieval startup index rebuild end [elapsed=%.1fs]", elapsed)

        app.state.startup_rebuild_task = asyncio.create_task(_startup_rebuild())

    if settings.retrieval_skill_tag_on_startup:
        logger.info("retrieval: SKILL_TAG_ON_STARTUP=true, scheduling immediate skill-tag refresh")

        async def _startup_skill_tag_refresh() -> None:
            logger.info("retrieval startup skill-tag refresh begin")
            loop = asyncio.get_running_loop()
            started = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _run_skill_tag_refresh(skip_lock=True)),
                    timeout=2350,
                )
            except asyncio.TimeoutError:
                logger.error("retrieval startup skill-tag refresh timed out after 39.2 minutes")
            except Exception as exc:
                logger.exception("retrieval startup skill-tag refresh failed: %s", exc)
            finally:
                elapsed = time.monotonic() - started
                logger.info("retrieval startup skill-tag refresh end [elapsed=%.1fs]", elapsed)

        app.state.startup_skill_tag_task = asyncio.create_task(_startup_skill_tag_refresh())

    # ── yield (app runs) ───────────────────────────────────────────────────
    yield

    # ── shutdown ───────────────────────────────────────────────────────────
    _scheduler = getattr(app.state, "retrieval_scheduler", None)
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("shutdown: scheduler stop failed: %s", exc)
    _reload_task = getattr(app.state, "reload_task", None)
    if _reload_task is not None:
        _reload_task.cancel()
    _redis = getattr(app.state, "retrieval_redis", None)
    if _redis is not None:
        try:
            _redis.close()
        except Exception as exc:
            logger.warning("shutdown: Redis close failed: %s", exc)
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
