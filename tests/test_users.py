"""
Tests for /users/me profile endpoints and role-based access control.
"""
from app.core.security import create_access_token
from app.models.user import GlobalRole, User


async def _login(client, registered_user):
    response = await client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    return response.json()["access_token"]


class TestUpdateProfile:
    async def test_update_full_name_and_locale(self, client, registered_user):
        token = await _login(client, registered_user)

        response = await client.patch(
            "/users/me",
            json={"full_name": "Updated Name", "locale": "uz"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["full_name"] == "Updated Name"
        assert body["locale"] == "uz"

    async def test_partial_update_leaves_other_fields_untouched(self, client, registered_user):
        token = await _login(client, registered_user)

        response = await client.patch(
            "/users/me", json={"locale": "ru"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["locale"] == "ru"
        assert body["full_name"] == registered_user["body"]["full_name"]  # unchanged from registration

    async def test_update_requires_auth(self, client):
        response = await client.patch("/users/me", json={"full_name": "x"})
        assert response.status_code == 401


class TestDeleteAccount:
    async def test_delete_deactivates_account(self, client, db_session, registered_user):
        token = await _login(client, registered_user)

        response = await client.delete("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204

        # A deactivated user can no longer log in.
        login_response = await client.post(
            "/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
        assert login_response.status_code == 403


class TestRoleBasedAccess:
    async def test_student_cannot_access_admin_only_route(self, client, db_session, registered_user):
        """Simulates an admin-only dependency using the real require_role check."""
        from fastapi import APIRouter, Depends

        from app.core.deps import require_admin
        from app.main import app

        probe_router = APIRouter()

        @probe_router.get("/_test/admin-only")
        async def admin_only(_user=Depends(require_admin)):
            return {"ok": True}

        app.include_router(probe_router)

        token = await _login(client, registered_user)
        response = await client.get(
            "/_test/admin-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403

    async def test_admin_can_access_admin_only_route(self, client, db_session, registered_user):
        from fastapi import APIRouter, Depends

        from app.core.deps import require_admin
        from app.main import app

        # Promote the registered user to admin directly in the DB.
        from sqlalchemy import select

        db_user = await db_session.scalar(select(User).where(User.email == registered_user["email"]))
        db_user.role = GlobalRole.ADMIN
        await db_session.commit()

        probe_router = APIRouter()

        @probe_router.get("/_test/admin-only-2")
        async def admin_only(_user=Depends(require_admin)):
            return {"ok": True}

        app.include_router(probe_router)

        token = create_access_token(str(db_user.id))
        response = await client.get(
            "/_test/admin-only-2", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
