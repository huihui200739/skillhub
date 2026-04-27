from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from plugins_market.core.auth import AuthContext, require_auth
from plugins_market.core.database import get_db
from plugins_market.schemas.common import ResponseModel
from plugins_market.schemas.site_notifications import SiteNotificationListData
from plugins_market.services import site_notifications as site_notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=ResponseModel[SiteNotificationListData],
)
async def get_notifications(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[SiteNotificationListData]:
    data = site_notifications_service.list_notifications(db, auth)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data=data,
    )


@router.post(
    "/read-all",
    response_model=ResponseModel[dict],
)
async def post_notifications_read_all(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_auth),
) -> ResponseModel[dict]:
    n = site_notifications_service.mark_notifications_read_all(db, auth)
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="ok",
        data={"updated": n},
    )
