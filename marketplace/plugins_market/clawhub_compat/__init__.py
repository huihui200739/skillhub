# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ClawHub CLI-compatible HTTP surface (same port as marketplace, `/api/v1`)."""

from .router import router

__all__ = ["router"]
