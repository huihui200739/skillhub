# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from fastapi import FastAPI

from plugins_market.core.config import settings
from plugins_market.clawhub_compat import router as clawhub_router
from plugins_market.routers import audit as audit_router_module
from plugins_market.routers import notifications as notifications_router
from plugins_market.routers import oauth_provider
from plugins_market.routers import plugin as plugin_routers
from plugins_market.routers.interaction import interaction_router
from plugins_market.routers.site_public import router as site_public_router


def router_register(app: FastAPI) -> None:
    """注册所有路由。"""

    app.include_router(plugin_routers.router, prefix="/api/v1")
    app.include_router(interaction_router, prefix="/api/v1")
    app.include_router(notifications_router.router, prefix="/api/v1")
    app.include_router(site_public_router, prefix="/api/v1")
    app.include_router(audit_router_module.router, prefix="/api/v1")
    app.include_router(oauth_provider.router, prefix="/api/v1/auth", tags=["auth"])
    if settings.clawhub_compat_enabled:
        app.include_router(clawhub_router, prefix="/api/v1")

