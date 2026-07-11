import logging

from sqlalchemy.orm import Session

from app.db.models import AuditLog, User
from app.utils.ids import new_id


logger = logging.getLogger("know_rag.audit")


def audit(event: str, payload: dict | None = None) -> None:
    logger.info("audit event=%s payload=%s", event, payload or {})


def record_audit(
    db: Session,
    actor: User | None,
    action: str,
    resource_type: str,
    resource_id: str,
    request_summary: dict | None = None,
    result_summary: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            id=new_id(),
            actor=actor.username if actor is not None else "",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_summary=request_summary or {},
            result_summary=result_summary or {},
        )
    )
