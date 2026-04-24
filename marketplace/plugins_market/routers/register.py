from fastapi import FastAPI

from plugins_market.core.config import settings
from plugins_market.clawhub_compat import router as clawhub_router
from plugins_market.routers import oauth_gitcode
from plugins_market.routers import plugin as plugin_routers
from plugins_market.routers.site_public import router as site_public_router


def router_register(app: FastAPI) -> None:
    """注册所有路由。"""

    app.include_router(plugin_routers.router, prefix="/api/v1")
    app.include_router(site_public_router, prefix="/api/v1")
    app.include_router(oauth_gitcode.router, prefix="/api/v1/auth", tags=["auth"])
    if settings.clawhub_compat_enabled:
        app.include_router(clawhub_router, prefix="/api/v1")

