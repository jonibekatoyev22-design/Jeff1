"""
User model — Stage 1 foundation.
"""
import enum
import uuid

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class GlobalRole(str, enum.Enum):
    """Platform-wide role. Organization-scoped roles live on OrgMember."""

    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[GlobalRole] = mapped_column(
        Enum(GlobalRole, name="global_role", values_callable=lambda obj: [e.value for e in obj]),
        default=GlobalRole.STUDENT,
        nullable=False,
    )
    locale: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Gates access until a teacher/admin approves the signup. Separate from
    # is_active (which is reused for soft account-deletion) so "pending
    # approval" and "deactivated" stay distinguishable.
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    org_memberships: Mapped[list["OrgMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
