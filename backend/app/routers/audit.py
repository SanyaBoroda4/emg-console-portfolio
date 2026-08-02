"""GET /api/audit — the ledger, admins only (managers get 403)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_role
from app.db import get_db
from app.models import AuditLog
from app.schemas import AuditListOut

router = APIRouter(
    prefix="/api/audit",
    tags=["audit"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("", response_model=AuditListOut)
def list_audit(
    review_item_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> AuditListOut:
    clauses = []
    if review_item_id is not None:
        clauses.append(AuditLog.review_item_id == review_item_id)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*clauses))
    entries = db.scalars(
        select(AuditLog)
        .where(*clauses)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return AuditListOut(entries=entries, total=total)
