"""
Shared FastAPI dependencies: DB session, current-user resolution, and
role-based access checks.
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import InvalidTokenError, TokenType, decode_token
from app.db.session import get_db
from app.models.user import GlobalRole, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error

    try:
        user_id = decode_token(token, TokenType.ACCESS)
    except InvalidTokenError:
        raise credentials_error

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise credentials_error

    user = await db.scalar(select(User).where(User.id == user_uuid))
    if user is None or not user.is_active or not user.is_approved:
        raise credentials_error
    return user


async def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Email address is not verified"
        )
    return user


def require_role(*allowed_roles: GlobalRole):
    """Dependency factory: only lets through users with one of the given global roles."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {[r.value for r in allowed_roles]}",
            )
        return user

    return _check


require_admin = require_role(GlobalRole.ADMIN)
require_teacher_or_admin = require_role(GlobalRole.TEACHER, GlobalRole.ADMIN)
