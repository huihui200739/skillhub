"""ClawHub CLI-compatible HTTP surface (same port as marketplace, `/api/v1`)."""

from .router import router

__all__ = ["router"]
