import pytest

from api.routes_auth import auth_metadata


@pytest.mark.asyncio
async def test_auth_metadata_exposes_ui_and_rbac_config():
    body = await auth_metadata()
    assert body["app"]["title"]
    assert "jwt_enabled" in body["auth"]
    assert "auth_required" in body["auth"]
    assert "login_enabled" in body["auth"]
    assert "anonymous_user_id" in body["auth"]
    assert body["rbac"]["roles"]
    assert body["rbac"]["departments"]
    assert body["rbac"]["role_options"]
    assert all("role_mask" in option for option in body["rbac"]["role_options"])
