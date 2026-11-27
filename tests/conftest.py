"""
Shared pytest fixtures: isolated in-memory SQLite DB per test + an
httpx AsyncClient wired to the FastAPI app with the DB dependency
overridden.

Tests never touch the real PostgreSQL instance.
"""
import os

# Required settings must exist before app.core.config.Settings() is
# instantiated anywhere (it is read at import time via get_settings()).
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
import app.db.session as db_session_module
import app.main as main_module
import app.routers.documents as documents_module
from app.db.session import Base, get_db
from app.main import app as fastapi_app
from app.models.user import GlobalRole, User


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    # Background tasks (e.g. document extraction) open their own session via
    # app.db.session.AsyncSessionLocal rather than the get_db dependency, so
    # the dependency override above doesn't reach them. Point that name — and
    # routers that imported it by value — at this test's session factory too,
    # so both the request and the background task see the same StaticPool
    # in-memory database instead of two unrelated ":memory:" databases.
    monkeypatch.setattr(db_session_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(documents_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(main_module, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def registered_user(client, db_session):
    """Registers a user and returns (email, password) for convenience.

    Every new signup starts pending (is_approved=False) and role=student —
    that's the real flow, covered by its own tests in test_admin.py. Most
    other tests just want *a* logged-in user who can exercise the
    document/quiz-authoring endpoints, so this fixture fast-forwards past
    the approval queue and promotes to teacher here, directly in the DB."""
    email = "teacher@example.com"
    password = "StrongPass1"
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Test Teacher"},
    )
    assert response.status_code == 201

    user = await db_session.scalar(select(User).where(User.email == email))
    user.role = GlobalRole.TEACHER
    user.is_approved = True
    await db_session.commit()

    return {"email": email, "password": password, "body": response.json()}


@pytest_asyncio.fixture
async def student_user(client, db_session):
    """An approved, plain-student account — for the student-facing
    quiz-taking endpoints, where teacher/admin authoring rights should NOT
    be granted."""
    email = "student@example.com"
    password = "StrongPass1"
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Test Student"},
    )
    assert response.status_code == 201

    user = await db_session.scalar(select(User).where(User.email == email))
    user.is_approved = True
    await db_session.commit()

    return {"email": email, "password": password, "body": response.json()}
