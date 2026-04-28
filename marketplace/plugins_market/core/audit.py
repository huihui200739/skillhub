import logging
import random
import string
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from plugins_market.core.context import get_request_id, get_duration_ms, _BJ_TZ
from plugins_market.models.base import Base
from sqlalchemy import Column, String, Integer, Text, JSON, DateTime, Index
from sqlalchemy.dialects.mysql import BIGINT

logger = logging.getLogger(__name__)

# Skill 上架审核操作（与 SKILL_MANAGE / PLUGIN_MANAGE 等区分）
EVENT_SKILL_MODERATION = "SKILL_MODERATION"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, index=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)

    event_type = Column(String(50), nullable=False)
    action = Column(String(20), nullable=False)

    operator_id = Column(String(64), nullable=False, index=True)
    operator_name = Column(String(100), nullable=True)

    resource_type = Column(String(30), nullable=False)
    resource_id = Column(String(100), nullable=True, index=True)
    resource_version = Column(String(30), nullable=True)

    result = Column(String(20), nullable=False)
    duration_ms = Column(Integer, nullable=False)
    detail = Column(Text, nullable=True)

    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)

    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime(3), nullable=False)

    __table_args__ = (
        Index("idx_event_type_action", event_type, action),
    )


@dataclass(frozen=True)
class AuditLogParams:
    db: Session
    event_type: str
    action: str
    operator_id: str
    operator_name: Optional[str] = None
    resource_type: str = ""
    resource_id: Optional[str] = None
    resource_version: Optional[str] = None
    result: str = "SUCCESS"
    detail: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class AuditService:
    
    @staticmethod
    def create_event_id() -> str:
        now_ms = int(time.time() * 1000)
        rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"audit_{now_ms}_{rand_chars}"
    
    @staticmethod
    def log(params: AuditLogParams) -> None:
        """
        Record an audit log entry.
        Only mutation operations should call this (PUBLISH, IMPORT, DELETE).
        Query and auth operations are intentionally not audited.
        """
        try:
            request_id = get_request_id() or "unknown"
            duration_ms = get_duration_ms()
            event_id = AuditService.create_event_id()
            
            audit_entry = AuditLog(
                request_id=request_id,
                event_id=event_id,
                event_type=params.event_type,
                action=params.action,
                operator_id=params.operator_id,
                operator_name=params.operator_name,
                resource_type=params.resource_type,
                resource_id=params.resource_id,
                resource_version=params.resource_version,
                result=params.result,
                duration_ms=duration_ms,
                detail=params.detail,
                ip_address=params.ip_address,
                user_agent=params.user_agent,
                extra=params.extra or {},
                created_at=datetime.now(_BJ_TZ),
            )
            
            params.db.add(audit_entry)
            params.db.commit()
            
            logger.info(
                f"Audit: {params.event_type}.{params.action}",
                extra={
                    "request_id": request_id,
                    "event_id": event_id,
                    "event_type": params.event_type,
                    "action": params.action,
                    "operator_id": params.operator_id,
                    "operator_name": params.operator_name,
                    "resource_type": params.resource_type,
                    "resource_id": params.resource_id,
                    "result": params.result,
                    "duration_ms": duration_ms,
                    "detail": params.detail,
                },
            )
            
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}", exc_info=True)
            try:
                params.db.rollback()
            except Exception as rollback_exc:
                logger.warning(f"Failed to rollback audit log transaction: {rollback_exc}")


# Convenience function
def audit_log(
    db: Session,
    event_type: str,
    action: str,
    operator_id: str,
    **kwargs
) -> None:
    """Simple wrapper for AuditService.log"""
    params = AuditLogParams(
        db=db,
        event_type=event_type,
        action=action,
        operator_id=operator_id,
        **kwargs,
    )
    AuditService.log(params)


def list_skill_moderation_audit_logs_for_operator(
    db: Session,
    *,
    operator_id: str,
    page: int,
    page_size: int,
) -> tuple[list[AuditLog], int]:
    """当前操作者作为审核员产生的 Skill 审核类审计记录（按时间倒序，分页）。"""
    safe_page = max(1, page)
    safe_size = min(max(1, page_size), 100)
    q = (
        db.query(AuditLog)
        .filter(
            AuditLog.event_type == EVENT_SKILL_MODERATION,
            AuditLog.operator_id == operator_id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    total = q.count()
    rows = q.offset((safe_page - 1) * safe_size).limit(safe_size).all()
    return rows, total
