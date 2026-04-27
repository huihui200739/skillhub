from __future__ import annotations

from typing import List, Optional, Sequence

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.orm import Session

from plugins_market.models.site_notifications import SiteNotificationDB

_MAX_PER_INBOX = 10


def inbox_key_for_login(login: str) -> str:
    s = (login or "").strip()
    if not s:
        return ""
    return f"l:{s}"


def inbox_key_for_user_id(user_id: str) -> str:
    s = (user_id or "").strip()
    if not s:
        return ""
    return f"u:{s}"


def insert_and_trim(
    db: Session,
    *,
    inbox_key: str,
    template: str,
    created_at_ms: int,
) -> Optional[SiteNotificationDB]:
    if not inbox_key or not template:
        return None
    row = SiteNotificationDB(
        inbox_key=inbox_key,
        template=template,
        created_at_ms=created_at_ms,
        read_at_ms=None,
    )
    db.add(row)
    db.flush()

    # 保留本收件箱下最新的 _MAX_PER_INBOX 条
    keep_ids: Sequence[int] = db.scalars(
        select(SiteNotificationDB.id)
        .where(SiteNotificationDB.inbox_key == inbox_key)
        .order_by(
            SiteNotificationDB.created_at_ms.desc(),
            SiteNotificationDB.id.desc(),
        )
        .limit(_MAX_PER_INBOX)
    ).all()
    if not keep_ids:
        return row
    keep_set = {int(x) for x in keep_ids}
    if not keep_set:
        return row
    db.execute(
        delete(SiteNotificationDB).where(
            and_(
                SiteNotificationDB.inbox_key == inbox_key,
                SiteNotificationDB.id.notin_(list(keep_set)),
            )
        )
    )
    return row


def list_for_inbox_keys(
    db: Session,
    keys: List[str],
    *,
    limit: int = 10,
) -> List[SiteNotificationDB]:
    ok = [k for k in keys if k and k.strip()]
    if not ok:
        return []
    return list(
        db.scalars(
            select(SiteNotificationDB)
            .where(SiteNotificationDB.inbox_key.in_(ok))
            .order_by(
                SiteNotificationDB.created_at_ms.desc(),
                SiteNotificationDB.id.desc(),
            )
            .limit(limit)
        ).all()
    )


def count_unread(
    db: Session,
    keys: List[str],
) -> int:
    ok = [k for k in keys if k and k.strip()]
    if not ok:
        return 0
    n = db.execute(
        select(func.count(SiteNotificationDB.id)).where(
            and_(
                SiteNotificationDB.inbox_key.in_(ok),
                SiteNotificationDB.read_at_ms.is_(None),
            )
        )
    ).scalar()
    return int(n or 0)


def mark_all_read(
    db: Session,
    keys: List[str],
    *,
    at_ms: int,
) -> int:
    ok = [k for k in keys if k and k.strip()]
    if not ok:
        return 0
    res = db.execute(
        update(SiteNotificationDB)
        .where(
            SiteNotificationDB.inbox_key.in_(ok),
            SiteNotificationDB.read_at_ms.is_(None),
        )
        .values(read_at_ms=at_ms)
    )
    return int(res.rowcount or 0)
