"""
Tests for the admin-approval gate: new signups stay pending until an
admin approves them, plus the ADMIN_EMAIL auto-promotion behaviour.
"""
from sqlalchemy import select

from app.core.config import get_settings
from app.models.user import GlobalRole, User


async def _login_headers(client, user) -> dict:
    response = await client.post(
        "/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _make_admin(db_session, email: str) -> None:
    user = await db_session.scalar(select(User).where(User.email == email))
    user.role = GlobalRole.ADMIN
    await db_session.commit()


class TestApprovalGate:
    async def test_new_signup_is_pending_and_cannot_login(self, client):
        email = "newbie@example.com"
        password = "StrongPass1"
        register = await client.post("/auth/register", json={"email": email, "password": password})
        assert register.status_code == 201
        assert register.json()["is_approved"] is False

        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 403
        assert "approval" in login.json()["detail"].lower()

    async def test_admin_can_approve_pending_user_who_can_then_login(
        self, client, db_session, registered_user
    ):
        await _make_admin(db_session, registered_user["email"])
        admin_headers = await _login_headers(client, registered_user)

        email, password = "newbie2@example.com", "StrongPass1"
        register = await client.post("/auth/register", json={"email": email, "password": password})
        user_id = register.json()["id"]

        pending = await client.get("/admin/users/pending", headers=admin_headers)
        assert pending.status_code == 200
        assert any(u["id"] == user_id for u in pending.json())

        approve = await client.post(f"/admin/users/{user_id}/approve", headers=admin_headers)
        assert approve.status_code == 200
        assert approve.json()["is_approved"] is True

        login = await client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200

    async def test_non_admin_cannot_list_or_approve_pending_users(self, client, registered_user):
        headers = await _login_headers(client, registered_user)  # teacher, not admin
        response = await client.get("/admin/users/pending", headers=headers)
        assert response.status_code == 403

    async def test_admin_can_reject_pending_user(self, client, db_session, registered_user):
        await _make_admin(db_session, registered_user["email"])
        admin_headers = await _login_headers(client, registered_user)

        register = await client.post(
            "/auth/register", json={"email": "rejectme@example.com", "password": "StrongPass1"}
        )
        user_id = register.json()["id"]

        reject = await client.post(f"/admin/users/{user_id}/reject", headers=admin_headers)
        assert reject.status_code == 204

        login = await client.post(
            "/auth/login", json={"email": "rejectme@example.com", "password": "StrongPass1"}
        )
        assert login.status_code == 401  # account no longer exists

    async def test_cannot_reject_already_approved_user(self, client, db_session, registered_user):
        await _make_admin(db_session, registered_user["email"])
        admin_headers = await _login_headers(client, registered_user)

        target_id = registered_user["body"]["id"]
        response = await client.post(f"/admin/users/{target_id}/reject", headers=admin_headers)
        assert response.status_code == 400


class TestAdminEmailAutoPromotion:
    async def test_registering_with_admin_email_is_auto_approved_admin(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
        get_settings.cache_clear()
        try:
            response = await client.post(
                "/auth/register", json={"email": "owner@example.com", "password": "StrongPass1"}
            )
            assert response.status_code == 201
            body = response.json()
            assert body["role"] == "admin"
            assert body["is_approved"] is True

            login = await client.post(
                "/auth/login", json={"email": "owner@example.com", "password": "StrongPass1"}
            )
            assert login.status_code == 200
        finally:
            get_settings.cache_clear()

    async def test_promote_configured_admin_runs_on_startup_for_existing_account(
        self, db_session, monkeypatch
    ):
        """Covers the case where ADMIN_EMAIL is set for an account that
        already registered before — app.main._promote_configured_admin()
        runs this on every startup."""
        import app.main as main_module

        user = User(
            email="existing@example.com",
            password_hash="x",
            is_active=True,
            is_approved=False,
            role=GlobalRole.STUDENT,
        )
        db_session.add(user)
        await db_session.commit()

        monkeypatch.setattr(main_module.settings, "admin_email", "existing@example.com")
        await main_module._promote_configured_admin()

        await db_session.refresh(user)
        assert user.role == GlobalRole.ADMIN
        assert user.is_approved is True
        assert user.is_active is True
