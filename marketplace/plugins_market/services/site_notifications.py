"""站内审核相关通知：两固定模板 + 写入时按收件箱淘汰至 10 条。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from plugins_market.core.review_admins import get_review_admin_usernames
from plugins_market.core.auth import AuthContext
from plugins_market.repositories.site_notification_repository import (
    count_unread,
    insert_and_trim,
    inbox_key_for_login,
    inbox_key_for_user_id,
    list_for_inbox_keys,
    mark_all_read,
)
from plugins_market.schemas.site_notifications import (
    SiteNotificationItem,
    SiteNotificationListData,
)

logger = logging.getLogger(__name__)

NOTIFICATION_TEMPLATE_ADMIN_PENDING = "admin_pending"
NOTIFICATION_TEMPLATE_USER_REVIEW_DONE = "user_review_done"

MSG_ADMIN_PENDING = "您有新的待审核内容，请前往个人中心查看。"
MSG_USER_REVIEW_DONE = "您的 skill 已审核完成，请前往个人中心查看。"


def message_for_template(template: str) -> str:
    if template == NOTIFICATION_TEMPLATE_ADMIN_PENDING:
        return MSG_ADMIN_PENDING
    if template == NOTIFICATION_TEMPLATE_USER_REVIEW_DONE:
        return MSG_USER_REVIEW_DONE
    return ""


def _inbox_keys_for_context(auth: AuthContext) -> List[str]:
    keys: List[str] = []
    uid = (auth.acting_user_id or "").strip()
    if uid:
        keys.append(inbox_key_for_user_id(uid))
    name = (auth.acting_user_name or "").strip()
    if name:
        lg = inbox_key_for_login(name)
        if lg and lg not in keys:
            keys.append(lg)
    return keys


def list_notifications(
    db: Session,
    auth: AuthContext,
) -> SiteNotificationListData:
    keys = _inbox_keys_for_context(auth)
    rows = list_for_inbox_keys(db, keys, limit=10)
    unread = count_unread(db, keys)
    items = [
        SiteNotificationItem(
            id=int(r.id),
            template=str(r.template),
            message=message_for_template(str(r.template)),
            created_at_ms=int(r.created_at_ms or 0),
            read=bool(r.read_at_ms is not None),
        )
        for r in rows
    ]
    return SiteNotificationListData(items=items, unread_count=unread)


def mark_notifications_read_all(db: Session, auth: AuthContext) -> int:
    """将当前用户相关收件箱内未读标为已读，并 commit（否则仅有 GET 的会话关闭时会回滚）。"""
    at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    n = mark_all_read(db, _inbox_keys_for_context(auth), at_ms=at_ms)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    return n


def notify_review_admins_new_skill_submission(db: Session) -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    any_ins = False
    try:
        for login in get_review_admin_usernames():
            lg = (login or "").strip()
            if not lg:
                continue
            key = inbox_key_for_login(lg)
            insert_and_trim(
                db,
                inbox_key=key,
                template=NOTIFICATION_TEMPLATE_ADMIN_PENDING,
                created_at_ms=now_ms,
            )
            any_ins = True
        if any_ins:
            db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception("admin pending notification failed: %s", e)


def notify_publisher_skill_review_finished(db: Session, *, publisher_id: str) -> None:
    uid = (publisher_id or "").strip()
    if not uid:
        return
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    key = inbox_key_for_user_id(uid)
    try:
        insert_and_trim(
            db,
            inbox_key=key,
            template=NOTIFICATION_TEMPLATE_USER_REVIEW_DONE,
            created_at_ms=now_ms,
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.exception("user review-done notification failed publisher=%s: %s", uid, e)
