"""
Admin-only endpoints: approve or reject pending signups. Every new account
starts with is_approved=False (see User model) except the configured
ADMIN_EMAIL account, which is auto-approved — everyone else needs a
teacher/admin to let them in here.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_admin
from app.models.user import User
from app.schemas.auth import UserOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users/pending", response_model=list[UserOut])
async def list_pending_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    result = await db.scalars(
        select(User).where(User.is_approved.is_(False)).order_by(User.created_at)
    )
    return list(result.all())


@router.post("/users/{user_id}/approve", response_model=UserOut)
async def approve_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_approved = True
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users/{user_id}/reject", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def reject_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Rejects (deletes) a pending signup. Does nothing to an already
    approved account — use account deactivation for that instead."""
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already approved — this endpoint is only for pending signups",
        )
    await db.delete(user)
    await db.commit()
