from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.user import GlobalRole, User
from app.routers import admin, auth, documents, questions, quizzes, users

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _promote_configured_admin()
    yield


async def _promote_configured_admin() -> None:
    """If ADMIN_EMAIL is set, make sure that account (if it already exists)
    is an approved admin — covers the case where the person registered
    before ADMIN_EMAIL was configured, so they don't need to re-register."""
    if not settings.admin_email:
        return
    async with AsyncSessionLocal() as db:
        user = await db.scalar(
            select(User).where(func.lower(User.email) == settings.admin_email.lower())
        )
        if user is not None and (user.role != GlobalRole.ADMIN or not user.is_approved):
            user.role = GlobalRole.ADMIN
            user.is_approved = True
            user.is_active = True
            await db.commit()


app = FastAPI(title="AI Assessment Platform API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(questions.router)
app.include_router(quizzes.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
